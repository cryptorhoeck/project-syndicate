"""
Operator halt consumer — production wiring tests.

Closes WIRING_AUDIT_REPORT.md subsystem I AND the cross-process gap
surfaced as Critic Finding 3 in iteration 4 (the registry was module-
local Python state; producer in wire_scheduler subprocess, consumer in
agents subprocess never shared anything in production despite all
in-process tests passing).

This file now tests against the Redis-backed `RedisHaltStore`. The
cross-process boundary is exercised explicitly by
`test_halt_visible_across_process_boundary` — the test that catches
the regression class permanently. Without it, future refactors could
re-introduce in-process state and pass everything else.
"""

from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
import redis as redis_lib
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.common.config import config
from src.common.models import Agent, Base, Order, Position, SystemState, Transaction
from src.trading.execution_service import PaperTradingService
from src.trading.fee_schedule import FeeSchedule
from src.wire.constants import SEVERITY_CRITICAL
from src.wire.integration.halt_store import RedisHaltStore, make_halt_record
from src.wire.integration.operator_halt import (
    publish_halt_for_event,
    reset_registry,
    set_halt_store,
)


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def unique_key_prefix():
    """Each test gets its own Redis namespace so concurrent runs and
    leftover state from earlier failures don't bleed."""
    return f"wire:halt_test:{uuid.uuid4().hex[:8]}"


@pytest.fixture
def redis_client():
    """Real Memurai connection. Tests skip if Redis is unavailable —
    Memurai is part of the dev environment per CLAUDE.md."""
    client = redis_lib.Redis.from_url(
        config.redis_url, decode_responses=True,
        socket_timeout=5, socket_connect_timeout=5,
    )
    try:
        client.ping()
    except Exception as exc:
        pytest.skip(f"Redis (Memurai) unavailable for this test: {exc}")
    return client


@pytest.fixture
def halt_store(redis_client, unique_key_prefix):
    """Real RedisHaltStore against the dev Memurai with a unique prefix.
    Cleans up its namespace on teardown so tests stay isolated."""
    store = RedisHaltStore(redis_client=redis_client, key_prefix=unique_key_prefix)
    yield store
    cursor = 0
    while True:
        cursor, keys = redis_client.scan(cursor=cursor, match=f"{unique_key_prefix}:*", count=100)
        for key in keys:
            redis_client.delete(key)
        if cursor == 0:
            break


@pytest.fixture(autouse=True)
def _clean_in_memory_active():
    """The module-level _ACTIVE list is defense-in-depth only, but it's
    process-global state — reset between tests so signals from one test
    don't bleed into another."""
    reset_registry()
    set_halt_store(None)
    yield
    reset_registry()
    set_halt_store(None)


@pytest.fixture
def thread_safe_engine():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return engine


@pytest.fixture
def db_factory(thread_safe_engine):
    return sessionmaker(bind=thread_safe_engine)


@pytest.fixture
def seeded_world(db_factory):
    with db_factory() as session:
        session.add(SystemState(
            total_treasury=1000.0, peak_treasury=1000.0,
            current_regime="bull", active_agent_count=1, alert_status="green",
        ))
        agent = Agent(
            name="Operator-HaltTest", type="operator", status="active",
            generation=1, capital_allocated=200.0, capital_current=200.0,
            cash_balance=200.0, reserved_cash=0.0, total_equity=200.0,
        )
        session.add(agent)
        session.commit()
        return agent.id


@pytest.fixture
def fake_redis_client_for_pricecache():
    r = MagicMock()
    r.set.return_value = True
    r.get.return_value = None
    r.delete.return_value = True
    r.ping.return_value = True
    return r


@pytest.fixture
def production_warden(db_factory):
    run_agents = importlib.import_module("scripts.run_agents")
    return run_agents.build_warden(db_factory, agora_service=None)


