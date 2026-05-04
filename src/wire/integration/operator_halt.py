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

PRODUCER FAIL-CLOSED SEMANTICS (symmetric with consumer):
  When `_halt_store` is set and a Redis write FAILS, the producer
  refuses to silently relocate the gap into its own in-process
  `_ACTIVE` list (which is invisible to consumers in different
  subprocesses — that would re-create the original cross-process bug
  at a new layer). Instead it:
    1. Logs CRITICAL with event_id / severity / exception detail.
    2. Posts to system-alerts via the alert publisher set via
       `set_alert_publisher(callable)` (Wire scheduler bootstrap wires
       this to Agora). Cross-process observable.
    3. Raises `OperatorHaltPublishError`. The caller (digester) decides
       whether to absorb or re-raise; today the digester catches and
       logs because the alert path has already screamed.
  This makes producer-side failure as loud and observable as the
  consumer side — both directions, both observable, neither silent.

  The module-level `_ACTIVE` Python list is in-process-only and is
  populated ONLY when no Redis store is configured at all
  (test fixtures, pre-bootstrap code paths). It is NEVER used as a
  silent fallback when a configured Redis store fails — that path
  raises. See
  `tests/test_operator_halt_consumer_wiring.py::
  test_producer_halt_publish_fails_closed_when_redis_raises` for the
  Critic-mandated guard.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

from src.wire.constants import (
    OPERATOR_HALT_EVENT_TYPES,
    SEVERITY_CRITICAL,
)
from src.wire.integration.halt_store import RedisHaltStore, make_halt_record

logger = logging.getLogger(__name__)


class OperatorHaltPublishError(RuntimeError):
    """Raised by `publish_halt_for_event` when a configured RedisHaltStore
    write fails. The producer refuses to silently fall back to its
    in-process `_ACTIVE` list (which is invisible cross-process). This
    exception forces the failure into the calling path — the digester
    catches it, the test_producer_halt_publish_fails_closed_when_redis_raises
    test asserts it, and the alert publisher mirrors the failure to
    system-alerts in parallel. See module docstring for the full
    fail-closed contract."""

    def __init__(
        self,
        message: str,
        *,
        trigger_event_id: int,
        coin: Optional[str],
        exchange: Optional[str],
        event_type: str,
        underlying: Exception,
    ) -> None:
        super().__init__(message)
        self.trigger_event_id = trigger_event_id
        self.coin = coin
        self.exchange = exchange
        self.event_type = event_type
        self.underlying = underlying


# Optional alert publisher. Wire scheduler bootstrap registers a
# callable that mirrors producer-side failures to system-alerts via
# Agora — cross-process observable. If unset, the CRITICAL log + raise
# are still loud, but Agora propagation is skipped. Same shape as
# `WireTicker.publisher`.
AlertPublisher = Callable[[str, dict], None]
_alert_publisher: Optional[AlertPublisher] = None


def set_alert_publisher(publisher: Optional[AlertPublisher]) -> None:
    """Wire scheduler bootstrap registers a publisher that posts the
    `wire.operator_halt.publish_failed` event class to Agora's
    system-alerts channel. Pure injection; no Agora import here."""
    global _alert_publisher
    _alert_publisher = publisher


def get_alert_publisher() -> Optional[AlertPublisher]:
    """Test/runtime introspection."""
    return _alert_publisher


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

    # PRIMARY PATH (production): write through to Redis when the
    # producer has been initialized via set_halt_store(). This is what
    # the consumer reads cross-process.
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
            # FAIL-CLOSED-AND-LOUD (Critic Finding 1, iteration 5):
            # The producer's in-process _ACTIVE is invisible to consumers
            # in different subprocesses. Silently appending here would
            # re-create the original cross-process gap one layer deeper.
            # Instead: scream + raise. The caller (digester) catches and
            # decides what to do; the alert mirror posts to system-alerts
            # so the failure is observable in any subprocess.
            logger.critical(
                "wire.operator_halt.redis_write_failed",
                extra={
                    "trigger_event_id": signal.trigger_event_id,
                    "coin": signal.coin,
                    "exchange": signal.exchange,
                    "event_type": signal.event_type,
                    "severity": signal.severity,
                    "error": str(exc),
                    "error_type": type(exc).__name__,
                },
            )
            if _alert_publisher is not None:
                try:
                    _alert_publisher(
                        "wire.operator_halt.publish_failed",
                        {
                            "trigger_event_id": signal.trigger_event_id,
                            "coin": signal.coin,
                            "exchange": signal.exchange,
                            "event_type": signal.event_type,
                            "severity": signal.severity,
                            "error": str(exc),
                            "error_type": type(exc).__name__,
                            "summary": (
                                f"Wire severity-5 halt could not be written "
                                f"to Redis. trigger_event_id="
                                f"{signal.trigger_event_id} "
                                f"event_type={signal.event_type} "
                                f"coin={signal.coin or '*'}. "
                                f"Cross-process visibility broken until "
                                f"resolved."
                            ),
                        },
                    )
                except Exception:
                    # The alert publisher is best-effort (it logs its
                    # own failure). The CRITICAL log + raise below are
                    # the load-bearing loudness — alert is a bonus.
                    logger.exception("wire.operator_halt.alert_publish_failed")
            raise OperatorHaltPublishError(
                f"Failed to publish operator halt to Redis: "
                f"trigger_event_id={signal.trigger_event_id} "
                f"event_type={signal.event_type} "
                f"coin={signal.coin or '*'} ({type(exc).__name__}: {exc})",
                trigger_event_id=signal.trigger_event_id,
                coin=signal.coin,
                exchange=signal.exchange,
                event_type=signal.event_type,
                underlying=exc,
            ) from exc

        logger.warning(
            "wire.operator_halt.issued",
            extra={
                "trigger_event_id": signal.trigger_event_id,
                "coin": signal.coin,
                "exchange": signal.exchange,
                "event_type": signal.event_type,
                "expires_at": signal.expires_at.isoformat(),
                "path": "redis",
            },
        )
        return signal

    # NO-STORE PATH: in-process only. Used by tests and pre-bootstrap
    # code paths where no Redis store has been configured. NOT a
    # production failure mode — production runners initialize the store
    # before the digester runs (and fail fast if construction fails).
    _ACTIVE.append(signal)
    logger.warning(
        "wire.operator_halt.issued",
        extra={
            "trigger_event_id": signal.trigger_event_id,
            "coin": signal.coin,
            "exchange": signal.exchange,
            "event_type": signal.event_type,
            "expires_at": signal.expires_at.isoformat(),
            "path": "in_memory_no_store",
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
