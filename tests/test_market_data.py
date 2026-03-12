"""Tests for the Market Data Service."""

__version__ = "0.8.0"

import pytest
from unittest.mock import MagicMock, AsyncMock

from src.common.market_data import MarketDataService, MarketSnapshot, MarketSummary


@pytest.fixture
def service():
    return MarketDataService()


class TestMockTickers:
    def test_mock_tickers_contains_btc(self, service):
        tickers = service._mock_tickers()
        assert "BTC/USDT" in tickers
        assert tickers["BTC/USDT"]["last"] > 0

    def test_mock_tickers_contains_top_10(self, service):
        tickers = service._mock_tickers()
        assert len(tickers) >= 10
        assert "ETH/USDT" in tickers
        assert "SOL/USDT" in tickers


class TestGetTopMarkets:
    @pytest.mark.asyncio
    async def test_returns_snapshots(self, service):
        markets = await service.get_top_markets(5)
        assert len(markets) == 5
        assert all(isinstance(m, MarketSnapshot) for m in markets)

    @pytest.mark.asyncio
    async def test_sorted_by_volume(self, service):
        markets = await service.get_top_markets(10)
        volumes = [m.volume_24h for m in markets]
        assert volumes == sorted(volumes, reverse=True)

    @pytest.mark.asyncio
    async def test_limit_respected(self, service):
        markets = await service.get_top_markets(3)
        assert len(markets) == 3


class TestGetMarketSummary:
    @pytest.mark.asyncio
    async def test_returns_summary(self, service):
        summary = await service.get_market_summary()
        assert isinstance(summary, MarketSummary)
        assert summary.btc_price > 0
        assert summary.total_markets_available > 0

    @pytest.mark.asyncio
    async def test_top_movers_included(self, service):
        summary = await service.get_market_summary()
        assert len(summary.top_movers) <= 5


class TestGetMarketSnapshot:
    @pytest.mark.asyncio
    async def test_existing_market(self, service):
        snap = await service.get_market_snapshot("BTC/USDT")
        assert snap is not None
        assert snap.symbol == "BTC/USDT"
        assert snap.price > 0

    @pytest.mark.asyncio
    async def test_nonexistent_market(self, service):
        snap = await service.get_market_snapshot("FAKE/USDT")
        assert snap is None


class TestFormatForContext:
    @pytest.mark.asyncio
    async def test_format_includes_btc(self, service):
        summary = await service.get_market_summary()
        text = service.format_for_context(summary)
        assert "BTC" in text
        assert "MARKET DATA" in text

    @pytest.mark.asyncio
    async def test_format_includes_regime(self, service):
        summary = await service.get_market_summary()
        text = service.format_for_context(summary)
        assert "Regime" in text


class TestCaching:
    @pytest.mark.asyncio
    async def test_cache_works(self, service):
        # First call
        await service.get_top_markets(5)
        assert service._cache

        # Second call should use cache
        cached = service._cache.copy()
        markets = await service.get_top_markets(5)
        assert service._cache == cached
