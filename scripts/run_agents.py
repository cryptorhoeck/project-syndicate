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
from src.common.price_cache import PriceCache
from src.risk.warden import Warden
from src.trading.execution_service import get_trading_service
from src.trading.fee_schedule import FeeSchedule
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
log = structlog.get_logger("run_agents")

_running = True

POLL_INTERVAL = 10  # seconds between scheduler polls


def _handle_signal(signum: int, _frame) -> None:
    global _running
    log.info("shutdown_signal_received", signal=signum)
    _running = False


def build_warden(db_factory, agora_service=None):
    """Construct the in-process Warden for the agent runtime.

    The Warden process (`scripts/run_warden.py`) is the source of truth for
    `system_state.alert_status` — it computes Yellow/Red/circuit_breaker
    via rolling drawdown and writes the verdict to the DB. This in-process
    Warden, constructed inside `run_agents.py`, refreshes its alert_status
    from the same DB column at the top of every `evaluate_trade` call so
    the trade gate reflects the live state. See WIRING_AUDIT_REPORT.md
    subsystem N for the wiring gap this closes.

    Same testable-helper pattern as `build_trading_service` —
    `test_warden_trade_gate_wiring.py` calls this directly so a future
    regression in the constructor surfaces in the suite, not in an Arena.
    """
    return Warden(
        db_session_factory=db_factory,
        agora_service=agora_service,
    )


def build_trading_service(db_factory, redis_client, agora_service=None, warden=None):
    """Construct the colony's TradeExecutionService for the configured mode.

    `warden` is now a required collaborator for production paths — pass the
    Warden instance from `build_warden` so PaperTradingService.evaluate_trade
    actually fires. The factory still accepts None for tests that
    deliberately construct without a Warden, but `run_agents.py` enforces
    non-None at runtime via the fail-fast check below.
    """
    price_cache = PriceCache(redis_client=redis_client)
    return get_trading_service(
        db_session_factory=db_factory,
        price_cache=price_cache,
        slippage_model=SlippageModel(),
        fee_schedule=FeeSchedule(),
        warden=warden,
        redis_client=redis_client,
        agora_service=agora_service,
    )


async def main() -> None:
    global _running

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    log.info("agent_runner_starting", version=__version__)

    # Shared resources
    engine = create_engine(config.database_url)
    db_factory = sessionmaker(bind=engine)
    redis_client = redis.Redis.from_url(
        config.redis_url, decode_responses=True,
        socket_timeout=10, socket_connect_timeout=5, retry_on_timeout=True,
    )

    # Claude API client
    claude = ClaudeClient(api_key=config.anthropic_api_key)

    # Optional: Agora service for action execution
    agora = None
    async_redis = None  # Initialize before try to avoid NameError on cleanup
    try:
        import redis.asyncio as aioredis
        from src.agora import create_agora_service
        async_redis = aioredis.from_url(config.redis_url, decode_responses=True)
        agora = await create_agora_service(db_factory, async_redis)
        log.info("agora_connected")
    except Exception as e:
        log.warning("agora_unavailable", error=str(e))

    # Warden — in-process safety gate. Closes the gap from
    # WIRING_AUDIT_REPORT.md subsystem N. Without this, PaperTradingService
    # has self.warden = None and the if-self.warden guard at
    # execution_service.py:172 short-circuits — every Yellow/Red/circuit-
    # breaker is detected by the Warden process but never gates a trade.
    try:
        warden = build_warden(db_factory, agora_service=agora)
    except Exception as exc:
        log.error("warden_construction_failed",
                  error=str(exc),
                  message="Refusing to start agents — colony's mechanical safety gate cannot be built.")
        sys.exit(2)
    if warden is None:
        log.error("warden_unavailable",
                  message="Refusing to start agents — Warden returned None.")
        sys.exit(2)
    log.info("warden_wired", impl=type(warden).__name__)

    # Trading service. This is the wiring that closes the gap exposed in
    # ARENA_TRADING_SERVICE_DIAGNOSIS.md. The factory returns the right
    # concrete TradeExecutionService for the configured mode (paper today).
    # If this raises or returns None, we surface and abort: every Operator
    # trade would otherwise fall into the [NO SERVICE] fallback again.
    # Warden is now passed in so PaperTradingService.evaluate_trade fires.
    trading_service = build_trading_service(
        db_factory, redis_client, agora_service=agora, warden=warden,
    )
    if trading_service is None:
        log.error("trading_service_unavailable",
                  trading_mode=config.trading_mode,
                  message="Refusing to start agents — Operator would silently no-op every trade.")
        sys.exit(2)
    if getattr(trading_service, "warden", None) is None:
        log.error("trading_service_warden_missing",
                  message="Refusing to start agents — TradeExecutionService was built without a Warden.")
        sys.exit(2)
    log.info("trading_service_wired",
             impl=type(trading_service).__name__,
             trading_mode=config.trading_mode,
             warden=type(getattr(trading_service, "warden", None)).__name__)

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
                                warden=warden,
                                config=config,
                                trading_service=trading_service,
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
        except Exception:
            pass
    if async_redis:
        try:
            await async_redis.close()
        except Exception:
            pass

    engine.dispose()
    log.info("agent_runner_stopped")


if __name__ == "__main__":
    asyncio.run(main())
