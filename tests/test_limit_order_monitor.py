"""Tests for LimitOrderMonitor — Phase 3C."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.common.models import Agent, Base, Order, Position, SystemState
from src.trading.fee_schedule import FeeSchedule
from src.trading.limit_order_monitor import LimitOrderMonitor


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
        name="Operator-Test", type="operator", status="active", generation=1,
        capital_allocated=100.0, capital_current=100.0,
        cash_balance=100.0, reserved_cash=11.0, total_equity=100.0,
        total_fees_paid=0.0, position_count=0,
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


def _make_limit_order(db_session, **kwargs):
    now = datetime.now(timezone.utc)
    defaults = dict(
        agent_id=1, agent_name="Operator-Test",
        order_type="limit", symbol="BTC/USDT", side="buy",
        requested_size_usd=10.0, requested_price=99.0,
        reserved_amount=11.0, reservation_released=False,
        requested_at=now, status="pending", execution_venue="paper",
    )
    defaults.update(kwargs)
    order = Order(**defaults)
    db_session.add(order)
    db_session.commit()
    return order


@pytest.fixture
def mock_price_cache():
    cache = MagicMock()
    cache.get_ticker = AsyncMock(return_value=(
        {"bid": 98.0, "ask": 98.5, "last": 98.25}, True
    ))
    return cache


@pytest.fixture
def mock_redis():
    r = MagicMock()
    r.set.return_value = True
    return r


@pytest.fixture
def monitor(db_factory, mock_price_cache, mock_redis):
    return LimitOrderMonitor(
        db_session_factory=db_factory,
        price_cache=mock_price_cache,
        fee_schedule=FeeSchedule(),
        redis_client=mock_redis,
    )


@pytest.mark.asyncio
async def test_fill_buy_on_price_cross(monitor, db_session, mock_price_cache):
    """Buy limit fills when ask <= requested_price."""
    order = _make_limit_order(db_session, requested_price=99.0)

    # Ask is 98.5, below limit of 99.0 — should fill
    result = await monitor.check_pending_orders()
    assert result["filled"] == 1

    db_session.refresh(order)
    assert order.status == "filled"
    assert order.fill_price <= 99.0  # Price improvement


@pytest.mark.asyncio
async def test_price_improvement(monitor, db_session, mock_price_cache):
    """Buy fills at min(limit, ask) for price improvement."""
    order = _make_limit_order(db_session, requested_price=100.0)

    result = await monitor.check_pending_orders()
    db_session.refresh(order)
    assert order.fill_price == 98.5  # ask < limit, so fills at ask


@pytest.mark.asyncio
async def test_no_fill_price_above_limit(monitor, db_session, mock_price_cache):
    """Buy limit should NOT fill when ask > requested_price."""
    order = _make_limit_order(db_session, requested_price=95.0)

    # Ask is 98.5, above limit of 95 — no fill
    result = await monitor.check_pending_orders()
    assert result["filled"] == 0

    db_session.refresh(order)
    assert order.status == "pending"


@pytest.mark.asyncio
async def test_sell_limit_fills(monitor, db_session, mock_price_cache):
    """Sell limit fills when bid >= requested_price."""
    mock_price_cache.get_ticker = AsyncMock(return_value=(
        {"bid": 102.0, "ask": 102.5, "last": 102.25}, True
    ))

    order = _make_limit_order(db_session, side="sell", requested_price=101.0)

    result = await monitor.check_pending_orders()
    assert result["filled"] == 1

    db_session.refresh(order)
    assert order.fill_price == 102.0  # bid > limit, so fills at bid (improvement)


@pytest.mark.asyncio
async def test_no_fill_on_stale_price(monitor, db_session, mock_price_cache):
    """Stale prices should not trigger fills."""
    mock_price_cache.get_ticker = AsyncMock(return_value=(
        {"bid": 98.0, "ask": 98.5}, False  # Not fresh
    ))

    order = _make_limit_order(db_session, requested_price=99.0)

    result = await monitor.check_pending_orders()
    assert result["skipped_stale"] == 1
    assert result["filled"] == 0


@pytest.mark.asyncio
async def test_24h_expiry(monitor, db_session, mock_price_cache):
    """Orders older than 24h should expire."""
    old_time = datetime.now(timezone.utc) - timedelta(hours=25)
    order = _make_limit_order(db_session, requested_at=old_time)

    result = await monitor.check_pending_orders()
    assert result["expired"] == 1

    db_session.refresh(order)
    assert order.status == "expired"


@pytest.mark.asyncio
async def test_reservation_released_on_expiry(monitor, db_session, mock_price_cache):
    """Expired order should release cash reservation."""
    old_time = datetime.now(timezone.utc) - timedelta(hours=25)
    order = _make_limit_order(db_session, requested_at=old_time, reserved_amount=11.0)

    agent = db_session.get(Agent, 1)
    reserved_before = agent.reserved_cash

    result = await monitor.check_pending_orders()

    db_session.refresh(agent)
    assert agent.reserved_cash < reserved_before


@pytest.mark.asyncio
async def test_reservation_released_on_fill(monitor, db_session, mock_price_cache):
    """Filled order should release cash reservation."""
    order = _make_limit_order(db_session, requested_price=99.0)

    result = await monitor.check_pending_orders()
    assert result["filled"] == 1

    db_session.refresh(order)
    assert order.reservation_released is True


@pytest.mark.asyncio
async def test_position_created_on_fill(monitor, db_session, mock_price_cache):
    """Filling a limit order should create a Position record."""
    order = _make_limit_order(db_session, requested_price=99.0)

    result = await monitor.check_pending_orders()

    positions = db_session.query(Position).filter(Position.agent_id == 1).all()
    assert len(positions) == 1
    assert positions[0].status == "open"
    assert positions[0].side == "long"
