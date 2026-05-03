"""WireTicker tests."""

from datetime import datetime, timezone

from sqlalchemy import select

from src.wire.constants import AGORA_EVENT_TICKER
from src.wire.models import WireEvent
from src.wire.publishing.ticker import WireTicker


def _make_event(session, *, severity: int, duplicate_of=None,
                published: bool = False) -> WireEvent:
    evt = WireEvent(
        canonical_hash=f"h-{severity}-{duplicate_of}-{published}",
        coin="BTC",
        event_type="other",
        severity=severity,
        summary="x",
        occurred_at=datetime.now(timezone.utc),
        duplicate_of=duplicate_of,
        published_to_ticker=published,
    )
    session.add(evt)
    session.commit()
    return evt


class TestPublishGate:
    def test_below_threshold_not_published(self, wire_seeded_session) -> None:
        captured = []
        ticker = WireTicker(publisher=lambda c, p: captured.append((c, p)))
        evt = _make_event(wire_seeded_session, severity=2)
        published = ticker.publish_event(wire_seeded_session, evt)
        assert published is False
        assert captured == []
        assert evt.published_to_ticker is False

    def test_at_threshold_published(self, wire_seeded_session) -> None:
        captured = []
        ticker = WireTicker(publisher=lambda c, p: captured.append((c, p)))
        evt = _make_event(wire_seeded_session, severity=3)
        published = ticker.publish_event(wire_seeded_session, evt)
        wire_seeded_session.commit()
        assert published is True
        assert len(captured) == 1
        assert captured[0][0] == AGORA_EVENT_TICKER
        assert captured[0][1]["severity"] == 3
        assert evt.published_to_ticker is True

    def test_critical_published(self, wire_seeded_session) -> None:
        captured = []
        ticker = WireTicker(publisher=lambda c, p: captured.append((c, p)))
        evt = _make_event(wire_seeded_session, severity=5)
        ticker.publish_event(wire_seeded_session, evt)
        assert len(captured) == 1
        assert captured[0][1]["severity"] == 5

    def test_duplicate_skipped(self, wire_seeded_session) -> None:
        canonical = _make_event(wire_seeded_session, severity=3)
        captured = []
        ticker = WireTicker(publisher=lambda c, p: captured.append((c, p)))
        dup = _make_event(wire_seeded_session, severity=3, duplicate_of=canonical.id)
        published = ticker.publish_event(wire_seeded_session, dup)
        # Canonical was created without going through ticker so its publish flag is False;
        # we're only testing the duplicate gate here.
        assert published is False

    def test_already_published_skipped(self, wire_seeded_session) -> None:
        captured = []
        ticker = WireTicker(publisher=lambda c, p: captured.append((c, p)))
        evt = _make_event(wire_seeded_session, severity=4, published=True)
        published = ticker.publish_event(wire_seeded_session, evt)
        assert published is False
        assert captured == []

    def test_no_publisher_still_marks_event(self, wire_seeded_session) -> None:
        ticker = WireTicker(publisher=None)
        evt = _make_event(wire_seeded_session, severity=3)
        published = ticker.publish_event(wire_seeded_session, evt)
        wire_seeded_session.commit()
        assert published is True
        assert evt.published_to_ticker is True

    def test_publisher_failure_does_not_mark(self, wire_seeded_session) -> None:
        def boom(c, p):
            raise RuntimeError("boom")
        ticker = WireTicker(publisher=boom)
        evt = _make_event(wire_seeded_session, severity=3)
        published = ticker.publish_event(wire_seeded_session, evt)
        assert published is False
        assert evt.published_to_ticker is False
