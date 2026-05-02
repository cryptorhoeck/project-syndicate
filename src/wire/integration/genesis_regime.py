"""
Genesis regime review hook.

Any wire_event with severity == 5 (deterministic only — Haiku cannot escalate
to 5) triggers a one-shot call to Genesis.review_regime(trigger_event_id=...).

This does NOT inject strategy. Genesis re-evaluates its existing regime
detection logic with the new signal as input. The hook is decoupled: callers
register a function via `register_severity_5_review_hook(func)`, and the
digester invokes it whenever it persists a severity-5 event.

Multiple hooks are supported (list), so dashboards and Operator halt logic
can subscribe alongside Genesis.
"""

from __future__ import annotations

import logging
from typing import Callable, Optional

from src.wire.constants import SEVERITY_CRITICAL

logger = logging.getLogger(__name__)


GenesisRegimeReviewHook = Callable[["dict"], None]


_HOOKS: list[GenesisRegimeReviewHook] = []


def register_severity_5_review_hook(func: GenesisRegimeReviewHook) -> None:
    """Add a callback to be invoked whenever a severity-5 event is digested.

    Callbacks receive a dict with keys: event_id, coin, event_type, summary,
    occurred_at (ISO string).
    """
    _HOOKS.append(func)


def reset_hooks() -> None:
    """Test seam."""
    _HOOKS.clear()


def fire_severity_5_hooks(payload: dict) -> int:
    """Invoke all registered hooks. Returns the count fired."""
    count = 0
    for hook in list(_HOOKS):
        try:
            hook(payload)
            count += 1
        except Exception:
            logger.exception("wire.genesis_regime.hook_failed")
    return count


def maybe_dispatch(
    *,
    event_id: int,
    severity: int,
    coin: Optional[str],
    event_type: str,
    summary: str,
    occurred_at_iso: Optional[str],
) -> bool:
    """Dispatch to hooks if severity == 5. Returns True if any fired."""
    if severity != SEVERITY_CRITICAL:
        return False
    payload = {
        "event_id": int(event_id),
        "severity": int(severity),
        "coin": coin,
        "event_type": event_type,
        "summary": summary,
        "occurred_at": occurred_at_iso,
    }
    count = fire_severity_5_hooks(payload)
    return count > 0
