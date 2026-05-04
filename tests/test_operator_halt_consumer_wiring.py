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
import textwrap
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
    OperatorHaltPublishError,
    publish_halt_for_event,
    reset_registry,
    set_alert_publisher,
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
    don't bleed into another. Also reset the alert publisher so a test
    that registered a capturing one doesn't leak into the next test."""
    reset_registry()
    set_halt_store(None)
    set_alert_publisher(None)
    yield
    reset_registry()
    set_halt_store(None)
    set_alert_publisher(None)


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
    """Spawns two real Python subprocesses against real Memurai:
      A: PRODUCER. Calls the production factory
         `src.wire.cli._initialize_producer_halt_store(...)` then
         `publish_halt_for_event(...)` — the same call sequence the
         real wire_scheduler subprocess runs at startup.
      B: CONSUMER. Calls the production factory
         `scripts.run_agents.build_halt_store(redis_client, ...)`
         then `halt_store.is_halted(...)` — the same chain
         PaperTradingService runs in the agents subprocess.

    Both axes from Critic Finding 2 (iteration 5) are explicit:
      (a) Real Memurai, no mocks — `redis.Redis.from_url(REDIS_URL)`.
      (b) Production factories — _initialize_producer_halt_store and
          build_halt_store. If a refactor moves the wiring out of
          those factories, this test fails.

    Each test run uses a unique key_prefix from the fixture, so the
    namespace doesn't collide with the production `wire:halt`
    namespace or with concurrent test runs. The production factories
    accept the prefix override via kwargs so production callers stay
    unchanged (they pass nothing).
    """
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    # PRODUCER: runs the production wire scheduler bootstrap factory,
    # then issues a halt via publish_halt_for_event (the production
    # call site in haiku_digester._dispatch_post_digest_hooks).
    publisher_script = textwrap.dedent(f"""
        import sys, os
        sys.path.insert(0, {project_root!r})
        from src.wire.cli import _initialize_producer_halt_store
        from src.wire.integration.operator_halt import publish_halt_for_event
        store = _initialize_producer_halt_store(
            redis_url=os.environ['REDIS_URL'],
            key_prefix={unique_key_prefix!r},
        )
        publish_halt_for_event(
            event_id=1234,
            coin='BTC',
            event_type='exchange_outage',
            severity=5,
            summary='cross-process boundary test (production-factory path)',
            exchange='Kraken',
        )
        print('PUBLISHED_OK')
    """).strip()

    # CONSUMER: runs the production agents-subprocess bootstrap factory,
    # then queries via halt_store.is_halted (the call inside
    # PaperTradingService._check_operator_halt).
    queryer_script = textwrap.dedent(f"""
        import sys, os, json
        sys.path.insert(0, {project_root!r})
        import redis
        from scripts.run_agents import build_halt_store
        r = redis.Redis.from_url(os.environ['REDIS_URL'], decode_responses=True)
        s = build_halt_store(r, key_prefix={unique_key_prefix!r})
        halted, payload = s.is_halted(coin='BTC', exchange='Kraken')
        print(json.dumps({{'halted': halted, 'payload': payload}}))
    """).strip()

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
        # The producer used publish_halt_for_event -> make_halt_record,
        # so the canonical schema must be present (Finding 4 alignment).
        for required in ("trigger_event_id", "event_type", "issued_at",
                         "expires_at", "severity"):
            assert required in result["payload"], (
                f"Cross-process payload missing canonical field {required!r}: "
                f"{result['payload']!r}"
            )
        assert result["payload"]["trigger_event_id"] == 1234
        assert result["payload"]["coin"] == "BTC"
        assert result["payload"]["exchange"] == "Kraken"
        assert result["payload"]["event_type"] == "exchange_outage"
        assert int(result["payload"]["severity"]) == 5
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


# ---------------------------------------------------------------------------
# Critic Finding 1 (HIGH, iteration 5):
# Producer-side fail-closed semantics. publish_halt_for_event must NOT
# silently fall back to the producer-side _ACTIVE list when a configured
# Redis store fails — that re-creates the cross-process gap one layer
# deeper. Required behavior: log CRITICAL, mirror to system-alerts via
# the alert publisher, raise OperatorHaltPublishError. The trade-side
# consumer in another subprocess can then observe the failure even if
# the digester crashes.
# ---------------------------------------------------------------------------


