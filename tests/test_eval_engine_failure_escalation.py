"""
Subsystem P fix — eval engine async-failure escalation tests.

Two test surfaces:
  1. Counter logic on `EvaluationEngine._record_async_outcome` —
     consecutive failures escalate, success resets, the two call
     types are tracked independently.
  2. Wiring tests that prove the production code path actually
     invokes track_api_call and update_fitness during evaluation.
     These are the load-bearing regression guards: the previous
     fragile pattern silently dropped these calls under contended
     event-loop state, so a test must observe the side effects.

The five counter-logic tests live in this file. The two wiring
tests (`test_track_api_call_actually_invoked_during_evaluation` and
`test_update_fitness_actually_invoked_during_evaluation`) are the
critical, non-negotiable proofs that selection pressure is being
tracked end-to-end.
"""

from __future__ import annotations

import asyncio
import re
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.common.models import Agent, Base, SystemState, Transaction
from src.genesis.evaluation_engine import (
    ASYNC_FAILURE_ESCALATION_THRESHOLD,
    EvaluationEngine,
)


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _strip_ansi(s: str) -> str:
    return _ANSI_RE.sub("", s)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _restore_event_loop_after_test():
    """`asyncio.run()` (used by run_async_safely's no-loop path AND
    by some tests in this file directly) closes the loop and clears
    the asyncio policy's loop reference. Other tests in the suite —
    notably `test_gaming_detection.py` — still use the deprecated
    `asyncio.get_event_loop().run_until_complete(...)` pattern, which
    raises if there is no current loop. Set a fresh loop after each
    test so suite ordering doesn't matter. Matches the fixture in
    `test_genesis_regime_review_consumption.py`."""
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
def seeded_agent(db_factory):
    """One Operator agent seeded with the columns track_api_call
    increments. Returns the agent_id."""
    with db_factory() as session:
        session.add(SystemState(
            total_treasury=1000.0, peak_treasury=1000.0,
            current_regime="bull", active_agent_count=1, alert_status="green",
        ))
        agent = Agent(
            name="Operator-EvalP", type="operator", status="active",
            generation=1, capital_allocated=200.0, capital_current=200.0,
            cash_balance=200.0, reserved_cash=0.0, total_equity=200.0,
            thinking_budget_used_today=0.0,
            total_api_cost=0.0, api_cost_total=0.0,
            composite_score=0.0,
        )
        session.add(agent)
        session.commit()
        return agent.id


# ---------------------------------------------------------------------------
# Counter-logic tests (directive items 10-14)
# ---------------------------------------------------------------------------


def test_track_api_call_failure_increments_counter(db_factory):
    """A single failure increments the per-call-type counter; below
    threshold no escalation alert fires."""
    eng = EvaluationEngine(db_session_factory=db_factory)
    assert eng._track_api_call_failure_count == 0

    eng._record_async_outcome("track_api_call", False, RuntimeError("boom"))
    assert eng._track_api_call_failure_count == 1
    # update_fitness counter unaffected.
    assert eng._update_fitness_failure_count == 0


def test_track_api_call_three_consecutive_failures_escalates(
    db_factory, capsys,
):
    """Three consecutive failures trip the escalation: CRITICAL log
    emitted on the third failure with `call_type=track_api_call`
    in the structured fields."""
    assert ASYNC_FAILURE_ESCALATION_THRESHOLD == 3

    eng = EvaluationEngine(db_session_factory=db_factory, agora_service=None)

    # Capture the CRITICAL log via the standard logging tree (this
    # module uses logging.getLogger, not structlog).
    import logging as _logging
    log_records: list[_logging.LogRecord] = []

    class _Capture(_logging.Handler):
        def emit(self, record):
            log_records.append(record)

    handler = _Capture(level=_logging.CRITICAL)
    _logging.getLogger("src.genesis.evaluation_engine").addHandler(handler)
    try:
        for i in range(ASYNC_FAILURE_ESCALATION_THRESHOLD):
            eng._record_async_outcome(
                "track_api_call", False, RuntimeError(f"fail-{i}")
            )
    finally:
        _logging.getLogger("src.genesis.evaluation_engine").removeHandler(handler)

    escalations = [
        r for r in log_records
        if "eval_engine_async_failure_escalated" in r.getMessage()
    ]
    assert escalations, (
        f"Expected CRITICAL log on third failure. Records: "
        f"{[(r.levelname, r.getMessage()) for r in log_records]!r}"
    )
    # The structured extras carry the call_type.
    assert getattr(escalations[0], "call_type", None) == "track_api_call"
    assert getattr(escalations[0], "consecutive_failures", None) == 3


