"""
Warden trade-gate — wiring tests.

Diagnosis: `WIRING_AUDIT_REPORT.md` subsystem N (commit f2e798d).

These are NOT unit tests of `Warden.evaluate_trade` — that surface is
covered elsewhere and was passing while the colony quietly had no
mechanical safety gate at trade time. The whole point of this file is
to assert that the **production code path** wires the Warden through
`run_agents.py:build_warden` → `build_trading_service` →
`PaperTradingService.warden` → `evaluate_trade`. A passing unit test on
a method that nothing invokes does not protect us. Same shape as the
trading-service wiring test from this morning.

Includes the regression-class guard that asserts the production
constructor produces a `PaperTradingService` with `warden is not None`,
plus an end-to-end test that injects a trade exceeding the per-agent
position limit and asserts Warden rejects it (NOT defense-in-depth
soft-pass).
"""

from __future__ import annotations

import importlib
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.agents.action_executor import ActionExecutor
from src.agents.thinking_cycle import ThinkingCycle
from src.common.models import Agent, Base, Order, Position, SystemState, Transaction
from src.risk.warden import Warden
from src.trading.execution_service import PaperTradingService


# ---------------------------------------------------------------------------
# Fixtures: production-shape wiring (no shortcuts)
# ---------------------------------------------------------------------------


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
    """SystemState (alert green) + an Operator with $200 cash."""
    with db_factory() as session:
        session.add(SystemState(
            total_treasury=1000.0, peak_treasury=1000.0,
            current_regime="bull", active_agent_count=1, alert_status="green",
        ))
        agent = Agent(
            name="Operator-WardenTest",
            type="operator",
            status="active",
            generation=1,
            capital_allocated=200.0,
            capital_current=200.0,
            cash_balance=200.0,
            reserved_cash=0.0,
            total_equity=200.0,
        )
        session.add(agent)
        session.commit()
        return agent.id


@pytest.fixture
def fake_redis_client():
    r = MagicMock()
    r.set.return_value = True
    r.get.return_value = None
    r.delete.return_value = True
    r.ping.return_value = True
    return r


@pytest.fixture
def fake_claude_client():
    client = MagicMock()
    client.call = AsyncMock()
    return client


@pytest.fixture
def production_warden(db_factory):
    """Build the in-process Warden via the EXACT helper run_agents.py uses."""
    run_agents = importlib.import_module("scripts.run_agents")
    return run_agents.build_warden(db_factory, agora_service=None)


@pytest.fixture
def production_trading_service(db_factory, fake_redis_client, production_warden):
    """Build the trading service via the EXACT helper run_agents.py uses,
    including the wired Warden."""
    run_agents = importlib.import_module("scripts.run_agents")
    return run_agents.build_trading_service(
        db_factory=db_factory,
        redis_client=fake_redis_client,
        agora_service=None,
        warden=production_warden,
    )


# ---------------------------------------------------------------------------
# REGRESSION-CLASS GUARDS
# ---------------------------------------------------------------------------


def test_trade_execution_invokes_warden_in_production_boot(
    db_factory, fake_redis_client, fake_claude_client, production_warden,
    production_trading_service,
):
    """Running the production wiring path MUST yield a PaperTradingService
    whose `.warden` attribute is a real Warden instance, AND a ThinkingCycle
    whose ActionExecutor's `.warden` is the same instance.

    This is the regression guard. If a future change drops the warden kwarg
    anywhere in the chain, this test fails before it can ship to an Arena.
    """
    # Trading service side.
    assert production_trading_service is not None
    assert isinstance(production_trading_service, PaperTradingService)
    assert production_trading_service.warden is not None, (
        "PaperTradingService.warden is None — every Yellow/Red/circuit-breaker "
        "would silently fail to gate trades. See WIRING_AUDIT_REPORT.md "
        "subsystem N."
    )
    assert isinstance(production_trading_service.warden, Warden)
    assert production_trading_service.warden is production_warden

    # ThinkingCycle / ActionExecutor side.
    with db_factory() as session:
        cycle = ThinkingCycle(
            db_session=session,
            claude_client=fake_claude_client,
            redis_client=fake_redis_client,
            agora_service=None,
            warden=production_warden,
            config=None,
            trading_service=production_trading_service,
        )

    assert cycle.action_executor is not None
    assert isinstance(cycle.action_executor, ActionExecutor)
    assert cycle.action_executor.warden is production_warden