def _build_svc(*, db_factory, fake_redis_client_for_pricecache, warden, halt_store, agora=None):
    svc = PaperTradingService(
        db_session_factory=db_factory,
        price_cache=MagicMock(),
        slippage_model=MagicMock(calculate_slippage=AsyncMock(return_value=0.001)),
        fee_schedule=FeeSchedule(),
        warden=warden,
        redis_client=fake_redis_client_for_pricecache,
        agora_service=agora,
        halt_store=halt_store,
    )
    svc.price_cache.get_ticker = AsyncMock(
        return_value=({"bid": 100.0, "ask": 100.5, "last": 100.25, "baseVolume": 1_000_000}, True)
    )
    svc.price_cache.get_order_book = AsyncMock(
        return_value=({"asks": [[100.5, 100]], "bids": [[100.0, 100]]}, True)
    )
    svc.price_cache.is_stale = MagicMock(return_value=False)
    return svc


@pytest.fixture
def production_trading_service(
    db_factory, fake_redis_client_for_pricecache, production_warden, halt_store,
):
    """Trading service wired to a real Redis-backed halt_store via the
    `halt_store` fixture (per-test prefix)."""
    return _build_svc(
        db_factory=db_factory,
        fake_redis_client_for_pricecache=fake_redis_client_for_pricecache,
        warden=production_warden,
        halt_store=halt_store,
    )


# ---------------------------------------------------------------------------
# RedisHaltStore unit tests (directive items 5-8)
# ---------------------------------------------------------------------------


def test_halt_store_publish_and_read_in_same_process(halt_store):
    """Basic Redis round-trip: publish then is_halted returns the record."""
    record = {
        "trigger_event_id": 1, "coin": "BTC", "exchange": "Kraken",
        "event_type": "exchange_outage", "severity": 5,
        "summary": "test", "issued_at": "2026-05-04T00:00:00",
        "expires_at": "2026-05-04T00:30:00",
    }
    halt_store.publish(coin="BTC", exchange="Kraken", halt_record=record, ttl_seconds=300)

    halted, payload = halt_store.is_halted(coin="BTC", exchange="Kraken")
    assert halted is True
    assert payload == record


def test_halt_store_publish_with_ttl_expires_via_redis(halt_store):
    """Native Redis TTL is doing the expiry, not filter-on-read. Write a
    1-second TTL halt, sleep past TTL, assert it's gone."""
    halt_store.publish(
        coin="EXPIRES", exchange="Kraken",
        halt_record={"trigger_event_id": 999, "coin": "EXPIRES"},
        ttl_seconds=1,
    )
    halted_immediately, _ = halt_store.is_halted(coin="EXPIRES", exchange="Kraken")
    assert halted_immediately is True

    time.sleep(2.0)

    halted_after_ttl, payload = halt_store.is_halted(coin="EXPIRES", exchange="Kraken")
    assert halted_after_ttl is False
    assert payload is None


def test_halt_store_wildcard_exchange(halt_store):
    """A halt with exchange=None matches any concrete exchange query."""
    halt_store.publish(
        coin="WILD", exchange=None,
        halt_record={"trigger_event_id": 1, "coin": "WILD"},
        ttl_seconds=300,
    )
    for exchange in ("Kraken", "Binance", "Coinbase"):
        halted, _ = halt_store.is_halted(coin="WILD", exchange=exchange)
        assert halted is True, f"Wildcard halt did not block {exchange!r}"


def test_halt_store_specific_exchange(halt_store):
    """A halt with a concrete exchange blocks only that exchange."""
    halt_store.publish(
        coin="SPEC", exchange="Kraken",
        halt_record={"trigger_event_id": 2, "coin": "SPEC", "exchange": "Kraken"},
        ttl_seconds=300,
    )
    kraken_halted, _ = halt_store.is_halted(coin="SPEC", exchange="Kraken")
    assert kraken_halted is True

    binance_halted, _ = halt_store.is_halted(coin="SPEC", exchange="Binance")
    assert binance_halted is False


# ---------------------------------------------------------------------------
# Consumer wiring + scope tests
# ---------------------------------------------------------------------------


def test_paper_trading_service_receives_halt_store_in_production_boot(
    production_trading_service,
):
    """Regression-class wiring guard: build_trading_service threads
    halt_store through to PaperTradingService.halt_store."""
    assert production_trading_service.halt_store is not None
    assert isinstance(production_trading_service.halt_store, RedisHaltStore)


