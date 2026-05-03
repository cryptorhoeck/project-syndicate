"""Volume floor + diversity breach tests."""

import itertools
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from src.wire.constants import (
    DIVERSITY_MAX_SHARE,
    DIVERSITY_WINDOW_HOURS,
    VOLUME_FLOOR_MIN_EVENTS,
    VOLUME_FLOOR_WINDOW_HOURS,
)
from src.wire.health.alerts import set_agora_publisher
from src.wire.health.breach_monitor import BreachMonitor
from src.wire.models import WireEvent, WireRawItem, WireSource


_seed_counter = itertools.count()


def _seed_event(session, *, source_name: str, digested_at: datetime,
                duplicate_of=None) -> WireEvent:
    src = session.execute(
        select(WireSource).where(WireSource.name == source_name)
    ).scalar_one()
    seq = next(_seed_counter)
    raw = WireRawItem(
        source_id=src.id,
        external_id=f"ext-{source_name}-{seq}",
        raw_payload={},
        occurred_at=digested_at,
    )
    session.add(raw)
    session.flush()
    evt = WireEvent(
        raw_item_id=raw.id,
        canonical_hash=f"hash-{source_name}-{seq}",
        coin="BTC",
        event_type="other",
        severity=2,
        summary="x",
        occurred_at=digested_at,
        digested_at=digested_at,
        duplicate_of=duplicate_of,
    )
    session.add(evt)
    session.commit()
    return evt


class TestVolumeFloor:
    def test_no_breach_with_enough_events(self, wire_seeded_session, fixed_now) -> None:
        for i in range(VOLUME_FLOOR_MIN_EVENTS):
            _seed_event(
                wire_seeded_session,
                source_name="cryptopanic",
                digested_at=fixed_now - timedelta(minutes=i * 10),
            )
        monitor = BreachMonitor(wire_seeded_session, now=fixed_now)
        breached, count = monitor.check_volume_floor()
        assert not breached
        assert count == VOLUME_FLOOR_MIN_EVENTS

    def test_breach_when_zero_events(self, wire_seeded_session, fixed_now) -> None:
        captured: list = []
        set_agora_publisher(lambda cls, payload: captured.append((cls, payload)))
        try:
            monitor = BreachMonitor(wire_seeded_session, now=fixed_now)
            breached, count = monitor.check_volume_floor()
        finally:
            set_agora_publisher(None)
        assert breached
        assert count == 0
        assert captured and captured[0][0] == "wire.volume_floor_breach"

    def test_old_events_dont_count(self, wire_seeded_session, fixed_now) -> None:
        # All events outside the 6h window.
        for i in range(VOLUME_FLOOR_MIN_EVENTS + 2):
            _seed_event(
                wire_seeded_session,
                source_name="cryptopanic",
                digested_at=fixed_now - timedelta(hours=VOLUME_FLOOR_WINDOW_HOURS + 2),
            )
        monitor = BreachMonitor(wire_seeded_session, now=fixed_now)
        breached, count = monitor.check_volume_floor()
        assert breached
        assert count == 0

    def test_duplicates_dont_count(self, wire_seeded_session, fixed_now) -> None:
        # Seed one canonical and many duplicates of it -> only canonical counts.
        canonical = _seed_event(
            wire_seeded_session,
            source_name="cryptopanic",
            digested_at=fixed_now - timedelta(minutes=10),
        )
        for i in range(5):
            _seed_event(
                wire_seeded_session,
                source_name="cryptopanic",
                digested_at=fixed_now - timedelta(minutes=10 + i),
                duplicate_of=canonical.id,
            )
        monitor = BreachMonitor(wire_seeded_session, now=fixed_now)
        breached, count = monitor.check_volume_floor()
        # Only the canonical event counts -> below VOLUME_FLOOR_MIN_EVENTS (3).
        assert count == 1
        assert breached


class TestDiversity:
    def test_single_source_dominance_breaches(self, wire_seeded_session, fixed_now) -> None:
        # 9 events from cryptopanic, 1 from kraken -> 90% from one source.
        for _ in range(9):
            _seed_event(
                wire_seeded_session,
                source_name="cryptopanic",
                digested_at=fixed_now - timedelta(minutes=1),
            )
        _seed_event(
            wire_seeded_session,
            source_name="kraken_announcements",
            digested_at=fixed_now - timedelta(minutes=1),
        )
        monitor = BreachMonitor(wire_seeded_session, now=fixed_now)
        breached, top, share = monitor.check_diversity()
        assert breached
        assert top == "cryptopanic"
        assert share > DIVERSITY_MAX_SHARE

    def test_balanced_sources_dont_breach(self, wire_seeded_session, fixed_now) -> None:
        # 5 each from two sources.
        for _ in range(5):
            _seed_event(
                wire_seeded_session,
                source_name="cryptopanic",
                digested_at=fixed_now - timedelta(minutes=1),
            )
            _seed_event(
                wire_seeded_session,
                source_name="kraken_announcements",
                digested_at=fixed_now - timedelta(minutes=1),
            )
        monitor = BreachMonitor(wire_seeded_session, now=fixed_now)
        breached, top, share = monitor.check_diversity()
        assert not breached
        assert share == 0.5

    def test_zero_events_does_not_breach_diversity(self, wire_seeded_session, fixed_now) -> None:
        monitor = BreachMonitor(wire_seeded_session, now=fixed_now)
        breached, top, share = monitor.check_diversity()
        assert not breached
        assert top is None
        assert share == 0.0


class TestRunReturnsReport:
    def test_run_combines_both_checks(self, wire_seeded_session, fixed_now) -> None:
        monitor = BreachMonitor(wire_seeded_session, now=fixed_now)
        report = monitor.run()
        # Empty DB -> volume floor breached, diversity not breached (zero total).
        assert report.volume_floor_breached is True
        assert report.diversity_breached is False
        assert report.volume_count == 0
