"""
Operator halt consumer — production wiring tests.

Closes WIRING_AUDIT_REPORT.md subsystem I: severity-5 events
(exchange_outage / withdrawal_halt / chain_halt) publish into the
in-memory _ACTIVE list. Phase 10 had unit tests on the producer side;
the consumer side (PaperTradingService reading the list before trades)
was unwired in production. These tests assert the production code path
actually consults the halt list at every trade-initiation point.

Pattern matches the trading-service and Warden wiring tests:
  - Build the trading service via the same helper run_agents.py uses
    (`build_trading_service`)
  - Inject a synthetic severity-5 halt via the actual producer
    (`publish_halt_for_event`)
  - Submit a trade via the production code path
  - Assert the trade is rejected/approved per the halt's scope

Three required tests per the directive:
  a) blocks affected coin in production boot
  b) does NOT block unaffected coin (per-coin scope)
  c) auto-lifts on signal expiry
"""

from __future__ import annotations

import importlib
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.common.models import Agent, Base, Order, Position, SystemState, Transaction
from src.trading.execution_service import PaperTradingService
from src.wire.constants import SEVERITY_CRITICAL
from src.wire.integration.operator_halt import (
    list_active,
    publish_halt_for_event,
    reset_registry,
)


# ---------------------------------------------------------------------------
# Fixtures: production-shape wiring with full collaborator chain
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_halt_registry():
    """Module-level _ACTIVE leaks across tests if not reset. Hard-clear
    before AND after each test so signals from one test don't bleed."""
    reset_registry()
    yield
    reset_registry()


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
            name="Operator-HaltTest",
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
def production_warden(db_factory):
    """Build the in-process Warden via the EXACT helper run_agents.py uses."""
    run_agents = importlib.import_module("scripts.run_agents")
    return run_agents.build_warden(db_factory, agora_service=None)


@pytest.fixture
def production_trading_service(db_factory, fake_redis_client, production_warden):
    """Build the trading service via the EXACT helper run_agents.py uses,
    including the wired Warden AND halt_checker. Production reality."""
    run_agents = importlib.import_module("scripts.run_agents")
    service = run_agents.build_trading_service(
        db_factory=db_factory,
        redis_client=fake_redis_client,
        agora_service=None,
        warden=production_warden,
        halt_checker=run_agents.wire_list_active_halts,
    )
    # Patch price feed: avoid real exchange calls.
    service.price_cache = MagicMock()
    service.price_cache.get_ticker = AsyncMock(
        return_value=({"bid": 100.0, "ask": 100.5, "last": 100.25, "baseVolume": 1_000_000}, True)
    )
    service.price_cache.get_order_book = AsyncMock(
        return_value=({"asks": [[100.5, 100]], "bids": [[100.0, 100]]}, True)
    )
    service.price_cache.is_stale = MagicMock(return_value=False)
    return service


# ---------------------------------------------------------------------------
# REGRESSION-CLASS GUARDS
# ---------------------------------------------------------------------------


def test_paper_trading_service_receives_halt_checker_in_production_boot(
    production_trading_service,
):
    """The build_trading_service helper threads halt_checker through to
    PaperTradingService.halt_checker. If a future change drops the kwarg
    anywhere in the chain, this test fails before it reaches an Arena."""
    assert production_trading_service.halt_checker is not None, (
        "PaperTradingService.halt_checker is None — the Operator would "
        "silently ignore active severity-5 halts. See WIRING_AUDIT_REPORT.md "
        "subsystem I."
    )
    assert callable(production_trading_service.halt_checker)


