"""Tests for Idle Analyzer — Phase 3D."""

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.common.models import Agent, AgentCycle, Base, Opportunity, Plan
from src.genesis.idle_analyzer import IdleAnalyzer


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


def _make_agent(session, name="TestAgent", role="operator"):
    agent = Agent(
        name=name, type=role, status="active",
        capital_allocated=100, capital_current=100,
        cash_balance=100, reserved_cash=0, total_equity=100,
        realized_pnl=0, unrealized_pnl=0, total_fees_paid=0,
        position_count=0,
    )
    session.add(agent)
    session.flush()
    return agent


@pytest.fixture
def analyzer():
    return IdleAnalyzer()


@pytest.mark.asyncio
async def test_post_loss_caution(db_session, analyzer):
    """Idle after a loss should be classified as post_loss_caution."""
    agent = _make_agent(db_session)
    now = datetime.now(timezone.utc)
    period_start = datetime(2026, 1, 1, tzinfo=timezone.utc)

    # Cycle 1: trade with a loss
    db_session.add(AgentCycle(
        agent_id=agent.id, cycle_number=1, timestamp=now,
        action_type="execute_trade", outcome_pnl=-5.0,
    ))
    # Cycle 2: idle (should be post_loss_caution)
    db_session.add(AgentCycle(
        agent_id=agent.id, cycle_number=2, timestamp=now,
        action_type="wait",
    ))
    db_session.flush()

    result = await analyzer.analyze_idle_periods(db_session, agent.id, period_start, now)
    assert result.total_idle == 1
    assert result.breakdown["post_loss_caution"] == 1


@pytest.mark.asyncio
async def test_no_input_classification(db_session, analyzer):
    """Idle strategist with no opportunities should be no_input."""
    agent = _make_agent(db_session, role="strategist")
    now = datetime.now(timezone.utc)
    period_start = datetime(2026, 1, 1, tzinfo=timezone.utc)

    # Idle cycle, no opportunities in pipeline
    db_session.add(AgentCycle(
        agent_id=agent.id, cycle_number=1, timestamp=now,
        action_type="wait",
    ))
    db_session.flush()

    result = await analyzer.analyze_idle_periods(db_session, agent.id, period_start, now)
    assert result.total_idle == 1
    assert result.breakdown["no_input"] == 1


@pytest.mark.asyncio
async def test_strategic_patience(db_session, analyzer):
    """Idle with patience keywords should be strategic_patience."""
    agent = _make_agent(db_session, role="scout")
    now = datetime.now(timezone.utc)
    period_start = datetime(2026, 1, 1, tzinfo=timezone.utc)

    db_session.add(AgentCycle(
        agent_id=agent.id, cycle_number=1, timestamp=now,
        action_type="wait",
        reasoning="Waiting for confirmation before entry. Market is consolidating.",
    ))
    db_session.flush()

    result = await analyzer.analyze_idle_periods(db_session, agent.id, period_start, now)
    assert result.total_idle == 1
    assert result.breakdown["strategic_patience"] == 1


@pytest.mark.asyncio
async def test_paralysis_default(db_session, analyzer):
    """Idle with work available and no excuse should be paralysis."""
    agent = _make_agent(db_session, role="operator")
    now = datetime.now(timezone.utc)
    period_start = datetime(2026, 1, 1, tzinfo=timezone.utc)

    # Create an approved plan (work available for operator)
    strat = _make_agent(db_session, name="Strat", role="strategist")
    plan = Plan(
        strategist_agent_id=strat.id, strategist_agent_name="Strat",
        plan_name="TestPlan", market="BTC/USDT", direction="long",
        entry_conditions="test", exit_conditions="test", thesis="test",
        status="approved", created_at=now,
    )
    db_session.add(plan)

    # Idle cycle with no reasoning
    db_session.add(AgentCycle(
        agent_id=agent.id, cycle_number=1, timestamp=now,
        action_type="idle",
    ))
    db_session.flush()

    result = await analyzer.analyze_idle_periods(db_session, agent.id, period_start, now)
    assert result.total_idle == 1
    assert result.breakdown["paralysis"] == 1


@pytest.mark.asyncio
async def test_idle_rate_calculation(db_session, analyzer):
    """Idle rate should be correctly calculated."""
    agent = _make_agent(db_session)
    now = datetime.now(timezone.utc)
    period_start = datetime(2026, 1, 1, tzinfo=timezone.utc)

    # 3 active + 2 idle = 40% idle rate
    for i in range(3):
        db_session.add(AgentCycle(
            agent_id=agent.id, cycle_number=i + 1, timestamp=now,
            action_type="execute_trade",
        ))
    for i in range(2):
        db_session.add(AgentCycle(
            agent_id=agent.id, cycle_number=i + 4, timestamp=now,
            action_type="wait",
        ))
    db_session.flush()

    result = await analyzer.analyze_idle_periods(db_session, agent.id, period_start, now)
    assert result.total_cycles == 5
    assert result.total_idle == 2
    assert result.idle_rate == pytest.approx(0.4)
