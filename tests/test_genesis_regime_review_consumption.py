"""
Genesis regime-review consumption — production wiring tests.

Closes WIRING_AUDIT_REPORT.md subsystem H. Severity-5 wire events fire
but no listener invoked Genesis regime review in production. Per War
Room iteration 1 directive on this branch (Option C):

  - Producer (haiku_digester) marks sev-5 events `regime_review_status
    = 'pending'` at INSERT.
  - Consumer (Genesis.run_cycle) at top-of-cycle reads pending rows,
    logs `genesis_consuming_regime_review` per row, runs the existing
    detect_regime() inline, and at end-of-cycle flips them to
    'reviewed'. At-least-once: an exception mid-cycle leaves rows
    pending for the next cycle.

These tests are the regression guard. The load-bearing one is
`test_severity_5_event_consumed_by_genesis_in_production_path`, which
goes end-to-end through the real HaikuDigester and the real
GenesisAgent constructor — production code paths, not hand-rolled
stubs. Without it, future refactors could break the wiring and pass
every other test in this file.
"""

from __future__ import annotations

import asyncio
import re
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import redis as redis_lib
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.common.config import config
from src.common.models import Agent, Base, SystemState
from src.genesis.genesis import (
    GenesisAgent,
    REGIME_REVIEW_BATCH_LIMIT,
    REGIME_REVIEW_MAX_ATTEMPTS,
    REGIME_REVIEW_QUERY_FAILURE_ALERT_THRESHOLD,
)
from src.wire.constants import (
    DIGESTION_STATUS_DIGESTED,
    SEVERITY_CRITICAL,
)
from src.wire.digest.haiku_digester import HaikuDigester
import src.wire.models  # noqa: F401  — register Wire tables on Base.metadata
from src.wire.models import WireEvent, WireRawItem, WireSource

from tests.wire.conftest import _seed_sources, make_fake_haiku_client


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _strip_ansi(text: str) -> str:
    """structlog's ConsoleRenderer wraps every key/value in ANSI color
    codes when stdout is a tty AND when not. Strip them so substring
    matches like ``event_id=42`` work in tests."""
    return _ANSI_RE.sub("", text)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _restore_event_loop_after_test():
    """`asyncio.run()` closes the loop AND clears the policy's loop ref.
    Other tests in the suite still use the deprecated
    `asyncio.get_event_loop().run_until_complete(...)` pattern, which
    raises if there is no current loop. Set a fresh loop after each
    test so subsequent tests are unaffected by our use of asyncio.run.
    """
    yield
    try:
        asyncio.set_event_loop(asyncio.new_event_loop())
    except Exception:
        pass


@pytest.fixture
def thread_safe_engine():
    """In-memory SQLite that the GenesisAgent constructor + run_cycle
    can hammer concurrently from the asyncio event loop."""
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
def memurai_available():
    """Skip tests that need a real Memurai if it isn't reachable. Same
    pattern as test_operator_halt_consumer_wiring."""
    client = redis_lib.Redis.from_url(
        config.redis_url, decode_responses=True,
        socket_timeout=2, socket_connect_timeout=2,
    )
    try:
        client.ping()
    except Exception as exc:
        pytest.skip(f"Memurai unavailable: {exc}")
    return client


@pytest.fixture
def seeded_world(db_factory):
    """SystemState row + Wire sources + a Genesis agents row.
    GenesisAgent.initialize() will idempotently create these too, but
    pre-seeding lets the cycle start without that side effect being
    test-significant."""
    with db_factory() as session:
        session.add(SystemState(
            total_treasury=1000.0, peak_treasury=1000.0,
            current_regime="bull", active_agent_count=0, alert_status="green",
        ))
        session.add(Agent(
            id=0, name="Genesis", type="genesis", status="active", generation=0,
            capital_allocated=0.0, capital_current=0.0,
            strategy_summary="Immortal God Node",
        ))
        session.flush()
        _seed_sources(session)
        session.commit()


def _insert_wire_event(
    session, *, severity: int, status: str = "pending",
    coin: str = "BTC", event_type: str = "exchange_outage",
) -> WireEvent:
    """Direct-insert helper. Bypasses the digester so tests of the
    consumption side don't have to set up a full Haiku roundtrip."""
    canonical = f"{coin}|{event_type}|{severity}|{status}|{datetime.now(timezone.utc).isoformat()}"
    event = WireEvent(
        canonical_hash=canonical,
        coin=coin,
        event_type=event_type,
        severity=severity,
        summary=f"Synthetic sev-{severity} {event_type} for {coin}",
        occurred_at=datetime.now(timezone.utc),
        haiku_cost_usd=0.0,
        regime_review_status=status,
    )
    session.add(event)
    session.commit()
    return event


def _make_raw_item(session, *, source_name: str = "kraken_announcements",
                   external_id: str = "synthetic-1",
                   deterministic_severity: int | None = None,
                   deterministic_event_type: str | None = None) -> WireRawItem:
    """Build a wire_raw_item ready for digestion. Mirrors the helper
    from tests/wire/test_haiku_digester.py."""
    src = session.execute(
        select(WireSource).where(WireSource.name == source_name)
    ).scalar_one()
    envelope = {
        "payload": {"foo": "bar"},
        "haiku_brief": "Synthetic event for regime-review test",
        "source_url": "https://example.com/x",
        "deterministic_severity": deterministic_severity,
        "deterministic_event_type": deterministic_event_type,
        "deterministic_coin": None,
        "deterministic_direction": None,
        "deterministic_is_macro": None,
    }
    raw = WireRawItem(
        source_id=src.id,
        external_id=external_id,
        raw_payload=envelope,
        occurred_at=datetime.now(timezone.utc),
    )
    session.add(raw)
    session.commit()
    return raw