# ---------------------------------------------------------------------------
# Per-directive: three integration tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_operator_halt_blocks_affected_coin_in_production_boot(
    db_factory, seeded_world, production_trading_service,
):
    """Boot through the production code path, inject a synthetic
    severity-5 exchange_outage halt for BTC, attempt a BTC trade, assert
    REJECTED with reason citing the halt event ID."""
    # Inject the halt via the actual producer (same path the digester uses).
    signal = publish_halt_for_event(
        event_id=42,
        coin="BTC",
        event_type="exchange_outage",
        severity=SEVERITY_CRITICAL,
        summary="SYNTHETIC: BTC exchange outage on Kraken",
    )
    assert signal is not None, "Halt was not published — preconditions failed"

    # Verify the halt is actually in the registry before trying the trade.
    active = list_active(coin="BTC")
    assert len(active) == 1
    assert active[0].trigger_event_id == 42

    # Submit a BTC market buy through the production wiring.
    result = await production_trading_service.execute_market_order(
        agent_id=seeded_world,
        symbol="BTC/USDT",
        side="buy",
        size_usd=10.0,  # tiny, well under any limit
    )

    assert result.success is False, (
        f"BTC trade was approved despite an active halt. "
        f"details={result.details}. If this fails, the Operator halt "
        f"consumer wiring (subsystem I) is broken — the halt list is "
        f"populated but nothing reads it."
    )
    # Reason must cite the halt event ID and event_type for War Room
    # diagnosability — not just "rejected".
    err = (result.error or "").lower()
    assert "halt" in err, f"Reason should cite halt: got {result.error!r}"
    assert "trigger_event_id=42" in (result.error or ""), (
        f"Reason should cite the trigger_event_id: got {result.error!r}"
    )
    assert "exchange_outage" in (result.error or ""), (
        f"Reason should cite the halt event_type: got {result.error!r}"
    )

    # The trade must NOT have produced a Position or Transaction (Order
    # row is rejected, but no exposure was taken).
    with db_factory() as session:
        positions = session.execute(select(Position)).scalars().all()
        transactions = session.execute(select(Transaction)).scalars().all()
        orders = session.execute(select(Order)).scalars().all()

    assert positions == [], "Halt-blocked trade opened a position"
    assert transactions == [], "Halt-blocked trade booked a transaction"
    assert len(orders) == 1
    assert orders[0].status == "rejected"


@pytest.mark.asyncio
async def test_operator_halt_does_not_block_unaffected_coin(
    db_factory, seeded_world, production_trading_service,
):
    """Per-coin scope: a BTC halt must NOT block ETH trading. Same setup
    as the previous test but the trade is for ETH/USDT — must be approved.

    This is the test that catches a future regression to colony-wide
    halt scope. The Phase 10 kickoff specified per-coin-per-exchange
    explicitly to avoid one bad source taking down all trading.
    """
    publish_halt_for_event(
        event_id=43,
        coin="BTC",
        event_type="exchange_outage",
        severity=SEVERITY_CRITICAL,
        summary="SYNTHETIC: BTC outage — should not block ETH",
    )
    assert list_active(coin="BTC")  # halt is active

    result = await production_trading_service.execute_market_order(
        agent_id=seeded_world,
        symbol="ETH/USDT",
        side="buy",
        size_usd=10.0,
    )

    assert result.success is True, (
        f"ETH trade was rejected despite the halt being BTC-only. "
        f"details={result.details}. If this fails, halt scope has "
        f"regressed from per-coin to colony-wide — kickoff violation."
    )

    # ETH trade actually booked.
    with db_factory() as session:
        eth_positions = session.execute(
            select(Position).where(Position.symbol == "ETH/USDT")
        ).scalars().all()
    assert len(eth_positions) == 1
    assert eth_positions[0].side == "long"