def test_run_agents_build_warden_returns_real_warden(db_factory):
    """The production helper must return a Warden, not None."""
    run_agents = importlib.import_module("scripts.run_agents")
    w = run_agents.build_warden(db_factory)
    assert isinstance(w, Warden)


# ---------------------------------------------------------------------------
# INTEGRATION: synthetic trade rejected by Warden gate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_warden_rejects_oversized_trade_through_production_path(
    db_factory, seeded_world, fake_redis_client, fake_claude_client,
    production_warden, production_trading_service,
):
    """End-to-end via the production wiring:
        1. Build ThinkingCycle the way run_agents.py does (with warden).
        2. Patch price_cache so no exchange call leaks out.
        3. Hand the Operator a trade that exceeds the per-agent position
           limit (PER_AGENT_MAX_POSITION_PCT defaults to ~25%; the test
           submits a $500 trade against a $200 capital agent — 250%).
        4. Assert: Warden's evaluate_trade fired and returned non-approved.
        5. Assert: Order row was written with status='rejected'.
        6. Assert: NO Position was created (no actual trade booked).
    """
    production_trading_service.price_cache = MagicMock()
    production_trading_service.price_cache.get_ticker = AsyncMock(
        return_value=({"bid": 100.0, "ask": 100.5, "last": 100.25, "baseVolume": 1_000_000}, True)
    )
    production_trading_service.price_cache.get_order_book = AsyncMock(
        return_value=({"asks": [[100.5, 100]], "bids": [[100.0, 100]]}, True)
    )
    production_trading_service.price_cache.is_stale = MagicMock(return_value=False)

    # Wrap evaluate_trade so we can confirm it was invoked. We do NOT replace
    # the implementation — we want the real gate to fire.
    real_evaluate_trade = production_warden.evaluate_trade
    invocations: list[dict] = []

    async def _spy_evaluate(req):
        invocations.append(dict(req))
        return await real_evaluate_trade(req)
    production_warden.evaluate_trade = _spy_evaluate

    with db_factory() as session:
        cycle = ThinkingCycle(
            db_session=session,
            claude_client=fake_claude_client,
            redis_client=fake_redis_client,
            agora_service=None,
            warden=production_warden,
            config=None,
            trading_service=production_trading_service,
        )
        operator = session.get(Agent, seeded_world)

        # Plan that exceeds per-agent position limit:
        # operator has $200 capital, plan asks $500 trade -> 250%.
        action = {
            "action": {
                "type": "execute_trade",
                "params": {
                    "market": "BTC/USDT",
                    "direction": "long",
                    "position_size_usd": 500.0,
                    "order_type": "market",
                },
            },
            "situation": "test",
            "confidence": {"score": 8, "reasoning": "test"},
            "recent_pattern": "test",
            "reasoning": "test",
            "self_note": "test",
        }

        result = await cycle.action_executor.execute(operator, action)

    # 1. Warden's evaluate_trade was invoked.
    assert len(invocations) == 1, (
        f"Warden.evaluate_trade was not invoked — got {len(invocations)} call(s). "
        "If this fails the trade-time safety gate is back to silent no-op."
    )
    assert invocations[0]["agent_id"] == seeded_world

    # 2. The action returned not-success (Warden rejected).
    assert result.success is False, (
        f"Trade was approved despite exceeding limits. details={result.details}"
    )

    # 3. The Order row exists with status='rejected'. NO Position should have
    #    been opened, no Transaction booked.
    with db_factory() as session:
        orders = session.execute(select(Order)).scalars().all()
        positions = session.execute(select(Position)).scalars().all()
        transactions = session.execute(select(Transaction)).scalars().all()

    assert len(orders) == 1
    assert orders[0].status == "rejected"
    assert "exceeds" in (orders[0].rejection_reason or "").lower() or \
           "limit" in (orders[0].rejection_reason or "").lower(), (
        f"rejection_reason='{orders[0].rejection_reason}' — Warden should "
        "have produced a position-limit message."
    )
    assert positions == [], "A position was opened despite Warden rejection"
    assert transactions == [], "A transaction was booked despite Warden rejection"


