"""Tests for cost tracking enhancements — Phase 3.5."""

__version__ = "0.1.0"

import pytest
from datetime import datetime, timezone
from unittest.mock import MagicMock

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.common.models import Agent, AgentCycle, Base, SystemState, Transaction
from src.risk.accountant import Accountant, MODEL_PRICING, SONNET_INPUT_RATE, SONNET_OUTPUT_RATE


@pytest.fixture
def db_factory():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)

    with factory() as session:
        session.add(SystemState(
            id=1, total_treasury=500.0, peak_treasury=1000.0,
            current_regime="crab", active_agent_count=1, alert_status="green",
        ))
        session.add(Agent(
            id=1, name="Test-Agent", type="scout", status="active",
            capital_allocated=50.0, capital_current=50.0,
            reputation_score=100.0, generation=1,
            thinking_budget_daily=0.50, thinking_budget_used_today=0.0,
            total_gross_pnl=0.0, total_true_pnl=0.0, total_api_cost=0.0,
            cycle_count=0,
        ))
        session.commit()

    return factory


@pytest.fixture
def accountant(db_factory):
    return Accountant(db_session_factory=db_factory)


class TestModelPricing:
    def test_haiku_pricing_exists(self):
        assert "claude-haiku-4-5-20251001" in MODEL_PRICING

    def test_haiku_cheaper_than_sonnet(self):
        haiku = MODEL_PRICING["claude-haiku-4-5-20251001"]
        sonnet = MODEL_PRICING["claude-sonnet-4-20250514"]
        assert haiku["input"] < sonnet["input"]
        assert haiku["output"] < sonnet["output"]

    def test_haiku_rates_correct(self):
        haiku = MODEL_PRICING["claude-haiku-4-5-20251001"]
        assert haiku["input"] == 1.0
        assert haiku["output"] == 5.0


class TestTrackApiCall:
    @pytest.mark.asyncio
    async def test_track_haiku_cost(self, accountant, db_factory):
        cost = await accountant.track_api_call(
            agent_id=1,
            model="claude-haiku-4-5-20251001",
            input_tokens=3000,
            output_tokens=500,
        )
        # 3000/1M * 1.0 + 500/1M * 5.0 = 0.003 + 0.0025 = 0.0055
        assert cost == pytest.approx(0.0055, abs=0.0001)

    @pytest.mark.asyncio
    async def test_track_sonnet_cost(self, accountant, db_factory):
        cost = await accountant.track_api_call(
            agent_id=1,
            model="claude-sonnet-4-20250514",
            input_tokens=3000,
            output_tokens=500,
        )
        # 3000/1M * 3.0 + 500/1M * 15.0 = 0.009 + 0.0075 = 0.0165
        assert cost == pytest.approx(0.0165, abs=0.0001)

    @pytest.mark.asyncio
    async def test_track_with_cache_tokens(self, accountant, db_factory):
        cost = await accountant.track_api_call(
            agent_id=1,
            model="claude-sonnet-4-20250514",
            input_tokens=500,
            output_tokens=200,
            cache_creation_tokens=1000,
            cache_read_tokens=2000,
        )
        # Standard input: 500/1M * 3.0 = 0.0015
        # Output: 200/1M * 15.0 = 0.003
        # Cache write: 1000/1M * 3.0 * 1.25 = 0.00375
        # Cache read: 2000/1M * 3.0 * 0.10 = 0.0006
        expected = 0.0015 + 0.003 + 0.00375 + 0.0006
        assert cost == pytest.approx(expected, abs=0.0001)

    @pytest.mark.asyncio
    async def test_agent_counters_updated(self, accountant, db_factory):
        await accountant.track_api_call(
            agent_id=1,
            model="claude-haiku-4-5-20251001",
            input_tokens=3000,
            output_tokens=500,
        )
        with db_factory() as session:
            agent = session.get(Agent, 1)
            assert agent.total_api_cost > 0
            assert agent.thinking_budget_used_today > 0


class TestSystemSummary:
    @pytest.mark.asyncio
    async def test_summary_includes_cost_fields(self, accountant):
        summary = await accountant.get_system_summary()

        assert "total_api_spend" in summary
        assert "total_api_spend_today" in summary
        assert "estimated_savings_today" in summary
        assert "estimated_savings_alltime" in summary
        assert "model_distribution_today" in summary
        assert "haiku_ratio_today" in summary
        assert "avg_cost_per_cycle_today" in summary
        assert "total_cycles_today" in summary

    @pytest.mark.asyncio
    async def test_model_distribution_format(self, accountant):
        summary = await accountant.get_system_summary()
        dist = summary["model_distribution_today"]
        assert "haiku" in dist
        assert "sonnet" in dist

    @pytest.mark.asyncio
    async def test_empty_system_has_zero_savings(self, accountant):
        summary = await accountant.get_system_summary()
        assert summary["estimated_savings_today"] >= 0
        assert summary["estimated_savings_alltime"] >= 0
