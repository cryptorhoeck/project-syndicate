"""Tests for Role-Specific Metric Calculators — Phase 3D."""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.common.models import (
    Agent, AgentCycle, AgentEquitySnapshot, Base, Opportunity,
    Plan, Position, RejectionTracking,
)
from src.genesis.role_metrics import (
    CriticMetrics, OperatorMetrics, ScoutMetrics, StrategistMetrics,
    get_metrics_calculator, normalize,
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
    yield session
    session.close()


def _make_agent(session, name="TestOp", role="operator", **kwargs):
    defaults = {
        "name": name, "type": role, "status": "active",
        "capital_allocated": 100.0, "capital_current": 100.0,
        "realized_pnl": 0.0, "unrealized_pnl": 0.0,
        "evaluation_count": 5, "profitable_evaluations": 3,
        "cash_balance": 100.0, "reserved_cash": 0.0,
        "total_equity": 100.0, "total_fees_paid": 0.0,
        "position_count": 0,
    }
    defaults.update(kwargs)
    agent = Agent(**defaults)
    session.add(agent)
    session.flush()
    return agent


# --- Normalization ---

def test_normalize_within_range():
    assert normalize(1.0, 0.0, 2.0) == 0.5

def test_normalize_at_min():
    assert normalize(0.0, 0.0, 2.0) == 0.0

def test_normalize_at_max():
    assert normalize(2.0, 0.0, 2.0) == 1.0

def test_normalize_clamped_above():
    assert normalize(5.0, 0.0, 2.0) == 1.0

def test_normalize_clamped_below():
    assert normalize(-5.0, 0.0, 2.0) == 0.0


# --- Operator Metrics ---

@pytest.mark.asyncio
async def test_operator_composite_known_inputs(db_session):
    agent = _make_agent(db_session, realized_pnl=10.0, unrealized_pnl=5.0)
    now = datetime.now(timezone.utc)
    period_start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    period_end = now

    # Add some cycles for API cost
    for i in range(5):
        cycle = AgentCycle(
            agent_id=agent.id, cycle_number=i + 1,
            api_cost_usd=0.10, timestamp=now,
        )
        db_session.add(cycle)
    db_session.flush()

    calc = OperatorMetrics()
    result = await calc.calculate(db_session, agent.id, period_start, period_end)

    assert result.composite_score >= 0.0
    assert result.composite_score <= 1.0
    assert "sharpe" in result.metric_breakdown
    assert "true_pnl_pct" in result.metric_breakdown
    assert "thinking_efficiency" in result.metric_breakdown
    assert "consistency" in result.metric_breakdown


@pytest.mark.asyncio
async def test_operator_profitable_scores_well(db_session):
    """A profitable operator should have a positive composite score."""
    agent = _make_agent(db_session, realized_pnl=20.0, unrealized_pnl=0.0)
    now = datetime.now(timezone.utc)
    period_start = datetime(2026, 1, 1, tzinfo=timezone.utc)

    db_session.add(AgentCycle(
        agent_id=agent.id, cycle_number=1, api_cost_usd=1.0, timestamp=now,
    ))
    db_session.flush()

    calc = OperatorMetrics()
    result = await calc.calculate(db_session, agent.id, period_start, now)
    assert result.composite_score > 0.0
    assert result.metric_breakdown["true_pnl_pct"]["raw"] > 0


# --- Scout Metrics ---

@pytest.mark.asyncio
async def test_scout_composite(db_session):
    agent = _make_agent(db_session, name="TestScout", role="scout")
    now = datetime.now(timezone.utc)
    period_start = datetime(2026, 1, 1, tzinfo=timezone.utc)

    # Create opportunities
    for i in range(10):
        opp = Opportunity(
            scout_agent_id=agent.id, scout_agent_name="TestScout",
            market="BTC/USDT", signal_type="breakout",
            details="Test", confidence=7, created_at=now,
        )
        if i < 3:  # 3 out of 10 converted
            opp.converted_to_plan_id = 1
        db_session.add(opp)
    db_session.add(AgentCycle(
        agent_id=agent.id, cycle_number=1, api_cost_usd=0.5,
        timestamp=now, action_type="scan",
    ))
    db_session.flush()

    calc = ScoutMetrics()
    result = await calc.calculate(db_session, agent.id, period_start, now)
    assert result.composite_score >= 0.0
    assert "intel_conversion" in result.metric_breakdown
    assert result.metric_breakdown["intel_conversion"]["raw"] == pytest.approx(0.3)


# --- Strategist Metrics ---

@pytest.mark.asyncio
async def test_strategist_composite(db_session):
    agent = _make_agent(db_session, name="TestStrat", role="strategist")
    now = datetime.now(timezone.utc)
    period_start = datetime(2026, 1, 1, tzinfo=timezone.utc)

    # Create plans
    for i in range(5):
        plan = Plan(
            strategist_agent_id=agent.id, strategist_agent_name="TestStrat",
            plan_name=f"Plan {i}", market="BTC/USDT", direction="long",
            entry_conditions="test", exit_conditions="test", thesis="test",
            created_at=now,
        )
        if i < 3:
            plan.critic_verdict = "approved"
        else:
            plan.critic_verdict = "rejected"
        db_session.add(plan)

    db_session.add(AgentCycle(
        agent_id=agent.id, cycle_number=1, api_cost_usd=0.3, timestamp=now,
    ))
    db_session.flush()

    calc = StrategistMetrics()
    result = await calc.calculate(db_session, agent.id, period_start, now)
    assert result.composite_score >= 0.0
    assert "plan_approval_rate" in result.metric_breakdown
    assert result.metric_breakdown["plan_approval_rate"]["raw"] == pytest.approx(0.6)


# --- Critic Metrics ---

@pytest.mark.asyncio
async def test_critic_composite(db_session):
    agent = _make_agent(db_session, name="TestCritic", role="critic")
    now = datetime.now(timezone.utc)
    period_start = datetime(2026, 1, 1, tzinfo=timezone.utc)

    # Create some plans reviewed by this critic
    for i in range(4):
        plan = Plan(
            strategist_agent_id=999, strategist_agent_name="SomeStrat",
            plan_name=f"Plan {i}", market="BTC/USDT", direction="long",
            entry_conditions="test", exit_conditions="test", thesis="test",
            critic_agent_id=agent.id, critic_agent_name="TestCritic",
            critic_verdict="approved" if i < 3 else "rejected",
            reviewed_at=now, created_at=now,
        )
        db_session.add(plan)

    db_session.add(AgentCycle(
        agent_id=agent.id, cycle_number=1, api_cost_usd=0.2, timestamp=now,
    ))
    db_session.flush()

    calc = CriticMetrics()
    result = await calc.calculate(db_session, agent.id, period_start, now)
    assert result.composite_score >= 0.0
    assert "rejection_value" in result.metric_breakdown
    assert "approval_accuracy" in result.metric_breakdown


@pytest.mark.asyncio
async def test_critic_rubber_stamp_penalty(db_session):
    """Critic with >90% approval rate gets penalty."""
    agent = _make_agent(db_session, name="RubberStamp", role="critic")
    now = datetime.now(timezone.utc)
    period_start = datetime(2026, 1, 1, tzinfo=timezone.utc)

    # 10 plans, all approved (100% approval rate)
    for i in range(10):
        plan = Plan(
            strategist_agent_id=999, strategist_agent_name="Strat",
            plan_name=f"Plan {i}", market="BTC/USDT", direction="long",
            entry_conditions="test", exit_conditions="test", thesis="test",
            critic_agent_id=agent.id, critic_agent_name="RubberStamp",
            critic_verdict="approved", reviewed_at=now, created_at=now,
        )
        db_session.add(plan)

    db_session.add(AgentCycle(
        agent_id=agent.id, cycle_number=1, api_cost_usd=0.1, timestamp=now,
    ))
    db_session.flush()

    calc = CriticMetrics()
    result = await calc.calculate(db_session, agent.id, period_start, now)
    assert result.metric_breakdown.get("rubber_stamp_penalty_applied") is True


# --- Factory ---

def test_get_metrics_calculator():
    assert isinstance(get_metrics_calculator("operator"), OperatorMetrics)
    assert isinstance(get_metrics_calculator("scout"), ScoutMetrics)
    assert isinstance(get_metrics_calculator("strategist"), StrategistMetrics)
    assert isinstance(get_metrics_calculator("critic"), CriticMetrics)
    assert get_metrics_calculator("genesis") is None