# ---------------------------------------------------------------------------
# Defense-in-depth: warden=None branch must scream, not soft-pass
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_warden_missing_branch_rejects_and_alerts(
    db_factory, seeded_world, fake_redis_client,
):
    """If a future bug constructs PaperTradingService with warden=None, the
    trade must be rejected with a CRITICAL log + system-alert mirror — NOT
    silently soft-passed. Mirrors the [NO SERVICE] defense-in-depth pattern.
    """
    from src.trading.fee_schedule import FeeSchedule
    from src.trading.slippage_model import SlippageModel
    from src.common.price_cache import PriceCache

    # Manually construct WITHOUT a warden — the production helper enforces
    # non-None, so we have to construct directly to exercise the fallback.
    svc = PaperTradingService(
        db_session_factory=db_factory,
        price_cache=MagicMock(),
        slippage_model=SlippageModel(),
        fee_schedule=FeeSchedule(),
        warden=None,
        redis_client=fake_redis_client,
        agora_service=None,
    )
    svc.price_cache.get_ticker = AsyncMock(
        return_value=({"bid": 100.0, "ask": 100.5, "last": 100.25, "baseVolume": 1_000_000}, True)
    )

    result = await svc.execute_market_order(
        agent_id=seeded_world, symbol="BTC/USDT", side="buy", size_usd=50.0,
    )

    assert result.success is False
    assert "warden missing" in (result.error or "").lower(), (
        f"Defense-in-depth must reject when warden is None. Got: {result.error}"
    )

    with db_factory() as session:
        orders = session.execute(select(Order)).scalars().all()
        positions = session.execute(select(Position)).scalars().all()

    assert len(orders) == 1
    assert orders[0].status == "rejected"
    assert positions == []


# ---------------------------------------------------------------------------
# Alert-status refresh path: the actual mechanism the hotfix added.
#
# These tests would FAIL without the `_refresh_alert_status_from_db()` call
# at the top of `evaluate_trade`. The previous integration test
# (`test_warden_rejects_oversized_trade_through_production_path`) exercises
# the position-limit branch, which doesn't depend on alert_status. These
# two tests cover the new mechanism explicitly. Per War Room Finding 2.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_evaluate_trade_refreshes_alert_status_from_db_and_blocks(
    db_factory, seeded_world, production_warden,
):
    """The mechanism: an in-process Warden constructed with default alert_status
    = "green" must hard-block a trade if the DB has alert_status = "red".

    Construct -> write red to DB -> call evaluate_trade -> assert rejected
    with a reason that cites the alert state, NOT the position limit. If
    the refresh mechanism breaks, this test fails: the in-process Warden
    would return approved (green) and the trade would slip through.
    """
    # Sanity: the production Warden starts as green.
    assert production_warden.alert_status == "green"
    assert production_warden._safety_state_unknown is False

    # Simulate the Warden process having flipped the colony to red.
    with db_factory() as session:
        state = session.execute(select(SystemState).limit(1)).scalar_one()
        state.alert_status = "red"
        session.commit()

    # Submit a trade that would otherwise pass position limits ($10 trade
    # against $200 capital = 5%, well under the 25% per-agent cap). The
    # ONLY thing that should block it is the alert refresh kicking in.
    result = await production_warden.evaluate_trade({
        "agent_id": seeded_world,
        "symbol": "BTC/USDT",
        "side": "buy",
        "amount": 0.1,
        "price": 100.0,  # trade_value = $10
    })

    assert result["status"] == "rejected"
    reason = result.get("reason", "")
    # Must cite RED alert (the real state), NOT position-limit, NOT
    # safety-state-unknown.
    assert "RED ALERT" in reason, (
        f"Expected reason to cite RED alert; got: {reason!r}. "
        "If this fails, the alert refresh mechanism is not wired."
    )
    # The Warden's in-memory state was updated by the refresh.
    assert production_warden.alert_status == "red"
    assert production_warden._safety_state_unknown is False


