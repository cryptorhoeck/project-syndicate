"""
Silent feed integration test — the most important test in Phase 10.

The Library reflection bug taught us: silent failures are the primary risk
class. If every source returns empty for a sustained window, the system
must SHOUT, not yawn. This test simulates 6h+ of empty results across all
enabled sources and asserts that wire.volume_floor_breach is logged.
"""

from datetime import datetime, timedelta, timezone
from typing import Iterable

from sqlalchemy import select

from src.wire.constants import AGORA_EVENT_VOLUME_FLOOR_BREACH
from src.wire.health.alerts import set_agora_publisher
from src.wire.health.breach_monitor import BreachMonitor
from src.wire.ingestors.runner import SourceRunner
from src.wire.models import WireEvent, WireRawItem, WireSource
from src.wire.sources.base import FetchedItem, WireSourceBase


class _EmptySource(WireSourceBase):
    name = "cryptopanic"

    def fetch_raw(self) -> Iterable[FetchedItem]:
        return []


def _patch_all_to_empty():
    from src.wire.ingestors import runner as runner_module

    original = dict(runner_module.SOURCE_REGISTRY)
    for name in list(runner_module.SOURCE_REGISTRY.keys()):
        class _Stub(_EmptySource):
            pass
        _Stub.name = name
        runner_module.SOURCE_REGISTRY[name] = _Stub

    def restore() -> None:
        runner_module.SOURCE_REGISTRY.clear()
        runner_module.SOURCE_REGISTRY.update(original)

    return restore


def test_six_hours_of_empty_fetches_breaches_volume_floor(
    wire_seeded_session, fixed_now
) -> None:
    """All sources return [] for 6h+ -> volume_floor_breach is alerted."""
    captured: list = []
    set_agora_publisher(lambda cls, payload: captured.append((cls, payload)))

    restore = _patch_all_to_empty()
    try:
        # Run the runner against all enabled sources to populate health rows
        # — confirming every source ran without producing items.
        runner = SourceRunner(session=wire_seeded_session)
        results = runner.run_enabled_sources()
        assert all(r.success for r in results)
        assert all(r.items_inserted == 0 for r in results)

        # No events exist; check the breach monitor.
        events = wire_seeded_session.execute(select(WireEvent)).scalars().all()
        assert events == []

        monitor = BreachMonitor(wire_seeded_session, now=fixed_now)
        report = monitor.run()
    finally:
        restore()
        set_agora_publisher(None)

    assert report.volume_floor_breached is True
    assert report.volume_count == 0
    breach_events = [c for c in captured if c[0] == AGORA_EVENT_VOLUME_FLOOR_BREACH]
    assert breach_events, "volume_floor_breach must be alerted on silent feed"
    payload = breach_events[0][1]
    assert payload["actual"] == 0
    assert payload["min_required"] >= 1


def test_silent_feed_alerts_only_when_below_threshold(
    wire_seeded_session, fixed_now
) -> None:
    """If we cross back above the floor, no further alerts fire on the next pass."""
    captured: list = []
    set_agora_publisher(lambda cls, payload: captured.append((cls, payload)))
    try:
        # Seed plenty of recent events to clear the floor.
        src = wire_seeded_session.execute(
            select(WireSource).where(WireSource.name == "cryptopanic")
        ).scalar_one()
        for i in range(5):
            raw = WireRawItem(
                source_id=src.id,
                external_id=f"x-{i}",
                raw_payload={},
                occurred_at=fixed_now - timedelta(minutes=10),
            )
            wire_seeded_session.add(raw)
            wire_seeded_session.flush()
            wire_seeded_session.add(
                WireEvent(
                    raw_item_id=raw.id,
                    canonical_hash=f"h{i}",
                    event_type="other",
                    severity=2,
                    summary=f"e{i}",
                    occurred_at=fixed_now - timedelta(minutes=10),
                    digested_at=fixed_now - timedelta(minutes=10),
                )
            )
        wire_seeded_session.commit()

        monitor = BreachMonitor(wire_seeded_session, now=fixed_now)
        report = monitor.run()
    finally:
        set_agora_publisher(None)

    assert report.volume_floor_breached is False
    assert all(c[0] != AGORA_EVENT_VOLUME_FLOOR_BREACH for c in captured)
