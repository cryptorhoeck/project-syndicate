"""
Wire Ticker — push side.

Any wire_event with severity >= TICKER_PUBLISH_MIN_SEVERITY (3) and
duplicate_of IS NULL is published as a `wire.ticker` Agora event the moment
the digester writes it. We mark `published_to_ticker = TRUE` so we don't
double-publish on retries.

The Ticker itself is publisher-agnostic: callers inject a callable
`(event_class: str, payload: dict) -> None`. Production wires this to the
Agora system message API; tests pass a list-capturing fake.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable, Optional, Protocol

from sqlalchemy.orm import Session

from src.wire.constants import AGORA_EVENT_TICKER, TICKER_PUBLISH_MIN_SEVERITY
from src.wire.models import WireEvent

logger = logging.getLogger(__name__)


class TickerPublisher(Protocol):
    def __call__(self, event_class: str, payload: dict) -> None: ...


def _serialize_event(event: WireEvent) -> dict:
    """Convert a WireEvent ORM row to a JSON-serializable ticker payload."""
    return {
        "id": event.id,
        "coin": event.coin,
        "is_macro": bool(event.is_macro),
        "event_type": event.event_type,
        "severity": int(event.severity),
        "direction": event.direction,
        "summary": event.summary,
        "source_url": event.source_url,
        "occurred_at": event.occurred_at.isoformat() if event.occurred_at else None,
    }


@dataclass
class WireTicker:
    """Sync ticker. Inject a publisher; call publish_event(event) after digestion."""

    publisher: Optional[TickerPublisher] = None

    def publish_event(
        self,
        session: Session,
        event: WireEvent,
    ) -> bool:
        """Publish if severity threshold and not duplicate. Returns True if published."""
        if event.severity < TICKER_PUBLISH_MIN_SEVERITY:
            return False
        if event.duplicate_of is not None:
            return False
        if event.published_to_ticker:
            return False

        payload = _serialize_event(event)
        if self.publisher is not None:
            try:
                self.publisher(AGORA_EVENT_TICKER, payload)
            except Exception:
                logger.exception("wire.ticker.publish_failed")
                return False

        event.published_to_ticker = True
        session.add(event)
        return True


def make_agora_publisher(
    post_system_message: Callable[..., object],
    *,
    channel: str = "system-alerts",
) -> TickerPublisher:
    """Adapt the project's Agora `post_system_message` (async) into a sync sink.

    The actual integration is wired up at the Agora-process boundary; this
    helper exists so the Wire CLI / scheduler can plug in without importing
    Agora directly. Production callers will likely build their own adapter that
    schedules onto the running event loop.
    """

    import asyncio

    def _publish(event_class: str, payload: dict) -> None:
        coro = post_system_message(
            channel=channel,
            content=f"[{event_class}] {payload.get('summary', '')}",
            metadata={"event_class": event_class, **payload},
        )
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = None
        if loop and loop.is_running():
            asyncio.ensure_future(coro)
        else:
            asyncio.run(coro)

    return _publish
