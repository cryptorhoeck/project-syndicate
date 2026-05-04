"""
Subsystem T-subset fix — MaintenanceService.run_all wiring tests.

Closes WIRING_AUDIT_REPORT.md subsystem T-subset. The Arena's 3-day
backlog of stale opportunities was direct evidence that
`expire_stale_opportunities`, `cleanup_stale_plans`, and
`prune_terminated_agent_memory` were never invoked in production —
only `reset_daily_budgets` was wired (under a daily gate inside
`Genesis._maybe_run_hourly_maintenance`).

War Room iteration 1 chose Option B over the original directive's
literal: `run_all()` invokes ONLY the three hourly-safe methods
(opportunities/plans/memory). `reset_daily_budgets` stays at its
existing daily-gated call site because resetting it more often than
once per day would let agents consume up to 24× their intended
daily thinking budget.

Test surfaces:
  1. run_all() unit tests — three methods invoked, reset_daily_budgets
     NOT invoked, per-task try/except, redis_client threaded through.
  2. Genesis wiring tests — _maybe_run_hourly_maintenance fires
     run_all hourly, daily gate fires independently, reset_daily_budgets
     invoked at exactly one site.
  3. Production-path "actually runs" tests — insert stale rows,
     invoke production path, assert the work actually happened.
"""

from __future__ import annotations

import asyncio
import inspect
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.agents.maintenance import MaintenanceService
from src.common.models import Agent, Base, Opportunity, Plan, SystemState


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _restore_event_loop_after_test():
    """asyncio.run() closes the loop and clears the policy's loop ref;
    other suite tests still use the deprecated `asyncio.get_event_loop()`
    pattern. Restore after each test (matches the regime-review and
    eval-engine fixes' pattern)."""
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
def seeded_world(db_factory):
    """SystemState row + Genesis agents row + scout/strategist for
    Opportunity/Plan FK targets. Returns dict of seeded ids."""
    with db_factory() as session:
        session.add(SystemState(
            total_treasury=1000.0, peak_treasury=1000.0,
            current_regime="bull", active_agent_count=2, alert_status="green",
        ))
        session.add(Agent(
            id=0, name="Genesis", type="genesis", status="active",
            generation=0, capital_allocated=0.0, capital_current=0.0,
            strategy_summary="Immortal God Node",
            thinking_budget_daily=2.0,
            thinking_budget_used_today=1.5,
        ))
        session.add(Agent(
            name="Scout-1", type="scout", status="active",
            generation=1, capital_allocated=100.0, capital_current=100.0,
            cash_balance=100.0, total_equity=100.0,
            thinking_budget_daily=0.5,
            thinking_budget_used_today=0.3,
        ))
        session.add(Agent(
            name="Strategist-1", type="strategist", status="active",
            generation=1, capital_allocated=100.0, capital_current=100.0,
            cash_balance=100.0, total_equity=100.0,
            thinking_budget_daily=0.5,
            thinking_budget_used_today=0.4,
        ))
        session.add(Agent(
            name="Terminated-1", type="operator", status="terminated",
            generation=1, capital_allocated=0.0, capital_current=0.0,
            cash_balance=0.0, total_equity=0.0,
        ))
        session.commit()
        return {
            r.name: r.id for r in session.execute(select(Agent)).scalars().all()
        }


# ---------------------------------------------------------------------------
# 1. run_all() unit tests (directive items 4-8)
# ---------------------------------------------------------------------------


def test_run_all_invokes_three_hourly_safe_methods(db_factory):
    """run_all calls expire_stale_opportunities, cleanup_stale_plans,
    and prune_terminated_agent_memory exactly once each."""
    maint = MaintenanceService(db_factory)
    with patch.multiple(
        maint,
        expire_stale_opportunities=MagicMock(return_value=0),
        cleanup_stale_plans=MagicMock(return_value=0),
        prune_terminated_agent_memory=MagicMock(return_value=0),
    ):
        asyncio.run(maint.run_all())
        maint.expire_stale_opportunities.assert_called_once_with()
        maint.cleanup_stale_plans.assert_called_once_with()
        maint.prune_terminated_agent_memory.assert_called_once()


