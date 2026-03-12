"""
Project Syndicate — Market Data Service

Lightweight market data wrapper providing summary data for agent context.
Uses the exchange service when available, mock/cached data as fallback.
"""

__version__ = "0.9.0"

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class MarketSnapshot:
    """A snapshot of a single market."""
    symbol: str
    price: float
    change_24h_pct: float
    volume_24h: float
    high_24h: float
    low_24h: float
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class MarketSummary:
    """Summary of the overall market state."""
    btc_price: float
    btc_change_24h: float
    total_markets_available: int
    regime: str  # from SystemState
    top_movers: list[MarketSnapshot] = field(default_factory=list)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


# Default markets to track when no exchange is available
DEFAULT_MARKETS = [
    "BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "XRP/USDT",
    "ADA/USDT", "AVAX/USDT", "DOT/USDT", "LINK/USDT", "MATIC/USDT",
]


class MarketDataService:
    """Provides market data for agent context assembly.

    When an exchange service is available, fetches live data.
    Otherwise, returns mock data suitable for paper trading.
    """

    CACHE_TTL_SECONDS = 60  # Cache tickers for 1 minute

    def __init__(self, exchange_service=None, db_session_factory=None):
        self.exchange = exchange_service
        self.db_factory = db_session_factory
        self._cache: dict[str, Any] = {}
        self._cache_time: float = 0

    async def get_top_markets(self, limit: int = 10) -> list[MarketSnapshot]:
        """Get top markets by volume.

        Args:
            limit: Number of markets to return.

        Returns:
            List of MarketSnapshot, sorted by 24h volume descending.
        """
        tickers = await self._get_tickers()
        snapshots = []

        for symbol, data in tickers.items():
            if not symbol.endswith("/USDT"):
                continue
            try:
                snapshots.append(MarketSnapshot(
                    symbol=symbol,
                    price=float(data.get("last", 0)),
                    change_24h_pct=float(data.get("percentage", 0) or 0),
                    volume_24h=float(data.get("quoteVolume", 0) or 0),
                    high_24h=float(data.get("high", 0) or 0),
                    low_24h=float(data.get("low", 0) or 0),
                ))
            except (ValueError, TypeError):
                continue

        snapshots.sort(key=lambda s: s.volume_24h, reverse=True)
        return snapshots[:limit]

    async def get_market_summary(self) -> MarketSummary:
        """Get an overall market summary.

        Returns:
            MarketSummary with BTC price, regime, and top movers.
        """
        tickers = await self._get_tickers()

        # Get BTC data
        btc = tickers.get("BTC/USDT", {})
        btc_price = float(btc.get("last", 0))
        btc_change = float(btc.get("percentage", 0) or 0)

        # Get regime from database
        regime = "unknown"
        if self.db_factory:
            try:
                from src.common.models import SystemState
                from sqlalchemy import select
                with self.db_factory() as session:
                    state = session.execute(select(SystemState).limit(1)).scalar_one_or_none()
                    if state:
                        regime = state.current_regime
            except Exception:
                pass

        top_movers = await self.get_top_markets(5)

        return MarketSummary(
            btc_price=btc_price,
            btc_change_24h=btc_change,
            total_markets_available=len(tickers),
            regime=regime,
            top_movers=top_movers,
        )

    async def get_market_snapshot(self, symbol: str) -> MarketSnapshot | None:
        """Get a snapshot for a specific market.

        Args:
            symbol: Trading pair (e.g., "BTC/USDT").

        Returns:
            MarketSnapshot or None if not found.
        """
        tickers = await self._get_tickers()
        data = tickers.get(symbol)
        if not data:
            return None

        try:
            return MarketSnapshot(
                symbol=symbol,
                price=float(data.get("last", 0)),
                change_24h_pct=float(data.get("percentage", 0) or 0),
                volume_24h=float(data.get("quoteVolume", 0) or 0),
                high_24h=float(data.get("high", 0) or 0),
                low_24h=float(data.get("low", 0) or 0),
            )
        except (ValueError, TypeError):
            return None

    def format_for_context(self, summary: MarketSummary) -> str:
        """Format market data for inclusion in agent context.

        Args:
            summary: The market summary to format.

        Returns:
            Formatted string for context assembly.
        """
        lines = [
            "=== MARKET DATA ===",
            f"BTC: ${summary.btc_price:,.2f} ({summary.btc_change_24h:+.1f}%)",
            f"Regime: {summary.regime}",
        ]

        if summary.top_movers:
            lines.append("Top movers:")
            for m in summary.top_movers:
                lines.append(
                    f"  {m.symbol}: ${m.price:,.4f} ({m.change_24h_pct:+.1f}%) "
                    f"vol=${m.volume_24h:,.0f}"
                )

        return "\n".join(lines)

    async def _get_tickers(self) -> dict:
        """Get ticker data, with caching.

        Returns:
            Dict of symbol → ticker data.
        """
        now = time.time()
        if self._cache and (now - self._cache_time) < self.CACHE_TTL_SECONDS:
            return self._cache

        if self.exchange:
            try:
                # Try primary exchange
                tickers = await self.exchange.primary.fetch_tickers()
                self._cache = tickers
                self._cache_time = now
                return tickers
            except Exception as e:
                logger.warning(f"Failed to fetch tickers from primary exchange: {e}")
                # Try secondary
                if self.exchange.secondary:
                    try:
                        tickers = await self.exchange.secondary.fetch_tickers()
                        self._cache = tickers
                        self._cache_time = now
                        return tickers
                    except Exception as e2:
                        logger.warning(f"Failed to fetch tickers from secondary exchange: {e2}")

        # Return mock data for paper trading / no exchange mode
        mock = self._mock_tickers()
        self._cache = mock
        self._cache_time = now
        return mock

    @staticmethod
    def _mock_tickers() -> dict:
        """Generate mock ticker data for testing/paper trading."""
        mock_prices = {
            "BTC/USDT": {"last": 67500.0, "percentage": 1.2, "quoteVolume": 2_500_000_000, "high": 68200, "low": 66800},
            "ETH/USDT": {"last": 3450.0, "percentage": 0.8, "quoteVolume": 1_200_000_000, "high": 3520, "low": 3400},
            "SOL/USDT": {"last": 142.5, "percentage": 3.5, "quoteVolume": 800_000_000, "high": 148, "low": 138},
            "BNB/USDT": {"last": 580.0, "percentage": -0.5, "quoteVolume": 400_000_000, "high": 590, "low": 575},
            "XRP/USDT": {"last": 0.62, "percentage": -1.2, "quoteVolume": 350_000_000, "high": 0.65, "low": 0.60},
            "ADA/USDT": {"last": 0.45, "percentage": 2.1, "quoteVolume": 200_000_000, "high": 0.47, "low": 0.43},
            "AVAX/USDT": {"last": 38.5, "percentage": 4.2, "quoteVolume": 180_000_000, "high": 40.0, "low": 37.0},
            "DOT/USDT": {"last": 7.8, "percentage": -0.3, "quoteVolume": 120_000_000, "high": 8.0, "low": 7.5},
            "LINK/USDT": {"last": 15.2, "percentage": 1.8, "quoteVolume": 150_000_000, "high": 15.8, "low": 14.9},
            "MATIC/USDT": {"last": 0.78, "percentage": 0.5, "quoteVolume": 100_000_000, "high": 0.80, "low": 0.76},
        }
        return mock_prices