def _make_genesis_with_mocks(db_factory, memurai_client) -> GenesisAgent:
    """Build a real GenesisAgent and silence the collaborators that
    would otherwise need live exchange data / real Agora / real Library
    services. The collaborators we mock are NOT the ones under test;
    the consumption + mark-reviewed steps run unmocked.

    Construction itself is the same constructor `genesis_runner.py`
    invokes in production — same arg shape, same ordering.
    """
    genesis = GenesisAgent(
        db_session_factory=db_factory,
        exchange_service=None,
        agora_service=None,
        library_service=None,
        economy_service=None,
    )
    # Treasury — would otherwise call exchange APIs.
    genesis.treasury.update_peak_treasury = AsyncMock(return_value=None)
    genesis.treasury.close_inherited_positions = AsyncMock(return_value=None)
    genesis.treasury.get_treasury_balance = AsyncMock(
        return_value={"total": 1000.0, "available": 800.0, "reserved": 200.0}
    )
    # Regime detector — out of scope for this fix.
    genesis.regime_detector.detect_regime = AsyncMock(
        return_value={"regime": "bull", "changed": False, "previous_regime": "bull"}
    )
    # Steps that would otherwise need full agent / agora plumbing.
    genesis._check_agent_health = MagicMock(
        return_value={"active": 0, "due_for_evaluation": [], "stale": []}
    )
    genesis._run_evaluations = AsyncMock(return_value={})
    genesis._make_spawn_decisions = AsyncMock(return_value={})
    genesis._check_reproduction = AsyncMock(return_value={})
    genesis._monitor_agora = AsyncMock(return_value={})
    genesis._check_hibernation_wake = AsyncMock(return_value=None)
    genesis._maybe_run_hourly_maintenance = AsyncMock(return_value=None)
    genesis._maybe_run_boot_sequence = AsyncMock(return_value=None)
    # post_to_agora is async — used by step 11 (log cycle) and step 3
    # (regime change posts). Stub to a no-op.
    genesis.post_to_agora = AsyncMock(return_value=None)
    # accountant.generate_leaderboard for step 6 — only invoked when
    # evaluations is truthy; we return {} above so this is unused, but
    # stub for safety.
    genesis.accountant.generate_leaderboard = AsyncMock(return_value=[])
    return genesis


# ---------------------------------------------------------------------------
# Tests 1-2: producer-side marker (digester sets regime_review_status)
# ---------------------------------------------------------------------------


def test_severity_5_event_marked_pending_at_insert(db_factory, seeded_world):
    """Real HaikuDigester writes a sev-5 wire_events row →
    regime_review_status='pending'. This is the producer-side wiring
    contract: every sev-5 event becomes a queue entry the moment it
    lands, no extra publish step required."""
    with db_factory() as session:
        _make_raw_item(
            session,
            source_name="kraken_announcements", external_id="syn-sev5",
            deterministic_severity=5, deterministic_event_type="withdrawal_halt",
        )
        # Haiku response is irrelevant for severity (deterministic
        # overrides) but is required for the schema validator.
        haiku = make_fake_haiku_client([
            '{"coin":"BTC","is_macro":false,"event_type":"withdrawal_halt",'
            '"severity":2,"direction":"bearish","summary":"override-me"}'
        ])
        digester = HaikuDigester(haiku_client=haiku, session=session)
        results = digester.digest_pending()
        assert len(results) == 1
        assert results[0].status == DIGESTION_STATUS_DIGESTED

        evt = session.execute(select(WireEvent)).scalar_one()
        assert evt.severity == SEVERITY_CRITICAL
        assert evt.regime_review_status == "pending", (
            f"sev-5 event should be queued for Genesis review, "
            f"got status={evt.regime_review_status!r}"
        )


def test_non_severity_5_events_marked_skipped(db_factory, seeded_world):
    """Sev 1-4 rows default to 'skipped' — Genesis should never touch
    them. Verifies the producer-side conditional and the column
    server_default."""
    severities = [1, 2, 3, 4]
    with db_factory() as session:
        for i, sev in enumerate(severities):
            _make_raw_item(
                session, external_id=f"syn-sev{sev}-{i}",
                source_name="kraken_announcements",
                deterministic_severity=sev,
                deterministic_event_type="other",
            )
        haiku = make_fake_haiku_client([
            '{"coin":"BTC","is_macro":false,"event_type":"other","severity":1,'
            '"direction":"neutral","summary":"low impact"}'
            for _ in severities
        ])
        digester = HaikuDigester(haiku_client=haiku, session=session)
        digester.digest_pending()

        events = session.execute(select(WireEvent).order_by(WireEvent.id)).scalars().all()
        assert len(events) == len(severities)
        for evt in events:
            assert evt.regime_review_status == "skipped", (
                f"sev-{evt.severity} event should be 'skipped', "
                f"got {evt.regime_review_status!r}"
            )


# ---------------------------------------------------------------------------
# Tests 3-7: consumer-side run_cycle wiring
# ---------------------------------------------------------------------------


def test_run_cycle_consumes_pending_rows_and_marks_reviewed(
    db_factory, seeded_world, memurai_available, capsys,
):
    """Three pending rows → run_cycle → all three marked 'reviewed' AND
    structured `genesis_consuming_regime_review` log emitted per row.
    This is the load-bearing happy path. Genesis uses structlog's
    PrintLoggerFactory, so logs land in stdout — captured via capsys
    rather than caplog."""
    with db_factory() as session:
        for i in range(3):
            _insert_wire_event(
                session, severity=5, status="pending",
                coin=("BTC", "ETH", "SOL")[i],
                event_type=("exchange_outage", "withdrawal_halt", "chain_halt")[i],
            )

    genesis = _make_genesis_with_mocks(db_factory, memurai_available)
    asyncio.run(genesis.run_cycle())

    with db_factory() as session:
        rows = session.execute(
            select(WireEvent).order_by(WireEvent.id)
        ).scalars().all()
        statuses = [r.regime_review_status for r in rows]
        assert statuses == ["reviewed", "reviewed", "reviewed"], (
            f"Expected all three rows marked reviewed, got {statuses!r}"
        )

    captured = capsys.readouterr().out
    consumed_lines = [
        line for line in captured.splitlines()
        if "genesis_consuming_regime_review" in line
    ]
    assert len(consumed_lines) == 3, (
        f"Expected 3 'genesis_consuming_regime_review' log lines, got "
        f"{len(consumed_lines)}.\nstdout:\n{captured}"
    )


def test_run_cycle_handles_zero_pending_gracefully(
    db_factory, seeded_world, memurai_available,
):
    """Empty queue, run_cycle proceeds with no errors. Defends against
    a regression where the consumption query mishandles empty results
    or the mark-reviewed UPDATE refuses an empty list."""
    genesis = _make_genesis_with_mocks(db_factory, memurai_available)
    report = asyncio.run(genesis.run_cycle())
    assert "regime_reviews_consumed" not in report
    assert "error" not in report