def test_run_all_does_not_call_reset_daily_budgets(db_factory):
    """LOAD-BEARING regression guard: if a future refactor adds
    reset_daily_budgets back into run_all, agents would lose 24x
    their daily thinking budget. Lock the contract behaviorally."""
    maint = MaintenanceService(db_factory)
    with patch.multiple(
        maint,
        expire_stale_opportunities=MagicMock(return_value=0),
        cleanup_stale_plans=MagicMock(return_value=0),
        prune_terminated_agent_memory=MagicMock(return_value=0),
        reset_daily_budgets=MagicMock(return_value=0),
    ):
        asyncio.run(maint.run_all())
        maint.reset_daily_budgets.assert_not_called()


def test_run_all_continues_when_one_method_raises(db_factory, caplog):
    """Per-task try/except: if expire_stale_opportunities raises,
    cleanup_stale_plans and prune_terminated_agent_memory still run.
    Failed task's count is 0 in the result dict."""
    import logging as _logging
    caplog.set_level(_logging.WARNING)

    maint = MaintenanceService(db_factory)
    with patch.multiple(
        maint,
        expire_stale_opportunities=MagicMock(
            side_effect=RuntimeError("synthetic failure"),
        ),
        cleanup_stale_plans=MagicMock(return_value=7),
        prune_terminated_agent_memory=MagicMock(return_value=2),
    ):
        result = asyncio.run(maint.run_all())

        # Other two methods ran despite the first raising.
        maint.cleanup_stale_plans.assert_called_once()
        maint.prune_terminated_agent_memory.assert_called_once()

    assert result == {
        "opportunities_expired": 0,  # failed -> 0
        "plans_cleaned": 7,
        "memory_pruned": 2,
    }

    # WARNING log captured the failed task name.
    failure_records = [
        r for r in caplog.records
        if "maintenance_task_failed" in r.getMessage()
        or getattr(r, "task", None) == "expire_stale_opportunities"
    ]
    assert failure_records, (
        f"Expected a WARNING for the failing task. Records: "
        f"{[(r.levelname, r.getMessage()) for r in caplog.records]!r}"
    )


def test_run_all_returns_per_task_counts(db_factory, seeded_world):
    """Happy path against a real (in-memory) DB. Insert stale data
    for all three task types; run_all reports the counts."""
    now = datetime.now(timezone.utc)
    scout_id = seeded_world["Scout-1"]
    strategist_id = seeded_world["Strategist-1"]
    with db_factory() as session:
        # 3 stale opportunities (status=new, expired in the past)
        for i in range(3):
            session.add(Opportunity(
                scout_agent_id=scout_id, scout_agent_name="Scout-1",
                market="BTC/USDT", signal_type="volume_breakout",
                details=f"stale-{i}",
                status="new",
                expires_at=now - timedelta(hours=1),
            ))
        # 2 stale plans (submitted >24h ago, no critic)
        for i in range(2):
            session.add(Plan(
                strategist_agent_id=strategist_id,
                strategist_agent_name="Strategist-1",
                plan_name=f"stale-plan-{i}", market="BTC/USDT",
                direction="long", entry_conditions="x", exit_conditions="y",
                thesis="z", status="submitted",
                submitted_at=now - timedelta(hours=25),
            ))
        session.commit()

    fake_redis = MagicMock()
    fake_redis.exists = MagicMock(return_value=False)  # nothing to prune
    fake_redis.delete = MagicMock(return_value=1)

    maint = MaintenanceService(db_factory)
    result = asyncio.run(maint.run_all(redis_client=fake_redis))

    assert result["opportunities_expired"] == 3
    assert result["plans_cleaned"] == 2
    assert result["memory_pruned"] == 0  # terminated agent has no key
    assert "budget_resets" not in result, (
        "run_all must NOT report a budget-reset count — that method "
        "is not invoked by run_all under the Option B contract."
    )


def test_run_all_passes_redis_client_to_prune_terminated_agent_memory(
    db_factory,
):
    """The optional redis_client parameter must be threaded through to
    prune_terminated_agent_memory (the only method that uses it)."""
    fake_redis = MagicMock()
    maint = MaintenanceService(db_factory)

    with patch.multiple(
        maint,
        expire_stale_opportunities=MagicMock(return_value=0),
        cleanup_stale_plans=MagicMock(return_value=0),
        prune_terminated_agent_memory=MagicMock(return_value=0),
    ):
        asyncio.run(maint.run_all(redis_client=fake_redis))
        maint.prune_terminated_agent_memory.assert_called_once_with(
            redis_client=fake_redis,
        )


# ---------------------------------------------------------------------------
# 2. Genesis wiring tests (directive items 9-11)
# ---------------------------------------------------------------------------


