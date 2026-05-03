"""
Wire alerting.

Tier 1 simply logs structured events. Tier 2 will publish wire.* system events
to The Agora; this module is the seam where that happens. Keep the public
function signature stable so Tier 2 only swaps the implementation.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Optional

logger = logging.getLogger("wire.alerts")

# Sink for Agora publishing — Tier 2/3 wires this in. Tier 1 leaves it None.
_agora_publisher: Optional[Callable[[str, dict[str, Any]], None]] = None


def set_agora_publisher(publisher: Optional[Callable[[str, dict[str, Any]], None]]) -> None:
    """Inject an Agora publisher of signature (event_class, payload)."""
    global _agora_publisher
    _agora_publisher = publisher


def log_alert(event_class: str, payload: dict[str, Any]) -> None:
    """Emit a Wire alert. Always logs structured. If an Agora publisher has
    been registered, also forwards to it."""
    logger.warning("wire.alert", extra={"event_class": event_class, **payload})
    if _agora_publisher is not None:
        try:
            _agora_publisher(event_class, payload)
        except Exception:  # pragma: no cover
            logger.exception("wire.agora_publish_failed", extra={"event_class": event_class})