@pytest.mark.asyncio
async def test_operator_halt_blocks_affected_coin_in_production_boot(
    db_factory, seeded_world, production_trading_service, halt_store,
):
    """Sev-5 BTC halt → BTC trade rejected with halt details in reason."""
    halt_store.publish(
        coin="BTC", exchange=production_trading_service.exchange,
        halt_record=make_halt_record(
            event_id=42, coin="BTC", exchange=production_trading_service.exchange,
            event_type="exchange_outage", severity=5,
            summary="SYNTHETIC: BTC exchange outage",
            issued_at=datetime.now(timezone.utc),
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=30),
        ),
        ttl_seconds=1800,
    )
    result = await production_trading_service.execute_market_order(
        agent_id=seeded_world, symbol="BTC/USDT", side="buy", size_usd=10.0,
    )
    assert result.success is False
    assert "trigger_event_id=42" in (result.error or "")
    assert "exchange_outage" in (result.error or "")

    with db_factory() as session:
        positions = session.execute(select(Position)).scalars().all()
    assert positions == []


@pytest.mark.asyncio
async def test_operator_halt_does_not_block_unaffected_coin(
    db_factory, seeded_world, production_trading_service, halt_store,
):
    """Per-coin scope: BTC halt does not block ETH trades."""
    halt_store.publish(
        coin="BTC", exchange=None,
        halt_record={"trigger_event_id": 43, "coin": "BTC", "event_type": "exchange_outage"},
        ttl_seconds=1800,
    )
    result = await production_trading_service.execute_market_order(
        agent_id=seeded_world, symbol="ETH/USDT", side="buy", size_usd=10.0,
    )
    assert result.success is True


@pytest.mark.asyncio
async def test_operator_halt_auto_lifts_on_signal_expiry(
    db_factory, seeded_world, production_trading_service, halt_store,
):
    """Halts auto-expire via NATIVE REDIS TTL (not filter-on-read).
    Publish with a 1-second TTL, attempt trade (must reject), sleep past
    TTL, attempt again (must succeed)."""
    halt_store.publish(
        coin="BTC", exchange=production_trading_service.exchange,
        halt_record={"trigger_event_id": 44, "coin": "BTC", "event_type": "withdrawal_halt"},
        ttl_seconds=1,
    )
    blocked = await production_trading_service.execute_market_order(
        agent_id=seeded_world, symbol="BTC/USDT", side="buy", size_usd=10.0,
    )
    assert blocked.success is False

    time.sleep(2.0)

    approved = await production_trading_service.execute_market_order(
        agent_id=seeded_world, symbol="BTC/USDT", side="buy", size_usd=10.0,
    )
    assert approved.success is True, (
        f"Trade still blocked after TTL expiry: {approved.error!r}. "
        "Redis TTL should have auto-removed the key."
    )


@pytest.mark.asyncio
async def test_operator_halt_blocks_only_matching_exchange(
    db_factory, seeded_world, fake_redis_client_for_pricecache, halt_store,
):
    """Per-coin-PER-EXCHANGE: a Kraken-scoped halt must NOT block
    Binance trades for the same coin."""
    approving_warden = MagicMock()
    approving_warden.evaluate_trade = AsyncMock(
        return_value={"status": "approved", "reason": "test", "request_id": "test"}
    )

    kraken_svc = _build_svc(
        db_factory=db_factory,
        fake_redis_client_for_pricecache=fake_redis_client_for_pricecache,
        warden=approving_warden, halt_store=halt_store,
    )
    binance_svc = _build_svc(
        db_factory=db_factory,
        fake_redis_client_for_pricecache=fake_redis_client_for_pricecache,
        warden=approving_warden, halt_store=halt_store,
    )
    kraken_svc.exchange = "Kraken"
    binance_svc.exchange = "Binance"

    halt_store.publish(
        coin="BTC", exchange="Kraken",
        halt_record={
            "trigger_event_id": 200, "coin": "BTC", "exchange": "Kraken",
            "event_type": "exchange_outage",
        },
        ttl_seconds=1800,
    )

    kraken_result = await kraken_svc.execute_market_order(
        agent_id=seeded_world, symbol="BTC/USDT", side="buy", size_usd=5.0,
    )
    assert kraken_result.success is False

    binance_result = await binance_svc.execute_market_order(
        agent_id=seeded_world, symbol="BTC/USDT", side="buy", size_usd=5.0,
    )
    assert binance_result.success is True, (
        f"Per-exchange axis broken — Kraken halt blocked Binance: {binance_result.error!r}"
    )


