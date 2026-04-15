"""
Project Syndicate — Background Price Fetcher

Continuously fetches ticker and OHLCV data from Kraken and writes to Redis.
Ensures PriceCache, SandboxDataAPI, and RegimeDetector always have fresh data.

Runs as a background process alongside the Arena.
Fetches every 15 seconds for tickers, every 60 seconds for OHLCV.
"""

__version__ = "0.1.0"

import asyncio
import json
import logging
import os
import sys
import time

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from dotenv import load_dotenv
load_dotenv(os.path.join(PROJECT_ROOT, ".env"), override=True)

import redis
import structlog
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from src.common.config import config
from src.common.exchange_service import ExchangeService

structlog.configure(
    processors=[
        structlog.stdlib.add_log_level,
        structlog.dev.ConsoleRenderer(),
    ],
    wrapper_class=structlog.stdlib.BoundLogger,
    context_class=dict,
    logger_factory=structlog.PrintLoggerFactory(),
)
log = structlog.get_logger("price_fetcher")

# Symbols to track — the core Arena markets
SYMBOLS = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "XRP/USDT", "ADA/USDT"]
OHLCV_TIMEFRAMES = ["1h", "4h", "1d"]
TICKER_INTERVAL = 15    # seconds between ticker fetches
OHLCV_INTERVAL = 120    # seconds between OHLCV fetches


async def fetch_and_cache_tickers(exchange: ExchangeService, r: redis.Redis) -> int:
    """Fetch tickers for all symbols and write to Redis. Returns count."""
    cached = 0
    for symbol in SYMBOLS:
        try:
            ticker = await exchange.get_ticker(symbol)
            if ticker and ticker.get("last"):
                data = {**ticker, "_cached_at": time.time()}
                r.set(f"price:{symbol}", json.dumps(data), ex=120)
                cached += 1
        except Exception as e:
            log.debug("ticker_fetch_failed", symbol=symbol, error=str(e))
    return cached


async def fetch_and_cache_ohlcv(exchange: ExchangeService, r: redis.Redis) -> int:
    """Fetch OHLCV for all symbols/timeframes and write to Redis. Returns count."""
    cached = 0
    for symbol in SYMBOLS:
        for tf in OHLCV_TIMEFRAMES:
            try:
                candles = await exchange.get_ohlcv(symbol, tf, limit=100)
                if candles:
                    data = {
                        "symbol": symbol,
                        "timeframe": tf,
                        "candles": candles,
                        "_cached_at": time.time(),
                    }
                    r.set(f"ohlcv:{symbol}:{tf}", json.dumps(data), ex=300)
                    cached += 1
            except Exception as e:
                log.debug("ohlcv_fetch_failed", symbol=symbol, timeframe=tf, error=str(e))
    return cached


async def update_regime_in_db() -> None:
    """Run regime detection and update system_state."""
    try:
        exchange = ExchangeService()
        from src.genesis.regime_detector import RegimeDetector
        engine = create_engine(config.database_url)
        factory = sessionmaker(bind=engine)
        detector = RegimeDetector(exchange, factory)

        result = await detector.detect_regime()
        regime = result.get("regime", "unknown")

        if regime != "unknown":
            with factory() as session:
                session.execute(text(
                    "UPDATE system_state SET current_regime = :regime WHERE id = 1"
                ), {"regime": regime})
                session.commit()
            log.info("regime_updated", regime=regime)

        await exchange.primary.close()
        if exchange.secondary:
            await exchange.secondary.close()
        engine.dispose()
    except Exception as e:
        log.warning("regime_update_failed", error=str(e))


async def main():
    log.info("price_fetcher_starting", symbols=SYMBOLS, ticker_interval=TICKER_INTERVAL)

    r = redis.Redis.from_url(
        config.redis_url,
        socket_timeout=10, socket_connect_timeout=5, retry_on_timeout=True,
    )
    try:
        r.ping()
    except Exception as e:
        log.error("redis_connection_failed", error=str(e))
        return

    exchange = ExchangeService()
    last_ohlcv = 0
    last_regime = 0
    cycle = 0

    try:
        while True:
            cycle += 1
            now = time.time()

            # Tickers every cycle
            count = await fetch_and_cache_tickers(exchange, r)
            if count > 0:
                log.info("tickers_cached", count=count, cycle=cycle)

            # OHLCV every OHLCV_INTERVAL
            if now - last_ohlcv >= OHLCV_INTERVAL:
                ohlcv_count = await fetch_and_cache_ohlcv(exchange, r)
                if ohlcv_count > 0:
                    log.info("ohlcv_cached", count=ohlcv_count, cycle=cycle)
                last_ohlcv = now

            # Regime detection every 5 minutes
            if now - last_regime >= 300:
                await update_regime_in_db()
                last_regime = now

            # Heartbeat
            r.set("heartbeat:price_fetcher", json.dumps({
                "cycle": cycle, "timestamp": time.time(),
                "tickers_cached": count,
            }), ex=60)

            await asyncio.sleep(TICKER_INTERVAL)

    except KeyboardInterrupt:
        log.info("price_fetcher_stopping")
    finally:
        await exchange.primary.close()
        if exchange.secondary:
            await exchange.secondary.close()


if __name__ == "__main__":
    asyncio.run(main())