@pytest.mark.asyncio
async def test_operator_halt_auto_lifts_on_signal_expiry(
    db_factory, seeded_world, production_trading_service,
):
    """Halts auto-expire via FILTER-ON-READ in `list_active` — there is
    NO background sweeper (Critic Finding 4). On every call, list_active
    filters out signals whose `is_active(now)` returns False (i.e.,
    `expires_at <= now`). When `expires_at` passes wallclock now, the
    consumer sees an empty list and approves the trade.

    The default auto-resume window is 30 min (DEFAULT_AUTO_EXPIRE_MINUTES).
    We force expiry deterministically by re-publishing the signal with an
    `issued_at` far enough in the past that `expires_at` (= issued_at +
    auto_expire_minutes) is already behind wallclock now. That exercises
    the SAME filter-on-read path a long-running Arena would hit naturally."""
    # Issue a halt with a 1-minute timer.
    publish_halt_for_event(
        event_id=44,
        coin="BTC",
        event_type="withdrawal_halt",
        severity=SEVERITY_CRITICAL,
        summary="SYNTHETIC: short-lived halt for expiry test",
        auto_expire_minutes=1,
    )
    # Confirm halt is active right now.
    assert list_active(coin="BTC")

    # First trade — must be blocked.
    blocked = await production_trading_service.execute_market_order(
        agent_id=seeded_world, symbol="BTC/USDT", side="buy", size_usd=10.0,
    )
    assert blocked.success is False, "Halt did not block the first trade"

    # Force expiry: simulate the 30-min timer elapsing by mutating the
    # internal _ACTIVE list's signals. We can't replace immutable frozen
    # dataclass fields, so we drop the signal and re-publish with an
    # already-expired timer. This is the same behavior list_active sees
    # naturally after wallclock passes the expires_at.
    from src.wire.integration import operator_halt as halt_mod
    halt_mod._ACTIVE.clear()
    # Re-publish with an issued_at far in the past so the auto-expire
    # window has already elapsed.
    past_issued = datetime.now(timezone.utc) - timedelta(minutes=30)
    publish_halt_for_event(
        event_id=45,
        coin="BTC",
        event_type="withdrawal_halt",
        severity=SEVERITY_CRITICAL,
        summary="SYNTHETIC: expired halt",
        auto_expire_minutes=1,  # expires_at = past_issued + 1 min ≈ 29 min ago
        now=past_issued,
    )
    # Sanity: list_active filters the expired signal out.
    assert list_active(coin="BTC") == [], (
        "list_active still returned an expired signal — auto-expiry semantics broken"
    )

    # Second trade — must now be approved (halt has lifted).
    approved = await production_trading_service.execute_market_order(
        agent_id=seeded_world, symbol="BTC/USDT", side="buy", size_usd=10.0,
    )
    assert approved.success is True, (
        f"BTC trade was rejected after halt expired. "
        f"details={approved.details}. Halts must auto-lift; if this fails "
        f"the expiry semantics regressed and the colony stays halted forever."
    )


# ---------------------------------------------------------------------------
# Defense-in-depth: halt_checker=None branch must scream, not silent-pass
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_halt_checker_missing_branch_rejects_and_alerts(
    db_factory, seeded_world, fake_redis_client, production_warden,
):
    """If a future bug constructs PaperTradingService with halt_checker=None,
    the trade must be rejected with CRITICAL log + system-alert mirror —
    NOT silently soft-passed. Mirrors the [NO SERVICE] / Warden-missing
    defense-in-depth pattern."""
    from src.trading.fee_schedule import FeeSchedule
    from src.trading.slippage_model import SlippageModel

    svc = PaperTradingService(
        db_session_factory=db_factory,
        price_cache=MagicMock(),
        slippage_model=SlippageModel(),
        fee_schedule=FeeSchedule(),
        warden=production_warden,
        redis_client=fake_redis_client,
        agora_service=None,
        halt_checker=None,  # the bug we're guarding against
    )
    svc.price_cache.get_ticker = AsyncMock(
        return_value=({"bid": 100.0, "ask": 100.5, "last": 100.25, "baseVolume": 1_000_000}, True)
    )

    result = await svc.execute_market_order(
        agent_id=seeded_world, symbol="BTC/USDT", side="buy", size_usd=10.0,
    )

    assert result.success is False
    assert "halt-checker missing" in (result.error or "").lower(), (
        f"Defense-in-depth must reject when halt_checker is None. "
        f"Got: {result.error!r}"
    )
    with db_factory() as session:
        positions = session.execute(select(Position)).scalars().all()
        orders = session.execute(select(Order)).scalars().all()
    assert positions == []
    assert len(orders) == 1
    assert orders[0].status == "rejected"