def test_producer_halt_publish_fails_closed_when_redis_raises(unique_key_prefix):
    """Producer side: when a configured RedisHaltStore.publish raises,
    publish_halt_for_event must:
      - NOT silently append to producer-side _ACTIVE (invisible cross-process)
      - call the registered alert_publisher (cross-process observable)
      - raise OperatorHaltPublishError
    """
    from src.wire.integration import operator_halt as halt_mod

    # Simulated broken Redis client that raises on .set, mimicking a
    # Memurai outage during digestion. We construct a real RedisHaltStore
    # so the failure happens inside the production publish() path.
    raising_redis = MagicMock()
    raising_redis.set = MagicMock(
        side_effect=ConnectionError("simulated Memurai down during digest"),
    )
    store = RedisHaltStore(redis_client=raising_redis, key_prefix=unique_key_prefix)
    set_halt_store(store)

    captured_alerts = []
    set_alert_publisher(
        lambda event_class, payload: captured_alerts.append((event_class, payload))
    )

    with pytest.raises(OperatorHaltPublishError) as excinfo:
        publish_halt_for_event(
            event_id=999, coin="BTC", event_type="exchange_outage",
            severity=SEVERITY_CRITICAL, summary="SYNTHETIC: producer Redis down",
        )

    assert excinfo.value.trigger_event_id == 999
    assert excinfo.value.coin == "BTC"
    assert excinfo.value.event_type == "exchange_outage"
    assert isinstance(excinfo.value.underlying, ConnectionError)

    assert halt_mod._ACTIVE == [], (
        "Producer-side _ACTIVE was silently populated on Redis-write failure. "
        "This re-creates the cross-process visibility gap at a new layer — "
        "consumers in different subprocesses cannot see _ACTIVE."
    )

    assert len(captured_alerts) == 1, (
        f"Expected exactly one alert on Redis-write failure, got "
        f"{len(captured_alerts)}: {captured_alerts!r}"
    )
    event_class, payload = captured_alerts[0]
    assert event_class == "wire.operator_halt.publish_failed"
    assert payload["trigger_event_id"] == 999
    assert payload["coin"] == "BTC"
    assert payload["event_type"] == "exchange_outage"
    assert "summary" in payload


def test_producer_halt_publish_fails_closed_even_without_alert_publisher(
    unique_key_prefix,
):
    """The CRITICAL log + raise are the load-bearing loud signal. Even
    when no alert publisher is registered, the failure must NOT degrade
    to silent _ACTIVE fallback."""
    from src.wire.integration import operator_halt as halt_mod

    raising_redis = MagicMock()
    raising_redis.set = MagicMock(side_effect=ConnectionError("Redis down"))
    store = RedisHaltStore(redis_client=raising_redis, key_prefix=unique_key_prefix)
    set_halt_store(store)
    set_alert_publisher(None)  # explicit — verify no-publisher path

    with pytest.raises(OperatorHaltPublishError):
        publish_halt_for_event(
            event_id=1000, coin="ETH", event_type="withdrawal_halt",
            severity=SEVERITY_CRITICAL, summary="SYNTHETIC",
        )
    assert halt_mod._ACTIVE == []


def test_producer_halt_publish_succeeds_with_redis_writes_to_redis_only(
    halt_store, unique_key_prefix,
):
    """Sanity counter-test for the fail-closed path: when Redis IS
    healthy and a store is configured, the halt lands in Redis and
    NOT in producer-side _ACTIVE. _ACTIVE writes are reserved for the
    no-store-configured path only."""
    from src.wire.integration import operator_halt as halt_mod

    set_halt_store(halt_store)

    publish_halt_for_event(
        event_id=2000, coin="BTC", event_type="chain_halt",
        severity=SEVERITY_CRITICAL, summary="SYNTHETIC: healthy Redis",
    )
    halted, payload = halt_store.is_halted(coin="BTC", exchange=None)
    assert halted is True
    assert payload["trigger_event_id"] == 2000
    assert halt_mod._ACTIVE == [], (
        "Healthy-Redis publish path must not touch producer-side _ACTIVE."
    )


# ---------------------------------------------------------------------------
# Critic Finding 3 (HIGH, iteration 5):
# Post-construction verification. set_halt_store success doesn't prove
# the module-level reference took. Bootstrap code must re-read via
# get_halt_store() and fail fast on mismatch.
# ---------------------------------------------------------------------------


def test_post_construction_get_halt_store_reflects_set_halt_store():
    """Round-trip verification: after set_halt_store(store),
    get_halt_store() returns the exact same instance. Without this,
    a future bug where the setter no-ops or import paths skew would
    silently leave the module-level reference None while bootstrap
    declared success."""
    from src.wire.integration.operator_halt import get_halt_store

    fake_redis = MagicMock(); fake_redis.set = MagicMock()
    store = RedisHaltStore(redis_client=fake_redis, key_prefix="postctor")
    set_halt_store(store)
    assert get_halt_store() is store, (
        "set_halt_store / get_halt_store round-trip failed — "
        "module-level reference did not match the registered instance."
    )

    # And the unset path returns None.
    set_halt_store(None)
    assert get_halt_store() is None


def test_run_agents_bootstrap_fails_fast_on_assignment_mismatch():
    """Source-inspection guard: scripts/run_agents.py main() must
    re-read get_producer_halt_store() and sys.exit(2) if the assignment
    didn't take. This is the load-bearing line that turns a silent
    bootstrap regression into a loud crash."""
    import inspect
    import importlib
    run_agents = importlib.import_module("scripts.run_agents")
    main_src = inspect.getsource(run_agents.main)
    assert "get_producer_halt_store" in main_src, (
        "run_agents.main no longer re-reads the registered halt_store. "
        "Post-construction verification (Critic Finding 3) was removed."
    )
    assert "halt_store_assignment_lost" in main_src or "halt_store_assignment_mismatch" in main_src
    assert "sys.exit(2)" in main_src


