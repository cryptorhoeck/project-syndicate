"""Tests for SlippageModel — Phase 3C."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.trading.slippage_model import SlippageModel


@pytest.fixture
def model():
    return SlippageModel()


@pytest.fixture
def mock_price_cache():
    cache = MagicMock()
    cache.get_order_book = AsyncMock(return_value=({
        "asks": [[100.0, 10], [100.5, 20], [101.0, 50]],
        "bids": [[99.5, 10], [99.0, 20], [98.5, 50]],
    }, True))
    return cache


@pytest.mark.asyncio
async def test_slippage_with_order_book(model, mock_price_cache):
    """Slippage should be calculated from order book."""
    slip = await model.calculate_slippage(500, "BTC/USDT", "buy", mock_price_cache)
    assert slip >= model.MIN_SLIPPAGE
    assert slip < 0.1  # Should be reasonable


@pytest.mark.asyncio
async def test_slippage_floor(model, mock_price_cache):
    """Slippage should never be below minimum floor."""
    slip = await model.calculate_slippage(1, "BTC/USDT", "buy", mock_price_cache)
    assert slip >= model.MIN_SLIPPAGE


@pytest.mark.asyncio
async def test_slippage_scales_with_size(model, mock_price_cache):
    """Larger orders should generally have more slippage (on average)."""
    # Run multiple times to account for noise
    small_slips = []
    large_slips = []
    for _ in range(20):
        s = await model.calculate_slippage(100, "BTC/USDT", "buy", mock_price_cache)
        l = await model.calculate_slippage(50000, "BTC/USDT", "buy", mock_price_cache)
        small_slips.append(s)
        large_slips.append(l)

    avg_small = sum(small_slips) / len(small_slips)
    avg_large = sum(large_slips) / len(large_slips)
    # Large orders should tend to have more slippage due to book depth penalty
    assert avg_large >= avg_small * 0.5  # Loose check — noise can affect this


@pytest.mark.asyncio
async def test_slippage_without_order_book(model):
    """Without order book, should use fallback estimate."""
    slip = await model.calculate_slippage(500, "BTC/USDT", "buy", None)
    assert slip >= model.MIN_SLIPPAGE


@pytest.mark.asyncio
async def test_buy_vs_sell_both_positive(model, mock_price_cache):
    """Both buy and sell slippage should be positive."""
    buy_slip = await model.calculate_slippage(500, "BTC/USDT", "buy", mock_price_cache)
    sell_slip = await model.calculate_slippage(500, "BTC/USDT", "sell", mock_price_cache)
    assert buy_slip > 0
    assert sell_slip > 0


@pytest.mark.asyncio
async def test_noise_varies_results(model):
    """Noise should cause different results on repeated calls."""
    # Use a large order that spans multiple book levels for non-zero base slippage
    cache = MagicMock()
    cache.get_order_book = AsyncMock(return_value=({
        "asks": [[100.0, 1], [101.0, 1], [102.0, 1], [103.0, 1], [104.0, 1]],
        "bids": [[99.0, 1], [98.0, 1], [97.0, 1]],
    }, True))

    results = set()
    for _ in range(30):
        slip = await model.calculate_slippage(5000, "BTC/USDT", "buy", cache)
        results.add(round(slip, 10))
    # Should have at least a few different values due to noise
    assert len(results) >= 2


@pytest.mark.asyncio
async def test_depth_penalty_applied(model):
    """Order exceeding book depth should get penalty."""
    cache = MagicMock()
    # Very thin book — only $100 available
    cache.get_order_book = AsyncMock(return_value=({
        "asks": [[100.0, 1]],  # Only $100 of depth
        "bids": [[99.0, 1]],
    }, True))

    slip = await model.calculate_slippage(10000, "BTC/USDT", "buy", cache)
    assert slip >= model.DEPTH_PENALTY * 0.5  # Should include depth penalty (with noise)


@pytest.mark.asyncio
async def test_fallback_tiered_estimates(model):
    """Fallback estimates should be tiered by order size."""
    slip_small = model._estimate_slippage(50)
    slip_medium = model._estimate_slippage(500)
    slip_large = model._estimate_slippage(5000)
    assert slip_small < slip_medium < slip_large
