"""Tests for Honesty Scorer — Phase 3D."""

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.common.models import Agent, AgentCycle, AgentReflection, Base
from src.genesis.honesty_scorer import HonestyScorer


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


def _make_agent(session):
    agent = Agent(
        name="TestAgent", type="operator", status="active",
        capital_allocated=100, capital_current=100,
        cash_balance=100, reserved_cash=0, total_equity=100,
        realized_pnl=0, unrealized_pnl=0, total_fees_paid=0,
        position_count=0,
    )
    session.add(agent)
    session.flush()
    return agent


@pytest.fixture
def scorer():
    return HonestyScorer()


@pytest.mark.asyncio
async def test_confidence_calibration_correlated(db_session, scorer):
    """High confidence on winning trades should score well."""
    agent = _make_agent(db_session)
    now = datetime.now(timezone.utc)

    # High confidence → positive outcome (correlated)
    for i in range(10):
        conf = 8 if i % 2 == 0 else 3
        pnl = 10.0 if i % 2 == 0 else -5.0
        db_session.add(AgentCycle(
            agent_id=agent.id, cycle_number=i + 1, timestamp=now,
            confidence_score=conf, outcome_pnl=pnl,
        ))
    db_session.flush()

    result = await scorer.calculate(
        db_session, agent.id, datetime(2026, 1, 1, tzinfo=timezone.utc), now
    )
    # Correlated data should score above 0.5
    assert result.confidence_calibration >= 0.5


@pytest.mark.asyncio
async def test_confidence_calibration_uncorrelated(db_session, scorer):
    """Random confidence/outcome should score near 0.5."""
    agent = _make_agent(db_session)
    now = datetime.now(timezone.utc)

    # Alternating confidence but same outcome
    for i in range(10):
        db_session.add(AgentCycle(
            agent_id=agent.id, cycle_number=i + 1, timestamp=now,
            confidence_score=i % 10 + 1, outcome_pnl=1.0,
        ))
    db_session.flush()

    result = await scorer.calculate(
        db_session, agent.id, datetime(2026, 1, 1, tzinfo=timezone.utc), now
    )
    # When all outcomes are the same, correlation is undefined → 0.5
    assert result.confidence_calibration == pytest.approx(0.5, abs=0.1)


@pytest.mark.asyncio
async def test_reflection_specificity_with_detail(db_session, scorer):
    """Reflections with numbers and symbols should score higher."""
    agent = _make_agent(db_session)
    now = datetime.now(timezone.utc)

    db_session.add(AgentReflection(
        agent_id=agent.id, cycle_number=1,
        what_worked="BTC/USDT trade gained 5.2% with a solid entry",
        what_failed="Should have set stop-loss tighter",
        lesson="Need to reduce position size when volatility is above 30%",
        created_at=now,
    ))
    db_session.flush()

    result = await scorer.calculate(
        db_session, agent.id, datetime(2026, 1, 1, tzinfo=timezone.utc), now
    )
    # Should score well (has numbers, symbols, action words)
    assert result.reflection_specificity > 0.5


@pytest.mark.asyncio
async def test_reflection_specificity_vague(db_session, scorer):
    """Vague reflections should score lower."""
    agent = _make_agent(db_session)
    now = datetime.now(timezone.utc)

    db_session.add(AgentReflection(
        agent_id=agent.id, cycle_number=1,
        what_worked="things went okay",
        what_failed="not great",
        lesson="try harder",
        created_at=now,
    ))
    db_session.flush()

    result = await scorer.calculate(
        db_session, agent.id, datetime(2026, 1, 1, tzinfo=timezone.utc), now
    )
    # Vague text should score lower
    assert result.reflection_specificity <= 0.5


@pytest.mark.asyncio
async def test_neutral_on_insufficient_data(db_session, scorer):
    """With no data, should return neutral 0.5 for all components."""
    agent = _make_agent(db_session)
    now = datetime.now(timezone.utc)

    result = await scorer.calculate(
        db_session, agent.id, datetime(2026, 1, 1, tzinfo=timezone.utc), now
    )
    assert result.confidence_calibration == 0.5
    assert result.self_note_accuracy == 0.5
    assert result.reflection_specificity == 0.5
    assert result.overall_score == 0.5