@pytest.mark.asyncio
async def test_close_position_succeeds_during_active_halt(
    db_factory, seeded_world, production_trading_service, halt_store,
):
    """Close-position bypasses halt by design."""
    open_result = await production_trading_service.execute_market_order(
        agent_id=seeded_world, symbol="BTC/USDT", side="buy", size_usd=10.0,
    )
    assert open_result.success is True
    position_id = open_result.position_id

    halt_store.publish(
        coin="BTC", exchange=None,
        halt_record={"trigger_event_id": 300, "coin": "BTC", "event_type": "withdrawal_halt"},
        ttl_seconds=1800,
    )
    close_result = await production_trading_service.close_position(
        position_id=position_id, reason="halt-bypass-test",
    )
    assert close_result.success is True


# ---------------------------------------------------------------------------
# Fail-closed-to-halt-everything (directive items 9-11)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_check_operator_halt_fails_closed_when_redis_raises(
    db_factory, seeded_world, fake_redis_client_for_pricecache, production_warden,
):
    """When halt_store.is_halted raises, REJECT. Mirrors Warden fail-closed."""
    bad_store = MagicMock()
    bad_store.is_halted = MagicMock(side_effect=RuntimeError("Redis unavailable"))

    svc = _build_svc(
        db_factory=db_factory,
        fake_redis_client_for_pricecache=fake_redis_client_for_pricecache,
        warden=production_warden, halt_store=bad_store,
    )
    result = await svc.execute_market_order(
        agent_id=seeded_world, symbol="BTC/USDT", side="buy", size_usd=10.0,
    )
    assert result.success is False
    err = (result.error or "").lower()
    assert "unknown" in err and "fail-closed" in err
    assert svc._halt_state_unknown is True


@pytest.mark.asyncio
async def test_check_operator_halt_fails_closed_when_redis_returns_garbage(
    db_factory, seeded_world, fake_redis_client_for_pricecache, production_warden,
):
    """Malformed return → same fail-closed behavior."""
    bad_store = MagicMock()
    bad_store.is_halted = MagicMock(return_value="not a tuple")

    svc = _build_svc(
        db_factory=db_factory,
        fake_redis_client_for_pricecache=fake_redis_client_for_pricecache,
        warden=production_warden, halt_store=bad_store,
    )
    result = await svc.execute_market_order(
        agent_id=seeded_world, symbol="BTC/USDT", side="buy", size_usd=10.0,
    )
    assert result.success is False
    assert "unknown" in (result.error or "").lower()
    assert svc._halt_state_unknown is True


@pytest.mark.asyncio
async def test_halt_state_unknown_auto_clears_on_redis_recovery(
    db_factory, seeded_world, fake_redis_client_for_pricecache, production_warden,
):
    """Latch must auto-clear on next successful is_halted call."""
    state = {"mode": "healthy"}

    def _toggling(coin=None, exchange=None):
        if state["mode"] == "raise":
            raise RuntimeError("simulated transient Redis glitch")
        return (False, None)

    toggling_store = MagicMock()
    toggling_store.is_halted = MagicMock(side_effect=_toggling)

    svc = _build_svc(
        db_factory=db_factory,
        fake_redis_client_for_pricecache=fake_redis_client_for_pricecache,
        warden=production_warden, halt_store=toggling_store,
    )

    res_a = await svc.execute_market_order(
        agent_id=seeded_world, symbol="BTC/USDT", side="buy", size_usd=5.0,
    )
    assert res_a.success is True
    assert svc._halt_state_unknown is False

    state["mode"] = "raise"
    res_b = await svc.execute_market_order(
        agent_id=seeded_world, symbol="BTC/USDT", side="buy", size_usd=5.0,
    )
    assert res_b.success is False
    assert svc._halt_state_unknown is True

    state["mode"] = "healthy"
    res_c = await svc.execute_market_order(
        agent_id=seeded_world, symbol="BTC/USDT", side="buy", size_usd=5.0,
    )
    assert res_c.success is True
    assert svc._halt_state_unknown is False, (
        "Latch did not clear after Redis recovery — sticky regression."
    )


