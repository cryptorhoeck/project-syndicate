"""
Tests for the Exchange Service — paper trading and error handling.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.common.exchange_service import PaperTradingService


@pytest.fixture
def paper_service():
    """Create a PaperTradingService with initial balance."""
    return PaperTradingService(initial_balance={"USD": 1000.0, "BTC": 0.0})


@pytest.mark.asyncio
async def test_paper_trading_place_order_and_fill(paper_service):
    """Paper trades should fill immediately and update balance."""
    # Mock the real exchange for ticker data
    mock_exchange = MagicMock()
    mock_exchange.get_ticker = AsyncMock(return_value={
        "symbol": "BTC/USD",
        "last": 50000.0,
        "bid": 49990.0,
        "ask": 50010.0,
        "volume": 1000.0,
        "change_24h": 1.5,
    })
    paper_service._real_exchange = mock_exchange

    # Place a buy order
    order = await paper_service.place_order(
        symbol="BTC/USD",
        side="buy",
        amount=0.01,
        order_type="market",
    )

    assert order["status"] == "closed"
    assert order["filled"] == 0.01
    assert order["price"] == 50000.0

    # Check balance updated
    balance = await paper_service.get_balance()
    assert balance["total"]["BTC"] == 0.01
    assert balance["total"]["USD"] < 1000.0  # Spent some USD


@pytest.mark.asyncio
async def test_paper_trading_insufficient_balance(paper_service):
    """Should raise on insufficient balance."""
    mock_exchange = MagicMock()
    mock_exchange.get_ticker = AsyncMock(return_value={"last": 50000.0})
    paper_service._real_exchange = mock_exchange

    with pytest.raises(Exception, match="Insufficient"):
        await paper_service.place_order(
            symbol="BTC/USD",
            side="buy",
            amount=1.0,  # $50,000 — way more than $1000 balance
            order_type="market",
        )


@pytest.mark.asyncio
async def test_paper_trading_sell(paper_service):
    """Should be able to sell after buying."""
    mock_exchange = MagicMock()
    mock_exchange.get_ticker = AsyncMock(return_value={"last": 50000.0})
    paper_service._real_exchange = mock_exchange

    # Buy first
    await paper_service.place_order(
        symbol="BTC/USD", side="buy", amount=0.01, order_type="market",
    )

    # Now sell
    mock_exchange.get_ticker = AsyncMock(return_value={"last": 52000.0})
    order = await paper_service.place_order(
        symbol="BTC/USD", side="sell", amount=0.01, order_type="market",
    )

    assert order["status"] == "closed"
    balance = await paper_service.get_balance()
    assert balance["total"]["BTC"] == 0.0
    # Should have more USD than we started with (profit from price increase)
    assert balance["total"]["USD"] > 990.0  # Account for fees


@pytest.mark.asyncio
async def test_paper_trading_get_open_orders(paper_service):
    """Open orders list should be empty after fills."""
    orders = await paper_service.get_open_orders()
    assert orders == []


@pytest.mark.asyncio
async def test_paper_trading_close_all(paper_service):
    """Close all should not crash on empty positions."""
    results = await paper_service.close_all_positions()
    assert isinstance(results, list)


@pytest.mark.asyncio
async def test_paper_trading_cancel_nonexistent():
    """Canceling a non-existent order should return not_found."""
    service = PaperTradingService()
    result = await service.cancel_order("fake-id", "BTC/USD")
    assert result["status"] == "not_found"
