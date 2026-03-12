"""Tests for PaperTradingSanityChecker — Phase 3C."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.common.models import Agent, Base, Order, Position, SystemState
from src.trading.sanity_checker import ConcentrationMonitor, PaperTradingSanityChecker


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

    state = SystemState(total_treasury=1000.0, peak_treasury=1000.0)
    session.add(state)

    agent = Agent(
        name="Operator-A", type="operator", status="active", generation=1,
        capital_allocated=100.0, capital_current=100.0,
        cash_balance=50.0, reserved_cash=0.0, total_equity=100.0,
        total_fees_paid=0.0, position_count=1,
    )
    session.add(agent)
    session.commit()

    yield session
    session.close()


@pytest.fixture
def db_factory(db_session):
    class FakeFactory:
        def __call__(self):
            return self
        def __enter__(self):
            return db_session
        def __exit__(self, *args):
            pass
    return FakeFactory()


@pytest.fixture
def checker(db_factory):
    return PaperTradingSanityChecker(db_session_factory=db_factory)


@pytest.fixture
def concentration(db_factory):
    return ConcentrationMonitor(db_session_factory=db_factory)


@pytest.mark.asyncio
async def test_negative_cash_detection(checker, db_session):
    """Agents with negative cash should be flagged as CRITICAL."""
    agent = db_session.get(Agent, 1)
    agent.cash_balance = -5.0
    db_session.commit()

    flagged = await checker.check_cash_balances()
    assert len(flagged) == 1
    assert flagged[0]["severity"] == "CRITICAL"


@pytest.mark.asyncio
async def test_positive_cash_ok(checker, db_session):
    """Agents with positive cash should NOT be flagged."""
    flagged = await checker.check_cash_balances()
    assert len(flagged) == 0


@pytest.mark.asyncio
async def test_equity_reconciliation(checker, db_session):
    """Equity drift > $0.01 should be auto-corrected."""
    agent = db_session.get(Agent, 1)
    agent.total_equity = 999.0  # Way off
    db_session.commit()

    corrections = await checker.check_equity_reconciliation()
    assert corrections >= 1

    db_session.refresh(agent)
    assert abs(agent.total_equity - 50.0) < 1.0  # Should be ~cash_balance since no positions


@pytest.mark.asyncio
async def test_orphaned_positions(checker, db_session):
    """Open positions for terminated agents should be flagged."""
    agent = db_session.get(Agent, 1)
    agent.status = "terminated"
    db_session.commit()

    pos = Position(
        agent_id=1, agent_name="Operator-A", symbol="BTC/USDT",
        side="long", entry_price=100.0, current_price=100.0,
        quantity=0.5, size_usd=50.0, status="open", execution_venue="paper",
    )
    db_session.add(pos)
    db_session.commit()

    orphans = await checker.check_orphaned_positions()
    assert len(orphans) == 1
    assert orphans[0]["agent_status"] == "terminated"


@pytest.mark.asyncio
async def test_no_orphans_for_active_agents(checker, db_session):
    """Active agents with positions should NOT be flagged as orphans."""
    pos = Position(
        agent_id=1, agent_name="Operator-A", symbol="BTC/USDT",
        side="long", entry_price=100.0, current_price=100.0,
        quantity=0.5, size_usd=50.0, status="open", execution_venue="paper",
    )
    db_session.add(pos)
    db_session.commit()

    orphans = await checker.check_orphaned_positions()
    assert len(orphans) == 0


@pytest.mark.asyncio
async def test_stale_reservation_cleanup(checker, db_session):
    """Expired orders with unreleased reservations should be auto-fixed."""
    order = Order(
        agent_id=1, agent_name="Operator-A",
        order_type="limit", symbol="BTC/USDT", side="buy",
        requested_size_usd=10.0, requested_price=99.0,
        reserved_amount=11.0, reservation_released=False,
        status="expired", execution_venue="paper",
    )
    db_session.add(order)

    agent = db_session.get(Agent, 1)
    agent.reserved_cash = 11.0
    db_session.commit()

    released = await checker.check_stale_reservations()
    assert released == 1

    db_session.refresh(order)
    assert order.reservation_released is True
    db_session.refresh(agent)
    assert agent.reserved_cash == 0.0


@pytest.mark.asyncio
async def test_concentration_warning(concentration, db_session):
    """Positions exceeding concentration threshold should warn."""
    # Single position = 100% concentration
    pos = Position(
        agent_id=1, agent_name="Operator-A", symbol="BTC/USDT",
        side="long", entry_price=100.0, current_price=100.0,
        quantity=0.5, size_usd=50.0, status="open", execution_venue="paper",
    )
    db_session.add(pos)
    db_session.commit()

    warnings = await concentration.check()
    assert len(warnings) >= 1
    assert warnings[0]["concentration_pct"] > 40


@pytest.mark.asyncio
async def test_concentration_ok_diversified(concentration, db_session):
    """Well-diversified positions should not trigger warnings."""
    # Add multiple small positions so no single one exceeds threshold
    for i, symbol in enumerate(["BTC/USDT", "ETH/USDT", "SOL/USDT", "ADA/USDT"]):
        pos = Position(
            agent_id=1, agent_name="Operator-A", symbol=symbol,
            side="long", entry_price=100.0, current_price=100.0,
            quantity=0.25, size_usd=25.0, status="open", execution_venue="paper",
        )
        db_session.add(pos)
    db_session.commit()

    warnings = await concentration.check()
    assert len(warnings) == 0


@pytest.mark.asyncio
async def test_run_all(db_factory, db_session):
    """run_all should return results for all checks."""
    checker = PaperTradingSanityChecker(db_session_factory=db_factory)
    results = await checker.run_all()
    assert "negative_cash" in results
    assert "equity_corrections" in results
    assert "orphaned_positions" in results
    assert "stale_reservations" in results
