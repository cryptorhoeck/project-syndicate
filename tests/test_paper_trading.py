"""Tests for PaperTradingService — Phase 3C."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.common.models import Agent, Base, Order, Position, SystemState, Transaction
from src.trading.execution_service import PaperTradingService
from src.trading.fee_schedule import FeeSchedule
from src.trading.slippage_model import SlippageModel


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

    # Seed system state
    state = SystemState(total_treasury=1000.0, peak_treasury=1000.0, active_agent_count=1)
    session.add(state)

    # Seed agent with cash
    agent = Agent(
        name="Operator-Test",
        type="operator",
        status="active",
        generation=1,
        capital_allocated=100.0,
        capital_current=100.0,
        cash_balance=100.0,
        reserved_cash=0.0,
        total_equity=100.0,
    )
    session.add(agent)
    session.commit()

    yield session
    session.close()


@pytest.fixture
def db_factory(db_session):
    """Returns a session factory that always yields the same session."""
    class FakeFactory:
        def __call__(self):
            return self
        def __enter__(self):
            return db_session
        def __exit__(self, *args):
            pass
    return FakeFactory()


@pytest.fixture
def mock_price_cache():
    cache = MagicMock()
    cache.get_ticker = AsyncMock(return_value=(
        {"bid": 100.0, "ask": 100.5, "last": 100.25, "baseVolume": 1000000}, True
    ))
    cache.get_order_book = AsyncMock(return_value=(
        {"asks": [[100.5, 100]], "bids": [[100.0, 100]]}, True
    ))
    cache.is_stale = MagicMock(return_value=False)
    return cache


@pytest.fixture
def mock_slippage():
    model = MagicMock(spec=SlippageModel)
    model.calculate_slippage = AsyncMock(return_value=0.001)  # 0.1%
    return model


@pytest.fixture
def mock_redis():
    r = MagicMock()
    r.set.return_value = True
    r.delete.return_value = True
    return r


@pytest.fixture
def approving_warden():
    """Warden mock that always approves. Required after hotfix
    `warden-trade-gate-wiring`: PaperTradingService now hard-rejects when
    `self.warden is None` (defense in depth). Tests that exercise the
    happy-path execution must inject a Warden — production wiring does
    too. The dedicated test for the soft-pass branch lives in
    `tests/test_warden_trade_gate_wiring.py`.
    """
    w = MagicMock()
    w.evaluate_trade = AsyncMock(return_value={"status": "approved", "reason": "test", "request_id": "test"})
    return w


@pytest.fixture
def service(db_factory, mock_price_cache, mock_slippage, mock_redis, approving_warden):
    return PaperTradingService(
        db_session_factory=db_factory,
        price_cache=mock_price_cache,
        slippage_model=mock_slippage,
        fee_schedule=FeeSchedule(),
        warden=approving_warden,
        redis_client=mock_redis,
    )


@pytest.mark.asyncio
async def test_market_buy(service, db_session):
    """Market buy should create position and deduct cash."""
    result = await service.execute_market_order(
        agent_id=1, symbol="BTC/USDT", side="buy", size_usd=10.0,
    )
    assert result.success is True
    assert result.order_id is not None
    assert result.position_id is not None
    assert result.fill_price > 0
    assert result.fee_usd > 0

    # Check agent cash decreased
    agent = db_session.get(Agent, 1)
    assert agent.cash_balance < 100.0
    assert agent.position_count == 1


@pytest.mark.asyncio
async def test_market_sell(service, db_session):
    """Market sell (short) should create position."""
    result = await service.execute_market_order(
        agent_id=1, symbol="ETH/USDT", side="sell", size_usd=10.0,
    )
    assert result.success is True

    # Check position is short
    pos = db_session.get(Position, result.position_id)
    assert pos.side == "short"


@pytest.mark.asyncio
async def test_insufficient_funds(service, db_session):
    """Order larger than balance should be rejected."""
    result = await service.execute_market_order(
        agent_id=1, symbol="BTC/USDT", side="buy", size_usd=200.0,
    )
    assert result.success is False
    assert "Insufficient" in result.error


@pytest.mark.asyncio
async def test_limit_order_creates_pending(service, db_session):
    """Limit order should create a pending order and reserve cash."""
    result = await service.execute_limit_order(
        agent_id=1, symbol="BTC/USDT", side="buy", size_usd=10.0, price=99.0,
    )
    assert result.success is True
    assert result.order_id is not None

    # Check reservation
    agent = db_session.get(Agent, 1)
    assert agent.reserved_cash > 0

    # Check order status
    order = db_session.get(Order, result.order_id)
    assert order.status == "pending"


@pytest.mark.asyncio
async def test_cancel_order(service, db_session):
    """Cancelling a pending order should release reservation."""
    place = await service.execute_limit_order(
        agent_id=1, symbol="BTC/USDT", side="buy", size_usd=10.0, price=99.0,
    )
    agent = db_session.get(Agent, 1)
    reserved_before = agent.reserved_cash

    cancel = await service.cancel_order(place.order_id)
    assert cancel.success is True
    assert cancel.released_amount > 0

    db_session.refresh(agent)
    assert agent.reserved_cash < reserved_before


@pytest.mark.asyncio
async def test_close_position(service, db_session):
    """Closing a position should update P&L and return cash."""
    # Open position
    open_result = await service.execute_market_order(
        agent_id=1, symbol="BTC/USDT", side="buy", size_usd=10.0,
    )
    agent = db_session.get(Agent, 1)
    cash_after_open = agent.cash_balance

    # Close position
    close_result = await service.close_position(open_result.position_id)
    assert close_result.success is True
    assert close_result.realized_pnl is not None

    db_session.refresh(agent)
    assert agent.cash_balance > cash_after_open  # Got some value back
    assert agent.position_count == 0


@pytest.mark.asyncio
async def test_close_position_redis_lock(service, mock_redis, db_session):
    """Double-close should fail due to Redis lock."""
    open_result = await service.execute_market_order(
        agent_id=1, symbol="BTC/USDT", side="buy", size_usd=10.0,
    )

    # First close succeeds
    close1 = await service.close_position(open_result.position_id)
    assert close1.success is True

    # Second close fails (position already closed)
    close2 = await service.close_position(open_result.position_id)
    assert close2.success is False


@pytest.mark.asyncio
async def test_warden_rejection(db_factory, mock_price_cache, mock_slippage, mock_redis):
    """Warden rejection should create rejected order."""
    mock_warden = MagicMock()
    mock_warden.evaluate_trade = AsyncMock(return_value={
        "status": "rejected", "reason": "RED ALERT", "request_id": "test123",
    })

    svc = PaperTradingService(
        db_session_factory=db_factory,
        price_cache=mock_price_cache,
        slippage_model=mock_slippage,
        fee_schedule=FeeSchedule(),
        warden=mock_warden,
        redis_client=mock_redis,
    )
    result = await svc.execute_market_order(
        agent_id=1, symbol="BTC/USDT", side="buy", size_usd=10.0,
    )
    assert result.success is False
    assert "RED ALERT" in result.error


@pytest.mark.asyncio
async def test_transaction_created(service, db_session):
    """Market order should create a Transaction record for Accountant."""
    await service.execute_market_order(
        agent_id=1, symbol="BTC/USDT", side="buy", size_usd=10.0,
    )
    txns = db_session.query(Transaction).filter(Transaction.agent_id == 1).all()
    assert len(txns) >= 1
    assert txns[0].exchange == "paper"


@pytest.mark.asyncio
async def test_agent_not_found(service):
    """Non-existent agent should return error."""
    result = await service.execute_market_order(
        agent_id=999, symbol="BTC/USDT", side="buy", size_usd=10.0,
    )
    assert result.success is False
    assert "not found" in result.error


@pytest.mark.asyncio
async def test_get_balance(service, db_session):
    """get_balance should return correct agent balance info."""
    bal = await service.get_balance(1)
    assert bal.cash_balance == 100.0
    assert bal.available_cash == 100.0
    assert bal.position_count == 0


@pytest.mark.asyncio
async def test_get_positions_empty(service):
    """No open positions should return empty list."""
    positions = await service.get_positions(1)
    assert positions == []