def test_run_cycle_exception_leaves_pending_intact(
    db_factory, seeded_world, memurai_available,
):
    """Force a step between consumption and mark-reviewed to raise.
    Pending rows must stay 'pending' for the next cycle (at-least-once
    semantics). The mark-reviewed UPDATE only runs if every prior step
    in the try block succeeded."""
    with db_factory() as session:
        _insert_wire_event(session, severity=5, status="pending", coin="BTC")
        _insert_wire_event(session, severity=5, status="pending", coin="ETH")

    genesis = _make_genesis_with_mocks(db_factory, memurai_available)
    # Force step 11 (the 'log cycle' post_to_agora) to raise — this is
    # after consumption (step 2c) and before mark-reviewed (step 12).
    # The whole try block aborts via the top-level except in run_cycle.
    genesis.post_to_agora = AsyncMock(
        side_effect=RuntimeError("simulated mid-cycle failure"),
    )

    report = asyncio.run(genesis.run_cycle())
    assert "error" in report

    with db_factory() as session:
        rows = session.execute(
            select(WireEvent).order_by(WireEvent.id)
        ).scalars().all()
        statuses = [r.regime_review_status for r in rows]
        assert statuses == ["pending", "pending"], (
            f"Mid-cycle exception lost the at-least-once guarantee — "
            f"rows should still be pending. Got: {statuses!r}"
        )


def test_run_cycle_bounds_consumption_at_50(
    db_factory, seeded_world, memurai_available,
):
    """100 pending rows → run_cycle processes only REGIME_REVIEW_BATCH_LIMIT
    (50) in one pass. Excess rows stay 'pending' for next cycle. Defends
    against an unbounded query monopolising Genesis on backlog catch-up."""
    assert REGIME_REVIEW_BATCH_LIMIT == 50  # contract guard

    with db_factory() as session:
        for i in range(100):
            _insert_wire_event(
                session, severity=5, status="pending",
                coin=f"COIN{i:03d}",
            )

    genesis = _make_genesis_with_mocks(db_factory, memurai_available)
    asyncio.run(genesis.run_cycle())

    with db_factory() as session:
        reviewed = session.execute(
            select(WireEvent).where(WireEvent.regime_review_status == "reviewed")
        ).scalars().all()
        pending = session.execute(
            select(WireEvent).where(WireEvent.regime_review_status == "pending")
        ).scalars().all()
        assert len(reviewed) == REGIME_REVIEW_BATCH_LIMIT
        assert len(pending) == 100 - REGIME_REVIEW_BATCH_LIMIT


def test_idempotent_already_reviewed_rows_not_reprocessed(
    db_factory, seeded_world, memurai_available, capsys,
):
    """Two run_cycle invocations. After the first, all rows are
    'reviewed'. The second invocation must NOT re-emit consumption logs
    or re-update — the SELECT WHERE status='pending' filters them out
    by design."""
    with db_factory() as session:
        _insert_wire_event(session, severity=5, status="pending", coin="BTC")

    genesis = _make_genesis_with_mocks(db_factory, memurai_available)

    asyncio.run(genesis.run_cycle())
    first_out = capsys.readouterr().out
    first_consumed = [
        line for line in first_out.splitlines()
        if "genesis_consuming_regime_review" in line
    ]
    assert len(first_consumed) == 1, (
        f"First cycle should consume the row once, got {len(first_consumed)}"
    )

    asyncio.run(genesis.run_cycle())
    second_out = capsys.readouterr().out
    second_consumed = [
        line for line in second_out.splitlines()
        if "genesis_consuming_regime_review" in line
    ]
    assert len(second_consumed) == 0, (
        f"Second run_cycle re-consumed already-reviewed rows. "
        f"WHERE status='pending' filter is broken. stdout:\n{second_out}"
    )


# ---------------------------------------------------------------------------
# Test 8 — CRITICAL: production-factory integration
# ---------------------------------------------------------------------------


# LOAD-BEARING TEST: this is the regression guard for subsystem H.
# Without it, future refactors could silently disconnect the digester
# from Genesis (re-creating the original gap) and pass every other
# test in this file. The directive (War Room iteration 1) requires
# real production code paths — actual HaikuDigester, actual
# GenesisAgent constructor — not hand-rolled stubs. The internal
# collaborators (treasury, regime_detector, etc.) are mocked because
# they need live exchange data; the queue read/write path is real and
# unmocked.
def test_severity_5_event_consumed_by_genesis_in_production_path(
    db_factory, seeded_world, memurai_available, capsys,
):
    """End-to-end through the production code paths:
        1. Real HaikuDigester ingests a synthetic raw item with
           deterministic_severity=5. The digester writes a wire_events
           row with regime_review_status='pending'.
        2. Real GenesisAgent constructor (the same one
           src/genesis/genesis_runner.py invokes) runs run_cycle.
        3. The new step 2c reads the pending row, emits the
           structured log; step 12 flips it to 'reviewed'.

    Failures of any step would mean a sev-5 event in production never
    reaches Genesis — exactly the gap subsystem H closes.
    """
    # 1. Real digester writes the queue entry.
    with db_factory() as session:
        _make_raw_item(
            session,
            source_name="kraken_announcements", external_id="prod-sev5",
            deterministic_severity=5, deterministic_event_type="exchange_outage",
        )
        haiku = make_fake_haiku_client([
            '{"coin":"BTC","is_macro":false,"event_type":"exchange_outage",'
            '"severity":3,"direction":"bearish","summary":"e2e production-path"}'
        ])
        digester = HaikuDigester(haiku_client=haiku, session=session)
        digester.digest_pending()

        evt = session.execute(select(WireEvent)).scalar_one()
        produced_event_id = evt.id
        assert evt.regime_review_status == "pending", (
            "Producer wiring broken — sev-5 event should be queued "
            "by HaikuDigester. Got status="
            f"{evt.regime_review_status!r}"
        )

    # Drain anything captured during digester construction (which
    # emits its own structlog lines for the halt-publish path).
    capsys.readouterr()

    # 2. Real GenesisAgent constructor + run_cycle.
    genesis = _make_genesis_with_mocks(db_factory, memurai_available)
    report = asyncio.run(genesis.run_cycle())

    # 3. Verify the consumption log fired with the correct event_id
    # AND the row was marked 'reviewed'. Both must hold — the log is
    # the producer-side evidence trail, the row is the queue contract.
    captured = _strip_ansi(capsys.readouterr().out)
    consume_lines = [
        line for line in captured.splitlines()
        if "genesis_consuming_regime_review" in line
    ]
    matching = [
        line for line in consume_lines
        if f"event_id={produced_event_id}" in line
    ]
    assert matching, (
        f"Genesis did not log consumption of event_id={produced_event_id}. "
        f"All consume lines: {consume_lines!r}\nfull stdout:\n{captured}"
    )

    with db_factory() as session:
        evt = session.get(WireEvent, produced_event_id)
        assert evt.regime_review_status == "reviewed", (
            f"Genesis ran but did not mark event_id={produced_event_id} "
            f"reviewed. Status={evt.regime_review_status!r}. report={report!r}"
        )