def _make_genesis_for_maintenance_test(db_factory):
    """Construct a real GenesisAgent for invoking
    `_maybe_run_hourly_maintenance` directly. Mocks only the
    collaborators that would otherwise need live Agora/Library/Economy
    (the maintenance code path is real and unmocked)."""
    import redis as redis_lib
    from src.common.config import config
    from src.genesis.genesis import GenesisAgent

    # Memurai gate.
    sanity = redis_lib.Redis.from_url(
        config.redis_url, decode_responses=True,
        socket_timeout=2, socket_connect_timeout=2,
    )
    try:
        sanity.ping()
    except Exception as exc:
        pytest.skip(f"Memurai unavailable: {exc}")

    g = GenesisAgent(
        db_session_factory=db_factory,
        exchange_service=None, agora_service=None,
        library_service=None, economy_service=None,
    )
    return g


def test_genesis_hourly_maintenance_invokes_run_all_every_hour(
    db_factory, seeded_world,
):
    """_maybe_run_hourly_maintenance runs run_all() on every hourly
    fire — independent of the daily gate. Run twice in succession
    (with the hourly gate manually advanced) and verify run_all
    fires both times."""
    g = _make_genesis_for_maintenance_test(db_factory)

    # Patch run_all so we can count calls without touching the DB.
    captured_calls: list[dict] = []

    async def _fake_run_all(self, redis_client=None):
        captured_calls.append({"redis_client": redis_client})
        return {
            "opportunities_expired": 0,
            "plans_cleaned": 0,
            "memory_pruned": 0,
        }

    with patch.object(MaintenanceService, "run_all", _fake_run_all):
        # First fire — no prior _last_hourly_maintenance.
        asyncio.run(g._maybe_run_hourly_maintenance())
        # Reset the hourly gate so we can fire again immediately.
        g._last_hourly_maintenance = None
        asyncio.run(g._maybe_run_hourly_maintenance())

    assert len(captured_calls) == 2, (
        f"run_all should fire on every hourly invocation; got "
        f"{len(captured_calls)} calls: {captured_calls!r}"
    )
    # redis_client threaded through both times.
    for call in captured_calls:
        assert call["redis_client"] is not None


def test_genesis_daily_budget_reset_independent_of_run_all(
    db_factory, seeded_world,
):
    """The daily-gated budget reset and the hourly-fired run_all must
    not interfere. Set _last_budget_reset_date to yesterday: BOTH
    fire on first invocation. Run again immediately: only run_all
    fires (daily gate held)."""
    g = _make_genesis_for_maintenance_test(db_factory)
    yesterday = datetime.now(timezone.utc).date() - timedelta(days=1)
    g._last_budget_reset_date = yesterday

    run_all_calls: list = []
    reset_calls: list = []

    async def _fake_run_all(self, redis_client=None):
        run_all_calls.append({"redis_client": redis_client})
        return {
            "opportunities_expired": 0,
            "plans_cleaned": 0,
            "memory_pruned": 0,
        }

    def _fake_reset(self):
        reset_calls.append(True)
        return 5

    with patch.object(MaintenanceService, "run_all", _fake_run_all), \
         patch.object(MaintenanceService, "reset_daily_budgets", _fake_reset):
        # Invocation 1: yesterday's date → daily gate fires AND run_all fires.
        asyncio.run(g._maybe_run_hourly_maintenance())
        assert len(run_all_calls) == 1
        assert len(reset_calls) == 1, (
            "Daily-gate budget reset did not fire on the cross-day boundary."
        )
        # Confirm the gate moved.
        assert g._last_budget_reset_date == datetime.now(timezone.utc).date()

        # Invocation 2 (same day): only run_all fires; daily gate holds.
        g._last_hourly_maintenance = None
        asyncio.run(g._maybe_run_hourly_maintenance())
        assert len(run_all_calls) == 2, (
            "run_all should still fire hourly even when daily gate is closed."
        )
        assert len(reset_calls) == 1, (
            "reset_daily_budgets fired twice in the same day — the daily "
            "gate is broken or run_all wrongly invoked it."
        )