# ---------------------------------------------------------------------------
# Iteration 4 — Critic findings (HIGH/MEDIUM/LOW)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_check_operator_halt_fails_closed_when_list_active_raises(
    db_factory, seeded_world, fake_redis_client, production_warden,
):
    """Critic Finding 1 (HIGH): if halt_checker raises, treat as halt-state-
    unknown and REJECT. Mirrors the Warden's fail-closed-to-red. Without
    this, a transient Wire glitch silently lets all trades through."""
    from src.trading.fee_schedule import FeeSchedule
    from src.trading.slippage_model import SlippageModel

    def _raising_checker(**kw):
        raise RuntimeError("simulated Wire registry unavailability")

    svc = PaperTradingService(
        db_session_factory=db_factory,
        price_cache=MagicMock(),
        slippage_model=SlippageModel(),
        fee_schedule=FeeSchedule(),
        warden=production_warden,
        redis_client=fake_redis_client,
        agora_service=None,
        halt_checker=_raising_checker,
    )
    svc.price_cache.get_ticker = AsyncMock(
        return_value=({"bid": 100.0, "ask": 100.5, "last": 100.25, "baseVolume": 1_000_000}, True)
    )

    result = await svc.execute_market_order(
        agent_id=seeded_world, symbol="BTC/USDT", side="buy", size_usd=10.0,
    )
    assert result.success is False
    err = (result.error or "").lower()
    assert "unknown" in err and "fail-closed" in err, (
        f"Expected halt-state-unknown reason; got {result.error!r}"
    )
    assert svc._halt_state_unknown is True


@pytest.mark.asyncio
async def test_check_operator_halt_fails_closed_when_list_active_returns_garbage(
    db_factory, seeded_world, fake_redis_client, production_warden,
):
    """Critic Finding 1 (HIGH): malformed return → same fail-closed
    behavior. A halt_checker that returns the wrong shape (e.g., None,
    a string, a dict) cannot be trusted; treat as unknown state."""
    from src.trading.fee_schedule import FeeSchedule
    from src.trading.slippage_model import SlippageModel

    def _garbage_checker(**kw):
        return "not a list"  # garbage

    svc = PaperTradingService(
        db_session_factory=db_factory,
        price_cache=MagicMock(),
        slippage_model=SlippageModel(),
        fee_schedule=FeeSchedule(),
        warden=production_warden,
        redis_client=fake_redis_client,
        agora_service=None,
        halt_checker=_garbage_checker,
    )
    svc.price_cache.get_ticker = AsyncMock(
        return_value=({"bid": 100.0, "ask": 100.5, "last": 100.25, "baseVolume": 1_000_000}, True)
    )

    result = await svc.execute_market_order(
        agent_id=seeded_world, symbol="BTC/USDT", side="buy", size_usd=10.0,
    )
    assert result.success is False
    err = (result.error or "").lower()
    assert "unknown" in err, f"Expected halt-state-unknown reason; got {result.error!r}"
    assert "expected list" in err or "expected: list" in err or "list" in err
    assert svc._halt_state_unknown is True


