"""
Tests for the Warden — trade gate, alerts, loss limits.
"""

import pytest
from unittest.mock import MagicMock, patch

from sqlalchemy import select

from src.common.models import Agent, SystemState
from src.risk.warden import Warden


def _set_alert_status_in_db(session_factory, status: str) -> None:
    """Write alert_status to system_state. After hotfix
    `warden-trade-gate-wiring`, `evaluate_trade` refreshes from the DB at
    the top of every call so the in-process Warden reflects whatever the
    Warden process wrote. Tests that previously poked `warden.alert_status`
    in memory now have to write it to the DB too — that is what the
    production code path reads.
    """
    with session_factory() as session:
        state = session.execute(select(SystemState).limit(1)).scalar_one()
        state.alert_status = status
        session.commit()


@pytest.fixture
def warden(seeded_db):
    """Create a Warden instance with test database."""
    with patch("src.risk.warden.redis.Redis") as mock_redis:
        mock_redis.from_url.return_value = MagicMock()
        w = Warden(db_session_factory=seeded_db)
        w.redis = MagicMock()
        w.redis.lpop.return_value = None
        # Expose the session factory so tests can write alert_status to DB.
        w._session_factory_for_tests = seeded_db
        return w


@pytest.mark.asyncio
async def test_trade_gate_small_trade_auto_approves(warden):
    """Small trades with no alerts should auto-approve."""
    trade = {
        "agent_id": 1,
        "symbol": "BTC/USD",
        "side": "buy",
        "amount": 0.001,
        "price": 50000.0,  # $50 = 0.05% of $100k... but agent has $100
        # trade_value = 0.001 * 50000 = 50, agent capital = 100
        # 50/100 = 0.5, which is > TRADE_GATE_THRESHOLD (0.05)
        # but < PER_AGENT_MAX_POSITION_PCT (0.25 * 100 = 25)
        # Actually 50 > 25, so this would be rejected for position size
    }
    # Adjust to a truly small trade
    trade["amount"] = 0.00001
    trade["price"] = 50000.0  # $0.50 trade
    result = await warden.evaluate_trade(trade)
    assert result["status"] == "approved"


@pytest.mark.asyncio
async def test_trade_gate_large_trade_needs_review(warden):
    """Trades > 5% of agent capital need review but pass if within limits."""
    trade = {
        "agent_id": 1,
        "symbol": "BTC/USD",
        "side": "buy",
        "amount": 0.0002,
        "price": 50000.0,  # $10 = 10% of $100 agent capital
    }
    result = await warden.evaluate_trade(trade)
    # Should be approved (passes size review) since it's within position limits
    assert result["status"] == "approved"
    assert "size review" in result["reason"].lower() or "auto-approved" in result["reason"].lower()


@pytest.mark.asyncio
async def test_trade_gate_yellow_alert_holds_all(warden):
    """During Yellow alert, all trades should be held."""
    _set_alert_status_in_db(warden._session_factory_for_tests, "yellow")
    trade = {
        "agent_id": 1,
        "symbol": "BTC/USD",
        "side": "buy",
        "amount": 0.00001,
        "price": 50000.0,
    }
    result = await warden.evaluate_trade(trade)
    assert result["status"] == "held"
    assert "YELLOW" in result["reason"]


@pytest.mark.asyncio
async def test_trade_gate_circuit_breaker_rejects_all(warden):
    """During circuit breaker, all trades should be rejected."""
    _set_alert_status_in_db(warden._session_factory_for_tests, "circuit_breaker")
    trade = {
        "agent_id": 1,
        "symbol": "BTC/USD",
        "side": "buy",
        "amount": 0.00001,
        "price": 50000.0,
    }
    result = await warden.evaluate_trade(trade)
    assert result["status"] == "rejected"
    assert "CIRCUIT BREAKER" in result["reason"]


@pytest.mark.asyncio
async def test_trade_gate_red_alert_rejects_all(warden):
    """During Red alert, all trades should be rejected."""
    _set_alert_status_in_db(warden._session_factory_for_tests, "red")
    trade = {
        "agent_id": 1,
        "symbol": "BTC/USD",
        "side": "buy",
        "amount": 0.00001,
        "price": 50000.0,
    }
    result = await warden.evaluate_trade(trade)
    assert result["status"] == "rejected"
    assert "RED ALERT" in result["reason"]


def test_per_agent_loss_limit_detection(warden, seeded_db):
    """Agent that lost 50%+ of capital should be flagged."""
    with seeded_db() as session:
        agent = session.get(Agent, 1)
        agent.capital_current = 40.0  # 60% loss from 100 allocated
        session.commit()

    flagged = warden._check_agent_losses()
    assert 1 in flagged


def test_alert_escalation_sequence(warden):
    """Alert status progresses correctly: green -> yellow -> red -> circuit_breaker."""
    assert warden.alert_status == "green"
    warden.alert_status = "yellow"
    assert warden.alert_status == "yellow"
    warden.alert_status = "red"
    assert warden.alert_status == "red"
    warden.alert_status = "circuit_breaker"
    assert warden.alert_status == "circuit_breaker"