# ---------------------------------------------------------------------------
# Source-inspection guard
# ---------------------------------------------------------------------------


def test_run_cycle_source_contains_consume_and_mark_steps():
    """Defense-in-depth: if a refactor accidentally removes the new
    steps from run_cycle, this test fails immediately. Same pattern as
    the wiring guards in test_operator_halt_consumer_wiring.
    """
    import inspect
    src = inspect.getsource(GenesisAgent.run_cycle)
    assert "_consume_pending_regime_reviews" in src, (
        "run_cycle no longer calls _consume_pending_regime_reviews — "
        "subsystem H wiring removed."
    )
    assert "_mark_regime_reviews_reviewed" in src, (
        "run_cycle no longer calls _mark_regime_reviews_reviewed — "
        "queue rows would stay pending forever."
    )


# ---------------------------------------------------------------------------
# Critic iteration 2 Finding 1 (HIGH): retry cap + last_error
# ---------------------------------------------------------------------------


def test_regime_review_attempt_count_increments_on_consumption(
    db_factory, seeded_world, memurai_available,
):
    """Happy path: each consumption increments attempt_count by 1.
    Mark-reviewed at end-of-cycle leaves the count at 1 (since it was
    only consumed once before being marked)."""
    with db_factory() as session:
        evt = _insert_wire_event(session, severity=5, status="pending", coin="BTC")
        evt_id = evt.id
        assert evt.attempt_count == 0  # baseline

    genesis = _make_genesis_with_mocks(db_factory, memurai_available)
    asyncio.run(genesis.run_cycle())

    with db_factory() as session:
        row = session.get(WireEvent, evt_id)
        assert row.regime_review_status == "reviewed"
        assert row.attempt_count == 1, (
            f"Happy path should increment attempt_count from 0 to 1, "
            f"got {row.attempt_count}"
        )
        assert row.last_error is None


def test_regime_review_marks_failed_after_three_attempts(
    db_factory, seeded_world, memurai_available,
):
    """Force exception three cycles in a row. After the 3rd cycle the
    row's attempt_count is at MAX. On the 4th cycle, the pre-flip
    pass sees attempt_count >= MAX and flips the row to 'failed'
    with the generic exceeded-max-attempts last_error.

    Note (Critic iteration 3 Finding 2): the cycle-level exception
    no longer batch-stamps last_error on consumed rows. Only per-row
    failures stamp; cycle-level failures (post_to_agora here) leave
    last_error untouched. The pre-flip pass populates a generic
    last_error message at the time of the flip.
    """
    assert REGIME_REVIEW_MAX_ATTEMPTS == 3  # contract guard

    with db_factory() as session:
        evt = _insert_wire_event(session, severity=5, status="pending", coin="BTC")
        evt_id = evt.id

    # Cycles 1-3: force run_cycle to raise after consumption.
    for cycle_num in range(REGIME_REVIEW_MAX_ATTEMPTS):
        genesis = _make_genesis_with_mocks(db_factory, memurai_available)
        genesis.post_to_agora = AsyncMock(
            side_effect=RuntimeError(f"simulated cycle {cycle_num+1} failure"),
        )
        asyncio.run(genesis.run_cycle())

    with db_factory() as session:
        row = session.get(WireEvent, evt_id)
        assert row.regime_review_status == "pending"
        assert row.attempt_count == REGIME_REVIEW_MAX_ATTEMPTS, (
            f"After {REGIME_REVIEW_MAX_ATTEMPTS} crash cycles, attempt_count "
            f"should be at the cap. Got {row.attempt_count}."
        )
        # last_error stays None — cycle-level failures don't stamp.
        assert row.last_error is None, (
            f"Cycle-level exception leaked into per-row last_error: "
            f"{row.last_error!r}. The Critic iteration 3 Finding 2 "
            f"contract forbids batch-stamping."
        )

    # Cycle 4: clean cycle. The pre-flip pass picks up the row at the
    # cap and flips it to 'failed' with a generic message.
    genesis = _make_genesis_with_mocks(db_factory, memurai_available)
    asyncio.run(genesis.run_cycle())

    with db_factory() as session:
        row = session.get(WireEvent, evt_id)
        assert row.regime_review_status == "failed", (
            f"After {REGIME_REVIEW_MAX_ATTEMPTS} failed attempts, row should "
            f"flip to 'failed' on the next cycle. Got {row.regime_review_status!r}."
        )
        assert row.last_error is not None
        assert "exceeded max" in row.last_error.lower()


def test_regime_review_failed_rows_excluded_from_consumption_query(
    db_factory, seeded_world, memurai_available, capsys,
):
    """Direct-insert a 'failed' row → run_cycle → not picked up. The
    consumption query filters by status='pending' so terminal rows
    never get consumed again."""
    with db_factory() as session:
        evt = WireEvent(
            canonical_hash="failed-row-test",
            coin="BTC", event_type="exchange_outage",
            severity=5,
            summary="Synthetic failed row",
            occurred_at=datetime.now(timezone.utc),
            haiku_cost_usd=0.0,
            regime_review_status="failed",
            attempt_count=REGIME_REVIEW_MAX_ATTEMPTS,
            last_error="exceeded max regime-review attempts",
        )
        session.add(evt)
        session.commit()
        evt_id = evt.id

    genesis = _make_genesis_with_mocks(db_factory, memurai_available)
    asyncio.run(genesis.run_cycle())

    captured = _strip_ansi(capsys.readouterr().out)
    consume_lines = [
        line for line in captured.splitlines()
        if "genesis_consuming_regime_review" in line
    ]
    assert not consume_lines, (
        f"'failed' row was re-consumed: {consume_lines!r}. The "
        f"WHERE status='pending' filter is broken."
    )

    with db_factory() as session:
        row = session.get(WireEvent, evt_id)
        assert row.regime_review_status == "failed"  # unchanged
        assert row.attempt_count == REGIME_REVIEW_MAX_ATTEMPTS  # unchanged


# ---------------------------------------------------------------------------
# Critic iteration 2 Finding 2: UPDATE filter race
# ---------------------------------------------------------------------------


