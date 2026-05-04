"""
Operator halt hook.

Severity-5 events with event_type in OPERATOR_HALT_EVENT_TYPES (exchange
outage, withdrawal halt, chain halt) raise an OperatorHaltSignal.
The Operator process queries the active halt list before executing any
trade and rejects trades whose (coin, exchange) is currently halted.

The halt is intentionally narrow:
  - per-coin-per-exchange, not colony-wide
  - auto-expires after `auto_expire_minutes` (Redis TTL)
  - explicit Genesis re-enable / operator override also clears it

PERSISTENCE (Redis is the source of truth):
  Producer (`publish_halt_for_event`) writes through `RedisHaltStore`
  when the module-level `_halt_store` has been initialized via
  `set_halt_store(store)`. Wire scheduler bootstrap (`src/wire/cli.py`)
  initializes it; agent runtime bootstrap (`scripts/run_agents.py`)
  initializes it on the consumer side. Both subprocesses thus point at
  the same Memurai instance and SEE THE SAME HALTS.

  The module-level `_ACTIVE` Python list is now defense-in-depth ONLY:
    - It receives writes when Redis writes fail (so a transient Redis
      blip doesn't lose the halt entirely on the producer side).
    - It is consulted by the consumer ONLY when `_halt_state_unknown`
      is set on the trading service AND Redis is the cause.
    - It is NEVER used as a silent primary path. See
      `tests/test_operator_halt_consumer_wiring.py::
      test_in_memory_fallback_used_only_when_state_unknown` for the
      contract guard.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from src.wire.constants import (
    OPERATOR_HALT_EVENT_TYPES,
    SEVERITY_CRITICAL,
)
from src.wire.integration.halt_store import RedisHaltStore, make_halt_record

logger = logging.getLogger(__name__)


# Default duration for an auto-resume timer. The kickoff calls for "30 min if
# no follow-up event"; we surface this as a constant so policy tuning is
# trivial via the parameter registry later.
DEFAULT_AUTO_EXPIRE_MINUTES = 30


@dataclass(slots=True, frozen=True)
class OperatorHaltSignal:
    """One operator halt request, derived from a severity-5 event.

    The signal is purposefully immutable; the registry decides when to clear.

    Scope semantics (per-coin-per-exchange, per Phase 10 kickoff):
      - `coin = None` ⇒ applies to ALL coins (rare; e.g., colony-wide halt)
      - `coin = "BTC"` ⇒ applies only to BTC trades
      - `exchange = None` ⇒ applies to ALL exchanges (default; safest)
      - `exchange = "kraken"` ⇒ applies only to Kraken trades

    Today's Wire source classification doesn't populate `exchange` at the
    digester layer (the kickoff didn't carry exchange through the event
    schema). Producers leave exchange=None, which means "block this coin
    on every exchange" — the safe-by-default reading. When source-side
    classification distinguishes "Kraken withdrawal_halt" from "Solana
    chain_halt", producers can populate `exchange` and the consumer will
    automatically narrow scope. See DEFERRED_ITEMS_TRACKER.md "Wire
    source-side exchange classification" entry added with this hotfix.
    """

    trigger_event_id: int
    coin: Optional[str]
    event_type: str
    severity: int
    issued_at: datetime
    expires_at: datetime
    summary: str
    exchange: Optional[str] = None

    def is_active(self, *, now: Optional[datetime] = None) -> bool:
        now = now or datetime.now(timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        expires = self.expires_at
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        return expires > now


# IN-MEMORY ONLY, PROCESS-LOCAL. No DB row, no Redis key, no file
# persistence. The list lives in this Python module's globals; readers
# and writers in the SAME process see the same list, but readers in a
# DIFFERENT process see their own (empty) list.
#
# That matters in production today: the Wire digester runs in the
# wire_scheduler subprocess and writes here; PaperTradingService runs in
# the agents subprocess and reads here; those are different processes.
# Halts published by the digester are NOT visible to the trading service.
# Tests pass because they exercise both sides in a single Python process.
#
# Tracked in DEFERRED_ITEMS_TRACKER.md "Wire halt cross-process
# visibility"; the persistence-layer plan there closes the cross-process
# gap. Until that lands, an Arena run that boots both subprocesses cannot
# rely on Wire-published halts to gate trades cross-process.
#
# Expiry policy: filter-on-read. `list_active` excludes signals whose
# `is_active(now=...)` returns False. There is NO background sweeper —
# expired signals remain in `_ACTIVE` until the next manual call to
# `expire_stale()` or until the process restarts. Tests of expiry
# semantics rely on the filter-on-read behavior in `list_active`, NOT on
# any sweeper running in the background.
_ACTIVE: list[OperatorHaltSignal] = []


# Optional Redis-backed halt store. Producer-side bootstrap (the wire
# scheduler at src/wire/cli.py) calls `set_halt_store(store)` at startup;
# from then on `publish_halt_for_event` writes-through to Redis.
# The consumer side (PaperTradingService) reads from its own
# RedisHaltStore reference passed in at construction; it does NOT rely
# on the module-level `_halt_store` here. Two distinct instances point
# at the same Memurai keyspace.
_halt_store: Optional[RedisHaltStore] = None


def set_halt_store(store: Optional[RedisHaltStore]) -> None:
    """Producer-side initialization. Pass the same RedisHaltStore the
    consumer side will read from (same Memurai instance + key prefix)."""
    global _halt_store
    _halt_store = store


def get_halt_store() -> Optional[RedisHaltStore]:
    """Test/runtime introspection."""
    return _halt_store


def reset_registry() -> None:
    """Test seam — empties the in-process registry. Does NOT touch Redis;
    tests that need a clean Redis namespace should pass a unique
    `key_prefix` to RedisHaltStore."""
    _ACTIVE.clear()


def publish_halt_for_event(
    *,
    event_id: int,
    coin: Optional[str],
    event_type: str,
    severity: int,
    summary: str,
    exchange: Optional[str] = None,
    auto_expire_minutes: int = DEFAULT_AUTO_EXPIRE_MINUTES,
    now: Optional[datetime] = None,
) -> Optional[OperatorHaltSignal]:
    """Issue a halt signal if this event qualifies. Returns the signal or None.

    Qualifies if severity == 5 AND event_type ∈ OPERATOR_HALT_EVENT_TYPES.

    `exchange` is optional; producers that don't yet populate it leave
    None, which means "halt this coin on every exchange". Producers that
    do populate it (e.g., a future Kraken-specific source upgrade) get
    narrower per-coin-per-exchange scope automatically.
    """
    if severity != SEVERITY_CRITICAL:
        return None
    if event_type not in OPERATOR_HALT_EVENT_TYPES:
        return None
    issued = now or datetime.now(timezone.utc)
    if issued.tzinfo is None:
        issued = issued.replace(tzinfo=timezone.utc)
    signal = OperatorHaltSignal(
        trigger_event_id=int(event_id),
        coin=coin,
        event_type=event_type,
        severity=int(severity),
        issued_at=issued,
        expires_at=issued + timedelta(minutes=int(auto_expire_minutes)),
        summary=summary,
        exchange=exchange,
    )

    # PRIMARY PATH: write through to Redis when the producer has been
    # initialized via set_halt_store(). This is what the consumer reads
    # in production. _ACTIVE only acts as a fallback when Redis writes
    # fail — see DEFENSE-IN-DEPTH below.
    redis_write_failed = False
    if _halt_store is not None:
        try:
            ttl_seconds = max(1, int((signal.expires_at - issued).total_seconds()))
            _halt_store.publish(
                coin=coin if coin is not None else "*",
                exchange=exchange,
                halt_record=make_halt_record(
                    event_id=event_id,
                    coin=coin,
                    exchange=exchange,
                    event_type=event_type,
                    severity=int(severity),
                    summary=summary,
                    issued_at=issued,
                    expires_at=signal.expires_at,
                ),
                ttl_seconds=ttl_seconds,
            )
        except Exception as exc:
            redis_write_failed = True
            logger.critical(
                "wire.operator_halt.redis_write_failed",
                extra={
                    "trigger_event_id": signal.trigger_event_id,
                    "coin": signal.coin,
                    "exchange": signal.exchange,
                    "event_type": signal.event_type,
                    "error": str(exc),
                },
            )

    # DEFENSE-IN-DEPTH: the in-memory _ACTIVE list is populated when
    # there is no Redis store OR the Redis write failed. The consumer
    # consults it ONLY when its own _halt_state_unknown flag is set
    # (i.e., its own Redis read has also failed). This ensures the
    # in-memory list is never silently used as a primary path.
    if _halt_store is None or redis_write_failed:
        _ACTIVE.append(signal)

    logger.warning(
        "wire.operator_halt.issued",
        extra={
            "trigger_event_id": signal.trigger_event_id,
            "coin": signal.coin,
            "exchange": signal.exchange,
            "event_type": signal.event_type,
            "expires_at": signal.expires_at.isoformat(),
            "redis_path": _halt_store is not None and not redis_write_failed,
            "in_memory_fallback": _halt_store is None or redis_write_failed,
        },
    )
    return signal


def list_active(
    *,
    now: Optional[datetime] = None,
    coin: Optional[str] = None,
    exchange: Optional[str] = None,
) -> list[OperatorHaltSignal]:
    """Return active signals matching the (coin, exchange) query.

    Match rules (per-coin-per-exchange):
      - signal.coin == None matches any coin query (covers colony-wide halts)
      - signal.coin == query.coin matches
      - signal.exchange == None matches any exchange query (broad halt)
      - signal.exchange == query.exchange matches
      - All other combinations exclude

    Calling with `coin=None` AND `exchange=None` returns every active
    signal (the audit / dashboard view).
    """
    now = now or datetime.now(timezone.utc)
    active = [s for s in _ACTIVE if s.is_active(now=now)]
    if coin is not None:
        active = [s for s in active if (s.coin is None) or (s.coin == coin)]
    if exchange is not None:
        active = [
            s for s in active
            if (s.exchange is None) or (s.exchange == exchange)
        ]
    return active


def expire_stale(*, now: Optional[datetime] = None) -> int:
    """Drop expired signals. Returns the count removed."""
    now = now or datetime.now(timezone.utc)
    before = len(_ACTIVE)
    survivors = [s for s in _ACTIVE if s.is_active(now=now)]
    _ACTIVE.clear()
    _ACTIVE.extend(survivors)
    return before - len(_ACTIVE)