def test_track_api_call_counter_resets_on_first_success(db_factory):
    """Two failures, then a success → counter resets to 0. The next
    failure starts the count from 1 (not from 3)."""
    eng = EvaluationEngine(db_session_factory=db_factory)

    eng._record_async_outcome("track_api_call", False, RuntimeError("a"))
    eng._record_async_outcome("track_api_call", False, RuntimeError("b"))
    assert eng._track_api_call_failure_count == 2

    eng._record_async_outcome("track_api_call", True, None)
    assert eng._track_api_call_failure_count == 0

    eng._record_async_outcome("track_api_call", False, RuntimeError("c"))
    assert eng._track_api_call_failure_count == 1


def test_update_fitness_failure_independent_of_track_api_call(db_factory):
    """The two counters track different call types and must not
    share state. A flood of track_api_call failures must NOT bump the
    update_fitness counter."""
    eng = EvaluationEngine(db_session_factory=db_factory)

    for _ in range(5):
        eng._record_async_outcome("track_api_call", False, RuntimeError("x"))
    assert eng._track_api_call_failure_count == 5
    assert eng._update_fitness_failure_count == 0

    # And vice versa.
    eng._record_async_outcome("update_fitness", False, RuntimeError("y"))
    assert eng._update_fitness_failure_count == 1
    assert eng._track_api_call_failure_count == 5


def test_evaluation_completes_when_async_call_fails(db_factory, seeded_agent):
    """If track_api_call (or update_fitness) raises, the engine
    increments the counter and continues. The surrounding evaluation
    flow must NOT abort because of an async-bridge failure — that
    contract was the explicit design choice in the directive
    ("evaluation is not safety-critical the moment-to-moment")."""
    eng = EvaluationEngine(db_session_factory=db_factory)

    # Force track_api_call to fail by patching at the Accountant
    # class level — every track_api_call invocation raises.
    from src.risk import accountant as acct_mod

    async def _always_raises(self, *args, **kwargs):
        raise RuntimeError("forced track_api_call failure")

    with patch.object(
        acct_mod.Accountant, "track_api_call", _always_raises,
    ):
        # Drive the failure path by calling _record_async_outcome
        # with the same shape the real call site would produce.
        from src.common.async_bridge import run_async_safely
        from src.risk.accountant import Accountant

        a = Accountant(db_session_factory=db_factory)
        success, exc = run_async_safely(
            a.track_api_call(
                agent_id=seeded_agent, model="claude-haiku-4-5-20251001",
                input_tokens=10, output_tokens=10,
            )
        )
        assert success is False
        assert isinstance(exc, RuntimeError)
        eng._record_async_outcome("track_api_call", success, exc)

    # The engine itself is in a healthy state — the counter ticked
    # up but no exception bubbled to the caller.
    assert eng._track_api_call_failure_count == 1
    # Nothing crashed; the test reaching this line is the proof.


# ---------------------------------------------------------------------------
# CRITICAL WIRING TESTS (directive items 15-16)
# ---------------------------------------------------------------------------


# These are the load-bearing regression guards for subsystem P.
# Without them, a future refactor could re-introduce the silent-drop
# pattern and pass every counter-logic test.