@pytest.mark.asyncio
async def test_evaluate_trade_fails_closed_when_no_state_row_exists(
    db_factory, seeded_world, production_warden,
):
    """Per War Room iteration-3 Finding 2-Empty-Row: the no-row branch is
    more likely in production than the raise branch (fresh DB, missed
    migration, deleted row, multi-tenant misconfig). Sibling test to the
    raise-case test.

    Patch `_get_system_state` to return None. Submit a small trade.
    Assert the same fail-closed-to-red behaviour: rejection with reason
    citing unknown-state, `_safety_state_unknown` set, alert_status
    forced to red.
    """
    production_warden._safety_state_unknown = False
    production_warden.alert_status = "green"

    production_warden._get_system_state = lambda: None

    result = await production_warden.evaluate_trade({
        "agent_id": seeded_world,
        "symbol": "BTC/USDT",
        "side": "buy",
        "amount": 0.05,
        "price": 100.0,  # trade_value = $5
    })

    assert result["status"] == "rejected"
    reason = result.get("reason", "")
    assert (
        "unknown" in reason.lower()
        or "fail" in reason.lower()
        or "read failed" in reason.lower()
    ), (
        f"Empty-row branch must produce same unknown-state reason as the "
        f"raise branch. Got: {reason!r}"
    )
    assert production_warden._safety_state_unknown is True
    assert production_warden.alert_status == "red"


@pytest.mark.asyncio
async def test_safety_unknown_auto_clears_on_db_recovery(
    db_factory, seeded_world, production_warden,
):
    """Per War Room iteration-3 Finding 1-Recovery: the fail-closed latch
    MUST auto-clear on the next successful refresh. A single transient DB
    blip cannot permanently halt the colony.

    Full cycle:
      (a) Healthy state -> evaluate_trade approves a small trade.
      (b) Force DB to raise on next refresh -> evaluate_trade rejects
          with safety-unknown reason.
      (c) Restore DB -> evaluate_trade approves again (the latch lifted).
      (d) Assert _safety_state_unknown is False and alert_status reflects
          the actual DB state, not the stale forced-red value.

    If a future change makes the latch sticky, this test fails. That is
    the protection against the DMS self-defeating-loop pattern recurring.
    """
    # ---- (a) Healthy: trade approves ----
    production_warden._safety_state_unknown = False
    production_warden.alert_status = "green"

    result_a = await production_warden.evaluate_trade({
        "agent_id": seeded_world,
        "symbol": "BTC/USDT",
        "side": "buy",
        "amount": 0.05,
        "price": 100.0,  # $5 — small, well under any limit
    })
    assert result_a["status"] == "approved", (
        f"Step (a): healthy state should approve a $5 trade. Got: {result_a}"
    )
    assert production_warden._safety_state_unknown is False
    # Refresh adopted the DB value (which is 'green' from the fixture).
    assert production_warden.alert_status == "green"

    # ---- (b) DB blip: refresh raises, trade hard-rejected ----
    real_get_state = production_warden._get_system_state

    def _raise(*args, **kwargs):
        raise RuntimeError("simulated transient DB blip")
    production_warden._get_system_state = _raise

    result_b = await production_warden.evaluate_trade({
        "agent_id": seeded_world,
        "symbol": "BTC/USDT",
        "side": "buy",
        "amount": 0.05,
        "price": 100.0,
    })
    assert result_b["status"] == "rejected", (
        f"Step (b): blip must trigger fail-closed reject. Got: {result_b}"
    )
    assert "unknown" in result_b.get("reason", "").lower()
    assert production_warden._safety_state_unknown is True
    assert production_warden.alert_status == "red"  # forced

    # ---- (c) DB restored: next refresh clears the latch ----
    production_warden._get_system_state = real_get_state

    result_c = await production_warden.evaluate_trade({
        "agent_id": seeded_world,
        "symbol": "BTC/USDT",
        "side": "buy",
        "amount": 0.05,
        "price": 100.0,
    })

    # ---- (d) Latch lifted, state reflects actual DB ----
    assert result_c["status"] == "approved", (
        f"Step (c-d): after DB recovery, latch must auto-clear and trade "
        f"must approve. Got: {result_c}. If this fails, the fail-closed "
        f"latch became sticky — DMS self-defeating-loop pattern recurring."
    )
    assert production_warden._safety_state_unknown is False, (
        "Latch did not clear after DB recovery — sticky regression."
    )
    # alert_status is now whatever the DB actually says, NOT the forced 'red'
    # we wrote during the blip.
    assert production_warden.alert_status == "green"


