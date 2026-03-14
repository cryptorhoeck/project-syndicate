"""
Project Syndicate — Agent Thinking Cycle Runner

Drives the OODA loop for all active agents.
Uses the CycleScheduler to determine who thinks next,
then runs ThinkingCycle.run() for each agent sequentially.

Processes agents one at a time (Phase 3A design: sequential processing).
Polls the scheduler every 10 seconds for newly eligible agents.

Usage: python scripts/run_agents.py
"""

__version__ = "1.0.0"

import asyncio
import logging
import os
import signal
import sys
import time

import structlog
from dotenv import load_dotenv

# Ensure project root is on sys.path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)
load_dotenv(os.path.join(PROJECT_ROOT, ".env"), override=True)

import redis
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.agents.claude_client import ClaudeClient
from src.agents.cycle_scheduler import CycleScheduler
from src.agents.thinking_cycle import ThinkingCycle
from src.common.config import config

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
log = structlog.get_logger("run_agents")

_running = True

POLL_INTERVAL = 10  # seconds between scheduler polls


def _handle_signal(signum: int, _frame) -> None:
    global _running
    log.info("shutdown_signal_received", signal=signum)
    _running = False


async def main() -> None:
    global _running

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    log.info("agent_runner_starting", version=__version__)

    # Shared resources
    engine = create_engine(config.database_url)
    db_factory = sessionmaker(bind=engine)
    redis_client = redis.Redis.from_url(config.redis_url, decode_responses=True)

    # Claude API client
    claude = ClaudeClient(api_key=config.anthropic_api_key)

    # Optional: Agora service for action execution
    agora = None
    try:
        import redis.asyncio as aioredis
        from src.agora import create_agora_service
        async_redis = aioredis.from_url(config.redis_url, decode_responses=True)
        agora = await create_agora_service(db_factory, async_redis)
        log.info("agora_connected")
    except Exception as e:
        log.warning("agora_unavailable", error=str(e))

    log.info("agent_runner_started", poll_interval=POLL_INTERVAL)

    try:
        while _running:
            try:
                with db_factory() as session:
                    scheduler = CycleScheduler(db_session=session, redis_client=redis_client)

                    # Schedule all eligible agents
                    queued = scheduler.schedule_all_active()
                    if queued:
                        log.info("agents_scheduled", count=len(queued), agent_ids=queued)

                    # Process agents one at a time
                    while _running:
                        agent_id = scheduler.get_next()
                        if agent_id is None:
                            break

                        log.info("cycle_starting", agent_id=agent_id)

                        # Create a fresh session for each cycle to avoid stale state
                        with db_factory() as cycle_session:
                            thinking_cycle = ThinkingCycle(
                                db_session=cycle_session,
                                claude_client=claude,
                                redis_client=redis_client,
                                agora_service=agora,
                                config=config,
                            )

                            result = await thinking_cycle.run(agent_id)

                            log.info(
                                "cycle_complete",
                                agent_id=agent_id,
                                success=result.success,
                                action=result.action_type,
                                cost=f"${result.api_cost:.4f}",
                                cycle=result.cycle_number,
                                reason=result.reason if not result.success else "",
                            )

            except Exception as e:
                log.error("cycle_loop_error", error=str(e))

            # Sleep in 1-second increments for responsive shutdown
            for _ in range(POLL_INTERVAL):
                if not _running:
                    break
                await asyncio.sleep(1)

    except KeyboardInterrupt:
        pass

    # Cleanup
    if agora:
        try:
            await agora.pubsub.shutdown()
            await async_redis.close()
        except Exception:
            pass

    log.info("agent_runner_stopped")


if __name__ == "__main__":
    asyncio.run(main())
