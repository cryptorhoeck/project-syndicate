"""
Project Syndicate — Warden Runner

Standalone script that starts the Warden as its own process,
independent of Genesis and all agents. Runs the 30-second check cycle.
"""

__version__ = "0.3.0"

import asyncio
import signal
import sys

import redis.asyncio as aioredis
import structlog
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.agora import create_agora_service
from src.common.config import config
from src.risk.warden import Warden

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
log = structlog.get_logger("warden_runner")

_running = True


def _handle_signal(signum: int, _frame) -> None:
    global _running
    log.info("shutdown_signal", signal=signum)
    _running = False


async def main() -> None:
    global _running
    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    engine = create_engine(config.database_url)
    session_factory = sessionmaker(bind=engine)

    # Initialize Agora service
    redis_client = aioredis.from_url(config.redis_url, decode_responses=True)
    agora = await create_agora_service(session_factory, redis_client)

    warden = Warden(db_session_factory=session_factory, agora_service=agora)

    interval = config.warden_cycle_interval_seconds
    log.info("warden_started", interval_seconds=interval)

    try:
        while _running:
            report = await warden.check_cycle()
            # Sleep in 1-second increments for responsive shutdown
            for _ in range(interval):
                if not _running:
                    break
                await asyncio.sleep(1)
    except KeyboardInterrupt:
        pass

    # Clean shutdown
    await agora.pubsub.shutdown()
    await redis_client.close()

    log.info("warden_stopped")


if __name__ == "__main__":
    asyncio.run(main())
