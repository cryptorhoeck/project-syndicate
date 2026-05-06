"""
Subsystems F + G — Strategist/Critic Archive helper wiring tests.

Closes WIRING_AUDIT_REPORT.md F (Strategist Archive helper) and G
(Critic Archive helper). Both helpers existed and were tested in
isolation but were NEVER constructed in production. This file's
load-bearing tests prove the two-direction integration:
  (a) ContextAssembler builds and uses the helper for the prefetch
      slice
  (b) Action handler + DB queue route the agent's `query_archive`
      deep-dive through to next-cycle delivery

Per the directive's CRITICAL TESTS: production-path tests
(test_*_actually_*_in_production_path) are non-negotiable. They
construct ContextAssembler / OutputValidator / ActionExecutor via
the real classes, not mocks of them.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from typing import Optional
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.agents.action_executor import ActionExecutor
from src.agents.context_assembler import ContextAssembler
from src.agents.output_validator import OutputValidator, ValidationFailure
from src.agents.roles import CRITIC_ACTIONS, STRATEGIST_ACTIONS
from src.common.models import Agent, Base, SystemState
import src.wire.models  # noqa: F401  — register wire tables on Base.metadata
from src.wire.integration.agent_context import (
    build_critic_archive_helper,
    build_strategist_archive_helper,
)
from src.wire.models import ArchiveQueryResult as ArchiveQueryResultRow
from src.wire.models import WireEvent


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _restore_event_loop_after_test():
    """Same pattern as the regime-review and eval-engine fixes."""
    yield
    try:
        asyncio.set_event_loop(asyncio.new_event_loop())
    except Exception:
        pass


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
def session(db_factory):
    s = db_factory()
    try:
        yield s
    finally:
        s.close()


@pytest.fixture
def seeded_world(session):
    """SystemState + Genesis row + Strategist + Critic + Scout +
    Operator. Returns dict mapping role → agent_id for tests."""
    session.add(SystemState(
        total_treasury=1000.0, peak_treasury=1000.0,
        current_regime="bull", active_agent_count=3, alert_status="green",
    ))
    session.add(Agent(
        id=0, name="Genesis", type="genesis", status="active",
        generation=0, capital_allocated=0.0, capital_current=0.0,
    ))
    strategist = Agent(
        name="Strategist-A", type="strategist", status="active",
        generation=1, capital_allocated=200.0, capital_current=200.0,
        cash_balance=200.0, total_equity=200.0,
        watched_markets=["BTC", "ETH"],
        thinking_budget_daily=0.5,
    )
    critic = Agent(
        name="Critic-A", type="critic", status="active",
        generation=1, capital_allocated=200.0, capital_current=200.0,
        cash_balance=200.0, total_equity=200.0,
        watched_markets=["BTC", "ETH"],
        thinking_budget_daily=0.5,
    )
    scout = Agent(
        name="Scout-A", type="scout", status="active",
        generation=1, capital_allocated=100.0, capital_current=100.0,
        cash_balance=100.0, total_equity=100.0,
        watched_markets=["BTC"],
        thinking_budget_daily=0.5,
    )
    operator = Agent(
        name="Operator-A", type="operator", status="active",
        generation=1, capital_allocated=200.0, capital_current=200.0,
        cash_balance=200.0, total_equity=200.0,
        watched_markets=["BTC"],
        thinking_budget_daily=0.5,
    )
    for a in (strategist, critic, scout, operator):
        session.add(a)
    session.commit()
    return {
        "strategist": strategist.id,
        "critic": critic.id,
        "scout": scout.id,
        "operator": operator.id,
    }


def _seed_wire_events(session, *, fixed_now: datetime) -> None:
    """Seed a mix of severity-3+ events:
      - 3 BTC events (different severities)
      - 2 ETH events
      - 1 SOL event (off-watch — should NOT appear in prefetch)
      - 1 macro event (no coin — should appear)
      - 1 BTC severity-2 event (below threshold — excluded)
    """
    rows = [
        ("BTC-1", "BTC", 5, "exchange_outage", 30),
        ("BTC-2", "BTC", 4, "withdrawal_halt", 60),
        ("BTC-3", "BTC", 3, "funding_extreme", 90),
        ("ETH-1", "ETH", 4, "tvl_drop", 45),
        ("ETH-2", "ETH", 3, "whale_transfer", 120),
        ("SOL-1", "SOL", 4, "chain_halt", 50),
        ("MACRO-1", None, 4, "macro_calendar", 75),
        ("BTC-LOW", "BTC", 2, "other", 10),
    ]
    for canonical, coin, sev, etype, mins_ago in rows:
        session.add(WireEvent(
            canonical_hash=canonical,
            coin=coin,
            event_type=etype,
            severity=sev,
            summary=f"synthetic {etype} for {coin or 'macro'}",
            occurred_at=fixed_now - timedelta(minutes=mins_ago),
            digested_at=fixed_now - timedelta(minutes=mins_ago),
            published_to_ticker=True,
        ))
    session.commit()


def _make_assembler(session) -> ContextAssembler:
    return ContextAssembler(session, token_budget=3000)


# ---------------------------------------------------------------------------
# Tests 8 & 9: prefetch slice present
# ---------------------------------------------------------------------------


def test_strategist_pre_fetch_slice_present_in_priority_context(
    session, seeded_world,
):
    """Strategist's priority context contains the formatted prefetch
    slice with 5 most recent severity-3+ events filtered to
    watched_markets + macro."""
    fixed_now = datetime.now(timezone.utc)
    _seed_wire_events(session, fixed_now=fixed_now)

    strategist = session.get(Agent, seeded_world["strategist"])
    assembler = _make_assembler(session)
    text = assembler._build_priority_context(strategist, token_budget=3000)

    assert "RECENT WIRE EVENTS (last 24h, severity 3+)" in text, (
        f"Strategist did not receive the Wire Archive prefetch slice. "
        f"Priority context (first 800 chars):\n{text[:800]}"
    )
    # BTC + ETH + macro should appear; SOL (off-watch) and BTC-LOW
    # (sev<3) should NOT.
    assert "[BTC]" in text
    assert "[ETH]" in text
    assert "[macro]" in text
    assert "[SOL]" not in text
    # Sev-2 event excluded.
    assert "BTC-LOW" not in text


def test_critic_pre_fetch_slice_present_in_priority_context(
    session, seeded_world,
):
    """Same shape for Critic — same filter (watched_markets + macro)."""
    fixed_now = datetime.now(timezone.utc)
    _seed_wire_events(session, fixed_now=fixed_now)

    critic = session.get(Agent, seeded_world["critic"])
    assembler = _make_assembler(session)
    text = assembler._build_priority_context(critic, token_budget=3000)

    assert "RECENT WIRE EVENTS (last 24h, severity 3+)" in text
    assert "[BTC]" in text
    assert "[ETH]" in text
    assert "[macro]" in text
    assert "[SOL]" not in text


# ---------------------------------------------------------------------------
# Test 10: prefetch must NOT consume Critic free_budget
# ---------------------------------------------------------------------------


def test_pre_fetch_does_not_consume_critic_free_budget(session, seeded_world):
    """Critic helper has free_budget=3 for agent-initiated queries.
    Prefetch reads (system-initiated, role-aware) MUST NOT decrement
    that counter — locked by helper docstring contract."""
    fixed_now = datetime.now(timezone.utc)
    _seed_wire_events(session, fixed_now=fixed_now)

    critic_id = seeded_world["critic"]
    helper = build_critic_archive_helper(
        session, agent_id=critic_id, free_budget=3,
    )

    # Two prefetch invocations.
    helper.prefetch(watched_markets=["BTC", "ETH"], limit=5)
    helper.prefetch(watched_markets=["BTC"], limit=3)

    # All 3 free-budget agent-initiated queries should still register
    # as free.
    for i in range(3):
        result = helper(coin="BTC", lookback_hours=24)
        assert result.free_query is True, (
            f"Agent-initiated query {i+1} was charged after prefetch — "
            f"prefetch consumed the free_budget. Cost: "
            f"{result.token_cost}"
        )

    # 4th agent-initiated query MUST charge.
    fourth = helper(coin="BTC", lookback_hours=24)
    assert fourth.free_query is False
    assert fourth.token_cost > 0


# ---------------------------------------------------------------------------
# Test 11+12: query_archive action handler (Strategist)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_query_archive_action_writes_pending_row_for_strategist(
    session, seeded_world,
):
    """Strategist emits query_archive → handler writes
    archive_query_results row with status='pending' and the agent's
    query/lookback/max_results."""
    fixed_now = datetime.now(timezone.utc)
    _seed_wire_events(session, fixed_now=fixed_now)

    strategist = session.get(Agent, seeded_world["strategist"])
    helper = build_strategist_archive_helper(
        session, agent_id=int(strategist.id),
    )

    executor = ActionExecutor(session)
    executor.archive_helper = helper

    parsed = {
        "action": {
            "type": "query_archive",
            "params": {
                "query": "BTC funding rate trends past week",
                "lookback_hours": 168,
                "max_results": 20,
            },
        }
    }
    result = await executor.execute(strategist, parsed)
    assert result.success, f"handler failed: {result.details!r}"

    rows = session.execute(
        select(ArchiveQueryResultRow).where(
            ArchiveQueryResultRow.requesting_agent_id == strategist.id,
        )
    ).scalars().all()
    assert len(rows) == 1
    row = rows[0]
    assert row.status == "pending"
    assert row.query_text == "BTC funding rate trends past week"
    assert row.lookback_hours == 168
    assert row.max_results == 20
    assert row.attempt_count == 0
    assert row.last_error is None


@pytest.mark.asyncio
async def test_query_archive_action_charges_strategist(session, seeded_world):
    """Every Strategist query_archive call charges (non-zero
    token_cost recorded in result_payload)."""
    fixed_now = datetime.now(timezone.utc)
    _seed_wire_events(session, fixed_now=fixed_now)

    strategist = session.get(Agent, seeded_world["strategist"])
    helper = build_strategist_archive_helper(
        session, agent_id=int(strategist.id),
    )
    executor = ActionExecutor(session)
    executor.archive_helper = helper

    parsed = {
        "action": {
            "type": "query_archive",
            "params": {"query": "test", "lookback_hours": 24, "max_results": 10},
        }
    }
    result = await executor.execute(strategist, parsed)
    assert result.success
    assert result.cost > 0, (
        f"Strategist query_archive returned cost={result.cost}; "
        f"every Strategist query must charge."
    )

    row = session.execute(select(ArchiveQueryResultRow)).scalar_one()
    payload = row.result_payload or {}
    assert payload.get("free_query") is False
    assert payload.get("token_cost", 0) > 0


# ---------------------------------------------------------------------------
# Tests 13-15: Critic free_budget mechanics
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_query_archive_action_first_three_free_for_critic(
    session, seeded_world,
):
    """Critic emits 3 query_archive actions with the SAME helper —
    all 3 must register as free (free_budget=3)."""
    fixed_now = datetime.now(timezone.utc)
    _seed_wire_events(session, fixed_now=fixed_now)

    critic = session.get(Agent, seeded_world["critic"])
    helper = build_critic_archive_helper(
        session, agent_id=int(critic.id), free_budget=3,
    )
    executor = ActionExecutor(session)
    executor.archive_helper = helper

    for i in range(3):
        parsed = {
            "action": {
                "type": "query_archive",
                "params": {
                    "query": f"critic-q-{i}",
                    "lookback_hours": 24,
                    "max_results": 5,
                },
            }
        }
        result = await executor.execute(critic, parsed)
        assert result.success, f"query {i+1} failed: {result.details!r}"
        assert result.cost == 0, (
            f"Critic query {i+1}/3 was charged (cost={result.cost}); "
            f"first 3 should be free."
        )

    rows = session.execute(
        select(ArchiveQueryResultRow).order_by(ArchiveQueryResultRow.id)
    ).scalars().all()
    assert len(rows) == 3
    for row in rows:
        payload = row.result_payload or {}
        assert payload.get("free_query") is True


@pytest.mark.asyncio
async def test_query_archive_action_charges_critic_after_third(
    session, seeded_world,
):
    """4th critic query in same cycle (same helper) — MUST charge."""
    fixed_now = datetime.now(timezone.utc)
    _seed_wire_events(session, fixed_now=fixed_now)

    critic = session.get(Agent, seeded_world["critic"])
    helper = build_critic_archive_helper(
        session, agent_id=int(critic.id), free_budget=3,
    )
    executor = ActionExecutor(session)
    executor.archive_helper = helper

    charged_costs: list[float] = []
    for i in range(4):
        parsed = {
            "action": {
                "type": "query_archive",
                "params": {
                    "query": f"q-{i}", "lookback_hours": 24, "max_results": 5,
                },
            }
        }
        result = await executor.execute(critic, parsed)
        assert result.success
        charged_costs.append(result.cost)

    assert charged_costs[0] == 0
    assert charged_costs[1] == 0
    assert charged_costs[2] == 0
    assert charged_costs[3] > 0, (
        f"4th critic query should charge; got cost={charged_costs[3]}. "
        f"All costs: {charged_costs!r}"
    )


@pytest.mark.asyncio
async def test_critic_free_budget_resets_per_cycle(session, seeded_world):
    """Cycle 1: 4 queries (1 charged). Cycle 2: fresh helper, 3
    queries should all be free."""
    fixed_now = datetime.now(timezone.utc)
    _seed_wire_events(session, fixed_now=fixed_now)

    critic = session.get(Agent, seeded_world["critic"])

    # Cycle 1 — exhaust free budget plus one charged.
    helper1 = build_critic_archive_helper(
        session, agent_id=int(critic.id), free_budget=3,
    )
    executor1 = ActionExecutor(session)
    executor1.archive_helper = helper1
    for i in range(4):
        parsed = {
            "action": {
                "type": "query_archive",
                "params": {"query": f"c1-{i}", "lookback_hours": 24, "max_results": 5},
            }
        }
        await executor1.execute(critic, parsed)

    # Cycle 2 — fresh helper, 3 fresh free queries.
    helper2 = build_critic_archive_helper(
        session, agent_id=int(critic.id), free_budget=3,
    )
    executor2 = ActionExecutor(session)
    executor2.archive_helper = helper2
    for i in range(3):
        parsed = {
            "action": {
                "type": "query_archive",
                "params": {"query": f"c2-{i}", "lookback_hours": 24, "max_results": 5},
            }
        }
        result = await executor2.execute(critic, parsed)
        assert result.cost == 0, (
            f"Cycle 2 query {i+1}/3 was charged (cost={result.cost}); "
            f"fresh helper must reset free_budget."
        )


# ---------------------------------------------------------------------------
# Test 16: pending consumption + delivered transition
# ---------------------------------------------------------------------------


def test_pending_archive_results_consumed_into_priority_context(
    session, seeded_world,
):
    """Direct-insert a pending archive_query_results row, invoke
    ContextAssembler._build_priority_context, assert the result is
    rendered AND the row flips to 'delivered'."""
    strategist = session.get(Agent, seeded_world["strategist"])
    row = ArchiveQueryResultRow(
        requesting_agent_id=int(strategist.id),
        query_text="prior cycle query",
        lookback_hours=24,
        max_results=5,
        result_payload={
            "events": [
                {
                    "id": 1, "coin": "BTC", "severity": 4,
                    "event_type": "funding_extreme",
                    "summary": "BTC funding 0.21% on Binance",
                    "occurred_at": datetime.now(timezone.utc).isoformat(),
                },
            ],
            "token_cost": 50, "free_query": False, "metadata": {},
        },
        status="pending",
    )
    session.add(row)
    session.commit()
    row_id = row.id

    assembler = _make_assembler(session)
    text = assembler._build_priority_context(strategist, token_budget=3000)

    assert "PRIOR ARCHIVE QUERY RESULTS" in text, (
        f"Pending archive result was not rendered into priority context. "
        f"Text head:\n{text[:600]}"
    )
    assert "prior cycle query" in text
    assert "BTC funding 0.21%" in text

    session.expire_all()
    after = session.get(ArchiveQueryResultRow, row_id)
    assert after.status == "delivered", (
        f"Row was rendered but not marked delivered. "
        f"status={after.status!r}"
    )
    assert after.delivered_at is not None


# ---------------------------------------------------------------------------
# Test 17: failed rows are not consumed
# ---------------------------------------------------------------------------


def test_failed_archive_query_does_not_block_next_cycle(session, seeded_world):
    """A 'failed' archive_query_results row stays in the table as a
    historical record but is NOT rendered into priority context."""
    strategist = session.get(Agent, seeded_world["strategist"])
    failed_row = ArchiveQueryResultRow(
        requesting_agent_id=int(strategist.id),
        query_text="this query failed irrecoverably",
        lookback_hours=24,
        max_results=5,
        result_payload=None,
        status="failed",
        attempt_count=3,
        last_error="RuntimeError: synthetic",
    )
    session.add(failed_row)
    session.commit()

    assembler = _make_assembler(session)
    text = assembler._build_priority_context(strategist, token_budget=3000)

    assert "this query failed irrecoverably" not in text, (
        f"Failed row was leaked into priority context: head:\n{text[:600]}"
    )
    assert "PRIOR ARCHIVE QUERY RESULTS" not in text, (
        "PRIOR ARCHIVE QUERY RESULTS section rendered for a 'failed' row"
    )

    # Row should still exist with status='failed'.
    session.expire_all()
    after = session.get(ArchiveQueryResultRow, failed_row.id)
    assert after.status == "failed"


# ---------------------------------------------------------------------------
# Test 18: validator rejects query_archive for Scout/Operator
# ---------------------------------------------------------------------------


def test_query_archive_rejected_for_scout_or_operator():
    """OutputValidator's action-space gate rejects query_archive for
    Scout and Operator with retryable=False (hallucinated action =
    no retry)."""
    validator = OutputValidator()
    raw = json.dumps({
        "situation": "test",
        "confidence": {"score": 5, "reasoning": "test"},
        "recent_pattern": "test",
        "action": {
            "type": "query_archive",
            "params": {
                "query": "test", "lookback_hours": 24, "max_results": 5,
            },
        },
        "reasoning": "test",
        "self_note": "test",
    })

    for role in ("scout", "operator"):
        result = validator.validate(role, raw)
        assert result.passed is False, (
            f"validator passed query_archive for {role!r}: {result!r}"
        )
        assert result.failure_type == ValidationFailure.INVALID_ACTION
        assert result.retryable is False

    # Sanity: validator passes for Strategist + Critic.
    for role in ("strategist", "critic"):
        result = validator.validate(role, raw)
        assert result.passed is True, (
            f"validator rejected query_archive for {role!r}: {result!r}"
        )


# ---------------------------------------------------------------------------
# CRITICAL TESTS — production-path proofs
# ---------------------------------------------------------------------------


def test_strategist_actually_receives_archive_slice_in_production_path(
    session, seeded_world,
):
    """LOAD-BEARING. Constructs ContextAssembler via the real
    constructor (not mocks of it), runs the real
    `_build_priority_context` against real seeded WireEvents,
    asserts the formatted slice appears in the rendered output.

    The audit's risk for subsystem F was that Strategists made plan
    decisions on a subset of available intelligence. This test
    proves the prefetch is now actually running in the production
    code path."""
    fixed_now = datetime.now(timezone.utc)
    _seed_wire_events(session, fixed_now=fixed_now)
    strategist = session.get(Agent, seeded_world["strategist"])

    # Production constructor.
    assembler = ContextAssembler(session, token_budget=3000)
    rendered = assembler._build_priority_context(
        strategist, token_budget=3000,
    )

    assert "RECENT WIRE EVENTS (last 24h, severity 3+)" in rendered
    # At least one in-watch event must be present.
    assert any(
        marker in rendered
        for marker in ("[BTC]", "[ETH]", "[macro]")
    )


def test_critic_actually_receives_archive_slice_in_production_path(
    session, seeded_world,
):
    """LOAD-BEARING. Same shape for Critic — the audit's subsystem G
    risk was Critics reviewing plans without macro/funding-rate
    context."""
    fixed_now = datetime.now(timezone.utc)
    _seed_wire_events(session, fixed_now=fixed_now)
    critic = session.get(Agent, seeded_world["critic"])

    assembler = ContextAssembler(session, token_budget=3000)
    rendered = assembler._build_priority_context(critic, token_budget=3000)

    assert "RECENT WIRE EVENTS (last 24h, severity 3+)" in rendered
    assert any(
        marker in rendered
        for marker in ("[BTC]", "[ETH]", "[macro]")
    )


def test_query_archive_action_actually_invokable_in_production_validator():
    """LOAD-BEARING. Production OutputValidator (constructed via the
    real class) accepts query_archive for Strategist/Critic and
    rejects for Scout/Operator. Without this, an agent's well-formed
    query_archive could be silently rejected as INVALID_ACTION even
    though it was added to the action map."""
    validator = OutputValidator()  # production constructor
    raw_template = {
        "situation": "test",
        "confidence": {"score": 5, "reasoning": "test"},
        "recent_pattern": "test",
        "action": {
            "type": "query_archive",
            "params": {
                "query": "test", "lookback_hours": 24, "max_results": 5,
            },
        },
        "reasoning": "test",
        "self_note": "test",
    }
    raw = json.dumps(raw_template)

    for role in ("strategist", "critic"):
        result = validator.validate(role, raw)
        assert result.passed is True, (
            f"production validator rejected query_archive for {role!r}: "
            f"{result.failure_detail}"
        )

    for role in ("scout", "operator"):
        result = validator.validate(role, raw)
        assert result.passed is False, (
            f"production validator passed query_archive for {role!r}"
        )
        assert result.retryable is False


@pytest.mark.asyncio
async def test_archive_query_pending_row_lifecycle(session, seeded_world):
    """Full producer-to-consumer cycle: Strategist emits a query →
    ActionExecutor writes pending row → next cycle's
    ContextAssembler consumes → row marked delivered."""
    fixed_now = datetime.now(timezone.utc)
    _seed_wire_events(session, fixed_now=fixed_now)
    strategist = session.get(Agent, seeded_world["strategist"])

    # === CYCLE N: agent emits query_archive ===
    helper = build_strategist_archive_helper(
        session, agent_id=int(strategist.id),
    )
    executor = ActionExecutor(session)
    executor.archive_helper = helper

    parsed = {
        "action": {
            "type": "query_archive",
            "params": {
                "query": "BTC funding rate week",
                "lookback_hours": 168, "max_results": 10,
            },
        }
    }
    result = await executor.execute(strategist, parsed)
    assert result.success

    # State after cycle N: one pending row.
    pending_after_n = session.execute(
        select(ArchiveQueryResultRow).where(
            ArchiveQueryResultRow.requesting_agent_id == strategist.id,
            ArchiveQueryResultRow.status == "pending",
        )
    ).scalars().all()
    assert len(pending_after_n) == 1
    pending_id = pending_after_n[0].id

    # === CYCLE N+1: ContextAssembler consumes ===
    assembler = ContextAssembler(session, token_budget=3000)
    rendered = assembler._build_priority_context(strategist, token_budget=3000)

    assert "BTC funding rate week" in rendered, (
        f"Producer-to-consumer lifecycle broken — query text missing "
        f"from N+1 priority context. Head:\n{rendered[:600]}"
    )

    session.expire_all()
    final = session.get(ArchiveQueryResultRow, pending_id)
    assert final.status == "delivered"
    assert final.delivered_at is not None

    # No remaining pending.
    still_pending = session.execute(
        select(ArchiveQueryResultRow).where(
            ArchiveQueryResultRow.requesting_agent_id == strategist.id,
            ArchiveQueryResultRow.status == "pending",
        )
    ).scalars().all()
    assert still_pending == []


@pytest.mark.asyncio
async def test_no_other_role_can_use_query_archive_via_action_executor(
    session, seeded_world,
):
    """Defense-in-depth: even if Scout or Operator somehow got a
    query_archive action past the validator, the action handler
    must reject. Verifies the explicit role check inside the
    handler."""
    scout = session.get(Agent, seeded_world["scout"])
    operator = session.get(Agent, seeded_world["operator"])

    executor = ActionExecutor(session)
    # Attach a helper anyway — the handler must reject on role check
    # before consulting the helper.
    helper = build_strategist_archive_helper(
        session, agent_id=int(scout.id),
    )
    executor.archive_helper = helper

    parsed = {
        "action": {
            "type": "query_archive",
            "params": {
                "query": "test", "lookback_hours": 24, "max_results": 5,
            },
        }
    }
    for actor in (scout, operator):
        result = await executor.execute(actor, parsed)
        assert result.success is False, (
            f"action handler accepted query_archive for type={actor.type!r}"
        )
        assert "strategist" in (result.details or "").lower() \
               and "critic" in (result.details or "").lower()


# ---------------------------------------------------------------------------
# Source-inspection guard
# ---------------------------------------------------------------------------


def test_query_archive_in_action_dispatch_table():
    """If a future refactor drops the dispatch entry, this test
    fails. Same risk class as fix H's run_cycle source-inspection
    guard."""
    import inspect
    from src.agents import action_executor as ae_mod
    src = inspect.getsource(ae_mod.ActionExecutor._get_handler)
    assert '"query_archive": self._handle_query_archive' in src, (
        "ActionExecutor._get_handler no longer wires query_archive — "
        "subsystems F+G fix is silently disabled."
    )


def test_archive_helpers_share_same_instance_in_thinking_cycle():
    """If a future refactor breaks the ThinkingCycle wiring that
    shares one helper instance across context_assembler and
    action_executor, the Critic's free_budget mechanics break
    (prefetch and agent queries would use separate counters)."""
    import inspect
    from src.agents import thinking_cycle as tc_mod
    src = inspect.getsource(tc_mod.ThinkingCycle.run)
    # Both sides must be set, in the same scope, to the same name.
    assert "self.context_assembler.archive_helper = archive_helper" in src
    assert "self.action_executor.archive_helper = archive_helper" in src


# ---------------------------------------------------------------------------
# Critic iteration 2 Finding 1: queue lifecycle (cap + per-row attribution)
# ---------------------------------------------------------------------------


def test_archive_query_attempt_count_exact_at_failure_cap(
    session, seeded_world,
):
    """Run consumption cycles until status='failed'. attempt_count
    at the time of the flip must equal MAX_ATTEMPTS_ARCHIVE_QUERY
    (NOT MAX+1). Mirrors fix H's
    test_regime_review_exact_attempt_count_at_failure_cap."""
    from src.wire.constants import MAX_ATTEMPTS_ARCHIVE_QUERY
    assert MAX_ATTEMPTS_ARCHIVE_QUERY == 3

    strategist = session.get(Agent, seeded_world["strategist"])
    # Seed a row with a deliberately-malformed payload so render
    # raises every cycle. result_payload is JSON-typed; using a
    # non-dict value (a string) makes ``payload.get("events")``
    # raise.
    row = ArchiveQueryResultRow(
        requesting_agent_id=int(strategist.id),
        query_text="poison-pill query",
        lookback_hours=24, max_results=5,
        result_payload="this-is-not-a-dict",  # render will explode
        status="pending", attempt_count=0,
    )
    session.add(row)
    session.commit()
    row_id = row.id

    assembler = ContextAssembler(session, token_budget=3000)

    # Cycles 1..MAX: row stays pending, attempt_count increments.
    for cycle in range(MAX_ATTEMPTS_ARCHIVE_QUERY):
        assembler._consume_pending_archive_results(strategist)
        session.expire_all()
        cur = session.get(ArchiveQueryResultRow, row_id)
        assert cur.status == "pending", (
            f"row flipped to {cur.status!r} on cycle {cycle+1}/"
            f"{MAX_ATTEMPTS_ARCHIVE_QUERY} — premature failure"
        )
        assert cur.attempt_count == cycle + 1, (
            f"cycle {cycle+1}: attempt_count={cur.attempt_count}, "
            f"expected {cycle+1}"
        )

    # Cycle MAX+1: pre-flip pass should fire. attempt_count stays
    # at the cap exactly — NO further increment past MAX.
    assembler._consume_pending_archive_results(strategist)
    session.expire_all()
    final = session.get(ArchiveQueryResultRow, row_id)
    assert final.status == "failed", (
        f"row not flipped after {MAX_ATTEMPTS_ARCHIVE_QUERY} attempts. "
        f"status={final.status!r}"
    )
    assert final.attempt_count == MAX_ATTEMPTS_ARCHIVE_QUERY, (
        f"Off-by-one bug: at the time of the flip, attempt_count "
        f"should equal MAX ({MAX_ATTEMPTS_ARCHIVE_QUERY}), got "
        f"{final.attempt_count}. The cap must NOT allow an extra "
        f"attempt past the limit."
    )
    assert final.last_error is not None, (
        "row at the cap must carry a non-empty last_error"
    )
    # last_error is either the most-recent per-row render error
    # (preserved by the cap path's `if not last_error:` guard) OR
    # the generic "exceeded max" message (if no per-row stamp ever
    # happened). Both are valid terminal states.
    msg = final.last_error.lower()
    assert ("exceeded max" in msg) or ("attributeerror" in msg) or ("error" in msg), (
        f"last_error not informative on terminal flip: "
        f"{final.last_error!r}"
    )


def test_archive_query_failed_row_excluded_from_consumption(
    session, seeded_world,
):
    """A row at status='failed' with attempt_count=MAX must not be
    selected by the consume pass — its attempt_count must NOT
    increment further."""
    from src.wire.constants import MAX_ATTEMPTS_ARCHIVE_QUERY

    strategist = session.get(Agent, seeded_world["strategist"])
    row = ArchiveQueryResultRow(
        requesting_agent_id=int(strategist.id),
        query_text="already-failed query",
        lookback_hours=24, max_results=5,
        result_payload=None,
        status="failed", attempt_count=MAX_ATTEMPTS_ARCHIVE_QUERY,
        last_error="terminal — exceeded retry cap",
    )
    session.add(row)
    session.commit()
    row_id = row.id

    assembler = ContextAssembler(session, token_budget=3000)
    assembler._consume_pending_archive_results(strategist)

    session.expire_all()
    after = session.get(ArchiveQueryResultRow, row_id)
    assert after.status == "failed"
    assert after.attempt_count == MAX_ATTEMPTS_ARCHIVE_QUERY, (
        f"'failed' row was re-incremented to "
        f"{after.attempt_count}; consume SELECT must exclude it."
    )


def test_archive_query_per_row_error_attribution(session, seeded_world):
    """Batch of 5 pending rows; ONE has a malformed payload. Per-row
    try/except inside the consume loop must stamp last_error on
    THAT row only — the other 4 keep last_error=NULL."""
    strategist = session.get(Agent, seeded_world["strategist"])

    bad_row_id: Optional[int] = None
    good_row_ids: list[int] = []
    for i in range(5):
        if i == 2:
            payload = "not-a-dict"  # render explodes
        else:
            payload = {
                "events": [], "token_cost": 0,
                "free_query": False, "metadata": {},
            }
        row = ArchiveQueryResultRow(
            requesting_agent_id=int(strategist.id),
            query_text=f"per-row-{i}",
            lookback_hours=24, max_results=5,
            result_payload=payload,
            status="pending", attempt_count=0,
        )
        session.add(row)
        session.flush()
        if i == 2:
            bad_row_id = row.id
        else:
            good_row_ids.append(row.id)
    session.commit()

    assembler = ContextAssembler(session, token_budget=3000)
    rendered = assembler._consume_pending_archive_results(strategist)

    session.expire_all()
    bad = session.get(ArchiveQueryResultRow, bad_row_id)
    assert bad.last_error is not None, (
        "offending row did not receive a last_error stamp"
    )
    assert bad.status == "pending", (
        f"offending row flipped prematurely to {bad.status!r}"
    )

    for gid in good_row_ids:
        good = session.get(ArchiveQueryResultRow, gid)
        assert good.last_error is None, (
            f"innocent row {gid} got a last_error stamp: "
            f"{good.last_error!r}"
        )
        assert good.status == "delivered", (
            f"innocent row {gid} not delivered: {good.status!r}"
        )

    # The 4 successful rows must appear in the rendered output.
    for i in (0, 1, 3, 4):
        assert f"per-row-{i}" in rendered


# ---------------------------------------------------------------------------
# Critic iteration 2 Finding 2: validator role-rejection (explicit per-role)
# ---------------------------------------------------------------------------


def _query_archive_validator_input() -> str:
    return json.dumps({
        "situation": "test",
        "confidence": {"score": 5, "reasoning": "test"},
        "recent_pattern": "test",
        "action": {
            "type": "query_archive",
            "params": {
                "query": "test", "lookback_hours": 24, "max_results": 5,
            },
        },
        "reasoning": "test",
        "self_note": "test",
    })


def test_validator_rejects_query_archive_for_scout():
    """Validator step-3 action-space gate consults SCOUT_ACTIONS;
    query_archive is NOT in that set → INVALID_ACTION with
    retryable=False."""
    validator = OutputValidator()
    result = validator.validate("scout", _query_archive_validator_input())
    assert result.passed is False
    assert result.failure_type == ValidationFailure.INVALID_ACTION
    assert result.retryable is False
    # Rejection must come from the validator (action-space check),
    # NOT from any executor-level defense check.
    assert "query_archive" in (result.failure_detail or "")
    assert "scout action space" in (result.failure_detail or "").lower()


def test_validator_rejects_query_archive_for_operator():
    """Same shape for Operator."""
    validator = OutputValidator()
    result = validator.validate("operator", _query_archive_validator_input())
    assert result.passed is False
    assert result.failure_type == ValidationFailure.INVALID_ACTION
    assert result.retryable is False
    assert "operator action space" in (result.failure_detail or "").lower()


def test_validator_accepts_query_archive_for_strategist():
    """Strategist's action-space includes query_archive → validation
    passes (no INVALID_ACTION)."""
    validator = OutputValidator()
    result = validator.validate(
        "strategist", _query_archive_validator_input(),
    )
    assert result.passed is True, (
        f"validator rejected query_archive for strategist: "
        f"{result.failure_type} {result.failure_detail!r}"
    )


def test_validator_accepts_query_archive_for_critic():
    """Same shape for Critic."""
    validator = OutputValidator()
    result = validator.validate("critic", _query_archive_validator_input())
    assert result.passed is True


# ---------------------------------------------------------------------------
# Critic iteration 2 Finding 3: prefetch failure path (Library reflection
# bug shape — visible degradation marker + escalation counter)
# ---------------------------------------------------------------------------


def test_prefetch_failure_returns_visible_degradation_marker(
    session, seeded_world,
):
    """Mock the helper's prefetch to raise. The slice MUST contain
    a visible "Wire Archive temporarily unavailable" marker so the
    agent sees it's running with degraded context — NOT an empty
    string (Library reflection bug shape)."""
    strategist = session.get(Agent, seeded_world["strategist"])

    class _BoomHelper:
        def __call__(self, **kwargs):
            return None
        def prefetch(self, **kwargs):
            raise RuntimeError("synthetic prefetch failure")

    assembler = ContextAssembler(session, token_budget=3000)
    assembler.archive_helper = _BoomHelper()
    text = assembler._build_archive_pre_fetch_slice(strategist)

    assert text != ""
    assert "Wire Archive temporarily unavailable" in text, (
        f"Degradation marker missing — agent would silently run "
        f"with reduced context. Got: {text!r}"
    )


def test_prefetch_failure_increments_counter(session, seeded_world):
    """Single failure increments the consecutive-failure counter."""
    strategist = session.get(Agent, seeded_world["strategist"])

    class _BoomHelper:
        def __call__(self, **kwargs):
            return None
        def prefetch(self, **kwargs):
            raise RuntimeError("boom")

    assembler = ContextAssembler(session, token_budget=3000)
    assembler.archive_helper = _BoomHelper()

    assert assembler._archive_prefetch_failure_count == 0
    assembler._build_archive_pre_fetch_slice(strategist)
    assert assembler._archive_prefetch_failure_count == 1


def test_prefetch_failure_three_consecutive_escalates(
    session, seeded_world, capsys,
):
    """Three consecutive failures → CRITICAL escalation log fires
    on the third with structured field
    `archive_prefetch_failure_escalated`."""
    import logging as _logging
    from src.wire.constants import ARCHIVE_PREFETCH_ESCALATION_THRESHOLD
    assert ARCHIVE_PREFETCH_ESCALATION_THRESHOLD == 3

    strategist = session.get(Agent, seeded_world["strategist"])

    class _BoomHelper:
        def __call__(self, **kwargs):
            return None
        def prefetch(self, **kwargs):
            raise RuntimeError("boom")

    log_records: list[_logging.LogRecord] = []

    class _Capture(_logging.Handler):
        def emit(self, record):
            log_records.append(record)

    handler = _Capture(level=_logging.CRITICAL)
    ca_logger = _logging.getLogger("src.agents.context_assembler")
    ca_logger.addHandler(handler)

    try:
        assembler = ContextAssembler(session, token_budget=3000)
        assembler.archive_helper = _BoomHelper()
        for _ in range(ARCHIVE_PREFETCH_ESCALATION_THRESHOLD):
            assembler._build_archive_pre_fetch_slice(strategist)
    finally:
        ca_logger.removeHandler(handler)

    escalations = [
        r for r in log_records
        if r.levelname == "CRITICAL"
        and "archive_prefetch_failure_escalated" in r.getMessage()
    ]
    assert escalations, (
        f"No CRITICAL escalation log after "
        f"{ARCHIVE_PREFETCH_ESCALATION_THRESHOLD} failures. records: "
        f"{[(r.levelname, r.getMessage()) for r in log_records]!r}"
    )
    rec = escalations[0]
    assert getattr(rec, "archive_prefetch_failure_escalated", None) is True
    assert getattr(rec, "consecutive_failures", None) == 3
    assert (
        getattr(rec, "threshold", None)
        == ARCHIVE_PREFETCH_ESCALATION_THRESHOLD
    )


def test_prefetch_failure_counter_resets_on_first_success(
    session, seeded_world,
):
    """Two failures, then a success → counter resets to 0."""
    fixed_now = datetime.now(timezone.utc)
    _seed_wire_events(session, fixed_now=fixed_now)

    strategist = session.get(Agent, seeded_world["strategist"])

    state = {"mode": "boom"}

    class _ToggleHelper:
        def __init__(self, sess, agent_id):
            self._real = build_strategist_archive_helper(
                sess, agent_id=agent_id,
            )
        def __call__(self, **kwargs):
            return self._real(**kwargs)
        def prefetch(self, **kwargs):
            if state["mode"] == "boom":
                raise RuntimeError("boom")
            return self._real.prefetch(**kwargs)

    assembler = ContextAssembler(session, token_budget=3000)
    assembler.archive_helper = _ToggleHelper(
        session, int(strategist.id),
    )

    assembler._build_archive_pre_fetch_slice(strategist)
    assembler._build_archive_pre_fetch_slice(strategist)
    assert assembler._archive_prefetch_failure_count == 2

    state["mode"] = "ok"
    assembler._build_archive_pre_fetch_slice(strategist)
    assert assembler._archive_prefetch_failure_count == 0, (
        f"Counter did not reset on first successful prefetch: "
        f"{assembler._archive_prefetch_failure_count}"
    )


def test_prefetch_failure_does_not_crash_agent_cycle(
    session, seeded_world,
):
    """Assembly must complete even when the prefetch raises. The
    agent receives the degradation marker; no exception
    propagates."""
    strategist = session.get(Agent, seeded_world["strategist"])

    class _BoomHelper:
        def __call__(self, **kwargs):
            return None
        def prefetch(self, **kwargs):
            raise RuntimeError("boom")

    assembler = ContextAssembler(session, token_budget=3000)
    assembler.archive_helper = _BoomHelper()

    text = assembler._build_priority_context(strategist, token_budget=3000)
    assert "Wire Archive temporarily unavailable" in text, (
        f"Priority context did not include the degradation marker. "
        f"head:\n{text[:600]}"
    )