def test_genesis_hourly_maintenance_does_not_call_reset_daily_budgets_directly_outside_gate():
    """Source-inspection guard. Read genesis.py and assert
    `reset_daily_budgets(` is referenced at exactly one site, and
    that site sits inside the daily-gate `if self._last_budget_reset_date != today:` block.
    """
    from src.genesis import genesis as g_mod
    src = inspect.getsource(g_mod)
    # Strip comments — we care about call sites, not documentation.
    code_lines = [
        line for line in src.splitlines()
        if not line.lstrip().startswith("#")
    ]
    code_only = "\n".join(code_lines)

    # Exactly one .reset_daily_budgets() call.
    call_sites = code_only.count(".reset_daily_budgets(")
    assert call_sites == 1, (
        f"reset_daily_budgets is called from {call_sites} sites in "
        f"genesis.py — expected exactly 1 (inside the daily gate). "
        f"If a future refactor moved the call into run_all() or added "
        f"a second call site, agents could lose their daily thinking "
        f"budget cap."
    )

    # The call site must be inside an `if self._last_budget_reset_date` block.
    # Anchored substring match: the daily-gate `if` line must appear
    # within the function body before the reset_daily_budgets call.
    reset_idx = code_only.find(".reset_daily_budgets(")
    daily_gate_idx = code_only.rfind(
        "if self._last_budget_reset_date", 0, reset_idx,
    )
    assert daily_gate_idx >= 0, (
        "reset_daily_budgets is called outside the daily-gate "
        "`if self._last_budget_reset_date != today:` block. "
        "Resetting more often than daily would let agents consume 24x "
        "their intended daily thinking budget."
    )
    # Must be reasonably close — same code block, not in a totally
    # different function. 500 chars is generous (block size in genesis.py).
    distance = reset_idx - daily_gate_idx
    assert distance < 500, (
        f"reset_daily_budgets call is {distance} chars away from the "
        f"daily-gate `if` — likely in a different block. The call must "
        f"sit inside the daily gate."
    )


# ---------------------------------------------------------------------------
# 3. CRITICAL: actually-runs tests (directive items 12-14)
# ---------------------------------------------------------------------------


def test_expire_stale_opportunities_actually_runs_in_production_path(
    db_factory, seeded_world,
):
    """LOAD-BEARING. The audit's evidence (3 days of stale
    opportunities in the last Arena) was a production-path bug,
    not a unit-test gap. Insert 5 stale + 5 fresh opportunities,
    invoke `_maybe_run_hourly_maintenance` through the production
    GenesisAgent, assert the 5 stale rows are now 'expired' and the
    5 fresh rows are still 'new'.

    This proves the production code path actually runs the work,
    not just that run_all is called somewhere.
    """
    now = datetime.now(timezone.utc)
    scout_id = seeded_world["Scout-1"]
    stale_ids: list[int] = []
    fresh_ids: list[int] = []
    with db_factory() as session:
        for i in range(5):
            opp = Opportunity(
                scout_agent_id=scout_id, scout_agent_name="Scout-1",
                market="BTC/USDT", signal_type="volume_breakout",
                details=f"stale-{i}",
                status="new",
                expires_at=now - timedelta(hours=1),  # past
            )
            session.add(opp)
            session.flush()
            stale_ids.append(opp.id)
        for i in range(5):
            opp = Opportunity(
                scout_agent_id=scout_id, scout_agent_name="Scout-1",
                market="ETH/USDT", signal_type="trend_reversal",
                details=f"fresh-{i}",
                status="new",
                expires_at=now + timedelta(hours=1),  # future
            )
            session.add(opp)
            session.flush()
            fresh_ids.append(opp.id)
        session.commit()

    g = _make_genesis_for_maintenance_test(db_factory)
    asyncio.run(g._maybe_run_hourly_maintenance())

    with db_factory() as session:
        for oid in stale_ids:
            row = session.get(Opportunity, oid)
            assert row.status == "expired", (
                f"Stale opportunity id={oid} not expired after the "
                f"production maintenance path. status={row.status!r}. "
                f"This is the audit-flagged bug."
            )
        for oid in fresh_ids:
            row = session.get(Opportunity, oid)
            assert row.status == "new", (
                f"Fresh opportunity id={oid} was wrongly expired. "
                f"status={row.status!r}"
            )


