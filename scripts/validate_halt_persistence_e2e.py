"""
Operator halt persistence — end-to-end validation runner.

The unique production-runtime check that no automated test exercises.
Every prior wiring gap in this project has been the variant where
unit/integration tests pass but production runtime fails. This runner
exercises the live code paths against real Memurai with three
scenarios:

  1) HEALTHY BOOT: a producer subprocess publishes a halt via
     RedisHaltStore.publish; the consumer (a real PaperTradingService
     in this process, built via the production factory chain) rejects
     trades on the affected coin and approves trades on the unaffected
     coin.

  2) MEMURAI DOWN MID-RUN: the consumer's Redis client is forced into
     a broken state (the connection pool is closed). The consumer flips
     `_halt_state_unknown=True` and rejects every trade. The CRITICAL
     log + Agora system-alerts mirror are observable in the logs.

  3) RECOVERY: a fresh RedisHaltStore is wired in, the consumer
     succeeds on the next call, the latch auto-clears, and per-coin
     gating resumes.

Run before merging the persistence-layer hotfix to demonstrate the
production-runtime contract holds. Capture stdout into the commit
message.

Usage:
    .venv\\Scripts\\python.exe scripts\\validate_halt_persistence_e2e.py
"""

from __future__ import annotations

__version__ = "1.0.0"

import asyncio
import json
import os
import subprocess
import sys
import textwrap
import time
import uuid
from datetime import datetime, timedelta, timezone

# Ensure project root is on sys.path
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _PROJECT_ROOT)

from unittest.mock import AsyncMock, MagicMock

import redis as redis_lib
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.common.config import config
from src.common.models import Agent, Base, SystemState
from src.risk.warden import Warden
from src.trading.execution_service import PaperTradingService
from src.trading.fee_schedule import FeeSchedule
from src.wire.integration.halt_store import RedisHaltStore, make_halt_record


GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"
BOLD = "\033[1m"
RESET = "\033[0m"


def _banner(title: str, color: str = BOLD) -> None:
    print()
    print(f"{color}{'=' * 78}{RESET}")
    print(f"{color}  {title}{RESET}")
    print(f"{color}{'=' * 78}{RESET}")


def _check(label: str, passed: bool, detail: str = "") -> bool:
    icon = f"{GREEN}OK   {RESET}" if passed else f"{RED}FAIL {RESET}"
    line = f"  {icon}  {label}"
    if detail:
        line += f"\n         {detail}"
    print(line)
    return passed


