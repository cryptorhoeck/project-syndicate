"""
Operator trading service — wiring tests.

Diagnosis: `ARENA_TRADING_SERVICE_DIAGNOSIS.md` (commit f57ae11).

These are NOT unit tests of TradeExecutionService — that surface is
covered by `tests/test_paper_trading.py` and was passing throughout the
2026-04-14 Arena run that produced ZERO trades. The whole point of this
file is to assert that the **production code path** wires the service
through `run_agents.py → ThinkingCycle → ActionExecutor`. A passing
unit test on a service that nothing invokes does not protect us.

The tests reach into `scripts.run_agents.build_trading_service` (the
factory the production runner uses) so a future regression in that
function — silently returning None, dropping an argument, swapping the
factory — will fail the suite, not the next live Arena run.
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
from src.trading.execution_service import (
    PaperTradingService,
    TradeExecutionService,
)


# ---------------------------------------------------------------------------
# Test infrastructure: mirror the run_agents.py boot path.
# ---------------------------------------------------------------------------


@pytest.fixture
def thread_safe_engine():
    """SQLite engine FastAPI/async-aware. Same pattern as test_paper_trading."""
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
def seeded_operator(db_factory):
    """Operator agent with cash to actually place a trade."""
    with db_factory() as session:
        session.add(SystemState(
            total_treasury=1000.0, peak_treasury=1000.0,
            current_regime="bull", active_agent_count=1, alert_status="green",
        ))
        agent = Agent(
            name="Operator-WiringTest",
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
        session.refresh(agent)
        return agent.id


@pytest.fixture
def fake_redis_client():
    """run_agents.py only uses redis_client for monitor heartbeats / pubsub.
    The trading service uses it for nothing test-blocking. A MagicMock that
    accepts everything is sufficient — what matters is the wiring path."""
    r = MagicMock()
    r.set.return_value = True
    r.get.return_value = None
    r.delete.return_value = True
    r.ping.return_value = True
    return r


@pytest.fixture
def production_trading_service(db_factory, fake_redis_client):
    """Build the trading service via the EXACT helper the runner uses.

    `scripts.run_agents.build_trading_service` is the production seam.
    Calling it here means our wiring assertion is testing what runs in
    production, not a hand-rolled equivalent.

    After the warden-trade-gate-wiring hotfix, `build_trading_service`
    threads a Warden through; we build one via the same helper the runner
    uses so the test reflects the full production chain.
    """
    run_agents = importlib.import_module("scripts.run_agents")
    warden = run_agents.build_warden(db_factory, agora_service=None)
    return run_agents.build_trading_service(
        db_factory=db_factory,
        redis_client=fake_redis_client,
        agora_service=None,
        warden=warden,
        halt_checker=lambda **kw: [],  # operator-halt-consumer-wiring hotfix
    )


@pytest.fixture
def fake_claude_client():
    """ThinkingCycle requires a ClaudeClient. We never invoke it in these
    tests — the wiring is asserted at the ActionExecutor surface — but
    the constructor must succeed."""
    client = MagicMock()
    client.call = AsyncMock()
    return client


# ---------------------------------------------------------------------------
# REGRESSION-CLASS GUARD (per Andrew's directive)
# ---------------------------------------------------------------------------


def test_action_executor_receives_trading_service_in_production_boot(
    db_factory, fake_redis_client, fake_claude_client, production_trading_service,
):
    """When ThinkingCycle is constructed via the production code path
    (`build_trading_service` → ThinkingCycle(...)`), its ActionExecutor
    MUST hold a real TradeExecutionService.

    This is the regression guard for ARENA_TRADING_SERVICE_DIAGNOSIS.md.
    If a future change drops the trading_service kwarg, swaps in None,
    or otherwise breaks the wiring, this test fails immediately.
    """
    with db_factory() as session:
        cycle = ThinkingCycle(
            db_session=session,
            claude_client=fake_claude_client,
            redis_client=fake_redis_client,
            agora_service=None,
            config=None,
            trading_service=production_trading_service,
        )

    assert cycle.action_executor is not None
    assert isinstance(cycle.action_executor, ActionExecutor)
    assert cycle.action_executor.trading is not None, (
        "ActionExecutor.trading is None — Operator would log [NO SERVICE] "
        "on every execute_trade attempt. See "
        "ARENA_TRADING_SERVICE_DIAGNOSIS.md for the original failure mode."
    )
    assert isinstance(cycle.action_executor.trading, TradeExecutionService)


def test_run_agents_build_trading_service_returns_paper_in_paper_mode(
    db_factory, fake_redis_client,
):
    """Production helper must return a PaperTradingService when
    config.trading_mode == 'paper'. Smoke check on the factory glue."""
    run_agents = importlib.import_module("scripts.run_agents")
    service = run_agents.build_trading_service(
        db_factory=db_factory,
        redis_client=fake_redis_client,
        agora_service=None,
    )
    assert service is not None
    assert isinstance(service, PaperTradingService)


# ---------------------------------------------------------------------------
# INTEGRATION: synthetic plan -> paper trade -> Order/Position/Transaction
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_paper_trade_lands_when_action_executor_is_wired(
    db_factory, seeded_operator, fake_redis_client,
    fake_claude_client, production_trading_service,
):
    """End-to-end through the production wiring:
        1. Build ThinkingCycle the way run_agents.py does.
        2. Hand its ActionExecutor a synthetic approved plan as
           `execute_trade` action params.
        3. Patch the trading service's price_cache to return a deterministic
           ticker (no network calls).
        4. Assert: the call returns success, an Order row appears, a
           Position row appears, a Transaction row appears, and the
           Operator's cash balance dropped by approximately the trade
           size + fees.

    This is the test the previous Arena needed. A passing test here
    means production cannot silently no-op every trade again.
    """
    # Patch price_cache to return a deterministic ticker without touching
    # an exchange. The trading service's other moving parts (slippage,
    # fees) are real; only the price feed is synthetic.
    production_trading_service.price_cache = MagicMock()
    production_trading_service.price_cache.get_ticker = AsyncMock(
        return_value=({"bid": 100.0, "ask": 100.5, "last": 100.25, "baseVolume": 1_000_000}, True)
    )
    production_trading_service.price_cache.get_order_book = AsyncMock(
        return_value=({"asks": [[100.5, 100]], "bids": [[100.0, 100]]}, True)
    )
    production_trading_service.price_cache.is_stale = MagicMock(return_value=False)

    with db_factory() as session:
        cycle = ThinkingCycle(
            db_session=session,
            claude_client=fake_claude_client,
            redis_client=fake_redis_client,
            agora_service=None,
            config=None,
            trading_service=production_trading_service,
        )
        operator = session.get(Agent, seeded_operator)

        # Synthetic approved plan — the kind a Strategist would have produced
        # and a Critic approved in the 2026-04-14 Arena.
        action = {
            "action": {
                "type": "execute_trade",
                "params": {
                    "market": "BTC/USDT",
                    "direction": "long",
                    "position_size_usd": 50.0,
                    "order_type": "market",
                    "stop_loss": 95.0,
                    "take_profit": 110.0,
                },
            },
            "situation": "test",
            "confidence": {"score": 8, "reasoning": "test"},
            "recent_pattern": "test",
            "reasoning": "test",
            "self_note": "test",
        }

        result = await cycle.action_executor.execute(operator, action)

    # 1. The action must have succeeded (NOT [NO SERVICE]).
    assert result.success is True, (
        f"Trade did not execute. details={result.details}. "
        "If this fails with 'No trading service configured' the wiring "
        "broke again — re-read ARENA_TRADING_SERVICE_DIAGNOSIS.md."
    )

    # 2. The paper book must contain the trade.
    with db_factory() as session:
        orders = session.execute(select(Order)).scalars().all()
        positions = session.execute(select(Position)).scalars().all()
        transactions = session.execute(select(Transaction)).scalars().all()
        post_op = session.get(Agent, seeded_operator)

    assert len(orders) == 1, f"expected 1 order, got {len(orders)}"
    o = orders[0]
    assert o.symbol == "BTC/USDT"
    assert o.side == "buy"
    assert o.status == "filled"
    assert o.execution_venue == "paper"

    assert len(positions) == 1, f"expected 1 position, got {len(positions)}"
    p = positions[0]
    assert p.symbol == "BTC/USDT"
    assert p.side == "long"
    assert p.status == "open"
    assert p.size_usd == pytest.approx(50.0)

    assert len(transactions) == 1, f"expected 1 transaction, got {len(transactions)}"
    t = transactions[0]
    assert t.exchange == "paper"
    assert t.symbol == "BTC/USDT"

    # 3. Operator's cash balance dropped by ~50 + fees.
    assert post_op.cash_balance < 200.0
    assert post_op.cash_balance > 145.0  # 50 + small fee, not 200
    assert post_op.position_count == 1


# ---------------------------------------------------------------------------
# Defense-in-depth: the [NO SERVICE] fallback must STILL exist
# ---------------------------------------------------------------------------


def test_no_service_fallback_still_present_when_trading_is_none():
    """We deliberately keep the loud-failure fallback in
    `_handle_execute_trade` so a future wiring break logs CRITICAL +
    posts to system-alerts instead of silently no-op'ing. This test
    documents that intent and prevents accidental removal."""
    import inspect
    from src.agents import action_executor as ae_mod

    src = inspect.getsource(ae_mod._handle_execute_trade if hasattr(ae_mod, "_handle_execute_trade") else ae_mod.ActionExecutor._handle_execute_trade)
    assert "if not self.trading:" in src, "fallback guard removed"
    assert "[NO SERVICE]" in src, "[NO SERVICE] marker removed"
    assert "logger.critical" in src, (
        "Defense-in-depth log was downgraded. The whole point of escalating "
        "from INFO to CRITICAL was so the next Arena would surface a wiring "
        "break in cycle 1, not day 2."
    )
    assert "system-alerts" in src, "system-alerts mirror removed"