def test_cleanup_stale_plans_actually_runs_in_production_path(
    db_factory, seeded_world,
):
    """Same shape for plans. Insert old + fresh plans, invoke
    production path, assert stale plans cleaned up."""
    now = datetime.now(timezone.utc)
    strategist_id = seeded_world["Strategist-1"]
    stale_ids: list[int] = []
    fresh_ids: list[int] = []
    with db_factory() as session:
        # Submitted >24h ago with no critic → should flip to draft.
        for i in range(3):
            plan = Plan(
                strategist_agent_id=strategist_id,
                strategist_agent_name="Strategist-1",
                plan_name=f"stale-{i}", market="BTC/USDT",
                direction="long", entry_conditions="x", exit_conditions="y",
                thesis="z", status="submitted",
                submitted_at=now - timedelta(hours=25),
            )
            session.add(plan)
            session.flush()
            stale_ids.append(plan.id)
        # Submitted recently → should stay 'submitted'.
        for i in range(3):
            plan = Plan(
                strategist_agent_id=strategist_id,
                strategist_agent_name="Strategist-1",
                plan_name=f"fresh-{i}", market="ETH/USDT",
                direction="short", entry_conditions="x", exit_conditions="y",
                thesis="z", status="submitted",
                submitted_at=now - timedelta(hours=1),
            )
            session.add(plan)
            session.flush()
            fresh_ids.append(plan.id)
        session.commit()

    g = _make_genesis_for_maintenance_test(db_factory)
    asyncio.run(g._maybe_run_hourly_maintenance())

    with db_factory() as session:
        for pid in stale_ids:
            row = session.get(Plan, pid)
            assert row.status == "draft", (
                f"Stale plan id={pid} not flipped to draft. "
                f"status={row.status!r}"
            )
        for pid in fresh_ids:
            row = session.get(Plan, pid)
            assert row.status == "submitted", (
                f"Fresh plan id={pid} wrongly flipped. "
                f"status={row.status!r}"
            )


def test_prune_terminated_agent_memory_actually_runs_in_production_path(
    db_factory, seeded_world,
):
    """Production-path proof for the Redis memory pruning. Seed a
    terminated agent with a Redis key for its short-term memory;
    after the production maintenance path runs, the key must be
    deleted."""
    import redis as redis_lib
    from src.common.config import config

    terminated_id = seeded_world["Terminated-1"]

    # Use a real Memurai key so we exercise the production redis_client
    # path. Skip if Memurai is unreachable (matches the helper's policy).
    try:
        r = redis_lib.Redis.from_url(config.redis_url, decode_responses=True)
        r.ping()
    except Exception as exc:
        pytest.skip(f"Memurai unavailable: {exc}")

    key = f"agent:{terminated_id}:recent_cycles"
    r.set(key, "synthetic memory entry")
    try:
        assert r.exists(key), "fixture seed failed — key not in Memurai"

        g = _make_genesis_for_maintenance_test(db_factory)
        asyncio.run(g._maybe_run_hourly_maintenance())

        assert not r.exists(key), (
            f"Memurai key {key!r} still exists after the production "
            f"maintenance path ran. prune_terminated_agent_memory was "
            f"either not invoked or did not remove the key."
        )
    finally:
        # Belt-and-braces cleanup in case the assertion fired before
        # the prune ran.
        try:
            r.delete(key)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Bonus: budget-cadence preservation guard
# ---------------------------------------------------------------------------


def test_thinking_budget_used_today_unchanged_by_run_all_path(
    db_factory, seeded_world,
):
    """LOAD-BEARING: this is the test that locks in the Option B
    cadence asymmetry. Pre-seed agents with non-zero
    `thinking_budget_used_today`; invoke the hourly maintenance
    path TWICE on the same UTC day (with the daily-gate already
    closed); assert agents' `thinking_budget_used_today` values are
    UNCHANGED. If a future refactor accidentally pulls
    `reset_daily_budgets` into `run_all`, this test fails — proving
    agents would have lost their budget caps.
    """
    g = _make_genesis_for_maintenance_test(db_factory)
    g._last_budget_reset_date = datetime.now(timezone.utc).date()  # closed

    with db_factory() as session:
        pre = {
            r.name: float(r.thinking_budget_used_today or 0)
            for r in session.execute(select(Agent)).scalars().all()
        }

    asyncio.run(g._maybe_run_hourly_maintenance())
    g._last_hourly_maintenance = None
    asyncio.run(g._maybe_run_hourly_maintenance())

    with db_factory() as session:
        post = {
            r.name: float(r.thinking_budget_used_today or 0)
            for r in session.execute(select(Agent)).scalars().all()
        }

    assert post == pre, (
        f"thinking_budget_used_today changed after running the hourly "
        f"maintenance path on a same-day boundary. The Option B contract "
        f"is broken — run_all() must NOT trigger budget reset.\n"
        f"pre:  {pre!r}\npost: {post!r}"
    )
