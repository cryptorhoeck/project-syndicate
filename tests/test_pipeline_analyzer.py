"""Tests for Pipeline Analyzer — Phase 3D."""

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.common.models import Agent, Base, Opportunity, Plan, Position
from src.genesis.pipeline_analyzer import PipelineAnalyzer


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


def _seed_agents(session):
    for role in ["scout", "strategist", "critic", "operator"]:
        agent = Agent(
            name=f"Test{role.title()}", type=role, status="active",
            capital_allocated=100, capital_current=100,
            cash_balance=100, reserved_cash=0, total_equity=100,
            realized_pnl=0, unrealized_pnl=0, total_fees_paid=0,
            position_count=0,
        )
        session.add(agent)
    session.flush()
    return session.query(Agent).all()


@pytest.fixture
def analyzer():
    return PipelineAnalyzer()


@pytest.mark.asyncio
async def test_empty_pipeline(db_session, analyzer):
    """Empty pipeline should report no bottleneck data."""
    now = datetime.now(timezone.utc)
    report = await analyzer.analyze(db_session, datetime(2026, 1, 1, tzinfo=timezone.utc), now)
    assert report.total_opportunities == 0
    assert report.bottleneck == "no_opportunities"


@pytest.mark.asyncio
async def test_bottleneck_at_scout_stage(db_session, analyzer):
    """If opportunities exist but none claimed, bottleneck is scout_to_strategist."""
    agents = _seed_agents(db_session)
    now = datetime.now(timezone.utc)

    # Create opportunities, none claimed
    for i in range(5):
        db_session.add(Opportunity(
            scout_agent_id=agents[0].id, scout_agent_name="Scout",
            market="BTC/USDT", signal_type="breakout", details="test",
            created_at=now,
        ))
    db_session.flush()

    report = await analyzer.analyze(db_session, datetime(2026, 1, 1, tzinfo=timezone.utc), now)
    assert report.total_opportunities == 5
    assert report.claimed_opportunities == 0
    assert report.bottleneck == "scout_to_strategist"


@pytest.mark.asyncio
async def test_bottleneck_approved_not_executed(db_session, analyzer):
    """Approved plans with no executions should flag operator_not_executing."""
    agents = _seed_agents(db_session)
    now = datetime.now(timezone.utc)

    # Create opps + claimed
    opp = Opportunity(
        scout_agent_id=agents[0].id, scout_agent_name="Scout",
        market="BTC/USDT", signal_type="breakout", details="test",
        claimed_by_agent_id=agents[1].id, created_at=now,
    )
    db_session.add(opp)
    db_session.flush()

    # Create approved plan
    plan = Plan(
        strategist_agent_id=agents[1].id, strategist_agent_name="Strat",
        plan_name="Test Plan", market="BTC/USDT", direction="long",
        entry_conditions="test", exit_conditions="test", thesis="test",
        critic_verdict="approved", created_at=now,
    )
    db_session.add(plan)
    db_session.flush()

    report = await analyzer.analyze(db_session, datetime(2026, 1, 1, tzinfo=timezone.utc), now)
    assert report.approved_plans == 1
    assert report.executed_plans == 0
    assert report.bottleneck == "operator_not_executing"


@pytest.mark.asyncio
async def test_conversion_rates(db_session, analyzer):
    """Test conversion rate calculation through full pipeline."""
    agents = _seed_agents(db_session)
    now = datetime.now(timezone.utc)

    # 10 opps, 5 claimed
    for i in range(10):
        opp = Opportunity(
            scout_agent_id=agents[0].id, scout_agent_name="Scout",
            market="BTC/USDT", signal_type="breakout", details="test",
            created_at=now,
        )
        if i < 5:
            opp.claimed_by_agent_id = agents[1].id
        db_session.add(opp)

    # 5 plans, 3 approved
    for i in range(5):
        plan = Plan(
            strategist_agent_id=agents[1].id, strategist_agent_name="Strat",
            plan_name=f"Plan {i}", market="BTC/USDT", direction="long",
            entry_conditions="test", exit_conditions="test", thesis="test",
            created_at=now,
        )
        plan.critic_verdict = "approved" if i < 3 else "rejected"
        db_session.add(plan)
    db_session.flush()

    report = await analyzer.analyze(db_session, datetime(2026, 1, 1, tzinfo=timezone.utc), now)
    assert report.stage_rates["scout_to_strategist"] == pytest.approx(0.5)
    assert report.stage_rates["strategist_to_critic"] == pytest.approx(0.6)