@pytest.mark.asyncio
async def test_halt_unknown_auto_clears_on_recovery(
    db_factory, seeded_world, fake_redis_client, production_warden,
):
    """Critic Finding 1 (HIGH): the fail-closed latch MUST auto-clear on
    the next successful halt_checker call. A single transient blip cannot
    permanently halt the colony — that would be the DMS self-defeating-
    loop pattern in a new costume.

    Cycle:
      (a) Healthy halt_checker → trade approves
      (b) Switch to raising checker → trade rejected, _halt_state_unknown=True
      (c) Switch back to healthy → trade approves, _halt_state_unknown=False
    """
    from src.trading.fee_schedule import FeeSchedule
    from src.trading.slippage_model import SlippageModel

    state = {"mode": "healthy"}

    def _toggling_checker(**kw):
        if state["mode"] == "raise":
            raise RuntimeError("simulated transient Wire glitch")
        return []  # healthy: no active halts

    svc = PaperTradingService(
        db_session_factory=db_factory,
        price_cache=MagicMock(),
        slippage_model=SlippageModel(),
        fee_schedule=FeeSchedule(),
        warden=production_warden,
        redis_client=fake_redis_client,
        agora_service=None,
        halt_checker=_toggling_checker,
    )
    svc.price_cache.get_ticker = AsyncMock(
        return_value=({"bid": 100.0, "ask": 100.5, "last": 100.25, "baseVolume": 1_000_000}, True)
    )
    svc.price_cache.get_order_book = AsyncMock(
        return_value=({"asks": [[100.5, 100]], "bids": [[100.0, 100]]}, True)
    )
    svc.price_cache.is_stale = MagicMock(return_value=False)

    # (a) Healthy
    res_a = await svc.execute_market_order(
        agent_id=seeded_world, symbol="BTC/USDT", side="buy", size_usd=5.0,
    )
    assert res_a.success is True, f"Step (a) approval expected: {res_a.error!r}"
    assert svc._halt_state_unknown is False

    # (b) Raising
    state["mode"] = "raise"
    res_b = await svc.execute_market_order(
        agent_id=seeded_world, symbol="BTC/USDT", side="buy", size_usd=5.0,
    )
    assert res_b.success is False
    assert svc._halt_state_unknown is True

    # (c) Recovered
    state["mode"] = "healthy"
    res_c = await svc.execute_market_order(
        agent_id=seeded_world, symbol="BTC/USDT", side="buy", size_usd=5.0,
    )
    assert res_c.success is True, (
        f"Step (c-recovery) approval expected: {res_c.error!r}. If this "
        f"fails the latch became sticky — DMS-class regression."
    )
    assert svc._halt_state_unknown is False, (
        "Latch did not clear after halt_checker recovery — sticky regression."
    )


@pytest.mark.asyncio
async def test_operator_halt_blocks_only_matching_exchange(
    db_factory, seeded_world, fake_redis_client,
):
    """Critic Finding 2 (HIGH): per-coin-PER-EXCHANGE scope must work.
    Publish a halt with exchange='kraken'. Same coin BTC, different
    exchanges, opposite outcomes:
      - BTC trade on a kraken-configured service → REJECTED
      - BTC trade on a binance-configured service → APPROVED
    This proves both axes of scope are honored."""
    from src.trading.fee_schedule import FeeSchedule
    from src.trading.slippage_model import SlippageModel

    # Approving Warden so the gate under test is the halt scope, not Warden.
    approving_warden = MagicMock()
    approving_warden.evaluate_trade = AsyncMock(
        return_value={"status": "approved", "reason": "test", "request_id": "test"}
    )

    def _make_svc(exchange_name: str):
        svc = PaperTradingService(
            db_session_factory=db_factory,
            price_cache=MagicMock(),
            slippage_model=SlippageModel(),
            fee_schedule=FeeSchedule(),
            warden=approving_warden,
            redis_client=fake_redis_client,
            agora_service=None,
            halt_checker=list_active,  # the real Wire consumer
        )
        # Override the per-exchange config so each service identifies as a
        # different venue. Production sets this from `config.default_exchange`
        # at __init__; we override post-init for the test.
        svc.exchange = exchange_name
        svc.price_cache.get_ticker = AsyncMock(
            return_value=({"bid": 100.0, "ask": 100.5, "last": 100.25, "baseVolume": 1_000_000}, True)
        )
        svc.price_cache.get_order_book = AsyncMock(
            return_value=({"asks": [[100.5, 100]], "bids": [[100.0, 100]]}, True)
        )
        svc.price_cache.is_stale = MagicMock(return_value=False)
        return svc

    kraken_svc = _make_svc("kraken")
    binance_svc = _make_svc("binance")

    # Publish a Kraken-scoped BTC halt.
    publish_halt_for_event(
        event_id=200, coin="BTC", event_type="exchange_outage",
        severity=SEVERITY_CRITICAL,
        summary="SYNTHETIC: Kraken-scoped BTC outage",
        exchange="kraken",
    )

    # BTC on Kraken → must REJECT
    kraken_result = await kraken_svc.execute_market_order(
        agent_id=seeded_world, symbol="BTC/USDT", side="buy", size_usd=5.0,
    )
    assert kraken_result.success is False, (
        f"Kraken-scoped halt should block BTC trades on Kraken. Got: "
        f"{kraken_result.error!r}"
    )
    assert "kraken" in (kraken_result.error or "").lower() or \
           "trigger_event_id=200" in (kraken_result.error or ""), (
        f"Reject reason should cite the Kraken halt: {kraken_result.error!r}"
    )

    # BTC on Binance → must APPROVE (different exchange axis)
    binance_result = await binance_svc.execute_market_order(
        agent_id=seeded_world, symbol="BTC/USDT", side="buy", size_usd=5.0,
    )
    assert binance_result.success is True, (
        f"Kraken-scoped halt should NOT block BTC trades on Binance. Got: "
        f"{binance_result.error!r}. If this fails, the per-exchange axis "
        f"of scope has regressed — kickoff violation."
    )