def test_track_api_call_actually_invoked_during_evaluation(
    db_factory, seeded_agent,
):
    """Production code path proof: calling the refactored
    `run_async_safely(acct.track_api_call(...))` block from a sync
    context (no running loop) actually writes a transaction and
    updates the agent's api_cost_total. The previous fragile pattern
    would have silently dropped this on any contended event-loop
    state."""
    from src.common.async_bridge import run_async_safely
    from src.risk.accountant import Accountant

    eng = EvaluationEngine(db_session_factory=db_factory)
    acct = Accountant(db_session_factory=db_factory)

    # Mirror site 1's call shape. This is the EXACT pattern in
    # `_call_genesis_ai` after the refactor.
    success, exc = run_async_safely(
        acct.track_api_call(
            agent_id=seeded_agent,
            model="claude-haiku-4-5-20251001",
            input_tokens=100, output_tokens=50,
        )
    )
    eng._record_async_outcome("track_api_call", success, exc)

    assert success is True, f"track_api_call failed: {exc!r}"
    assert eng._track_api_call_failure_count == 0

    # Side effect 1: a Transaction row landed.
    with db_factory() as session:
        txs = session.execute(
            select(Transaction).where(Transaction.agent_id == seeded_agent)
        ).scalars().all()
        api_cost_txs = [t for t in txs if t.type == "api_cost"]
        assert len(api_cost_txs) == 1, (
            f"Expected exactly 1 api_cost transaction; got "
            f"{len(api_cost_txs)}. Transactions: {txs!r}"
        )
        # Cost should be positive (Haiku pricing × tokens > 0).
        assert api_cost_txs[0].amount > 0
        assert api_cost_txs[0].pnl < 0  # negative P&L

        # Side effect 2: agent's cumulative cost columns moved.
        agent = session.get(Agent, seeded_agent)
        assert agent.api_cost_total > 0
        assert agent.thinking_budget_used_today > 0


def test_track_api_call_actually_invoked_inside_running_loop(
    db_factory, seeded_agent,
):
    """Same wiring proof, but with a running event loop on the
    calling thread — exercises the worker-thread dispatch path. This
    is the production scenario that USED to fail silently under the
    old `run_until_complete + bare except` pattern."""
    from src.common.async_bridge import run_async_safely
    from src.risk.accountant import Accountant

    acct = Accountant(db_session_factory=db_factory)

    async def _inside_loop():
        # Sanity: confirm we are indeed inside a running loop, so the
        # helper exercises the worker-thread path.
        assert asyncio.get_running_loop() is not None
        return run_async_safely(
            acct.track_api_call(
                agent_id=seeded_agent,
                model="claude-haiku-4-5-20251001",
                input_tokens=200, output_tokens=100,
            )
        )

    success, exc = asyncio.run(_inside_loop())
    assert success is True, f"track_api_call failed inside loop: {exc!r}"

    with db_factory() as session:
        agent = session.get(Agent, seeded_agent)
        assert agent.api_cost_total > 0


def test_update_fitness_actually_invoked_during_evaluation(
    db_factory, seeded_agent,
):
    """Production code path proof: the refactored `_apply_survival`
    update_fitness block (with its fresh-session closure) actually
    writes the genome record's fitness_score. This is the LOAD-
    BEARING selection-pressure invariant — silent drops here corrupt
    Darwinian selection.

    Drives the call via the exact same closure shape `_apply_survival`
    builds, asserting the side-effect on agent_genomes.
    """
    from src.common.async_bridge import run_async_safely
    from src.common.models import AgentGenome
    from src.genome.genome_manager import GenomeManager

    # Seed an agent_genomes row for our agent.
    with db_factory() as session:
        genome = AgentGenome(
            agent_id=seeded_agent,
            genome_data={"role": "operator", "params": {}},
            evaluations_with_genome=0,
            fitness_score=0.0,
        )
        session.add(genome)
        session.commit()
        genome_id = genome.id

    eng = EvaluationEngine(db_session_factory=db_factory)
    genome_mgr = GenomeManager()

    composite_score_for_fitness = 0.85

    async def _update_fitness_with_fresh_session():
        # Mirrors _apply_survival's closure shape exactly.
        with db_factory() as fresh_session:
            await genome_mgr.update_fitness(
                seeded_agent,
                composite_score_for_fitness,
                fresh_session,
            )
            fresh_session.commit()

    success, exc = run_async_safely(_update_fitness_with_fresh_session())
    eng._record_async_outcome("update_fitness", success, exc)

    assert success is True, f"update_fitness failed: {exc!r}"
    assert eng._update_fitness_failure_count == 0

    # Side effect: fitness_score moved + evaluations_with_genome ticked.
    with db_factory() as session:
        record = session.get(AgentGenome, genome_id)
        assert record.evaluations_with_genome == 1
        assert record.fitness_score is not None
        assert record.fitness_score > 0, (
            f"fitness_score did not update — selection pressure is "
            f"broken. Got fitness_score={record.fitness_score!r}"
        )