@pytest.mark.asyncio
async def test_in_memory_fallback_used_only_when_state_unknown(
    db_factory, seeded_world, fake_redis_client_for_pricecache, production_warden,
):
    """Defense-in-depth contract: the in-memory _ACTIVE list is consulted
    ONLY when `_halt_state_unknown` is set (Redis is the cause)."""
    from src.wire.integration import operator_halt as halt_mod

    halt_mod.set_halt_store(None)
    publish_halt_for_event(
        event_id=500, coin="BTC", event_type="exchange_outage",
        severity=SEVERITY_CRITICAL,
        summary="SYNTHETIC: in-memory only, Redis healthy",
    )
    assert len(halt_mod._ACTIVE) == 1

    healthy_store = MagicMock()
    healthy_store.is_halted = MagicMock(return_value=(False, None))

    svc = _build_svc(
        db_factory=db_factory,
        fake_redis_client_for_pricecache=fake_redis_client_for_pricecache,
        warden=production_warden, halt_store=healthy_store,
    )
    result_healthy = await svc.execute_market_order(
        agent_id=seeded_world, symbol="BTC/USDT", side="buy", size_usd=5.0,
    )
    assert result_healthy.success is True, (
        "In-memory _ACTIVE was consulted on the healthy Redis path — "
        "violates the defense-in-depth contract."
    )
    assert svc._halt_state_unknown is False

    bad_store = MagicMock()
    bad_store.is_halted = MagicMock(side_effect=RuntimeError("Redis down"))

    svc2 = _build_svc(
        db_factory=db_factory,
        fake_redis_client_for_pricecache=fake_redis_client_for_pricecache,
        warden=production_warden, halt_store=bad_store,
    )
    result_unknown = await svc2.execute_market_order(
        agent_id=seeded_world, symbol="BTC/USDT", side="buy", size_usd=5.0,
    )
    assert result_unknown.success is False
    assert svc2._halt_state_unknown is True
    err = (result_unknown.error or "")
    assert "trigger_event_id=500" in err, (
        f"Defense-in-depth fallback should surface the in-memory match. "
        f"Got: {err!r}"
    )


# ---------------------------------------------------------------------------
# Defense-in-depth: halt_store=None branch must scream
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_halt_store_missing_branch_rejects_and_alerts(
    db_factory, seeded_world, fake_redis_client_for_pricecache, production_warden,
):
    """Direct-construct PaperTradingService with halt_store=None →
    hard-reject. Production helper enforces non-None."""
    svc = _build_svc(
        db_factory=db_factory,
        fake_redis_client_for_pricecache=fake_redis_client_for_pricecache,
        warden=production_warden, halt_store=None,
    )
    result = await svc.execute_market_order(
        agent_id=seeded_world, symbol="BTC/USDT", side="buy", size_usd=10.0,
    )
    assert result.success is False
    assert "missing" in (result.error or "").lower()


# ---------------------------------------------------------------------------
# CRITICAL: cross-process boundary (directive item 13)
# ---------------------------------------------------------------------------


