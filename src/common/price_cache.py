"""
Project Syndicate — Price Cache

Redis-backed price cache for ticker data and order books.
Short TTLs (10s) keep data fresh; stale threshold (60s) for safety checks.
Falls back to stale cache on exchange errors.
"""

__version__ = "0.9.0"

import json
import logging
import time
from typing import Any

from src.common.config import config

logger = logging.getLogger(__name__)


class PriceCache:
    """Redis-backed cache for ticker and order book data.

    Attributes:
        TICKER_TTL: Seconds before a ticker is considered stale for cache purposes.
        ORDER_BOOK_TTL: Seconds before an order book is considered stale.
        STALE_THRESHOLD: Seconds beyond which data should NOT be used for stop/TP triggers.
    """

    TICKER_TTL: int = config.price_cache_ticker_ttl
    ORDER_BOOK_TTL: int = config.price_cache_orderbook_ttl
    STALE_THRESHOLD: int = config.stale_price_threshold

    def __init__(self, redis_client, exchange_service=None):
        """
        Args:
            redis_client: Redis client instance.
            exchange_service: Optional exchange service for fetching live data.
        """
        self.redis = redis_client
        self.exchange = exchange_service

    async def get_ticker(self, symbol: str) -> tuple[dict | None, bool]:
        """Get ticker data for a symbol.

        Args:
            symbol: Trading pair (e.g., "BTC/USDT").

        Returns:
            Tuple of (ticker_dict, is_fresh). is_fresh=False if data is older
            than TICKER_TTL but younger than STALE_THRESHOLD. Returns (None, False)
            if no data available at all.
        """
        cache_key = f"price:{symbol}"
        cached = self._get_cached(cache_key)

        if cached is not None:
            age = time.time() - cached.get("_cached_at", 0)
            is_fresh = age <= self.TICKER_TTL
            return cached, is_fresh

        # Cache miss — fetch from exchange
        if self.exchange:
            try:
                ticker = await self.exchange.get_ticker(symbol)
                if ticker:
                    self._set_cached(cache_key, ticker, self.TICKER_TTL)
                    return ticker, True
            except Exception as e:
                logger.warning(f"Exchange ticker fetch failed for {symbol}: {e}")

        return None, False

    async def get_order_book(self, symbol: str, limit: int = 20) -> tuple[dict | None, bool]:
        """Get order book for a symbol.

        Args:
            symbol: Trading pair.
            limit: Number of levels per side.

        Returns:
            Tuple of (order_book_dict, is_fresh).
        """
        cache_key = f"orderbook:{symbol}"
        cached = self._get_cached(cache_key)

        if cached is not None:
            age = time.time() - cached.get("_cached_at", 0)
            is_fresh = age <= self.ORDER_BOOK_TTL
            return cached, is_fresh

        # Cache miss — fetch from exchange
        if self.exchange:
            try:
                book = await self.exchange.get_order_book(symbol, limit=limit)
                if book:
                    self._set_cached(cache_key, book, self.ORDER_BOOK_TTL)
                    return book, True
            except Exception as e:
                logger.warning(f"Exchange order book fetch failed for {symbol}: {e}")

        return None, False

    async def batch_fetch_tickers(self, symbols: list[str]) -> dict[str, dict]:
        """Fetch tickers for multiple symbols, using cache where possible.

        Args:
            symbols: List of trading pairs.

        Returns:
            Dict mapping symbol to ticker data.
        """
        result = {}
        to_fetch = []

        for symbol in symbols:
            cache_key = f"price:{symbol}"
            cached = self._get_cached(cache_key)
            if cached is not None:
                age = time.time() - cached.get("_cached_at", 0)
                if age <= self.TICKER_TTL:
                    result[symbol] = cached
                    continue
            to_fetch.append(symbol)

        # Fetch uncached symbols from exchange
        if to_fetch and self.exchange:
            for symbol in to_fetch:
                try:
                    ticker = await self.exchange.get_ticker(symbol)
                    if ticker:
                        self._set_cached(f"price:{symbol}", ticker, self.TICKER_TTL)
                        result[symbol] = ticker
                except Exception as e:
                    logger.warning(f"Batch fetch failed for {symbol}: {e}")

        return result

    def is_stale(self, symbol: str) -> bool:
        """Check if cached ticker data is beyond the stale threshold.

        Args:
            symbol: Trading pair.

        Returns:
            True if data is stale or missing.
        """
        cache_key = f"price:{symbol}"
        cached = self._get_cached(cache_key)
        if cached is None:
            return True
        age = time.time() - cached.get("_cached_at", 0)
        return age > self.STALE_THRESHOLD

    def _get_cached(self, key: str) -> dict | None:
        """Read a cached JSON value from Redis."""
        try:
            raw = self.redis.get(key)
            if raw is None:
                return None
            return json.loads(raw)
        except Exception:
            return None

    def _set_cached(self, key: str, data: dict, ttl: int) -> None:
        """Write a JSON value to Redis with TTL."""
        try:
            data_with_ts = {**data, "_cached_at": time.time()}
            self.redis.set(key, json.dumps(data_with_ts), ex=ttl * 6)  # Redis TTL longer than logical TTL
        except Exception as e:
            logger.warning(f"Cache write failed for {key}: {e}")
