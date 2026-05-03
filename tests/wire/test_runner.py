"""Runner tests — fetch persistence + dedup + health updates."""

from typing import Iterable

from sqlalchemy import select

from src.wire.constants import HEALTH_DEGRADED, HEALTH_HEALTHY
from src.wire.ingestors.runner import SourceRunner
from src.wire.models import WireRawItem, WireSource, WireSourceHealth
from src.wire.sources.base import FetchedItem, SourceFetchError, WireSourceBase


class _StubSource(WireSourceBase):
    name = "cryptopanic"  # alias to a seeded row

    def __init__(self, items, fail_with: Exception | None = None):
        super().__init__()
        self._items = items
        self._fail_with = fail_with

    def fetch_raw(self) -> Iterable[FetchedItem]:
        if self._fail_with:
            raise self._fail_with
        return list(self._items)


def _items(*ids: str) -> list[FetchedItem]:
    return [
        FetchedItem(
            external_id=eid,
            raw_payload={"x": eid},
            haiku_brief=f"item {eid}",
        )
        for eid in ids
    ]


class TestRunnerHappyPath:
    def test_persists_new_items(self, wire_seeded_session) -> None:
        source_row = wire_seeded_session.execute(
            select(WireSource).where(WireSource.name == "cryptopanic")
        ).scalar_one()
        runner = SourceRunner(session=wire_seeded_session)
        result = runner.run_source(
            source_row,
            instance=_StubSource(_items("a", "b", "c")),
        )
        assert result.success
        assert result.items_seen == 3
        assert result.items_inserted == 3

        rows = wire_seeded_session.execute(select(WireRawItem)).scalars().all()
        assert len(rows) == 3

    def test_health_marked_healthy_on_success(self, wire_seeded_session) -> None:
        source_row = wire_seeded_session.execute(
            select(WireSource).where(WireSource.name == "cryptopanic")
        ).scalar_one()
        runner = SourceRunner(session=wire_seeded_session)
        runner.run_source(source_row, instance=_StubSource(_items("a")))
        health = wire_seeded_session.get(WireSourceHealth, source_row.id)
        assert health.status == HEALTH_HEALTHY
        assert health.consecutive_failures == 0
        assert health.last_fetch_success is not None

    def test_envelope_contains_payload_and_brief(self, wire_seeded_session) -> None:
        source_row = wire_seeded_session.execute(
            select(WireSource).where(WireSource.name == "cryptopanic")
        ).scalar_one()
        runner = SourceRunner(session=wire_seeded_session)
        runner.run_source(source_row, instance=_StubSource(_items("a")))
        row = wire_seeded_session.execute(select(WireRawItem)).scalar_one()
        assert row.raw_payload["payload"] == {"x": "a"}
        assert row.raw_payload["haiku_brief"] == "item a"


class TestRunnerDedup:
    def test_duplicate_external_id_skipped(self, wire_seeded_session) -> None:
        source_row = wire_seeded_session.execute(
            select(WireSource).where(WireSource.name == "cryptopanic")
        ).scalar_one()
        runner = SourceRunner(session=wire_seeded_session)
        # First run inserts 2.
        first = runner.run_source(source_row, instance=_StubSource(_items("a", "b")))
        assert first.items_inserted == 2
        # Second run sees the same items + a new one; only the new one inserts.
        second = runner.run_source(
            source_row, instance=_StubSource(_items("a", "b", "c"))
        )
        assert second.items_seen == 3
        assert second.items_inserted == 1

        rows = wire_seeded_session.execute(select(WireRawItem)).scalars().all()
        assert {r.external_id for r in rows} == {"a", "b", "c"}


class TestRunnerFailures:
    def test_source_fetch_error_marks_degraded(self, wire_seeded_session) -> None:
        source_row = wire_seeded_session.execute(
            select(WireSource).where(WireSource.name == "cryptopanic")
        ).scalar_one()
        runner = SourceRunner(session=wire_seeded_session)
        result = runner.run_source(
            source_row,
            instance=_StubSource([], fail_with=SourceFetchError("network down")),
        )
        assert not result.success
        assert "network down" in (result.error or "")

        health = wire_seeded_session.get(WireSourceHealth, source_row.id)
        assert health.status == HEALTH_DEGRADED
        assert health.consecutive_failures == 1
        assert "network down" in (health.last_fetch_error or "")

    def test_unknown_source_name_records_failure(self, wire_seeded_session) -> None:
        # Seed a row with a name that has no implementation in SOURCE_REGISTRY.
        unknown = WireSource(
            name="not_implemented",
            display_name="Not implemented",
            tier="A",
            fetch_interval_seconds=300,
            enabled=True,
            requires_api_key=False,
            base_url="http://x",
        )
        wire_seeded_session.add(unknown)
        wire_seeded_session.commit()
        runner = SourceRunner(session=wire_seeded_session)
        result = runner.run_source(unknown)
        assert not result.success
        assert "no implementation" in (result.error or "")


class TestRunEnabledSources:
    def test_skips_disabled_sources(self, wire_seeded_session) -> None:
        runner = SourceRunner(session=wire_seeded_session)
        # Without injecting instances, the registered Tier 1 sources will try
        # real HTTP. We swap them out by monkeypatching the registry.
        from src.wire.ingestors import runner as runner_module

        class _Empty(WireSourceBase):
            name = "cryptopanic"
            def fetch_raw(self) -> Iterable[FetchedItem]:
                return []

        original = dict(runner_module.SOURCE_REGISTRY)
        runner_module.SOURCE_REGISTRY.clear()
        runner_module.SOURCE_REGISTRY.update({
            "kraken_announcements": _Empty,
            "cryptopanic": _Empty,
            "defillama": _Empty,
        })
        try:
            results = runner.run_enabled_sources()
        finally:
            runner_module.SOURCE_REGISTRY.clear()
            runner_module.SOURCE_REGISTRY.update(original)

        names = {r.source_name for r in results}
        # Only enabled sources should run; cryptopanic is disabled in seed
        # (mirrors migration phase_10_wire_005), the other two stay enabled.
        assert names == {"kraken_announcements", "defillama"}
