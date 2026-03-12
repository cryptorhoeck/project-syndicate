"""
Project Syndicate — Genesis Runner

Standalone script that starts the Genesis main loop.
Runs the 5-minute cycle with graceful shutdown.
"""

__version__ = "0.2.0"

import asyncio
import signal
import sys

import structlog
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.common.config import config
from src.genesis.genesis import GenesisAgent

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
log = structlog.get_logger("genesis_runner")

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

    genesis = GenesisAgent(
        db_session_factory=session_factory,
        exchange_service=None,  # Will be set when exchange keys are configured
    )
    await genesis.initialize()

    interval = config.genesis_cycle_interval_seconds
    log.info("genesis_started", interval_seconds=interval)

    try:
        while _running:
            report = await genesis.run_cycle()
            log.info("genesis_cycle_complete", keys=list(report.keys()))

            # Sleep in 1-second increments for responsive shutdown
            for _ in range(interval):
                if not _running:
                    break
                await asyncio.sleep(1)
    except KeyboardInterrupt:
        pass

    await genesis.flush_costs_to_db()
    log.info("genesis_stopped")


if __name__ == "__main__":
    asyncio.run(main())
