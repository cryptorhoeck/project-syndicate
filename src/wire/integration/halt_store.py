"""
Redis-backed Operator halt store.

Closes the cross-process gap surfaced by Critic Finding 3 in
hotfix/operator-halt-consumer-wiring iteration 4: the previous
`_ACTIVE` registry was a module-level Python list, process-local. The
producer (digester in the wire_scheduler subprocess) wrote to its own
copy; the consumer (PaperTradingService in the agents subprocess) read
from a separate, always-empty copy. Tests passed because they exercised
both sides in a single Python process.

Redis is now the source of truth for active halts. Memurai is already
running in production; native Redis TTL matches the 30-min auto-expiry
semantics; cross-process reads are sub-millisecond. The module-level
`_ACTIVE` in `operator_halt.py` becomes defense-in-depth only — it is
populated when Redis writes fail AND consulted only when the consumer's
`_halt_state_unknown` flag is set, never as a silent primary path.

Key pattern: ``wire:halt:{coin}:{exchange}``
  - `coin` is the canonical coin symbol (e.g., ``BTC``, ``ETH``)
  - `exchange` is the venue (e.g., ``Kraken``, ``Binance``) OR the literal
    string ``*`` for a wildcard (cross-exchange) halt.

A `is_halted(coin, exchange)` query checks BOTH the specific key
``wire:halt:{coin}:{exchange}`` AND the wildcard key ``wire:halt:{coin}:*``,
returning halted=True if either exists. This honors the Phase 10
per-coin-per-exchange scope and gracefully widens to "halt this coin
on every exchange" when the producer leaves exchange unset.

Test isolation: tests can pass a unique `key_prefix` (default
``wire:halt``) so concurrent test runs don't collide on the production
namespace.

EXPIRY MECHANISM (Critic Finding 5, iteration 5):
Native Redis TTL. `publish()` calls `SET key value EX ttl_seconds`,
which atomically writes the record and arms Redis's own TTL clock.
Redis auto-deletes the key when the TTL elapses, so `is_halted()`
naturally returns False past `expires_at` without any filter-on-read
logic. No background sweeper, no manual expiry walk — Redis is the
clock. The auto-lift test
`tests/test_operator_halt_consumer_wiring.py::
test_halt_store_publish_with_ttl_expires_via_redis` exercises this
exact path with a 1-second TTL.
"""

from __future__ import annotations

__version__ = "0.1.0"

import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)


WILDCARD_EXCHANGE = "*"


class RedisHaltStore:
    """Cross-process halt registry backed by Redis. See module docstring.

    Construction contract: a non-None redis client is required. The runtime
    (`scripts/run_agents.py`, `src/wire/cli.py`) constructs the store at
    startup and `sys.exit(2)`s on failure — same wiring contract as Warden
    and TradeExecutionService.
    """

    def __init__(
        self,
        redis_client,
        *,
        key_prefix: str = "wire:halt",
    ) -> None:
        if redis_client is None:
            # Same fail-fast contract the directive locks in for the
            # broader wiring sweep. Caller is expected to construct
            # before calling — but a defensive sys.exit here ensures a
            # future caller cannot silently drop the contract.
            logger.critical(
                "halt_store_constructed_without_redis_client",
                extra={"key_prefix": key_prefix},
            )
            sys.exit(2)
        self.redis = redis_client
        self.key_prefix = key_prefix.rstrip(":")

    # ------------------------------------------------------------------
    # Key helpers
    # ------------------------------------------------------------------

    def _key(self, coin: str, exchange: Optional[str]) -> str:
        ex = exchange if exchange is not None else WILDCARD_EXCHANGE
        return f"{self.key_prefix}:{coin}:{ex}"

    def _wildcard_key(self, coin: str) -> str:
        return f"{self.key_prefix}:{coin}:{WILDCARD_EXCHANGE}"

    def _scan_pattern(self) -> str:
        return f"{self.key_prefix}:*"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def publish(
        self,
        coin: str,
        exchange: Optional[str],
        halt_record: dict[str, Any],
        ttl_seconds: int,
    ) -> None:
        """Write a halt record. Native Redis TTL handles auto-expiry.

        `halt_record` is JSON-serialized; downstream readers
        (`is_halted`, `list_active`) deserialize and surface the dict
        verbatim. Producers should populate at least:
            event_id, severity, source/event_type, expires_at (ISO),
            coin, exchange (None or string)
        but the store is dict-shape-agnostic — what goes in comes out.
        """
        key = self._key(coin, exchange)
        payload = json.dumps(halt_record, default=str)
        # SET key value EX ttl — atomic write + TTL set in one command.
        # If Redis raises, the caller's fail-fast path triggers.
        self.redis.set(key, payload, ex=int(ttl_seconds))

    def is_halted(
        self,
        coin: str,
        exchange: Optional[str],
    ) -> tuple[bool, Optional[dict[str, Any]]]:
        """Returns (True, record) if a halt blocks (coin, exchange), else
        (False, None). Checks the specific key first, then the wildcard
        ``coin:*`` key. The most-specific match wins for the returned
        record."""
        # Most-specific first.
        if exchange is not None:
            specific = self._key(coin, exchange)
            payload = self.redis.get(specific)
            if payload:
                return True, _decode_payload(payload)

        # Wildcard fallback.
        wildcard = self._wildcard_key(coin)
        payload = self.redis.get(wildcard)
        if payload:
            return True, _decode_payload(payload)

        return False, None

    def list_active(self) -> list[dict[str, Any]]:
        """All currently-active halt records. Redis TTL drops expired
        keys automatically before this can see them, so the returned
        list is by definition non-expired.

        Used by dashboards / audit; not part of the trade-time gate.
        """
        out: list[dict[str, Any]] = []
        cursor = 0
        pattern = self._scan_pattern()
        # SCAN avoids blocking Redis on KEYS for large keyspaces.
        while True:
            cursor, keys = self.redis.scan(cursor=cursor, match=pattern, count=100)
            for key in keys:
                payload = self.redis.get(key)
                if payload:
                    out.append(_decode_payload(payload))
            if cursor == 0:
                break
        return out

    def clear(
        self,
        coin: str,
        exchange: Optional[str],
    ) -> None:
        """Manual delete. Used for tests and operator override (e.g.,
        Genesis revoking a halt)."""
        self.redis.delete(self._key(coin, exchange))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _decode_payload(payload) -> dict[str, Any]:
    """Decode whatever Redis returned. Handles bytes (raw client) and
    str (decode_responses=True) transparently."""
    if isinstance(payload, bytes):
        payload = payload.decode("utf-8")
    return json.loads(payload)


def make_halt_record(
    *,
    event_id: int,
    coin: Optional[str],
    exchange: Optional[str],
    event_type: str,
    severity: int,
    summary: str,
    issued_at: datetime,
    expires_at: datetime,
) -> dict[str, Any]:
    """Build the canonical halt-record dict producers push into the
    store. Centralised so the schema doesn't drift between
    producer (operator_halt.publish_halt_for_event) and consumer
    (execution_service._check_operator_halt)."""
    return {
        "trigger_event_id": int(event_id),
        "coin": coin,
        "exchange": exchange,
        "event_type": event_type,
        "severity": int(severity),
        "summary": summary,
        "issued_at": issued_at.isoformat(),
        "expires_at": expires_at.isoformat(),
    }