def test_mid_cycle_inserts_remain_pending(
    db_factory, seeded_world, memurai_available,
):
    """A new pending row inserted between consumption (step 2c) and
    mark-reviewed (step 12) must NOT be marked 'reviewed'. The UPDATE
    filters by id IN (consumed_ids), so a row whose ID was never in
    that list is untouched.

    Implementation: stub `_mark_regime_reviews_reviewed` to insert a
    new pending row BEFORE running the original UPDATE — this is the
    closest deterministic test for the race window."""
    with db_factory() as session:
        original = _insert_wire_event(
            session, severity=5, status="pending", coin="BTC",
        )
        original_id = original.id

    genesis = _make_genesis_with_mocks(db_factory, memurai_available)

    # Wrap the real mark helper. Before it runs, insert a new pending
    # row simulating a digester landing a sev-5 event mid-cycle.
    real_mark = genesis._mark_regime_reviews_reviewed
    new_row_holder = {"id": None}

    def _wrapped_mark(event_ids):
        with db_factory() as session:
            new_row = _insert_wire_event(
                session, severity=5, status="pending", coin="ETH",
            )
            new_row_holder["id"] = new_row.id
        real_mark(event_ids)

    genesis._mark_regime_reviews_reviewed = _wrapped_mark
    asyncio.run(genesis.run_cycle())

    with db_factory() as session:
        original_row = session.get(WireEvent, original_id)
        new_row = session.get(WireEvent, new_row_holder["id"])
        assert original_row.regime_review_status == "reviewed"
        assert new_row.regime_review_status == "pending", (
            f"Mid-cycle insert was silently marked reviewed — the UPDATE "
            f"filter is too broad. Got status={new_row.regime_review_status!r}."
        )


# ---------------------------------------------------------------------------
# Critic iteration 2 Finding 3: backfill 30-min cutoff
# ---------------------------------------------------------------------------


def test_backfill_marks_old_sev_5_as_skipped(db_factory):
    """Pre-populate sev-5 rows at 1m / 35m / 24h ages, run the
    migration's backfill SQL against an empty schema, verify only the
    1m row is 'pending'. The 30-min cutoff matches the operator-halt
    auto-expire TTL — anything older is historical, not actionable."""
    from datetime import timedelta
    import importlib.util
    import os as _os
    from sqlalchemy import text as sql_text

    # alembic/versions/ isn't a real Python package, so import the
    # migration module by file path to read its BACKFILL_WINDOW_MINUTES
    # constant. This keeps the test source-of-truth-coupled to the
    # migration without duplicating the value.
    _project_root = _os.path.dirname(
        _os.path.dirname(_os.path.abspath(__file__))
    )
    _mig_path = _os.path.join(
        _project_root, "alembic", "versions",
        "phase_10_wire_006_regime_review_status.py",
    )
    _spec = importlib.util.spec_from_file_location(
        "_mig006", _mig_path,
    )
    _mig = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_mig)
    assert _mig.BACKFILL_WINDOW_MINUTES == 30

    now = datetime.now(timezone.utc)
    ages_minutes = [1, 35, 24 * 60]  # 1m (recent), 35m (just past), 24h (old)
    coins = ["RECENT", "JUSTPAST", "OLD"]

    with db_factory() as session:
        for age_min, coin in zip(ages_minutes, coins):
            evt = WireEvent(
                canonical_hash=f"backfill-test-{coin}",
                coin=coin, event_type="exchange_outage",
                severity=5,
                summary=f"sev-5 from {age_min}m ago",
                occurred_at=now - timedelta(minutes=age_min),
                haiku_cost_usd=0.0,
                # No regime_review_status set — uses server default
                # 'skipped' as if newly migrated.
            )
            session.add(evt)
        session.commit()

        # Apply the migration's backfill SQL directly. This is the
        # same parametrized statement the migration runs in upgrade()
        # — verifying the WHERE clause boundaries hold against a real
        # SQL backend.
        cutoff = now - timedelta(minutes=_mig.BACKFILL_WINDOW_MINUTES)
        session.execute(
            sql_text(
                "UPDATE wire_events SET regime_review_status = 'pending' "
                "WHERE severity = 5 AND duplicate_of IS NULL "
                "AND occurred_at >= :cutoff"
            ),
            {"cutoff": cutoff},
        )
        session.commit()

        rows_by_coin = {
            r.coin: r for r in session.execute(select(WireEvent)).scalars().all()
        }
        assert rows_by_coin["RECENT"].regime_review_status == "pending", (
            "Sev-5 row 1m old should be queued for review."
        )
        assert rows_by_coin["JUSTPAST"].regime_review_status == "skipped", (
            f"Sev-5 row 35m old (just past 30m cutoff) should stay skipped, "
            f"got {rows_by_coin['JUSTPAST'].regime_review_status!r}."
        )
        assert rows_by_coin["OLD"].regime_review_status == "skipped", (
            f"Sev-5 row 24h old should stay skipped to prevent stale "
            f"replay, got {rows_by_coin['OLD'].regime_review_status!r}."
        )


# ---------------------------------------------------------------------------
# Critic iteration 2 Finding 5: consumption-query failure escalation
# ---------------------------------------------------------------------------


def test_consumption_query_failure_escalates_after_three_cycles(
    db_factory, seeded_world, memurai_available, capsys,
):
    """The consumption query path itself raises. Three consecutive
    cycles of failure escalate to CRITICAL + system-alert (instead of
    the silent WARNING the original handler emitted). On the 4th
    cycle, restore the query to working — counter resets and no
    further escalation log fires."""
    assert REGIME_REVIEW_QUERY_FAILURE_ALERT_THRESHOLD == 3

    genesis = _make_genesis_with_mocks(db_factory, memurai_available)

    # Capture system-alert posts so the test asserts the alert went
    # out, not just the CRITICAL log.
    alert_posts: list[dict] = []

    async def _capture_post(channel, content, **kw):
        alert_posts.append({"channel": channel, "content": content, **kw})

    genesis.post_to_agora = AsyncMock(side_effect=_capture_post)

    # Force three consecutive failures of the consumption query.
    raise_helper = MagicMock(
        side_effect=RuntimeError("simulated DB unreachable"),
    )
    genesis._consume_pending_regime_reviews = raise_helper

    for _ in range(REGIME_REVIEW_QUERY_FAILURE_ALERT_THRESHOLD):
        asyncio.run(genesis.run_cycle())

    captured = _strip_ansi(capsys.readouterr().out)
    escalated_logs = [
        line for line in captured.splitlines()
        if "regime_review_query_failure_escalated" in line
    ]
    assert escalated_logs, (
        f"After {REGIME_REVIEW_QUERY_FAILURE_ALERT_THRESHOLD} consecutive "
        f"consumption-query failures, expected a CRITICAL escalation log. "
        f"None found in:\n{captured}"
    )
    assert (
        genesis._regime_review_query_failure_count
        >= REGIME_REVIEW_QUERY_FAILURE_ALERT_THRESHOLD
    )

    # Verify the system-alert post landed (importance=2 = critical).
    escalation_alerts = [
        a for a in alert_posts
        if a.get("channel") == "system-alerts"
        and "REGIME REVIEW" in (a.get("content") or "")
    ]
    assert escalation_alerts, (
        f"No system-alert post for the escalation. all posts: {alert_posts!r}"
    )
    assert escalation_alerts[0].get("importance") == 2

    # Cycle 4: restore the query to working. Counter resets; no new
    # escalation log fires.
    genesis._consume_pending_regime_reviews = MagicMock(return_value=[])
    capsys.readouterr()  # drain
    alert_posts.clear()
    asyncio.run(genesis.run_cycle())

    assert genesis._regime_review_query_failure_count == 0, (
        f"Counter should reset on first successful consumption. "
        f"Got {genesis._regime_review_query_failure_count}"
    )
    captured4 = _strip_ansi(capsys.readouterr().out)
    fresh_escalations = [
        line for line in captured4.splitlines()
        if "regime_review_query_failure_escalated" in line
    ]
    assert not fresh_escalations, (
        f"Fresh escalation log fired during recovery cycle: {fresh_escalations!r}"
    )
    fresh_alerts = [a for a in alert_posts if a.get("channel") == "system-alerts"]
    assert not fresh_alerts, (
        f"Fresh system-alert post during recovery cycle: {fresh_alerts!r}"
    )


