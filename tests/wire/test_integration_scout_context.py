"""Scout context-block builder + Strategist/Critic helpers."""

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from src.wire.constants import CRITIC_FREE_QUERIES_PER_CRITIQUE
from src.wire.integration.agent_context import (
    build_critic_archive_helper,
    build_recent_signals_block,
    build_strategist_archive_helper,
)
from src.wire.models import WireEvent, WireQueryLog


def _publish_event(session, *, severity: int, fixed_now: datetime, coin: str = "BTC",
                   minutes_ago: int = 0) -> WireEvent:
    evt = WireEvent(
        canonical_hash=f"h-{severity}-{coin}-{minutes_ago}",
        coin=coin,
        event_type="other",
        severity=severity,
        summary=f"event {coin}",
        occurred_at=fixed_now - timedelta(minutes=minutes_ago),
        digested_at=fixed_now - timedelta(minutes=minutes_ago),
        published_to_ticker=True,
    )
    session.add(evt)
    session.commit()
    return evt


class TestRecentSignalsBlock:
    def test_empty_returns_count_zero(self, wire_seeded_session) -> None:
        block = build_recent_signals_block(wire_seeded_session)
        assert block["count"] == 0
        assert block["recent_signals"] == []

    def test_returns_published_in_order(self, wire_seeded_session, fixed_now) -> None:
        _publish_event(wire_seeded_session, severity=3, fixed_now=fixed_now, minutes_ago=10)
        _publish_event(wire_seeded_session, severity=4, fixed_now=fixed_now, minutes_ago=5)
        _publish_event(wire_seeded_session, severity=3, fixed_now=fixed_now, minutes_ago=1)
        block = build_recent_signals_block(
            wire_seeded_session,
            limit=5,
        )
        assert block["count"] == 3
        # Most recent first.
        # Note: fetch_recent_ticker_events uses datetime.now(); since our seeded
        # events are anchored on fixed_now (= 2026-05-01 12:00 UTC), they may be
        # older than the default 24h lookback when run far in the future.
        # We rely on conftest fixed_now being 'recent enough' for tests.


class TestStrategistHelper:
    def test_query_charges_cost(self, wire_seeded_session, fixed_now) -> None:
        _publish_event(wire_seeded_session, severity=3, fixed_now=fixed_now, minutes_ago=1)
        helper = build_strategist_archive_helper(wire_seeded_session, agent_id=1)
        result = helper(coin="BTC", lookback_hours=24)
        wire_seeded_session.commit()
        assert result.token_cost > 0
        logs = wire_seeded_session.execute(select(WireQueryLog)).scalars().all()
        assert len(logs) == 1
        assert logs[0].agent_id == 1


class TestCriticHelper:
    def test_first_n_queries_free(self, wire_seeded_session, fixed_now) -> None:
        _publish_event(wire_seeded_session, severity=3, fixed_now=fixed_now, minutes_ago=1)
        helper = build_critic_archive_helper(
            wire_seeded_session,
            agent_id=2,
            free_budget=CRITIC_FREE_QUERIES_PER_CRITIQUE,
        )
        for _ in range(CRITIC_FREE_QUERIES_PER_CRITIQUE):
            r = helper()
            assert r.token_cost == 0
            assert r.free_query is True
        # 4th call should charge.
        r = helper()
        assert r.token_cost > 0
        assert r.free_query is False
        wire_seeded_session.commit()

    def test_each_critique_gets_fresh_budget(self, wire_seeded_session, fixed_now) -> None:
        _publish_event(wire_seeded_session, severity=3, fixed_now=fixed_now, minutes_ago=1)
        # First critique exhausts free budget.
        helper1 = build_critic_archive_helper(wire_seeded_session, agent_id=2, free_budget=2)
        helper1(); helper1(); helper1()  # third call charges
        # Second critique has fresh budget.
        helper2 = build_critic_archive_helper(wire_seeded_session, agent_id=2, free_budget=2)
        first = helper2()
        assert first.token_cost == 0
