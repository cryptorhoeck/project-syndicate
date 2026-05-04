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
from src.genesis.genesis import GenesisAgent, REGIME_REVIEW_BATCH_LIMIT
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