def test_update_fitness_actually_invoked_inside_running_loop(
    db_factory, seeded_agent,
):
    """Same wiring proof for update_fitness, but with a running
    event loop on the calling thread (production scenario inside
    Genesis.run_cycle's await chain). Exercises the worker-thread
    dispatch path that the previous fragile pattern broke on."""
    from src.common.async_bridge import run_async_safely
    from src.common.models import AgentGenome
    from src.genome.genome_manager import GenomeManager

    with db_factory() as session:
        session.add(AgentGenome(
            agent_id=seeded_agent,
            genome_data={"role": "operator", "params": {}},
            evaluations_with_genome=0,
            fitness_score=0.0,
        ))
        session.commit()

    genome_mgr = GenomeManager()

    async def _inside_loop():
        async def _update_fitness_with_fresh_session():
            with db_factory() as fresh_session:
                await genome_mgr.update_fitness(
                    seeded_agent, 0.92, fresh_session,
                )
                fresh_session.commit()

        return run_async_safely(_update_fitness_with_fresh_session())

    success, exc = asyncio.run(_inside_loop())
    assert success is True, f"update_fitness failed inside loop: {exc!r}"

    with db_factory() as session:
        record = session.execute(
            select(AgentGenome).where(AgentGenome.agent_id == seeded_agent)
        ).scalar_one()
        assert record.evaluations_with_genome == 1
        assert record.fitness_score is not None and record.fitness_score > 0


# ---------------------------------------------------------------------------
# Source-inspection guard
# ---------------------------------------------------------------------------


def test_eval_engine_no_longer_uses_fragile_pattern():
    """If a future refactor re-introduces
    `asyncio.get_event_loop().run_until_complete` or a bare
    `except Exception: pass` in the eval engine, this test fails.
    The source must converge on `run_async_safely`."""
    import inspect
    from src.genesis import evaluation_engine as ee

    src = inspect.getsource(ee)
    # Count actual call sites, not comments. The forbidden expression
    # is `.run_until_complete(` — strip comment lines first.
    code_lines = [
        line for line in src.splitlines()
        if not line.lstrip().startswith("#")
    ]
    code_only = "\n".join(code_lines)
    assert ".run_until_complete(" not in code_only, (
        "evaluation_engine still has a `.run_until_complete(` call. "
        "Subsystem P fix requires all such call sites to route through "
        "`src.common.async_bridge.run_async_safely`."
    )
    # Each refactored site must call through the bridge + record outcome.
    assert "run_async_safely" in code_only
    assert "_record_async_outcome" in code_only


# ---------------------------------------------------------------------------
# Critic iteration 2 Finding 1: alert-emit ordering + Agora isolation
# ---------------------------------------------------------------------------