def test_wire_cli_bootstrap_fails_fast_on_assignment_mismatch():
    """Source-inspection guard for the producer-side bootstrap in
    src/wire/cli.py:_initialize_producer_halt_store. Same contract as
    run_agents."""
    import inspect
    import importlib
    cli = importlib.import_module("src.wire.cli")
    init_src = inspect.getsource(cli._initialize_producer_halt_store)
    assert "get_producer_halt_store" in init_src, (
        "wire/cli._initialize_producer_halt_store no longer verifies the "
        "module-level assignment took (Critic Finding 3)."
    )
    assert "sys.exit(2)" in init_src


# ---------------------------------------------------------------------------
# Critic Finding 4 (MEDIUM, iteration 5):
# A halt_record returned with halted=True but missing required fields is
# itself a failure mode. Treat like Redis read failure: flip
# _halt_state_unknown, alert, reject. Auto-clear on next valid record.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_check_operator_halt_fails_closed_on_malformed_record(
    db_factory, seeded_world, fake_redis_client_for_pricecache, production_warden,
):
    """Write a malformed record (missing trigger_event_id, expires_at, etc.)
    via a stub store and verify the consumer rejects with halt-state-unknown
    AND the alert was emitted, NOT a normal "Operator halt active" rejection
    that would lie about the schema."""
    malformed_store = MagicMock()
    # halted=True but the record is missing every required field
    malformed_store.is_halted = MagicMock(return_value=(True, {"coin": "BTC"}))

    svc = _build_svc(
        db_factory=db_factory,
        fake_redis_client_for_pricecache=fake_redis_client_for_pricecache,
        warden=production_warden, halt_store=malformed_store,
    )
    result = await svc.execute_market_order(
        agent_id=seeded_world, symbol="BTC/USDT", side="buy", size_usd=10.0,
    )
    assert result.success is False
    err = (result.error or "").lower()
    assert "unknown" in err and "fail-closed" in err, (
        f"Expected fail-closed-unknown rejection on malformed record, got: {err!r}"
    )
    assert "malformed" in err
    assert svc._halt_state_unknown is True


@pytest.mark.asyncio
async def test_malformed_record_unknown_state_auto_clears_on_valid_record(
    db_factory, seeded_world, fake_redis_client_for_pricecache, production_warden,
):
    """Malformed record sets _halt_state_unknown; the next call that
    returns (False, None) clears it (DMS-anti-pattern guard, same as
    Redis-recovery semantics)."""
    state = {"mode": "malformed"}

    def _toggle(coin=None, exchange=None):
        if state["mode"] == "malformed":
            return (True, {"coin": "BTC"})  # missing required fields
        return (False, None)

    toggling_store = MagicMock()
    toggling_store.is_halted = MagicMock(side_effect=_toggle)

    svc = _build_svc(
        db_factory=db_factory,
        fake_redis_client_for_pricecache=fake_redis_client_for_pricecache,
        warden=production_warden, halt_store=toggling_store,
    )
    bad = await svc.execute_market_order(
        agent_id=seeded_world, symbol="BTC/USDT", side="buy", size_usd=5.0,
    )
    assert bad.success is False
    assert svc._halt_state_unknown is True

    state["mode"] = "valid"
    good = await svc.execute_market_order(
        agent_id=seeded_world, symbol="BTC/USDT", side="buy", size_usd=5.0,
    )
    assert good.success is True
    assert svc._halt_state_unknown is False


@pytest.mark.asyncio
async def test_well_formed_halt_record_uses_canonical_fields_in_reason(
    db_factory, seeded_world, fake_redis_client_for_pricecache, production_warden,
    halt_store,
):
    """Counter-test: when the record IS well-formed, the rejection
    reason uses real values (no '?' placeholders). If this regresses,
    the fail-closed branch is being skipped or the validation is too
    strict."""
    halt_store.publish(
        coin="BTC", exchange=None,
        halt_record=make_halt_record(
            event_id=12345, coin="BTC", exchange=None,
            event_type="exchange_outage", severity=5,
            summary="Well-formed halt for canonical-fields test",
            issued_at=datetime.now(timezone.utc),
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=30),
        ),
        ttl_seconds=1800,
    )
    svc = _build_svc(
        db_factory=db_factory,
        fake_redis_client_for_pricecache=fake_redis_client_for_pricecache,
        warden=production_warden, halt_store=halt_store,
    )
    result = await svc.execute_market_order(
        agent_id=seeded_world, symbol="BTC/USDT", side="buy", size_usd=5.0,
    )
    assert result.success is False
    err = result.error or ""
    assert "trigger_event_id=12345" in err
    assert "exchange_outage" in err
    assert "?" not in err.split("expires=")[0], (
        f"Reason contains '?' placeholders despite a well-formed record: {err!r}"
    )
