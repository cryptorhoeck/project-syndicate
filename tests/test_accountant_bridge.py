"""Tests for Accountant Bridge — Phase 3C.

Verifies that paper trading operations correctly write Transaction records
that the Accountant can read for P&L calculation.
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.common.models import Agent, Base, Position, SystemState, Transaction
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

    state = SystemState(total_treasury=1000.0, peak_treasury=1000.0)
    session.add(state)

    agent = Agent(
        name="Operator-Test", type="operator", status="active", generation=1,
        capital_allocated=100.0, capital_current=100.0,
        cash_balance=100.0, reserved_cash=0.0, total_equity=100.0,
        total_fees_paid=0.0,
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
def service(db_factory, mock_price_cache):
    slippage = MagicMock(spec=SlippageModel)
    slippage.calculate_slippage = AsyncMock(return_value=0.001)

    redis = MagicMock()
    redis.set.return_value = True
    redis.delete.return_value = True

    # After hotfix `warden-trade-gate-wiring`, PaperTradingService hard-rejects
    # when warden is None. Inject an approving Warden mock — production
    # wiring also injects a real Warden.
    approving_warden = MagicMock()
    approving_warden.evaluate_trade = AsyncMock(
        return_value={"status": "approved", "reason": "test", "request_id": "test"}
    )

    return PaperTradingService(
        db_session_factory=db_factory,
        price_cache=mock_price_cache,
        slippage_model=slippage,
        fee_schedule=FeeSchedule(),
        warden=approving_warden,
        redis_client=redis,
    )


@pytest.mark.asyncio
async def test_entry_transaction_written(service, db_session):
    """Opening a position should write a Transaction with exchange='paper'."""
    await service.execute_market_order(
        agent_id=1, symbol="BTC/USDT", side="buy", size_usd=10.0,
    )

    txns = db_session.query(Transaction).filter(Transaction.agent_id == 1).all()
    assert len(txns) == 1
    assert txns[0].type == "spot"
    assert txns[0].exchange == "paper"
    assert txns[0].side == "buy"
    assert txns[0].fee > 0
    assert txns[0].price > 0


@pytest.mark.asyncio
async def test_exit_transaction_written(service, db_session):
    """Closing a position should write an exit Transaction with P&L."""
    # Open
    open_result = await service.execute_market_order(
        agent_id=1, symbol="BTC/USDT", side="buy", size_usd=10.0,
    )

    # Close
    await service.close_position(open_result.position_id)

    txns = db_session.query(Transaction).filter(Transaction.agent_id == 1).all()
    assert len(txns) == 2

    exit_txn = txns[1]
    assert exit_txn.side == "sell"
    assert exit_txn.pnl != 0 or exit_txn.fee > 0  # At minimum fees affect P&L


@pytest.mark.asyncio
async def test_fee_data_in_transactions(service, db_session):
    """Transaction records should contain correct fee data."""
    await service.execute_market_order(
        agent_id=1, symbol="BTC/USDT", side="buy", size_usd=10.0,
    )

    txn = db_session.query(Transaction).first()
    assert txn.fee > 0
    # Kraken taker fee on $10 = $0.026
    assert abs(txn.fee - 0.026) < 0.01


@pytest.mark.asyncio
async def test_accountant_can_sum_paper_trades(service, db_session):
    """Accountant-style query should correctly sum paper trade P&L."""
    from sqlalchemy import func

    # Open and close a position
    open_result = await service.execute_market_order(
        agent_id=1, symbol="BTC/USDT", side="buy", size_usd=10.0,
    )
    await service.close_position(open_result.position_id)

    # Accountant-style query: sum P&L for agent
    total_pnl = db_session.query(func.sum(Transaction.pnl)).filter(
        Transaction.agent_id == 1,
        Transaction.exchange == "paper",
    ).scalar()
    assert total_pnl is not None

    total_fees = db_session.query(func.sum(Transaction.fee)).filter(
        Transaction.agent_id == 1,
        Transaction.exchange == "paper",
    ).scalar()
    assert total_fees > 0