@pytest.mark.asyncio
async def test_close_position_succeeds_during_active_halt(
    db_factory, seeded_world, production_trading_service,
):
    """Critic Finding 5 (LOW): close_position must bypass the halt check
    by design (cannot refuse to close a losing position because of a
    halt). This test enforces that contract rather than leaving it as
    a comment in the commit message.

    Open a BTC position, publish a BTC halt, attempt close — must succeed.
    """
    # Open a BTC position with no halt active.
    open_result = await production_trading_service.execute_market_order(
        agent_id=seeded_world, symbol="BTC/USDT", side="buy", size_usd=10.0,
    )
    assert open_result.success is True
    position_id = open_result.position_id
    assert position_id is not None

    # Now publish a BTC halt.
    publish_halt_for_event(
        event_id=300, coin="BTC", event_type="withdrawal_halt",
        severity=SEVERITY_CRITICAL,
        summary="SYNTHETIC: halt issued AFTER position was opened",
    )
    assert list_active(coin="BTC")  # halt is active

    # Attempt close — must succeed despite halt (close-by-design bypass).
    close_result = await production_trading_service.close_position(
        position_id=position_id, reason="halt-bypass-test",
    )
    assert close_result.success is True, (
        f"close_position must bypass the halt check (cannot refuse to close "
        f"a losing position because of a halt). Got: {close_result.error!r}. "
        f"If this fails, close was incorrectly halt-gated — verify the halt "
        f"check stays out of close_position / _do_close_position."
    )


def test_halt_consumer_present_in_both_market_and_limit_paths():
    """Source-inspection regression guard. Both execute paths must call
    `_check_operator_halt` before placing the order. A future cleanup
    that drops one of them shouldn't quietly land."""
    import inspect
    from src.trading import execution_service as svc_mod

    market_src = inspect.getsource(svc_mod.PaperTradingService.execute_market_order)
    limit_src = inspect.getsource(svc_mod.PaperTradingService.execute_limit_order)

    for src, name in ((market_src, "execute_market_order"),
                      (limit_src, "execute_limit_order")):
        assert "_check_operator_halt" in src, (
            f"{name} no longer calls _check_operator_halt — Wire severity-5 "
            f"halts would silently fail to gate trades on this path."
        )

    helper_src = inspect.getsource(svc_mod.PaperTradingService._check_operator_halt)
    assert "halt_checker" in helper_src, "halt_checker reference removed"
    assert "_raise_halt_block_alert" in helper_src, (
        "Loud alert call missing from halt-block branch"
    )
    assert "_raise_halt_checker_missing_alert" in helper_src, (
        "Defense-in-depth alert missing from halt-checker-None branch"
    )
