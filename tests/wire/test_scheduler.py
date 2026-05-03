"""Scheduler tick + due-time logic."""

from datetime import datetime, timedelta, timezone
from typing import Iterable

from sqlalchemy import select

from src.wire.ingestors.scheduler import IngestorScheduler
from src.wire.models import WireRawItem, WireSource
from src.wire.sources.base import FetchedItem, WireSourceBase


class _Counter:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self) -> Iterable[FetchedItem]:
        self.calls += 1
        return [
            FetchedItem(
                external_id=f"sched-{self.calls}",
                raw_payload={},
                haiku_brief="brief",
            )
        ]


def _patch_registry(items_per_call):
    """Replace registered Tier 1 sources with a stub. Returns restore callable."""
    from src.wire.ingestors import runner as runner_module

    counter = _Counter()

    class _Stub(WireSourceBase):
        name = "cryptopanic"
        def fetch_raw(self) -> Iterable[FetchedItem]:
            return counter()

    original = dict(runner_module.SOURCE_REGISTRY)
    runner_module.SOURCE_REGISTRY.clear()
    runner_module.SOURCE_REGISTRY.update({
        "kraken_announcements": _Stub,
        "cryptopanic": _Stub,
        "defillama": _Stub,
    })

    def restore() -> None:
        runner_module.SOURCE_REGISTRY.clear()
        runner_module.SOURCE_REGISTRY.update(original)

    return counter, restore


class _SessionFactory:
    def __init__(self, factory) -> None:
        self._factory = factory

    def __call__(self):
        return self._factory()


class TestTick:
    def test_first_tick_runs_all_enabled(self, wire_session_factory, wire_seeded_session) -> None:
        # wire_seeded_session already committed seed. Use a fresh session per call.
        counter, restore = _patch_registry(items_per_call=1)
        try:
            sched = IngestorScheduler(session_factory=wire_session_factory)
            summary = sched.tick()
            # 2 Tier-1 enabled (kraken_announcements, defillama).
            # cryptopanic disabled per migration phase_10_wire_005.
            assert len(summary["runs"]) == 2
            assert all(r.success for r in summary["runs"])
        finally:
            restore()

    def test_second_tick_skips_not_due(self, wire_session_factory, wire_seeded_session) -> None:
        counter, restore = _patch_registry(items_per_call=1)
        try:
            sched = IngestorScheduler(session_factory=wire_session_factory)
            sched.tick()
            calls_after_first = counter.calls
            # Second tick immediately — none due
            summary = sched.tick()
            assert summary["runs"] == []
            assert counter.calls == calls_after_first
        finally:
            restore()

    def test_advancing_time_makes_due(self, wire_session_factory, wire_seeded_session) -> None:
        counter, restore = _patch_registry(items_per_call=1)
        clock = {"now": datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc)}

        def fake_now() -> datetime:
            return clock["now"]

        try:
            sched = IngestorScheduler(
                session_factory=wire_session_factory,
                now_func=fake_now,
            )
            sched.tick()
            initial_calls = counter.calls
            # Jump 2 hours.
            clock["now"] = clock["now"] + timedelta(hours=2)
            summary = sched.tick()
            assert len(summary["runs"]) == 2
            assert counter.calls > initial_calls
        finally:
            restore()


class TestSleepInterval:
    def test_sleep_seconds_floor(self) -> None:
        sched = IngestorScheduler(session_factory=lambda: None)
        # No next_due entries -> floor.
        assert sched._sleep_seconds() == sched.min_loop_interval_seconds