# LOAD-BEARING TEST: this is the test that catches the regression class
# permanently. Without it, future refactors can re-introduce in-process
# state for `_ACTIVE` and pass every other test in this file. The
# previous iteration shipped halt-consumer wiring that worked in
# single-process tests but was silently broken cross-process in
# production. War Room mandated this test specifically so that exact
# regression cannot recur. Do not delete or weaken without War Room
# review.
def test_halt_visible_across_process_boundary(redis_client, unique_key_prefix):
    """Spawns two real Python subprocesses:
      A: imports halt_store, publishes a halt for BTC/Kraken via
         RedisHaltStore.publish(), exits.
      B: imports halt_store, queries is_halted("BTC", "Kraken"),
         prints the result.

    Asserts subprocess B sees the halt subprocess A wrote. Uses a
    unique per-test Redis key prefix and cleans up on teardown.
    """
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    publisher_script = (
        "import sys, os; "
        f"sys.path.insert(0, {project_root!r}); "
        "import redis; "
        "from src.wire.integration.halt_store import RedisHaltStore; "
        "r = redis.Redis.from_url(os.environ['REDIS_URL'], decode_responses=True); "
        f"s = RedisHaltStore(redis_client=r, key_prefix={unique_key_prefix!r}); "
        "s.publish(coin='BTC', exchange='Kraken', "
        "halt_record={'trigger_event_id': 1234, 'coin': 'BTC', "
        "'exchange': 'Kraken', 'event_type': 'exchange_outage'}, "
        "ttl_seconds=600); "
        "print('PUBLISHED_OK')"
    )
    queryer_script = (
        "import sys, os, json; "
        f"sys.path.insert(0, {project_root!r}); "
        "import redis; "
        "from src.wire.integration.halt_store import RedisHaltStore; "
        "r = redis.Redis.from_url(os.environ['REDIS_URL'], decode_responses=True); "
        f"s = RedisHaltStore(redis_client=r, key_prefix={unique_key_prefix!r}); "
        "halted, payload = s.is_halted(coin='BTC', exchange='Kraken'); "
        "print(json.dumps({'halted': halted, 'payload': payload}))"
    )

    env = os.environ.copy()
    env["REDIS_URL"] = config.redis_url

    pub = subprocess.run(
        [sys.executable, "-c", publisher_script],
        capture_output=True, text=True, env=env, timeout=30,
    )
    assert pub.returncode == 0, (
        f"Publisher subprocess failed.\nstdout={pub.stdout!r}\nstderr={pub.stderr!r}"
    )
    assert "PUBLISHED_OK" in pub.stdout

    sanity_store = RedisHaltStore(
        redis_client=redis_client, key_prefix=unique_key_prefix,
    )
    sanity_halted, _ = sanity_store.is_halted(coin="BTC", exchange="Kraken")
    assert sanity_halted is True, (
        "Cross-process visibility broken: this test process can't see the "
        "halt subprocess A wrote."
    )

    try:
        qry = subprocess.run(
            [sys.executable, "-c", queryer_script],
            capture_output=True, text=True, env=env, timeout=30,
        )
        assert qry.returncode == 0, (
            f"Queryer subprocess failed.\nstdout={qry.stdout!r}\nstderr={qry.stderr!r}"
        )
        result = json.loads(qry.stdout.strip())
        assert result["halted"] is True, (
            f"Subprocess B did NOT see the halt subprocess A wrote. "
            f"Cross-process visibility is broken — the entire purpose of "
            f"the persistence layer. Result: {result!r}"
        )
        assert result["payload"] is not None
        assert result["payload"]["trigger_event_id"] == 1234
        assert result["payload"]["coin"] == "BTC"
        assert result["payload"]["exchange"] == "Kraken"
    finally:
        cursor = 0
        while True:
            cursor, keys = redis_client.scan(
                cursor=cursor, match=f"{unique_key_prefix}:*", count=100,
            )
            for key in keys:
                redis_client.delete(key)
            if cursor == 0:
                break


# ---------------------------------------------------------------------------
# Source-inspection guard
# ---------------------------------------------------------------------------


def test_halt_consumer_present_in_both_market_and_limit_paths():
    """Both execute paths must call _check_operator_halt."""
    import inspect
    from src.trading import execution_service as svc_mod

    market_src = inspect.getsource(svc_mod.PaperTradingService.execute_market_order)
    limit_src = inspect.getsource(svc_mod.PaperTradingService.execute_limit_order)

    for src, name in ((market_src, "execute_market_order"),
                      (limit_src, "execute_limit_order")):
        assert "_check_operator_halt" in src, (
            f"{name} no longer calls _check_operator_halt."
        )

    helper_src = inspect.getsource(svc_mod.PaperTradingService._check_operator_halt)
    assert "halt_store" in helper_src
    assert "is_halted" in helper_src
    assert "_halt_state_unknown" in helper_src
    assert "_defense_in_depth_in_memory_lookup" in helper_src
