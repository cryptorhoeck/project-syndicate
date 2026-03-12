"""Tests for Cross-Agent Awareness — Phase 3D."""

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.common.models import Agent, Base, Position, SystemState


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

    state = SystemState(
        total_treasury=1000.0, peak_treasury=1000.0,
        current_regime="bull", alert_status="green",
    )
    session.add(state)
    session.flush()
    yield session
    session.close()


@pytest.fixture
def db_factory(db_session):
    class FakeFactory:
        def __call__(self): return self
        def __enter__(self): return db_session
        def __exit__(self, *args): pass
    return FakeFactory()


def _make_agent(session, **kwargs):
    defaults = {
        "name": "TestOp", "type": "operator", "status": "active",
        "capital_allocated": 100.0, "capital_current": 100.0,
        "cash_balance": 100.0, "reserved_cash": 0.0,
        "total_equity": 100.0, "realized_pnl": 0.0,
        "unrealized_pnl": 0.0, "total_fees_paid": 0.0,
        "position_count": 0,
    }
    defaults.update(kwargs)
    agent = Agent(**defaults)
    session.add(agent)
    session.flush()
    return agent


# --- Warden Concentration Tests ---

@pytest.mark.asyncio
async def test_warden_rejects_over_concentration_limit(db_session, db_factory):
    """Warden should reject trade exceeding 50% concentration."""
    from src.risk.warden import Warden

    agent = _make_agent(db_session, capital_allocated=100.0)

    # Existing position: 40% of capital in BTC/USDT
    pos = Position(
        agent_id=agent.id, agent_name="TestOp",
        symbol="BTC/USDT", side="long",
        entry_price=100, current_price=100,
        quantity=0.4, size_usd=40.0,
        status="open", execution_venue="paper",
    )
    db_session.add(pos)
    db_session.flush()

    warden = Warden(db_session_factory=db_factory)
    warden.alert_status = "green"

    result = await warden.evaluate_trade({
        "agent_id": agent.id,
        "symbol": "BTC/USDT",
        "amount": 1,
        "price": 25,  # $25 trade → total BTC exposure = 65/125 = 52%
    })

    assert result["status"] == "rejected"
    assert "concentration" in result["reason"].lower()


@pytest.mark.asyncio
async def test_warden_approves_with_concentration_warning(db_session, db_factory):
    """Warden should approve with warning at 35% threshold."""
    from src.risk.warden import Warden

    agent = _make_agent(db_session, capital_allocated=100.0)

    # Existing position: 25% in BTC
    pos = Position(
        agent_id=agent.id, agent_name="TestOp",
        symbol="BTC/USDT", side="long",
        entry_price=100, current_price=100,
        quantity=0.25, size_usd=25.0,
        status="open", execution_venue="paper",
    )
    db_session.add(pos)
    db_session.flush()

    warden = Warden(db_session_factory=db_factory)
    warden.alert_status = "green"

    result = await warden.evaluate_trade({
        "agent_id": agent.id,
        "symbol": "BTC/USDT",
        "amount": 1,
        "price": 20,  # Total BTC exposure = 45, concentration = 45/(100+20) = 37.5%
    })

    assert result["status"] == "approved"
    assert result.get("concentration_warning") is True


# --- Context Assembler Portfolio Awareness ---

def test_portfolio_awareness_in_operator_context(db_session):
    """Operator context should include portfolio status."""
    from src.agents.context_assembler import ContextAssembler

    agent = _make_agent(
        db_session,
        cash_balance=75.0, reserved_cash=10.0,
        realized_pnl=5.0, total_fees_paid=0.5,
    )

    # Add open position
    pos = Position(
        agent_id=agent.id, agent_name="TestOp",
        symbol="BTC/USDT", side="long",
        entry_price=100, current_price=105,
        quantity=0.25, size_usd=25.0,
        unrealized_pnl=1.25, unrealized_pnl_pct=5.0,
        status="open", execution_venue="paper",
    )
    db_session.add(pos)
    db_session.flush()

    assembler = ContextAssembler(db_session)
    portfolio_text = assembler._build_portfolio_awareness(agent)

    assert "PORTFOLIO STATUS" in portfolio_text
    assert "BTC/USDT" in portfolio_text
    assert "long" in portfolio_text
    assert "Cash:" in portfolio_text


def test_portfolio_awareness_not_shown_for_scout(db_session):
    """Non-operator agents should not get portfolio awareness."""
    from src.agents.context_assembler import ContextAssembler

    agent = _make_agent(db_session, name="TestScout", type="scout")

    assembler = ContextAssembler(db_session)
    portfolio_text = assembler._build_portfolio_awareness(agent)

    assert portfolio_text == ""


def test_evaluation_feedback_injection(db_session):
    """Evaluation scorecard should be injected and then cleared."""
    from src.agents.context_assembler import ContextAssembler

    agent = _make_agent(db_session)
    agent.evaluation_scorecard = {
        "result": "survived",
        "composite_score": 0.72,
        "rank": 2,
        "metrics": {"sharpe": {"raw": 1.5, "normalized": 0.625}},
    }
    db_session.add(agent)
    db_session.flush()

    assembler = ContextAssembler(db_session)
    feedback = assembler._build_evaluation_feedback(agent)

    assert "EVALUATION FEEDBACK" in feedback
    assert "survived" in feedback
    assert "0.720" in feedback

    # Should be cleared after injection
    assert agent.evaluation_scorecard is None