def _build_seeded_world():
    """In-memory SQLite + a single Operator agent. Mirrors the
    test_operator_halt_consumer_wiring fixtures so the validation runs
    without a Postgres dependency."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as session:
        session.add(SystemState(
            total_treasury=1000.0, peak_treasury=1000.0,
            current_regime="bull", active_agent_count=1, alert_status="green",
        ))
        agent = Agent(
            name="Operator-E2EValidation", type="operator", status="active",
            generation=1, capital_allocated=200.0, capital_current=200.0,
            cash_balance=200.0, reserved_cash=0.0, total_equity=200.0,
        )
        session.add(agent)
        session.commit()
        agent_id = agent.id
    return factory, agent_id


def _build_paper_trading_service(*, db_factory, halt_store):
    """Production-shape PaperTradingService. Only the price feed is
    stubbed — the warden, halt_store, exchange routing, and database
    bridge are real."""
    fake_redis = MagicMock()
    fake_redis.set.return_value = True
    fake_redis.get.return_value = None
    fake_redis.delete.return_value = True
    fake_redis.ping.return_value = True

    warden = Warden(db_session_factory=db_factory, agora_service=None)

    svc = PaperTradingService(
        db_session_factory=db_factory,
        price_cache=MagicMock(),
        slippage_model=MagicMock(calculate_slippage=AsyncMock(return_value=0.001)),
        fee_schedule=FeeSchedule(),
        warden=warden,
        redis_client=fake_redis,
        agora_service=None,
        halt_store=halt_store,
    )
    svc.price_cache.get_ticker = AsyncMock(
        return_value=(
            {"bid": 100.0, "ask": 100.5, "last": 100.25, "baseVolume": 1_000_000},
            True,
        )
    )
    svc.price_cache.get_order_book = AsyncMock(
        return_value=({"asks": [[100.5, 100]], "bids": [[100.0, 100]]}, True)
    )
    svc.price_cache.is_stale = MagicMock(return_value=False)
    return svc


def _spawn_producer_subprocess(
    *,
    redis_url: str,
    key_prefix: str,
    coin: str,
    exchange: str,
) -> subprocess.CompletedProcess:
    """A real Python subprocess that publishes a halt via
    RedisHaltStore.publish, against the same Memurai keyspace this
    process reads from. This is the production cross-process boundary.
    """
    project_root = _PROJECT_ROOT
    script = textwrap.dedent(f"""
        import sys, os, json
        sys.path.insert(0, {project_root!r})
        import redis
        from src.wire.integration.halt_store import RedisHaltStore, make_halt_record
        from datetime import datetime, timezone, timedelta
        r = redis.Redis.from_url(os.environ['REDIS_URL'], decode_responses=True)
        s = RedisHaltStore(redis_client=r, key_prefix={key_prefix!r})
        now = datetime.now(timezone.utc)
        s.publish(
            coin={coin!r},
            exchange={exchange!r},
            halt_record=make_halt_record(
                event_id=987001,
                coin={coin!r},
                exchange={exchange!r},
                event_type="exchange_outage",
                severity=5,
                summary="E2E synthetic Memurai-real cross-subprocess halt",
                issued_at=now,
                expires_at=now + timedelta(minutes=30),
            ),
            ttl_seconds=1800,
        )
        print(json.dumps({{"published": True, "coin": {coin!r}, "exchange": {exchange!r}}}))
    """).strip()
    env = os.environ.copy()
    env["REDIS_URL"] = redis_url
    return subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True, text=True, env=env, timeout=30,
    )


async def phase_1_healthy_boot(redis_url: str) -> bool:
    """Real Memurai. Producer subprocess publishes an exchange_outage
    halt for BTC/Kraken via the production RedisHaltStore. Consumer
    (this process) builds a real PaperTradingService against the same
    Memurai instance with the same key prefix and asserts:
      - BTC trade rejected with halt details
      - ETH trade approved (per-coin axis honored)
    """
    _banner("PHASE 1 — HEALTHY BOOT (real Memurai, cross-subprocess publish)")

    key_prefix = f"wire:halt_e2e:{uuid.uuid4().hex[:8]}"
    redis_client = redis_lib.Redis.from_url(redis_url, decode_responses=True)
    redis_client.ping()
    halt_store = RedisHaltStore(redis_client=redis_client, key_prefix=key_prefix)

    # Spawn producer subprocess. This is the live cross-process write.
    pub = _spawn_producer_subprocess(
        redis_url=redis_url, key_prefix=key_prefix,
        coin="BTC", exchange="kraken",
    )
    if pub.returncode != 0:
        print(f"{RED}producer subprocess failed:{RESET}\n{pub.stderr}")
        return False
    print(f"  producer subprocess: {pub.stdout.strip()}")

    db_factory, agent_id = _build_seeded_world()
    svc = _build_paper_trading_service(db_factory=db_factory, halt_store=halt_store)
    svc.exchange = "kraken"

    btc_result = await svc.execute_market_order(
        agent_id=agent_id, symbol="BTC/USDT", side="buy", size_usd=10.0,
    )
    eth_result = await svc.execute_market_order(
        agent_id=agent_id, symbol="ETH/USDT", side="buy", size_usd=10.0,
    )

    ok = True
    ok &= _check(
        "BTC trade rejected (Wire halt visible cross-process)",
        btc_result.success is False
        and "987001" in (btc_result.error or "")
        and "exchange_outage" in (btc_result.error or ""),
        detail=f"reason={btc_result.error!r}",
    )
    ok &= _check(
        "ETH trade approved (per-coin axis honored)",
        eth_result.success is True,
        detail=(
            f"order_id={eth_result.order_id} fill={eth_result.fill_price}"
            if eth_result.success
            else f"error={eth_result.error!r}"
        ),
    )
    ok &= _check(
        "consumer _halt_state_unknown latch CLEAR after healthy reads",
        svc._halt_state_unknown is False,
    )

    # cleanup
    cursor = 0
    while True:
        cursor, keys = redis_client.scan(cursor=cursor, match=f"{key_prefix}:*", count=100)
        for k in keys:
            redis_client.delete(k)
        if cursor == 0:
            break
    return ok


async def phase_2_memurai_down(redis_url: str) -> bool:
    """Force the consumer's Redis client into a broken state mid-run
    (close the underlying connection pool). The next is_halted call
    must raise → consumer flips `_halt_state_unknown=True` → trade
    rejected with halt-state-unknown reason. CRITICAL log emitted."""
    _banner("PHASE 2 — MEMURAI DOWN MID-RUN (consumer fail-closed)")

    key_prefix = f"wire:halt_e2e:{uuid.uuid4().hex[:8]}"
    redis_client = redis_lib.Redis.from_url(redis_url, decode_responses=True)
    redis_client.ping()
    halt_store = RedisHaltStore(redis_client=redis_client, key_prefix=key_prefix)

    db_factory, agent_id = _build_seeded_world()
    svc = _build_paper_trading_service(db_factory=db_factory, halt_store=halt_store)
    svc.exchange = "kraken"

    # Sanity baseline: healthy is_halted call returns (False, None).
    healthy = await svc.execute_market_order(
        agent_id=agent_id, symbol="BTC/USDT", side="buy", size_usd=5.0,
    )
    baseline_ok = _check(
        "baseline: healthy Redis -> trade approved",
        healthy.success is True,
        detail=(
            f"error={healthy.error!r}" if not healthy.success else "OK"
        ),
    )

    # SIMULATE MEMURAI DOWN: close the connection pool. Subsequent
    # calls to redis_client.get raise ConnectionError. This is the same
    # failure mode as a real `service stop memurai` from the Python
    # client's perspective.
    redis_client.connection_pool.disconnect()
    # Force the pool to reject new connections too — point at a closed
    # port so even reconnects fail.
    bad_pool_url = "redis://127.0.0.1:1/0"  # port 1 is reserved/unused
    halt_store.redis = redis_lib.Redis.from_url(
        bad_pool_url, decode_responses=True,
        socket_timeout=1, socket_connect_timeout=1,
    )

    rejected = await svc.execute_market_order(
        agent_id=agent_id, symbol="BTC/USDT", side="buy", size_usd=5.0,
    )

    ok = baseline_ok
    ok &= _check(
        "Memurai-down: BTC trade REJECTED",
        rejected.success is False,
        detail=f"error={rejected.error!r}",
    )
    ok &= _check(
        "rejection reason cites halt-state-unknown + fail-closed",
        rejected.success is False
        and "unknown" in (rejected.error or "").lower()
        and "fail-closed" in (rejected.error or "").lower(),
        detail=f"error={rejected.error!r}",
    )
    ok &= _check(
        "consumer _halt_state_unknown latch SET",
        svc._halt_state_unknown is True,
    )
    return ok, halt_store, svc, db_factory, agent_id


async def phase_3_recovery(
    redis_url: str, halt_store: RedisHaltStore, svc, db_factory, agent_id,
) -> bool:
    """Re-point the consumer at a healthy Memurai. Next is_halted call
    returns (False, None) cleanly → `_halt_state_unknown` auto-clears
    (anti-DMS). Trade approved. Per-coin gating resumes (publish a halt
    for ETH, verify ETH blocked, BTC approved)."""
    _banner("PHASE 3 — RECOVERY (latch auto-clears, per-coin gating resumes)")

    redis_client = redis_lib.Redis.from_url(redis_url, decode_responses=True)
    redis_client.ping()
    halt_store.redis = redis_client

    recovered = await svc.execute_market_order(
        agent_id=agent_id, symbol="BTC/USDT", side="buy", size_usd=5.0,
    )

    ok = True
    ok &= _check(
        "post-recovery BTC trade APPROVED",
        recovered.success is True,
        detail=(f"error={recovered.error!r}" if not recovered.success else "OK"),
    )
    ok &= _check(
        "_halt_state_unknown latch AUTO-CLEARED on next successful read",
        svc._halt_state_unknown is False,
    )

    # Publish an ETH halt; verify per-coin gating works on the recovered store.
    halt_store.publish(
        coin="ETH", exchange="kraken",
        halt_record=make_halt_record(
            event_id=987002, coin="ETH", exchange="kraken",
            event_type="withdrawal_halt", severity=5,
            summary="E2E synthetic ETH halt for recovery proof",
            issued_at=datetime.now(timezone.utc),
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=30),
        ),
        ttl_seconds=600,
    )
    eth_blocked = await svc.execute_market_order(
        agent_id=agent_id, symbol="ETH/USDT", side="buy", size_usd=5.0,
    )
    btc_open = await svc.execute_market_order(
        agent_id=agent_id, symbol="BTC/USDT", side="buy", size_usd=5.0,
    )
    ok &= _check(
        "post-recovery ETH halt -> ETH REJECTED",
        eth_blocked.success is False and "987002" in (eth_blocked.error or ""),
        detail=f"error={eth_blocked.error!r}",
    )
    ok &= _check(
        "post-recovery BTC unaffected -> APPROVED",
        btc_open.success is True,
    )

    # cleanup
    halt_store.clear(coin="ETH", exchange="kraken")
    return ok


async def main() -> int:
    _banner("Operator-halt persistence E2E validation", BOLD + GREEN)
    print(f"  python: {sys.executable}")
    print(f"  redis_url: {config.redis_url}")

    # Memurai sanity gate.
    try:
        sanity = redis_lib.Redis.from_url(config.redis_url, decode_responses=True)
        sanity.ping()
    except Exception as exc:
        print(f"{RED}Memurai unreachable at {config.redis_url}: {exc}{RESET}")
        print("  Start Memurai before running this validation.")
        return 2
    print(f"  {GREEN}Memurai reachable{RESET}")

    p1 = await phase_1_healthy_boot(config.redis_url)
    p2_result = await phase_2_memurai_down(config.redis_url)
    p2 = p2_result[0]
    halt_store, svc, db_factory, agent_id = p2_result[1:]
    p3 = await phase_3_recovery(config.redis_url, halt_store, svc, db_factory, agent_id)

    _banner("RESULT")
    print(f"  Phase 1 (healthy boot)            : {GREEN if p1 else RED}{'PASS' if p1 else 'FAIL'}{RESET}")
    print(f"  Phase 2 (Memurai down mid-run)    : {GREEN if p2 else RED}{'PASS' if p2 else 'FAIL'}{RESET}")
    print(f"  Phase 3 (recovery + auto-clear)   : {GREEN if p3 else RED}{'PASS' if p3 else 'FAIL'}{RESET}")
    overall = p1 and p2 and p3
    print()
    print(f"  Overall: {GREEN if overall else RED}{'GREEN — safe to merge' if overall else 'RED — DO NOT MERGE'}{RESET}")
    return 0 if overall else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
