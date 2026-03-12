"""Integration tests for the Thinking Cycle Engine."""

__version__ = "0.7.0"

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.common.models import Agent, AgentCycle, Base, SystemState
from src.agents.budget_gate import BudgetStatus
from src.agents.claude_client import APIResponse
from src.agents.thinking_cycle import ThinkingCycle, CycleResult


def _mock_normal_response():
    """Build a mock API response with valid normal cycle output."""
    output = json.dumps({
        "situation": "BTC consolidating near support with rising volume",
        "confidence": {"score": 7, "reasoning": "Strong volume pattern"},
        "recent_pattern": "Last cycles idle, waiting for setup",
        "action": {
            "type": "broadcast_opportunity",
            "params": {
                "market": "BTC/USDT",
                "signal": "volume_breakout",
                "urgency": "medium",
                "details": "Volume 3x average at support",
            },
        },
        "reasoning": "Volume spike at support suggests accumulation ending",
        "self_note": "Watch BTC/USDT next cycle",
    })
    return APIResponse(
        content=output,
        input_tokens=500,
        output_tokens=150,
        cost_usd=0.004,
        latency_ms=2100,
        model="claude-sonnet-4-20250514",
        stop_reason="end_turn",
    )


def _mock_reflection_response():
    """Build a mock API response with valid reflection output."""
    output = json.dumps({
        "what_worked": "Volume-based signals had 70% accuracy",
        "what_failed": "False breakout on SOL twice",
        "pattern_detected": "Better accuracy on higher timeframes",
        "lesson": "Wait for 4h candle close, not just wick",
        "confidence_trend": "improving",
        "confidence_reason": "Hit rate improved from 50% to 70%",
        "strategy_note": "Focus on BTC and ETH only",
        "memory_promotion": [],
        "memory_demotion": [],
    })
    return APIResponse(
        content=output,
        input_tokens=600,
        output_tokens=200,
        cost_usd=0.005,
        latency_ms=2500,
        model="claude-sonnet-4-20250514",
        stop_reason="end_turn",
    )


def _mock_invalid_response():
    """Build a mock API response with invalid JSON."""
    return APIResponse(
        content="This is not JSON at all, I'm just chatting",
        input_tokens=500,
        output_tokens=50,
        cost_usd=0.002,
        latency_ms=1500,
        model="claude-sonnet-4-20250514",
        stop_reason="end_turn",
    )


@pytest.fixture
def db_session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    session = factory()

    session.add(SystemState(
        id=1, total_treasury=500.0, peak_treasury=1000.0,
        current_regime="crab", active_agent_count=3, alert_status="green",
    ))
    session.add(Agent(
        id=1, name="Scout-Alpha", type="scout", status="active",
        capital_allocated=50.0, capital_current=52.0,
        reputation_score=120.0, generation=1,
        thinking_budget_daily=0.50, thinking_budget_used_today=0.0,
        total_gross_pnl=5.0, total_true_pnl=3.0, total_api_cost=0.05,
        cycle_count=0,
    ))
    session.commit()
    yield session
    session.close()


@pytest.fixture
def mock_claude():
    client = MagicMock()
    client.call = AsyncMock(return_value=_mock_normal_response())
    client.call_repair = AsyncMock(return_value=_mock_normal_response())
    return client


@pytest.fixture
def cycle_engine(db_session, mock_claude):
    return ThinkingCycle(
        db_session=db_session,
        claude_client=mock_claude,
    )


class TestFullCycle:
    @pytest.mark.asyncio
    async def test_normal_cycle_succeeds(self, cycle_engine, db_session):
        result = await cycle_engine.run(agent_id=1)

        assert result.success
        assert result.action_type == "broadcast_opportunity"
        assert result.api_cost > 0

        # Check that cycle was recorded in DB
        cycles = db_session.query(AgentCycle).filter(AgentCycle.agent_id == 1).all()
        assert len(cycles) == 1
        assert cycles[0].action_type == "broadcast_opportunity"
        assert cycles[0].validation_passed

    @pytest.mark.asyncio
    async def test_agent_stats_updated(self, cycle_engine, db_session):
        await cycle_engine.run(agent_id=1)

        agent = db_session.query(Agent).get(1)
        assert agent.cycle_count == 1
        assert agent.total_api_cost > 0.05  # original + new cost
        assert agent.last_cycle_at is not None

    @pytest.mark.asyncio
    async def test_nonexistent_agent(self, cycle_engine):
        result = await cycle_engine.run(agent_id=999)
        assert not result.success
        assert result.reason == "agent_not_found"


class TestReflectionCycle:
    @pytest.mark.asyncio
    async def test_reflection_on_10th_cycle(self, cycle_engine, mock_claude, db_session):
        # Set cycle count to 10 (triggers reflection)
        agent = db_session.query(Agent).get(1)
        agent.cycle_count = 10
        db_session.commit()

        mock_claude.call = AsyncMock(return_value=_mock_reflection_response())

        result = await cycle_engine.run(agent_id=1)
        assert result.success
        assert result.action_type == "reflection"

    @pytest.mark.asyncio
    async def test_no_reflection_on_cycle_1(self, cycle_engine, db_session):
        agent = db_session.query(Agent).get(1)
        agent.cycle_count = 1
        db_session.commit()

        result = await cycle_engine.run(agent_id=1)
        assert result.success
        assert result.action_type != "reflection"


class TestValidationFailure:
    @pytest.mark.asyncio
    async def test_invalid_json_triggers_retry(self, cycle_engine, mock_claude, db_session):
        # First call returns invalid JSON, repair returns valid
        mock_claude.call = AsyncMock(return_value=_mock_invalid_response())
        mock_claude.call_repair = AsyncMock(return_value=_mock_normal_response())

        result = await cycle_engine.run(agent_id=1)
        assert result.success
        mock_claude.call_repair.assert_called_once()

    @pytest.mark.asyncio
    async def test_double_failure_records_failed_cycle(self, cycle_engine, mock_claude, db_session):
        # Both calls return invalid JSON
        mock_claude.call = AsyncMock(return_value=_mock_invalid_response())
        mock_claude.call_repair = AsyncMock(return_value=_mock_invalid_response())

        result = await cycle_engine.run(agent_id=1)
        assert result.failed
        assert "validation_failed" in result.reason

        # Check failed cycle was recorded
        cycles = db_session.query(AgentCycle).filter(AgentCycle.agent_id == 1).all()
        assert len(cycles) == 1
        assert not cycles[0].validation_passed


class TestBudgetExhaustion:
    @pytest.mark.asyncio
    async def test_skip_when_budget_exhausted(self, cycle_engine, db_session):
        agent = db_session.query(Agent).get(1)
        agent.thinking_budget_used_today = 0.50  # fully spent
        db_session.commit()

        result = await cycle_engine.run(agent_id=1)
        assert result.skipped
        assert result.reason == "budget_exhausted"

    @pytest.mark.asyncio
    async def test_terminated_agent_skipped(self, cycle_engine, db_session):
        agent = db_session.query(Agent).get(1)
        agent.status = "terminated"
        db_session.commit()

        result = await cycle_engine.run(agent_id=1)
        assert not result.success
        assert "terminated" in result.reason