# ---------------------------------------------------------------------------
# Critic iteration 3 Finding 1 (HIGH): attempt-cap correctness
# ---------------------------------------------------------------------------


def test_regime_review_exact_attempt_count_at_failure_cap(
    db_factory, seeded_world, memurai_available,
):
    """Run cycles until the row is marked 'failed'. Verify
    attempt_count == MAX_ATTEMPTS at the time of the flip — NOT
    MAX_ATTEMPTS+1. Locks in the no-extra-attempt-past-the-cap
    contract."""
    assert REGIME_REVIEW_MAX_ATTEMPTS == 3

    with db_factory() as session:
        evt = _insert_wire_event(session, severity=5, status="pending", coin="BTC")
        evt_id = evt.id

    # Three crash cycles take attempt_count from 0 to 3.
    for i in range(REGIME_REVIEW_MAX_ATTEMPTS):
        g = _make_genesis_with_mocks(db_factory, memurai_available)
        g.post_to_agora = AsyncMock(side_effect=RuntimeError(f"cycle {i+1}"))
        asyncio.run(g.run_cycle())

    with db_factory() as session:
        row = session.get(WireEvent, evt_id)
        assert row.attempt_count == REGIME_REVIEW_MAX_ATTEMPTS
        assert row.regime_review_status == "pending"

    # Cycle 4: the pre-flip pass flips the row WITHOUT incrementing
    # again. attempt_count must remain at the cap exactly.
    g = _make_genesis_with_mocks(db_factory, memurai_available)
    asyncio.run(g.run_cycle())

    with db_factory() as session:
        row = session.get(WireEvent, evt_id)
        assert row.regime_review_status == "failed"
        assert row.attempt_count == REGIME_REVIEW_MAX_ATTEMPTS, (
            f"Off-by-one bug: at the time of the flip, attempt_count "
            f"should equal MAX ({REGIME_REVIEW_MAX_ATTEMPTS}), got "
            f"{row.attempt_count}. The cap must NOT allow an extra "
            f"attempt past the limit."
        )


def test_regime_review_failed_row_excluded_from_select(
    db_factory, seeded_world, memurai_available, capsys,
):
    """Direct-insert a row at status='failed' with attempt_count=MAX.
    Run cycle. Assert the row is NOT consumed (no log line, no
    attempt_count increment). Defends the SELECT exclusion of
    capped-and-flipped rows — the load-bearing guard against the
    'consumed forever' poison-pill scenario."""
    with db_factory() as session:
        evt = WireEvent(
            canonical_hash="failed-select-test",
            coin="BTC", event_type="exchange_outage",
            severity=5,
            summary="Already-failed row",
            occurred_at=datetime.now(timezone.utc),
            haiku_cost_usd=0.0,
            regime_review_status="failed",
            attempt_count=REGIME_REVIEW_MAX_ATTEMPTS,
            last_error="exceeded max attempts",
        )
        session.add(evt)
        session.commit()
        evt_id = evt.id

    g = _make_genesis_with_mocks(db_factory, memurai_available)
    asyncio.run(g.run_cycle())

    captured = _strip_ansi(capsys.readouterr().out)
    consume_lines = [
        l for l in captured.splitlines()
        if "genesis_consuming_regime_review" in l
        and f"event_id={evt_id}" in l
    ]
    assert not consume_lines, (
        f"'failed' row was selected/consumed: {consume_lines!r}. "
        f"SELECT exclusion is broken."
    )

    with db_factory() as session:
        row = session.get(WireEvent, evt_id)
        assert row.regime_review_status == "failed"
        assert row.attempt_count == REGIME_REVIEW_MAX_ATTEMPTS, (
            f"attempt_count was incremented for a 'failed' row: "
            f"{row.attempt_count}. The SELECT must filter terminal rows out."
        )


# ---------------------------------------------------------------------------
# Critic iteration 3 Finding 2 (HIGH): per-row last_error attribution
# ---------------------------------------------------------------------------


