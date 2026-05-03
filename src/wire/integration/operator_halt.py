"""
Operator halt hook.

Severity-5 events with event_type in OPERATOR_HALT_EVENT_TYPES (exchange
outage, withdrawal halt, chain halt) raise an OperatorHaltSignal.
The Operator process polls for active signals at the start of each cycle
and skips trade execution for the affected coin/exchange while the signal
is in effect.

The halt is intentionally narrow:
  - per-coin (event.coin), not colony-wide
  - auto-expires after `auto_expire_minutes`
  - explicit Genesis re-enable also clears it

This module's surface is small: publish, list_active, expire_stale.
The actual Operator integration lives in the trading layer; this is the
publishing seam.
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

logger = logging.getLogger(__name__)


# Default duration for an auto-resume timer. The kickoff calls for "30 min if
# no follow-up event"; we surface this as a constant so policy tuning is
# trivial via the parameter registry later.
DEFAULT_AUTO_EXPIRE_MINUTES = 30


@dataclass(slots=True, frozen=True)
class OperatorHaltSignal:
    """One operator halt request, derived from a severity-5 event.

    The signal is purposefully immutable; the registry decides when to clear.
    """

    trigger_event_id: int
    coin: Optional[str]
    event_type: str
    severity: int
    issued_at: datetime
    expires_at: datetime
    summary: str

    def is_active(self, *, now: Optional[datetime] = None) -> bool:
        now = now or datetime.now(timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        expires = self.expires_at
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        return expires > now


# In-process registry. The Wire scheduler runs in its own process; the
# Operator process consults this via a shared persistence layer. For Phase 10
# we keep the registry in Redis-friendly memory; downstream phases may move
# it to a dedicated table.
_ACTIVE: list[OperatorHaltSignal] = []


def reset_registry() -> None:
    """Test seam — empties the in-process registry."""
    _ACTIVE.clear()


def publish_halt_for_event(
    *,
    event_id: int,
    coin: Optional[str],
    event_type: str,
    severity: int,
    summary: str,
    auto_expire_minutes: int = DEFAULT_AUTO_EXPIRE_MINUTES,
    now: Optional[datetime] = None,
) -> Optional[OperatorHaltSignal]:
    """Issue a halt signal if this event qualifies. Returns the signal or None.

    Qualifies if severity == 5 AND event_type ∈ OPERATOR_HALT_EVENT_TYPES.
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
    )
    _ACTIVE.append(signal)
    logger.warning(
        "wire.operator_halt.issued",
        extra={
            "trigger_event_id": signal.trigger_event_id,
            "coin": signal.coin,
            "event_type": signal.event_type,
            "expires_at": signal.expires_at.isoformat(),
        },
    )
    return signal


def list_active(
    *,
    now: Optional[datetime] = None,
    coin: Optional[str] = None,
) -> list[OperatorHaltSignal]:
    """Return active signals, optionally filtered by coin."""
    now = now or datetime.now(timezone.utc)
    active = [s for s in _ACTIVE if s.is_active(now=now)]
    if coin is not None:
        active = [s for s in active if (s.coin is None) or (s.coin == coin)]
    return active


def expire_stale(*, now: Optional[datetime] = None) -> int:
    """Drop expired signals. Returns the count removed."""
    now = now or datetime.now(timezone.utc)
    before = len(_ACTIVE)
    survivors = [s for s in _ACTIVE if s.is_active(now=now)]
    _ACTIVE.clear()
    _ACTIVE.extend(survivors)
    return before - len(_ACTIVE)
