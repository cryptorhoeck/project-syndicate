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
from src.wire.integration.halt_store import RedisHaltStore
from src.wire.integration.operator_halt import (
    get_halt_store as get_producer_halt_store,
    list_active as wire_list_active_halts,
    set_halt_store as set_producer_halt_store,
)

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


def build_halt_store(redis_client, *, key_prefix: str | None = None) -> RedisHaltStore:
    """Construct the colony's RedisHaltStore from the live Redis client.

    Closes the cross-process gap surfaced in iteration 4 of the operator
    halt hotfix: producer (wire_scheduler subprocess) and consumer
    (this — agents subprocess) both point at the same Memurai instance
    via this store, so halts published in one are visible in the other.

    Wiring contract: redis_client must be non-None. RedisHaltStore's
    constructor sys.exit(2)s on None to enforce the same pattern as
    Warden / TradeExecutionService.

    `key_prefix` is an optional override for tests (Critic Finding 2,
    iteration 5). Production callers pass nothing — the default
    `wire:halt` namespace is used and producer/consumer agree by
    construction. The cross-process boundary test passes a unique
    per-run prefix so concurrent test runs don't collide on the
    production keyspace.
    """
    if key_prefix is None:
        return RedisHaltStore(redis_client=redis_client)
    return RedisHaltStore(redis_client=redis_client, key_prefix=key_prefix)


def build_trading_service(
    db_factory, redis_client, agora_service=None, warden=None, halt_store=None,
):
    """Construct the colony's TradeExecutionService for the configured mode.

    `warden` is required in production paths so PaperTradingService.warden
    is non-None and Warden's evaluate_trade actually fires.

    `halt_store` is the Wire severity-5 consumer (a `RedisHaltStore`).
    Without it the Operator would silently ignore active halts
    (subsystem I from the wiring audit). Production runners enforce
    non-None via fail-fast in main(); the factory still accepts None to
    keep test fixtures simple.
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
        halt_store=halt_store,
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

    # Halt store. Cross-process severity-5 halt registry (Memurai-backed).
    # Producer side is initialized in src/wire/cli.py; this is the
    # consumer side. Both subprocesses construct against the same Redis
    # instance so halts published in one are visible in the other.
    try:
        halt_store = build_halt_store(redis_client)
    except SystemExit:
        raise  # propagate sys.exit(2) from RedisHaltStore None-guard
    except Exception as exc:
        log.error("halt_store_construction_failed",
                  error=str(exc),
                  message="Refusing to start agents — Wire halt registry cannot be built.")
        sys.exit(2)
    log.info("halt_store_wired", impl=type(halt_store).__name__)
    # Also initialize the producer-side module-level reference so any
    # in-process publish_halt_for_event call (e.g. tests) writes to
    # Redis. The wire_scheduler subprocess has its own initialization
    # in src/wire/cli.py.
    set_producer_halt_store(halt_store)
    # Post-construction verification (Critic Finding 3, iteration 5):
    # set_halt_store() success doesn't prove the module-level reference
    # took. Re-read it via get_halt_store() and fail fast if the assignment
    # was lost or import-path skewed.
    registered = get_producer_halt_store()
    if registered is None:
        log.error(
            "halt_store_assignment_lost",
            message="set_producer_halt_store completed but get_producer_halt_store returned None.",
        )
        sys.exit(2)
    if registered is not halt_store:
        log.error(
            "halt_store_assignment_mismatch",
            expected=id(halt_store), registered=id(registered),
            message="Module-level halt_store reference is not the instance we just registered.",
        )
        sys.exit(2)

    # Trading service. Closes ARENA_TRADING_SERVICE_DIAGNOSIS.md (Phase 3C
    # was never wired) + WIRING_AUDIT_REPORT.md subsystems N (Warden) and
    # I (Operator halt consumer). The factory returns the right concrete
    # TradeExecutionService for the configured mode (paper today). All
    # collaborators are wired here; main() fails fast on any None.
    trading_service = build_trading_service(
        db_factory, redis_client,
        agora_service=agora,
        warden=warden,
        halt_store=halt_store,
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
    if getattr(trading_service, "halt_store", None) is None:
        log.error("trading_service_halt_store_missing",
                  message="Refusing to start agents — TradeExecutionService was built without a halt_store. "
                          "Wire severity-5 halts would be silently ignored.")
        sys.exit(2)
    log.info("trading_service_wired",
             impl=type(trading_service).__name__,
             trading_mode=config.trading_mode,
             warden=type(getattr(trading_service, "warden", None)).__name__,
             halt_store=type(getattr(trading_service, "halt_store", None)).__name__)

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
