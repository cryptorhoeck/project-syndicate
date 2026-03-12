"""Tests for PositionMonitor — Phase 3C."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.common.models import Agent, Base, Position, SystemState, Transaction
from src.trading.fee_schedule import FeeSchedule
from src.trading.position_monitor import PositionMonitor
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

    state = SystemState(total_treasury=1000.0, peak_treasury=1000.0)
    session.add(state)

    agent = Agent(
        name="Operator-Test", type="operator", status="active", generation=1,
        capital_allocated=100.0, capital_current=100.0,
        cash_balance=50.0, total_equity=100.0, unrealized_pnl=0.0,
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


def _make_position(db_session, **kwargs):
    defaults = dict(
        agent_id=1, agent_name="Operator-Test", symbol="BTC/USDT",
        side="long", entry_price=100.0, current_price=100.0,
        quantity=0.5, size_usd=50.0, status="open", execution_venue="paper",
    )
    defaults.update(kwargs)
    pos = Position(**defaults)
    db_session.add(pos)
    db_session.commit()
    return pos


@pytest.fixture
def mock_price_cache():
    cache = MagicMock()
    cache.batch_fetch_tickers = AsyncMock(return_value={
        "BTC/USDT": {"bid": 105.0, "ask": 105.5, "last": 105.25},
    })
    cache.get_ticker = AsyncMock(return_value=(
        {"bid": 105.0, "ask": 105.5}, True
    ))
    cache.is_stale = MagicMock(return_value=False)
    return cache


@pytest.fixture
def mock_slippage():
    model = MagicMock(spec=SlippageModel)
    model.calculate_slippage = AsyncMock(return_value=0.001)
    return model


@pytest.fixture
def mock_redis():
    r = MagicMock()
    r.set.return_value = True
    r.delete.return_value = True
    return r


@pytest.fixture
def monitor(db_factory, mock_price_cache, mock_slippage, mock_redis):
    return PositionMonitor(
        db_session_factory=db_factory,
        price_cache=mock_price_cache,
        slippage_model=mock_slippage,
        fee_schedule=FeeSchedule(),
        redis_client=mock_redis,
    )


@pytest.mark.asyncio
async def test_unrealized_pnl_update(monitor, db_session):
    """Position P&L should update on each check."""
    pos = _make_position(db_session, entry_price=100.0)
    result = await monitor.check_all_positions()
    assert result["updated"] >= 1

    db_session.refresh(pos)
    assert pos.unrealized_pnl != 0  # Price moved to 105


@pytest.mark.asyncio
async def test_stop_loss_long(monitor, db_session, mock_price_cache):
    """Long stop-loss triggers when bid <= stop_loss."""
    pos = _make_position(db_session, entry_price=100.0, stop_loss=95.0)

    # Set price below stop
    mock_price_cache.batch_fetch_tickers = AsyncMock(return_value={
        "BTC/USDT": {"bid": 94.0, "ask": 94.5, "last": 94.25},
    })
    mock_price_cache.get_ticker = AsyncMock(return_value=({"bid": 94.0, "ask": 94.5}, True))

    result = await monitor.check_all_positions()
    assert result["stopped"] >= 1

    db_session.refresh(pos)
    assert pos.status == "stopped_out"
    assert pos.realized_pnl is not None


@pytest.mark.asyncio
async def test_stop_loss_short(monitor, db_session, mock_price_cache):
    """Short stop-loss triggers when ask >= stop_loss."""
    pos = _make_position(db_session, side="short", entry_price=100.0, stop_loss=105.0)

    mock_price_cache.batch_fetch_tickers = AsyncMock(return_value={
        "BTC/USDT": {"bid": 106.0, "ask": 106.5, "last": 106.25},
    })

    result = await monitor.check_all_positions()
    assert result["stopped"] >= 1


@pytest.mark.asyncio
async def test_take_profit_long(monitor, db_session, mock_price_cache):
    """Long take-profit triggers when bid >= take_profit."""
    pos = _make_position(db_session, entry_price=100.0, take_profit=104.0)

    # Price is already at 105, above TP
    result = await monitor.check_all_positions()
    assert result["tp_hit"] >= 1

    db_session.refresh(pos)
    assert pos.status == "take_profit_hit"
    assert pos.close_price == 104.0  # Fills at TP price


@pytest.mark.asyncio
async def test_stale_price_skips_stops(monitor, db_session, mock_price_cache):
    """Stale prices should NOT trigger stops."""
    pos = _make_position(db_session, entry_price=100.0, stop_loss=106.0)

    mock_price_cache.is_stale = MagicMock(return_value=True)

    result = await monitor.check_all_positions()
    assert result["stale_skipped"] >= 1

    db_session.refresh(pos)
    assert pos.status == "open"  # Not stopped despite price triggering


@pytest.mark.asyncio
async def test_exception_resilience(monitor, db_session, mock_price_cache):
    """Monitor should not crash on individual position errors."""
    _make_position(db_session)
    mock_price_cache.batch_fetch_tickers = AsyncMock(side_effect=Exception("Redis error"))

    # Should not raise
    result = await monitor.check_all_positions()
    assert result is not None


@pytest.mark.asyncio
async def test_transaction_written_on_close(monitor, db_session, mock_price_cache):
    """Closing a position should write a Transaction record."""
    pos = _make_position(db_session, entry_price=100.0, stop_loss=106.0)

    mock_price_cache.batch_fetch_tickers = AsyncMock(return_value={
        "BTC/USDT": {"bid": 94.0, "ask": 94.5, "last": 94.25},
    })
    mock_price_cache.get_ticker = AsyncMock(return_value=({"bid": 94.0, "ask": 94.5}, True))

    await monitor.check_all_positions()

    txns = db_session.query(Transaction).filter(Transaction.agent_id == 1).all()
    assert len(txns) >= 1
    assert txns[0].exchange == "paper"