@pytest.mark.asyncio
async def test_evaluate_trade_fails_closed_to_red_when_db_read_raises(
    db_factory, seeded_world, production_warden,
):
    """Per War Room Finding 1: when alert_status cannot be read, the
    Warden treats safety state as unknown and rejects. The reason text
    must indicate UNKNOWN (not a real RED alert) so on-call readers can
    distinguish "colony is in red" from "we don't know what state we're in".

    Patch `_get_system_state` to raise. Submit a small trade. Assert
    rejection with a safety-state-unknown reason. Confirm the
    `_safety_state_unknown` flag is set.
    """
    # Pre-condition: clean state.
    production_warden._safety_state_unknown = False
    production_warden.alert_status = "green"

    def _raise(*args, **kwargs):
        raise RuntimeError("simulated DB unavailability")

    production_warden._get_system_state = _raise

    result = await production_warden.evaluate_trade({
        "agent_id": seeded_world,
        "symbol": "BTC/USDT",
        "side": "buy",
        "amount": 0.05,
        "price": 100.0,  # trade_value = $5, well below any limit
    })

    assert result["status"] == "rejected"
    reason = result.get("reason", "")
    assert (
        "unknown" in reason.lower()
        or "fail" in reason.lower()
        or "read failed" in reason.lower()
    ), (
        f"Reason must distinguish unknown-state from real RED alert. "
        f"Got: {reason!r}"
    )
    # Internal flag was set, alert_status was forced to red.
    assert production_warden._safety_state_unknown is True
    assert production_warden.alert_status == "red"


def test_warden_missing_branch_present_in_both_market_and_limit_paths():
    """Regression guard. Document via source inspection that the loud
    warden-missing fallback exists in BOTH execute_market_order and
    execute_limit_order. A future cleanup that drops one of them shouldn't
    quietly land."""
    import inspect
    from src.trading import execution_service as svc_mod

    market_src = inspect.getsource(svc_mod.PaperTradingService.execute_market_order)
    limit_src = inspect.getsource(svc_mod.PaperTradingService.execute_limit_order)

    for src, name in ((market_src, "execute_market_order"),
                      (limit_src, "execute_limit_order")):
        assert "if self.warden is None:" in src, (
            f"defense-in-depth warden-missing guard removed from {name}"
        )
        assert "_raise_warden_missing_alert" in src, (
            f"loud alert call missing from {name}"
        )

    helper_src = inspect.getsource(svc_mod.PaperTradingService._raise_warden_missing_alert)
    assert "log.critical" in helper_src, "fallback log was downgraded from CRITICAL"
    assert "system-alerts" in helper_src, "system-alerts mirror removed"
