"""
Tests for the Accountant — P&L, Sharpe ratio, composite score, leaderboard.
"""

import pytest
from datetime import datetime, timedelta, timezone

from src.common.models import Agent, Transaction
from src.risk.accountant import Accountant


@pytest.fixture
def accountant(seeded_db, mock_currency):
    """Create an Accountant with test database and 1:1 currency mock."""
    return Accountant(db_session_factory=seeded_db, currency_service=mock_currency)


@pytest.fixture
def agent_with_trades(seeded_db):
    """Seed agent with a set of trades and API costs."""
    with seeded_db() as session:
        agent = session.get(Agent, 1)
        agent.capital_allocated = 100.0
        agent.capital_current = 110.0
        agent.evaluation_count = 10
        agent.profitable_evaluations = 7

        now = datetime.now(timezone.utc)
        trades = [
            Transaction(agent_id=1, type="spot", symbol="BTC/USD", side="buy", amount=0.001, price=50000, pnl=5.0, fee=0.1, timestamp=now - timedelta(days=3)),
            Transaction(agent_id=1, type="spot", symbol="BTC/USD", side="sell", amount=0.001, price=52000, pnl=8.0, fee=0.1, timestamp=now - timedelta(days=2)),
            Transaction(agent_id=1, type="spot", symbol="ETH/USD", side="buy", amount=0.01, price=3000, pnl=-3.0, fee=0.05, timestamp=now - timedelta(days=1)),
            Transaction(agent_id=1, type="api_cost", amount=0.50, pnl=-0.50, fee=0.0, timestamp=now),
        ]
        for t in trades:
            session.add(t)
        session.commit()

    return seeded_db


@pytest.mark.asyncio
async def test_pnl_calculation(agent_with_trades):
    """P&L should sum trades correctly and subtract API costs."""
    accountant = Accountant(db_session_factory=agent_with_trades)
    pnl = await accountant.calculate_agent_pnl(1)

    assert pnl["gross_pnl"] == 10.0  # 5 + 8 - 3
    assert pnl["api_cost"] == 0.50
    assert pnl["true_pnl"] == 9.50  # 10 - 0.50
    assert pnl["trade_count"] == 3  # Excludes api_cost transactions
    assert pnl["win_rate"] > 0


@pytest.mark.asyncio
async def test_sharpe_ratio_calculation(agent_with_trades):
    """Sharpe ratio should be calculable with trade history."""
    accountant = Accountant(db_session_factory=agent_with_trades)
    sharpe = await accountant.calculate_sharpe_ratio(1, period_days=14)
    # Should return a number (may be 0 if insufficient data)
    assert isinstance(sharpe, float)


@pytest.mark.asyncio
async def test_composite_score_with_known_inputs(agent_with_trades):
    """Composite score should be between 0 and 1."""
    accountant = Accountant(db_session_factory=agent_with_trades)
    score = await accountant.calculate_composite_score(1)
    assert 0.0 <= score <= 1.0


@pytest.mark.asyncio
async def test_thinking_efficiency(agent_with_trades):
    """Thinking efficiency = True P&L / API Cost."""
    accountant = Accountant(db_session_factory=agent_with_trades)
    efficiency = await accountant.calculate_thinking_efficiency(1)
    # true_pnl=9.5, api_cost=0.5 → efficiency=19.0
    assert efficiency == 19.0


@pytest.mark.asyncio
async def test_consistency(seeded_db):
    """Consistency = profitable_evaluations / evaluation_count."""
    accountant = Accountant(db_session_factory=seeded_db)
    consistency = await accountant.calculate_consistency(1)
    # 3/5 = 0.6
    assert consistency == 0.6


@pytest.mark.asyncio
async def test_leaderboard_generation(agent_with_trades):
    """Leaderboard should return ranked agents."""
    accountant = Accountant(db_session_factory=agent_with_trades)
    leaderboard = await accountant.generate_leaderboard()
    assert isinstance(leaderboard, list)
    if leaderboard:
        assert "rank" in leaderboard[0]
        assert "composite_score" in leaderboard[0]
        assert leaderboard[0]["rank"] == 1
