"""Tests for PriceCache — Phase 3C."""

import json
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.common.price_cache import PriceCache


@pytest.fixture
def mock_redis():
    r = MagicMock()
    r.get.return_value = None
    r.set.return_value = True
    return r


@pytest.fixture
def mock_exchange():
    ex = MagicMock()
    ex.get_ticker = AsyncMock(return_value={
        "bid": 100.0, "ask": 100.5, "last": 100.25, "baseVolume": 1000000,
    })
    ex.get_order_book = AsyncMock(return_value={
        "asks": [[100.5, 10], [101.0, 20]], "bids": [[100.0, 10], [99.5, 20]],
    })
    return ex


@pytest.fixture
def cache(mock_redis, mock_exchange):
    return PriceCache(redis_client=mock_redis, exchange_service=mock_exchange)


@pytest.mark.asyncio
async def test_cache_miss_fetches_from_exchange(cache, mock_exchange):
    """Cache miss should fetch from exchange and cache the result."""
    ticker, is_fresh = await cache.get_ticker("BTC/USDT")
    assert ticker is not None
    assert is_fresh is True
    assert ticker["bid"] == 100.0
    mock_exchange.get_ticker.assert_called_once_with("BTC/USDT")


@pytest.mark.asyncio
async def test_cache_hit_returns_cached(cache, mock_redis):
    """Cached data should be returned without hitting exchange."""
    cached_data = json.dumps({
        "bid": 99.0, "ask": 99.5, "_cached_at": time.time(),
    })
    mock_redis.get.return_value = cached_data

    ticker, is_fresh = await cache.get_ticker("ETH/USDT")
    assert ticker is not None
    assert ticker["bid"] == 99.0
    assert is_fresh is True


@pytest.mark.asyncio
async def test_stale_cache_returns_not_fresh(cache, mock_redis):
    """Data older than TTL but younger than stale threshold returns is_fresh=False."""
    cached_data = json.dumps({
        "bid": 99.0, "ask": 99.5, "_cached_at": time.time() - 15,  # 15s old, TTL=10
    })
    mock_redis.get.return_value = cached_data

    ticker, is_fresh = await cache.get_ticker("ETH/USDT")
    assert ticker is not None
    assert is_fresh is False


@pytest.mark.asyncio
async def test_is_stale_beyond_threshold(cache, mock_redis):
    """Data beyond stale threshold should be flagged as stale."""
    cached_data = json.dumps({
        "bid": 99.0, "_cached_at": time.time() - 120,  # 120s old, threshold=60
    })
    mock_redis.get.return_value = cached_data
    assert cache.is_stale("BTC/USDT") is True


@pytest.mark.asyncio
async def test_is_stale_missing_data(cache, mock_redis):
    """Missing data should be stale."""
    mock_redis.get.return_value = None
    assert cache.is_stale("BTC/USDT") is True


@pytest.mark.asyncio
async def test_batch_fetch(cache, mock_exchange, mock_redis):
    """Batch fetch should return tickers for all requested symbols."""
    result = await cache.batch_fetch_tickers(["BTC/USDT", "ETH/USDT"])
    assert len(result) == 2


@pytest.mark.asyncio
async def test_order_book_cache_miss(cache, mock_exchange):
    """Order book cache miss fetches from exchange."""
    book, is_fresh = await cache.get_order_book("BTC/USDT")
    assert book is not None
    assert is_fresh is True
    assert len(book["asks"]) == 2


@pytest.mark.asyncio
async def test_no_exchange_returns_none(mock_redis):
    """No exchange service should return (None, False)."""
    cache = PriceCache(redis_client=mock_redis, exchange_service=None)
    ticker, is_fresh = await cache.get_ticker("BTC/USDT")
    assert ticker is None
    assert is_fresh is False


@pytest.mark.asyncio
async def test_exchange_error_fallback(cache, mock_exchange, mock_redis):
    """Exchange error should not crash — returns (None, False)."""
    mock_exchange.get_ticker = AsyncMock(side_effect=Exception("Network error"))
    ticker, is_fresh = await cache.get_ticker("BTC/USDT")
    assert ticker is None
    assert is_fresh is False
