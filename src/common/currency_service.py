"""
Project Syndicate — Currency Conversion Service

Handles USDT/CAD and USD/CAD conversion for the accounting layer.
Agents trade in USDT pairs; the owner sees everything in CAD.

Rate source: Kraken CAD/USDT pair (inverted to get USDT→CAD).
Fallback: configurable static rate if Kraken unavailable.
Caching: Redis with configurable TTL (default 5 minutes).
"""

__version__ = "1.0.0"

import logging
import time

import redis

from src.common.config import config

logger = logging.getLogger(__name__)

REDIS_KEY_USDT_CAD = "syndicate:fx:usdt_cad_rate"
REDIS_KEY_USD_CAD = "syndicate:fx:usd_cad_rate"
REDIS_KEY_RATE_TIMESTAMP = "syndicate:fx:rate_updated_at"


class CurrencyService:
    """Fetches and caches USDT/CAD and USD/CAD exchange rates.

    Two conversion paths:
      - USDT → CAD: for trading P&L, capital, treasury display
      - USD → CAD: for API costs (Anthropic bills in USD)

    Rate hierarchy:
      1. Redis cache (if fresh within TTL)
      2. Live fetch from Kraken via ccxt
      3. Config fallback rate (static, for testing or outages)
      4. Hardcoded emergency fallback (1.38)
    """

    def __init__(self, redis_client: redis.Redis | None = None):
        self._redis = redis_client
        self._local_cache: dict[str, float] = {}
        self._local_cache_time: float = 0.0

    # ── Public API ──────────────────────────────────────────

    def get_usdt_cad_rate(self) -> float:
        """Get the current USDT → CAD rate (how many CAD per 1 USDT).

        Checks Redis cache first, then fetches live from Kraken,
        then falls back to config rate.
        """
        # Manual override for testing
        if config.usdt_cad_manual_override > 0:
            return config.usdt_cad_manual_override

        # Check local in-memory cache (avoids Redis round-trip in hot paths)
        now = time.time()
        if (
            "usdt_cad" in self._local_cache
            and (now - self._local_cache_time) < config.currency_cache_ttl_seconds
        ):
            return self._local_cache["usdt_cad"]

        # Check Redis cache
        rate = self._get_cached_rate(REDIS_KEY_USDT_CAD)
        if rate is not None:
            self._local_cache["usdt_cad"] = rate
            self._local_cache_time = now
            return rate

        # Fetch live from Kraken
        rate = self._fetch_usdt_cad_from_kraken()
        if rate is not None:
            self._cache_rate(REDIS_KEY_USDT_CAD, rate)
            self._local_cache["usdt_cad"] = rate
            self._local_cache_time = now
            logger.info(f"USDT/CAD rate fetched from Kraken: {rate:.4f}")
            return rate

        # Fallback to config
        fallback = config.usdt_cad_fallback_rate
        logger.warning(f"Using fallback USDT/CAD rate: {fallback:.4f}")
        return fallback

    def get_usd_cad_rate(self) -> float:
        """Get the current USD → CAD rate (for API cost conversion).

        For simplicity, uses the USDT/CAD rate as approximation since
        USDT ≈ 1.00 USD. If a separate USD/CAD rate is needed in the
        future, this method can be extended.
        """
        if config.usd_cad_manual_override > 0:
            return config.usd_cad_manual_override

        # USD/CAD ≈ USDT/CAD since USDT is pegged to USD
        # Use the same rate source for consistency
        rate = self._get_cached_rate(REDIS_KEY_USD_CAD)
        if rate is not None:
            return rate

        # Fall back to USDT/CAD rate (close enough)
        usdt_rate = self.get_usdt_cad_rate()

        # Cache it separately so we can distinguish later if needed
        self._cache_rate(REDIS_KEY_USD_CAD, usdt_rate)
        return usdt_rate

    def usdt_to_cad(self, amount_usdt: float) -> float:
        """Convert a USDT amount to CAD."""
        if amount_usdt == 0:
            return 0.0
        return round(amount_usdt * self.get_usdt_cad_rate(), 6)

    def cad_to_usdt(self, amount_cad: float) -> float:
        """Convert a CAD amount to USDT."""
        if amount_cad == 0:
            return 0.0
        rate = self.get_usdt_cad_rate()
        if rate == 0:
            return 0.0
        return round(amount_cad / rate, 6)

    def usd_to_cad(self, amount_usd: float) -> float:
        """Convert a USD amount to CAD (for API costs)."""
        if amount_usd == 0:
            return 0.0
        return round(amount_usd * self.get_usd_cad_rate(), 6)

    # ── Internal ────────────────────────────────────────────

    def _fetch_usdt_cad_from_kraken(self) -> float | None:
        """Fetch live USDT/CAD rate from Kraken using synchronous ccxt.

        Kraken lists the pair as USDT/CAD. The 'last' price is how many
        CAD you get per 1 USDT — exactly what we need.
        """
        try:
            import ccxt

            kraken = ccxt.kraken({
                "enableRateLimit": True,
                "timeout": 10000,
            })
            ticker = kraken.fetch_ticker("USDT/CAD")
            last_price = ticker.get("last")
            if last_price and last_price > 0:
                # Also update the timestamp
                self._cache_rate(REDIS_KEY_RATE_TIMESTAMP, time.time())
                return float(last_price)
            logger.warning("Kraken USDT/CAD ticker returned no last price")
            return None
        except Exception as e:
            logger.warning(f"Failed to fetch USDT/CAD from Kraken: {e}")
            return None

    def _get_cached_rate(self, key: str) -> float | None:
        """Read a cached rate from Redis."""
        if not self._redis:
            return None
        try:
            value = self._redis.get(key)
            if value is not None:
                return float(value)
        except Exception as e:
            logger.debug(f"Redis cache read failed for {key}: {e}")
        return None

    def _cache_rate(self, key: str, value: float) -> None:
        """Write a rate to Redis with TTL."""
        if not self._redis:
            return
        try:
            self._redis.setex(
                key,
                config.currency_cache_ttl_seconds,
                str(value),
            )
        except Exception as e:
            logger.debug(f"Redis cache write failed for {key}: {e}")

    def invalidate_cache(self) -> None:
        """Clear all cached rates (useful for testing)."""
        self._local_cache.clear()
        self._local_cache_time = 0.0
        if self._redis:
            try:
                self._redis.delete(REDIS_KEY_USDT_CAD, REDIS_KEY_USD_CAD, REDIS_KEY_RATE_TIMESTAMP)
            except Exception:
                pass