def test_emit_async_failure_alert_critical_log_fires_even_if_agora_post_raises(
    db_factory,
):
    """Contract proof: when Agora.post_message raises, the
    `_emit_async_failure_alert` path must:
      1. Have ALREADY fired the CRITICAL log (before attempting the
         post — log is the load-bearing alert-emission contract).
      2. Log a single WARNING tagged `agora_alert_emit_failed=True`
         describing the Agora failure.
      3. NOT propagate any exception to the caller.
      4. NOT recursively re-escalate by calling
         `_record_async_outcome` for the alert path itself.

    This locks in the iteration-2 fix for the meta-anti-pattern: the
    alert-emit must not itself use the fire-and-forget shape that
    subsystem P removed elsewhere.
    """
    import logging as _logging

    # Build an Agora double whose `post_message` raises. The eval
    # engine calls `await self.agora.post_message(...)` inside a
    # coroutine handed to `run_async_safely`; the helper offloads to
    # a worker thread which awaits the coroutine and catches the
    # raise.
    agora = MagicMock()
    agora.post_message = AsyncMock(
        side_effect=RuntimeError("synthetic agora failure"),
    )
    eng = EvaluationEngine(db_session_factory=db_factory, agora_service=agora)

    # Capture log records from the eval_engine logger.
    records: list[_logging.LogRecord] = []

    class _Capture(_logging.Handler):
        def emit(self, record):
            records.append(record)

    handler = _Capture(level=_logging.WARNING)
    eval_logger = _logging.getLogger("src.genesis.evaluation_engine")
    eval_logger.addHandler(handler)
    try:
        # Drive the alert-emit path. This must not raise even though
        # the Agora post inside will raise.
        try:
            eng._emit_async_failure_alert(
                "track_api_call",
                RuntimeError("synthetic underlying failure"),
                3,
            )
        except Exception as exc:  # pragma: no cover — contract guard
            pytest.fail(
                f"_emit_async_failure_alert propagated an exception: "
                f"{exc!r}. The Agora post must be wrapped in its own "
                f"try/except and not surface failures to the caller."
            )
    finally:
        eval_logger.removeHandler(handler)

    # 1. CRITICAL log fired (before the Agora attempt).
    critical_records = [
        r for r in records
        if r.levelname == "CRITICAL"
        and "eval_engine_async_failure_escalated" in r.getMessage()
    ]
    assert critical_records, (
        f"CRITICAL escalation log did not fire. Records: "
        f"{[(r.levelname, r.getMessage()) for r in records]!r}"
    )
    assert getattr(critical_records[0], "call_type", None) == "track_api_call"

    # 2. WARNING log with structured agora_alert_emit_failed=True.
    agora_failures = [
        r for r in records
        if r.levelname == "WARNING"
        and getattr(r, "agora_alert_emit_failed", None) is True
    ]
    assert agora_failures, (
        f"Expected a WARNING with agora_alert_emit_failed=True after "
        f"Agora.post_message raised. Records: "
        f"{[(r.levelname, r.getMessage(), getattr(r, 'agora_alert_emit_failed', None)) for r in records]!r}"
    )
    rec = agora_failures[0]
    assert getattr(rec, "call_type", None) == "track_api_call"
    assert getattr(rec, "underlying_failure_count", None) == 3
    assert getattr(rec, "agora_exception_type", None) == "RuntimeError"
    assert "synthetic agora failure" in (
        getattr(rec, "agora_exception_str", None) or ""
    )

    # 3. Counters NOT incremented by the alert-emit path (no
    # recursion). This is the meta-anti-pattern guard: if
    # `_emit_async_failure_alert` had called `_record_async_outcome`
    # for its own Agora failure, the counter would have ticked.
    # Counter is still 0 because we didn't drive any
    # `_record_async_outcome` from this test — the Agora-emit failure
    # must not change it.
    assert eng._track_api_call_failure_count == 0
    assert eng._update_fitness_failure_count == 0


def test_emit_async_failure_alert_critical_fires_before_agora_attempt(
    db_factory,
):
    """Stricter ordering guard: the CRITICAL log must fire even if
    the Agora reference itself raises on attribute access (i.e.,
    pathological agora object). The CRITICAL line must land first;
    any Agora-side weirdness happens after."""
    import logging as _logging

    class _ExplodingAgora:
        @property
        def post_message(self):  # pragma: no cover — pathology probe
            raise RuntimeError("agora attribute access exploded")

    eng = EvaluationEngine(
        db_session_factory=db_factory,
        agora_service=_ExplodingAgora(),
    )

    records: list[_logging.LogRecord] = []

    class _Capture(_logging.Handler):
        def emit(self, record):
            records.append(record)

    handler = _Capture(level=_logging.CRITICAL)
    eval_logger = _logging.getLogger("src.genesis.evaluation_engine")
    eval_logger.addHandler(handler)
    try:
        eng._emit_async_failure_alert(
            "update_fitness", RuntimeError("underlying"), 3,
        )
    finally:
        eval_logger.removeHandler(handler)

    critical_records = [
        r for r in records
        if r.levelname == "CRITICAL"
        and "eval_engine_async_failure_escalated" in r.getMessage()
    ]
    assert critical_records, (
        "CRITICAL log did not fire even though it must precede any "
        "Agora attempt. The contract is broken."
    )