def test_last_error_attaches_to_offending_row_only(
    db_factory, seeded_world, memurai_available,
):
    """Batch of 5 pending rows, force exception during processing of
    row #3 (index 2). Verify last_error is set on row #3 ONLY; rows
    1, 2, 4, 5 keep last_error NULL.

    Test seam: override `_process_pending_regime_review_row` on the
    instance to raise selectively. The production helper increments
    attempt_count + emits the structured log; the test override
    raises BEFORE doing either when called for the offending row,
    so attempt_count for rows 1-2 increments (they processed before
    the exception) and rows 3-5 stay at 0 (3 raised, 4-5 never
    reached because rows 1-2's increments already committed in the
    same session — but the per-row try/except keeps the loop going).
    """
    with db_factory() as session:
        rows = [
            _insert_wire_event(
                session, severity=5, status="pending", coin=f"COIN{i}",
                event_type=f"event_{i}",
            )
            for i in range(5)
        ]
        all_ids = [r.id for r in rows]
        offending_id = rows[2].id

    g = _make_genesis_with_mocks(db_factory, memurai_available)

    real_process = g._process_pending_regime_review_row

    def _selective_process(row):
        if row.id == offending_id:
            raise RuntimeError("simulated row-3 processing failure")
        return real_process(row)

    g._process_pending_regime_review_row = _selective_process
    asyncio.run(g.run_cycle())

    with db_factory() as session:
        rows_after = {
            r.id: r for r in session.execute(
                select(WireEvent).where(WireEvent.id.in_(all_ids))
            ).scalars().all()
        }

    offender = rows_after[offending_id]
    assert offender.last_error is not None, (
        "last_error not stamped on the offending row."
    )
    assert "RuntimeError" in offender.last_error
    assert "row-3" in offender.last_error
    # Offender stayed pending (NOT consumed -> NOT marked reviewed).
    assert offender.regime_review_status == "pending"

    for rid, r in rows_after.items():
        if rid == offending_id:
            continue
        assert r.last_error is None, (
            f"Innocent row id={rid} got a last_error stamp: "
            f"{r.last_error!r}. Per-row attribution is broken — the "
            f"offending row's failure leaked into the rest of the batch."
        )
        # Innocent rows were consumed and marked reviewed.
        assert r.regime_review_status == "reviewed", (
            f"Innocent row id={rid} not marked reviewed: "
            f"status={r.regime_review_status!r}"
        )
        assert r.attempt_count == 1


# ---------------------------------------------------------------------------
# Critic iteration 3 Finding 4 (MEDIUM): consecutive-only contract
# ---------------------------------------------------------------------------


def test_escalation_does_not_fire_on_intermittent_pattern(
    db_factory, seeded_world, memurai_available, capsys,
):
    """Intermittent failure pattern (fail, success, fail, fail, fail)
    must NOT escalate. Counter resets on success in cycle 2; only 3
    consecutive failures (cycles 3-5) follow, which equals the
    threshold but the pattern as a whole had 4/5 failures.

    War Room locked the consecutive-only contract in iteration 3
    (Finding 4). The cumulative-window detector lives in
    DEFERRED_ITEMS_TRACKER.md as a future observability improvement.
    """
    g = _make_genesis_with_mocks(db_factory, memurai_available)

    alert_posts: list[dict] = []

    async def _capture_post(channel, content, **kw):
        alert_posts.append({"channel": channel, "content": content, **kw})

    g.post_to_agora = AsyncMock(side_effect=_capture_post)

    fail_helper = MagicMock(side_effect=RuntimeError("intermittent"))
    success_helper = MagicMock(return_value=[])

    # Pattern: fail, success, fail, fail, fail.
    cycle_pattern = [
        fail_helper,     # cycle 1 — fail (counter -> 1)
        success_helper,  # cycle 2 — success (counter -> 0)
        fail_helper,     # cycle 3 — fail (counter -> 1)
        fail_helper,     # cycle 4 — fail (counter -> 2)
        fail_helper,     # cycle 5 — fail (counter -> 3, AT threshold)
    ]
    # Threshold is 3; the third consecutive failure (cycle 5) WILL
    # escalate by design. To exercise the "intermittent does not
    # escalate" property, the pattern must have failures spread such
    # that the counter never reaches the threshold. Adjust to:
    # fail, success, fail, fail (counter = 2, below threshold).
    cycle_pattern = [
        fail_helper,     # counter -> 1
        success_helper,  # counter -> 0
        fail_helper,     # counter -> 1
        fail_helper,     # counter -> 2
        success_helper,  # counter -> 0
        fail_helper,     # counter -> 1
    ]
    # 4 failures across 6 cycles, never 3 in a row -> NO escalation.

    for helper in cycle_pattern:
        g._consume_pending_regime_reviews = helper
        asyncio.run(g.run_cycle())

    captured = _strip_ansi(capsys.readouterr().out)
    escalations = [
        l for l in captured.splitlines()
        if "regime_review_query_failure_escalated" in l
    ]
    assert not escalations, (
        f"Intermittent failure pattern escalated despite never having "
        f"{REGIME_REVIEW_QUERY_FAILURE_ALERT_THRESHOLD} consecutive "
        f"failures. Found escalations: {escalations!r}"
    )

    escalation_alerts = [
        a for a in alert_posts
        if a.get("channel") == "system-alerts"
        and "REGIME REVIEW" in (a.get("content") or "")
    ]
    assert not escalation_alerts, (
        f"Intermittent pattern emitted a system-alert: {escalation_alerts!r}"
    )

    # Counter reset on the final success cycle.
    assert g._regime_review_query_failure_count == 1, (
        f"Counter should reflect the trailing failure count "
        f"(1 after the final fail), got "
        f"{g._regime_review_query_failure_count}"
    )


# ---------------------------------------------------------------------------
# Critic iteration 4 Finding 1 (HIGH): migration backfill idempotency
# ---------------------------------------------------------------------------


