"""Tests for EquitySnapshotService — Phase 3C."""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.common.models import Agent, AgentEquitySnapshot, Base, Position, SystemState
from src.trading.equity_snapshots import EquitySnapshotService


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
        cash_balance=80.0, reserved_cash=0.0, total_equity=100.0,
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
def service(db_factory):
    return EquitySnapshotService(db_session_factory=db_factory)


@pytest.mark.asyncio
async def test_snapshot_creation(service, db_session):
    """Should create equity snapshots for active agents."""
    count = await service.take_snapshots()
    assert count == 1

    snaps = db_session.query(AgentEquitySnapshot).all()
    assert len(snaps) == 1
    assert snaps[0].agent_id == 1
    assert snaps[0].cash_balance == 80.0


@pytest.mark.asyncio
async def test_snapshot_includes_position_value(service, db_session):
    """Snapshot equity should include open position values."""
    pos = Position(
        agent_id=1, agent_name="Operator-A", symbol="BTC/USDT",
        side="long", entry_price=100.0, current_price=110.0,
        quantity=0.2, size_usd=20.0, status="open", execution_venue="paper",
    )
    db_session.add(pos)
    db_session.commit()

    await service.take_snapshots()

    snaps = db_session.query(AgentEquitySnapshot).all()
    assert snaps[0].position_value == 22.0  # 110 * 0.2
    assert snaps[0].equity == 102.0  # 80 + 22


@pytest.mark.asyncio
async def test_daily_returns_calculation(service, db_session):
    """Should calculate daily returns from snapshots."""
    now = datetime.now(timezone.utc)

    # Create snapshots on different days
    for i, equity in enumerate([100.0, 105.0, 103.0, 110.0]):
        snap = AgentEquitySnapshot(
            agent_id=1,
            equity=equity,
            cash_balance=equity,
            position_value=0.0,
            snapshot_at=now - timedelta(days=4 - i),
        )
        db_session.add(snap)
    db_session.commit()

    returns = await service.get_daily_returns(1, days=30)
    assert len(returns) == 3  # 4 data points → 3 returns
    assert returns[0] == pytest.approx(0.05, abs=0.001)  # 100→105 = 5%


@pytest.mark.asyncio
async def test_daily_returns_insufficient_data(service, db_session):
    """Should return empty list with insufficient data."""
    returns = await service.get_daily_returns(1, days=30)
    assert returns == []


@pytest.mark.asyncio
async def test_no_snapshot_for_zero_cash(service, db_session):
    """Agents with zero cash should not get snapshots."""
    agent = db_session.get(Agent, 1)
    agent.cash_balance = 0.0
    db_session.commit()

    count = await service.take_snapshots()
    assert count == 0


@pytest.mark.asyncio
async def test_updates_agent_equity(service, db_session):
    """Taking snapshots should update agent.total_equity."""
    agent = db_session.get(Agent, 1)
    agent.total_equity = 0.0
    db_session.commit()

    await service.take_snapshots()

    db_session.refresh(agent)
    assert agent.total_equity == 80.0  # cash_balance with no positions
