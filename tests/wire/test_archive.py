"""WireArchive query + cost tests."""

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from src.wire.constants import (
    ARCHIVE_QUERY_BASE_TOKENS,
    ARCHIVE_QUERY_LOOKBACK_PENALTY_TOKENS,
    ARCHIVE_QUERY_PER_RESULT_TOKENS,
    CRITIC_FREE_QUERIES_PER_CRITIQUE,
)
from src.wire.models import WireEvent, WireQueryLog
from src.wire.publishing.archive import (
    ArchiveQueryParams,
    WireArchive,
    calculate_query_cost,
    fetch_recent_ticker_events,
)


def _seed_events(session, *, fixed_now: datetime, count: int = 5,
                 coin: str = "BTC", severity: int = 3,
                 published: bool = True) -> list[WireEvent]:
    out = []
    for i in range(count):
        evt = WireEvent(
            canonical_hash=f"hash-{coin}-{i}-{severity}",
            coin=coin,
            event_type="other",
            severity=severity,
            summary=f"event {i}",
            occurred_at=fixed_now - timedelta(minutes=i * 5),
            digested_at=fixed_now - timedelta(minutes=i * 5),
            published_to_ticker=published,
        )
        session.add(evt)
        out.append(evt)
    session.commit()
    return out


class TestCostCalc:
    def test_base_only(self) -> None:
        cost = calculate_query_cost(ArchiveQueryParams(), results_count=0)
        assert cost == ARCHIVE_QUERY_BASE_TOKENS

    def test_per_result_added(self) -> None:
        cost = calculate_query_cost(ArchiveQueryParams(), results_count=10)
        assert cost == ARCHIVE_QUERY_BASE_TOKENS + 10 * ARCHIVE_QUERY_PER_RESULT_TOKENS

    def test_lookback_penalty_only_above_threshold(self) -> None:
        cost_short = calculate_query_cost(
            ArchiveQueryParams(lookback_hours=24), results_count=0
        )
        cost_long = calculate_query_cost(
            ArchiveQueryParams(lookback_hours=72), results_count=0
        )
        assert cost_long - cost_short == ARCHIVE_QUERY_LOOKBACK_PENALTY_TOKENS


class TestQueryFiltering:
    def test_filters_by_coin(self, wire_seeded_session, fixed_now) -> None:
        _seed_events(wire_seeded_session, fixed_now=fixed_now, coin="BTC", count=3)
        _seed_events(wire_seeded_session, fixed_now=fixed_now, coin="ETH", count=2)
        archive = WireArchive(session=wire_seeded_session, now_func=lambda: fixed_now)
        result = archive.query(
            ArchiveQueryParams(coin="ETH", lookback_hours=24, min_severity=1, limit=10),
            agent_id=1,
        )
        assert len(result.events) == 2
        assert all(e["coin"] == "ETH" for e in result.events)

    def test_filters_by_min_severity(self, wire_seeded_session, fixed_now) -> None:
        _seed_events(wire_seeded_session, fixed_now=fixed_now, severity=2, count=3)
        _seed_events(wire_seeded_session, fixed_now=fixed_now, severity=4, count=2)
        archive = WireArchive(session=wire_seeded_session, now_func=lambda: fixed_now)
        result = archive.query(
            ArchiveQueryParams(min_severity=3, limit=10),
            agent_id=1,
        )
        assert len(result.events) == 2
        assert all(e["severity"] >= 3 for e in result.events)

    def test_excludes_duplicates(self, wire_seeded_session, fixed_now) -> None:
        canonicals = _seed_events(wire_seeded_session, fixed_now=fixed_now, count=2)
        # add a duplicate pointing at canonicals[0]
        dup = WireEvent(
            canonical_hash="hash-dup",
            coin="BTC",
            event_type="other",
            severity=3,
            summary="dup",
            occurred_at=fixed_now,
            digested_at=fixed_now,
            duplicate_of=canonicals[0].id,
        )
        wire_seeded_session.add(dup)
        wire_seeded_session.commit()

        archive = WireArchive(session=wire_seeded_session, now_func=lambda: fixed_now)
        result = archive.query(ArchiveQueryParams(limit=10), agent_id=1)
        assert len(result.events) == 2

    def test_lookback_window_enforced(self, wire_seeded_session, fixed_now) -> None:
        _seed_events(wire_seeded_session, fixed_now=fixed_now - timedelta(hours=48), count=2)
        archive = WireArchive(session=wire_seeded_session, now_func=lambda: fixed_now)
        result = archive.query(ArchiveQueryParams(lookback_hours=24), agent_id=1)
        assert result.events == []


class TestQueryLogging:
    def test_query_logged_with_cost(self, wire_seeded_session, fixed_now) -> None:
        _seed_events(wire_seeded_session, fixed_now=fixed_now, count=3)
        archive = WireArchive(session=wire_seeded_session, now_func=lambda: fixed_now)
        result = archive.query(ArchiveQueryParams(), agent_id=1)
        wire_seeded_session.commit()
        logs = wire_seeded_session.execute(select(WireQueryLog)).scalars().all()
        assert len(logs) == 1
        assert logs[0].agent_id == 1
        assert logs[0].results_count == 3
        assert logs[0].token_cost == result.token_cost
        assert logs[0].token_cost > 0

    def test_free_query_logs_zero_cost(self, wire_seeded_session, fixed_now) -> None:
        _seed_events(wire_seeded_session, fixed_now=fixed_now, count=3)
        archive = WireArchive(session=wire_seeded_session, now_func=lambda: fixed_now)
        result = archive.query(ArchiveQueryParams(), agent_id=1, is_free=True)
        wire_seeded_session.commit()
        assert result.token_cost == 0
        assert result.free_query is True
        logs = wire_seeded_session.execute(select(WireQueryLog)).scalars().all()
        assert logs[0].token_cost == 0


class TestRecentTickerFetch:
    def test_only_published_returned(self, wire_seeded_session, fixed_now) -> None:
        _seed_events(
            wire_seeded_session, fixed_now=fixed_now, count=2, published=False
        )
        _seed_events(
            wire_seeded_session, fixed_now=fixed_now, count=3, published=True
        )
        events = fetch_recent_ticker_events(
            wire_seeded_session, limit=10, now=fixed_now
        )
        assert len(events) == 3

    def test_limit_respected(self, wire_seeded_session, fixed_now) -> None:
        _seed_events(wire_seeded_session, fixed_now=fixed_now, count=10, published=True)
        events = fetch_recent_ticker_events(
            wire_seeded_session, limit=4, now=fixed_now
        )
        assert len(events) == 4
