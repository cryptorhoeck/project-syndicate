"""
Project Syndicate — Run Trading Monitors

Starts the PositionMonitor and LimitOrderMonitor as async tasks
in the same process (shared price cache and Redis connection).

Usage: python scripts/run_trading.py
"""

__version__ = "0.9.0"

import asyncio
import os
import signal
import sys

import structlog
from dotenv import load_dotenv

# Ensure project root is on sys.path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)
load_dotenv(os.path.join(PROJECT_ROOT, ".env"))

import redis
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.common.config import config
from src.common.price_cache import PriceCache
from src.trading.fee_schedule import FeeSchedule
from src.trading.limit_order_monitor import LimitOrderMonitor
from src.trading.position_monitor import PositionMonitor
from src.trading.slippage_model import SlippageModel

# Logging
structlog.configure(
    processors=[
        structlog.stdlib.add_log_level,
        structlog.dev.ConsoleRenderer(),
    ],
    wrapper_class=structlog.stdlib.BoundLogger,
    context_class=dict,
    logger_factory=structlog.PrintLoggerFactory(),
)
log = structlog.get_logger("run_trading")

_running = True


def _handle_signal(signum: int, _frame) -> None:
    global _running
    log.info("shutdown_signal_received", signal=signum)
    _running = False


async def main() -> None:
    global _running

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    log.info("trading_monitors_starting", version=__version__, mode=config.trading_mode)

    # Setup shared resources
    engine = create_engine(config.database_url)
    db_factory = sessionmaker(bind=engine)
    redis_client = redis.Redis.from_url(
        config.redis_url, decode_responses=True,
        socket_timeout=10, socket_connect_timeout=5, retry_on_timeout=True,
    )

    price_cache = PriceCache(redis_client=redis_client)
    slippage_model = SlippageModel()
    fee_schedule = FeeSchedule()

    # Create monitors
    position_monitor = PositionMonitor(
        db_session_factory=db_factory,
        price_cache=price_cache,
        slippage_model=slippage_model,
        fee_schedule=fee_schedule,
        redis_client=redis_client,
    )

    limit_order_monitor = LimitOrderMonitor(
        db_session_factory=db_factory,
        price_cache=price_cache,
        fee_schedule=fee_schedule,
        redis_client=redis_client,
    )

    log.info("trading_monitors_started")

    # Run both monitors concurrently
    tasks = [
        asyncio.create_task(position_monitor.run()),
        asyncio.create_task(limit_order_monitor.run()),
    ]

    try:
        # Wait until shutdown signal
        while _running:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        position_monitor.stop()
        limit_order_monitor.stop()

        # Cancel tasks
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

        # Clean up DB engine
        engine.dispose()

    log.info("trading_monitors_stopped")


if __name__ == "__main__":
    asyncio.run(main())