def test_backfill_migration_idempotent(db_factory):
    """The migration's backfill UPDATE filters by `regime_review_status
    = 'skipped'` so a second run cannot re-flip rows that have since
    been classified by the consumer (e.g. consumed-and-reviewed,
    failed, or in-flight pending). Pre-populates rows in varied states,
    invokes the migration's `backfill_pending_status()` helper twice,
    asserts the second run is a no-op.

    Critic iteration 4 follow-up 2: the test imports the shared helper
    `backfill_pending_status` directly from the migration module — the
    same helper `upgrade()` invokes — so the production code path and
    the test exercise IDENTICAL SQL. No Python reimplementation.
    """
    from datetime import timedelta
    import importlib.util
    import os as _os

    _project_root = _os.path.dirname(
        _os.path.dirname(_os.path.abspath(__file__))
    )
    _mig_path = _os.path.join(
        _project_root, "alembic", "versions",
        "phase_10_wire_006_regime_review_status.py",
    )
    _spec = importlib.util.spec_from_file_location("_mig006_idem", _mig_path)
    _mig = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_mig)

    now = datetime.now(timezone.utc)

    # Mix of rows that will exercise the WHERE clause:
    #   - a recent sev-5 still 'skipped'         -> should flip to 'pending'
    #   - an old sev-5 still 'skipped'           -> should stay 'skipped'
    #   - a recent sev-5 already 'reviewed'      -> must NOT flip back to 'pending'
    #   - a recent sev-5 already 'failed'        -> must NOT flip back to 'pending'
    #   - a recent sev-5 already 'pending'       -> stays 'pending' (no-op)
    fixtures = [
        ("RECENT_SKIP",    1,  5, "skipped"),
        ("OLD_SKIP",       60, 5, "skipped"),
        ("ALREADY_REVIEW", 1,  5, "reviewed"),
        ("ALREADY_FAIL",   1,  5, "failed"),
        ("ALREADY_PEND",   1,  5, "pending"),
    ]
    with db_factory() as session:
        for coin, age_min, sev, status in fixtures:
            evt = WireEvent(
                canonical_hash=f"backfill-idem-{coin}",
                coin=coin, event_type="exchange_outage",
                severity=sev,
                summary=f"backfill idempotency fixture {coin}",
                occurred_at=now - timedelta(minutes=age_min),
                haiku_cost_usd=0.0,
                regime_review_status=status,
                attempt_count=(
                    REGIME_REVIEW_MAX_ATTEMPTS if status == "failed" else 0
                ),
            )
            session.add(evt)
        session.commit()

        # FIRST RUN — invoke the migration's shared helper directly
        # against this session's connection. Production `upgrade()`
        # passes `op.get_bind()` (a Connection); we pass
        # `session.connection()` for shape-equivalent semantics on
        # SQLAlchemy 2.0 (where `Engine.execute` was removed).
        cutoff = now - timedelta(minutes=_mig.BACKFILL_WINDOW_MINUTES)
        _mig.backfill_pending_status(session.connection(), cutoff)
        session.commit()

        first_state = {
            r.coin: r.regime_review_status
            for r in session.execute(select(WireEvent)).scalars().all()
        }

        # SECOND RUN of the same helper, with cutoff recomputed (in
        # production a re-run of `alembic upgrade head` runs at a
        # fresh wall-clock; the WHERE-by-status filter is what makes
        # the second run a no-op even though cutoff drifted).
        second_cutoff = datetime.now(timezone.utc) - timedelta(
            minutes=_mig.BACKFILL_WINDOW_MINUTES
        )
        _mig.backfill_pending_status(session.connection(), second_cutoff)
        session.commit()

        second_state = {
            r.coin: r.regime_review_status
            for r in session.execute(select(WireEvent)).scalars().all()
        }

    # Expected post-first-run state.
    assert first_state["RECENT_SKIP"] == "pending"
    assert first_state["OLD_SKIP"] == "skipped"
    assert first_state["ALREADY_REVIEW"] == "reviewed"
    assert first_state["ALREADY_FAIL"] == "failed"
    assert first_state["ALREADY_PEND"] == "pending"

    # Idempotency: state after the second run is identical.
    assert second_state == first_state, (
        f"Backfill is NOT idempotent. "
        f"first_state={first_state!r} "
        f"second_state={second_state!r}. "
        f"Re-running `alembic upgrade head` corrupted classifications "
        f"the consumer had already produced."
    )


# ---------------------------------------------------------------------------
# Critic iteration 4 Finding 2 (MEDIUM): commit failure does not leak
# ---------------------------------------------------------------------------


def test_consume_returns_empty_list_on_commit_failure(
    db_factory, seeded_world, memurai_available,
):
    """Mock `session.commit()` to raise on the first cycle and succeed
    on the second. Verify caller-observable invariants:
      - cycle 1: no event_ids leak to the mark-reviewed path
                 (cycle_report.regime_reviews_consumed is None or 0,
                  the row's attempt_count is unchanged due to rollback,
                  the row's status remains 'pending', counter
                  incremented to 1)
      - cycle 2: row is consumed normally, attempt_count starts fresh
                 from DB state and increments to 1, status='reviewed',
                 counter resets to 0.

    Implementation detail: helper raises on commit failure; run_cycle
    step 2c's existing try/except catches and sets
    `consumed_regime_review_ids = []`. Test asserts the run_cycle-layer
    behavior because that is the contract the rest of the system
    relies on (mark-reviewed path, escalation, cycle_report).
    """
    with db_factory() as session:
        evt = _insert_wire_event(
            session, severity=5, status="pending", coin="BTC",
        )
        evt_id = evt.id

    g = _make_genesis_with_mocks(db_factory, memurai_available)

    # Inject a one-shot commit failure ONLY on sessions created by the
    # consume helper, so other run_cycle steps (health check, dms
    # monitor) aren't disturbed. Toggle the state flag immediately
    # before invoking the consume helper, untoggle after.
    real_factory = g.db_session_factory
    fail_state = {"active": False}

    def _maybe_failing_factory():
        session = real_factory()
        if fail_state["active"]:
            def _raising_commit():
                raise RuntimeError("simulated commit failure")
            session.commit = _raising_commit
        return session

    g.db_session_factory = _maybe_failing_factory

    # Wrap the consume helper so the failure window is narrow:
    # only the helper's own session.commit raises, and only on the
    # first invocation (cycle 2 must execute normally).
    real_consume = g._consume_pending_regime_reviews
    one_shot = {"used": False}

    def _consume_with_failing_commit():
        if not one_shot["used"]:
            one_shot["used"] = True
            fail_state["active"] = True
            try:
                return real_consume()
            finally:
                fail_state["active"] = False
        return real_consume()

    g._consume_pending_regime_reviews = _consume_with_failing_commit

    # CYCLE 1 — commit fails.
    report1 = asyncio.run(g.run_cycle())
    assert report1.get("regime_reviews_consumed") in (None, 0), (
        f"event_ids leaked into cycle_report despite commit failure: "
        f"{report1.get('regime_reviews_consumed')!r}"
    )

    with db_factory() as session:
        row = session.get(WireEvent, evt_id)
        assert row.regime_review_status == "pending", (
            f"Row was marked '{row.regime_review_status}' even though "
            f"the commit that would have persisted the increment "
            f"failed. The mark-reviewed UPDATE ran on a leaked event_id."
        )
        assert row.attempt_count == 0, (
            f"attempt_count={row.attempt_count} despite rollback. "
            f"Increments must roll back when the commit fails."
        )
    assert g._regime_review_query_failure_count >= 1, (
        f"Counter should have incremented for the commit failure. "
        f"Got {g._regime_review_query_failure_count}."
    )

    # CYCLE 2 — commit succeeds, row processes normally from fresh
    # DB state (attempt_count=0).
    report2 = asyncio.run(g.run_cycle())
    assert report2.get("regime_reviews_consumed") == 1
    with db_factory() as session:
        row = session.get(WireEvent, evt_id)
        assert row.regime_review_status == "reviewed"
        assert row.attempt_count == 1, (
            f"Cycle 2 should have started attempt_count fresh from "
            f"DB state (0) and incremented to 1; got {row.attempt_count}."
        )
    assert g._regime_review_query_failure_count == 0, (
        f"Counter should reset on successful consumption. Got "
        f"{g._regime_review_query_failure_count}."
    )
