"""
Wire scheduler — Arena wiring tests.

Closes WIRING_AUDIT_REPORT.md subsystem U: the Wire scheduler must be in
`scripts/run_arena.py`'s PROCESSES dict, must use the production
`run-scheduler --with-digest` invocation, must be in `shutdown_order`,
and must have a working preflight verifier that hard-aborts the Arena if
the scheduler fails to attempt a fetch within 60 seconds.

Same wiring-contract pattern as the trading-service and Warden hotfixes:
the test asserts the production code path produces a working scheduler,
not a unit test of the scheduler in isolation. Phase 10 already had unit
tests on the Wire scheduler that passed throughout the period when the
scheduler wasn't being started in production at all — those are not the
tests that catch this class of bug.
"""

from __future__ import annotations

import importlib
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Static structural guards: PROCESSES, shutdown_order, command shape
# ---------------------------------------------------------------------------


def _import_run_arena():
    """Import scripts.run_arena fresh. Module-level state (PROCESSES dict,
    shutdown_order references) is what we're inspecting."""
    return importlib.import_module("scripts.run_arena")


def test_wire_scheduler_in_processes_dict():
    """Regression guard: a future change that drops the wire_scheduler
    entry would silently re-introduce the WIRING_AUDIT_REPORT.md
    subsystem U bug. This test fails first."""
    run_arena = _import_run_arena()
    assert "wire_scheduler" in run_arena.PROCESSES, (
        "scripts.run_arena.PROCESSES is missing 'wire_scheduler'. The Arena "
        "would boot with an empty Wire pipeline — wire_events stay empty, "
        "severity-5 hooks never fire, Scout recent_signals always empty. "
        "See WIRING_AUDIT_REPORT.md subsystem U."
    )


def test_wire_scheduler_command_uses_production_cli_invocation():
    """The cmd must invoke the same Wire CLI command an operator would
    type for a production run. `--with-digest` is required so the Haiku
    digester runs alongside fetching — without it, the scheduler fetches
    raw items but never produces wire_events, and downstream consumers
    see empty data even though the scheduler is alive."""
    run_arena = _import_run_arena()
    entry = run_arena.PROCESSES["wire_scheduler"]
    cmd = entry["cmd"]

    # Must be `python -m src.wire.cli run-scheduler --with-digest`
    assert "-m" in cmd, f"wire_scheduler cmd should use module form, got: {cmd}"
    assert "src.wire.cli" in cmd, f"wire_scheduler cmd must invoke src.wire.cli, got: {cmd}"
    assert "run-scheduler" in cmd, f"wire_scheduler cmd missing run-scheduler subcommand, got: {cmd}"
    assert "--with-digest" in cmd, (
        f"wire_scheduler cmd missing --with-digest. Without this flag the "
        f"scheduler fetches raw items but never digests them into "
        f"wire_events — the dashboard, Scout context, and severity-5 hooks "
        f"all see empty data. cmd: {cmd}"
    )


def test_wire_scheduler_marked_critical():
    """Critical=True signals intent — the Arena should not silently drop
    wire_scheduler if a future runner consults the flag."""
    run_arena = _import_run_arena()
    entry = run_arena.PROCESSES["wire_scheduler"]
    assert entry.get("critical") is True, (
        "wire_scheduler must be marked critical. The intelligence layer "
        "is required for severity-5 safety hooks to fire."
    )


def test_wire_scheduler_starts_before_agents_in_processes_dict():
    """Insertion order in PROCESSES drives start order in the Arena's
    spawn loop. Wire scheduler MUST start before agents, because Scout's
    first cycle reads wire_events and a Strategist/Critic running before
    the Wire is up sees an empty dataset."""
    run_arena = _import_run_arena()
    keys = list(run_arena.PROCESSES.keys())
    assert keys.index("wire_scheduler") < keys.index("agents"), (
        f"wire_scheduler must start before agents. Order: {keys}"
    )
    assert keys.index("wire_scheduler") < keys.index("genesis"), (
        f"wire_scheduler must start before genesis (so any Genesis-driven "
        f"boot-sequence agents see live Wire). Order: {keys}"
    )


def test_wire_scheduler_in_shutdown_order():
    """Names absent from SHUTDOWN_ORDER are orphaned at Arena exit. Verify
    three things, since each in isolation can be spoofed:
      (1) the SHUTDOWN_ORDER module-level constant exists,
      (2) wire_scheduler is in it (runtime — a code comment cannot satisfy),
      (3) main() actually consumes SHUTDOWN_ORDER (otherwise a future
          refactor that hardcodes a different list inside main() would
          leave wire_scheduler in the constant but never get terminated).

    Without (3), a future refactor could pass (1) and (2) while quietly
    breaking the wiring — the MEDIUM Critic flagged in iteration review.
    """
    import inspect
    run_arena = _import_run_arena()

    # (1) constant exists
    assert hasattr(run_arena, "SHUTDOWN_ORDER"), (
        "scripts.run_arena.SHUTDOWN_ORDER missing — was the module-level "
        "constant removed? main() depends on it for graceful shutdown."
    )
    # (2) wire_scheduler is in it
    assert "wire_scheduler" in run_arena.SHUTDOWN_ORDER, (
        f"wire_scheduler missing from SHUTDOWN_ORDER ({run_arena.SHUTDOWN_ORDER}). "
        "The scheduler subprocess would be orphaned at Arena exit."
    )
    # (3) main() actually iterates over SHUTDOWN_ORDER
    main_src = inspect.getsource(run_arena.main)
    assert "SHUTDOWN_ORDER" in main_src, (
        "main() does not reference SHUTDOWN_ORDER. The module-level "
        "constant is in place but unused — wire_scheduler (and every "
        "other name in the list) would orphan at Arena exit. If main()'s "
        "shutdown loop was refactored to a different identifier, update "
        "this test to match — but make sure the new identifier is exactly "
        "what main() iterates over for graceful termination."
    )


# ---------------------------------------------------------------------------
# _verify_wire_scheduler_alive: behavior under controlled DB state
# ---------------------------------------------------------------------------


@pytest.fixture
def patch_run_arena_db(monkeypatch):
    """Replaces `create_engine` inside `scripts.run_arena` with a factory
    that returns canned `(latest_attempt, boot_at)` rows. Yields a setter
    the test uses to control what each query returns."""
    import scripts.run_arena as run_arena_mod

    state = {"row": None}

    class _FakeConn:
        def execute(self, _stmt):
            class _Result:
                @staticmethod
                def fetchone():
                    return state["row"]
            return _Result()
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False

    class _FakeEngine:
        def connect(self):
            return _FakeConn()
        def dispose(self):
            pass

    def _fake_create_engine(*args, **kwargs):
        return _FakeEngine()

    # The function does `from sqlalchemy import create_engine, text` inside
    # the body, so patch sqlalchemy at the module level.
    import sqlalchemy
    monkeypatch.setattr(sqlalchemy, "create_engine", _fake_create_engine)

    def _set_row(row):
        state["row"] = row

    return _set_row


def test_verify_wire_scheduler_alive_returns_true_when_fetch_is_fresh(patch_run_arena_db):
    """Happy path: a wire_source has attempted a fetch fresher than the
    Arena boot timestamp. Function returns True quickly."""
    run_arena = _import_run_arena()
    now = datetime.now(timezone.utc).replace(microsecond=0)
    boot_at = now - timedelta(seconds=10)
    latest = now  # fresh
    patch_run_arena_db((latest, boot_at))

    assert run_arena._verify_wire_scheduler_alive(timeout_seconds=5) is True


def test_verify_wire_scheduler_alive_returns_false_when_no_fetch_in_window(patch_run_arena_db):
    """Failure path: latest_attempt is None or older than boot. Function
    polls for the timeout window and returns False. Use a short timeout to
    keep the test fast."""
    run_arena = _import_run_arena()
    now = datetime.now(timezone.utc).replace(microsecond=0)
    boot_at = now  # boot just stamped
    # Latest attempt is BEFORE boot — this is what you'd see if the
    # scheduler hasn't ticked yet (or never ticks).
    latest = now - timedelta(seconds=120)
    patch_run_arena_db((latest, boot_at))

    t0 = time.time()
    result = run_arena._verify_wire_scheduler_alive(timeout_seconds=2)
    elapsed = time.time() - t0

    assert result is False
    assert elapsed >= 2.0, "verify must wait the full timeout before failing"


def test_verify_wire_scheduler_alive_returns_false_when_no_row(patch_run_arena_db):
    """No wire_source rows enabled (or fresh DB) — same fail-closed
    behavior. Function does not approve in the absence of evidence."""
    run_arena = _import_run_arena()
    patch_run_arena_db((None, None))

    assert run_arena._verify_wire_scheduler_alive(timeout_seconds=2) is False


def test_verify_wire_scheduler_alive_handles_mixed_tz_columns(patch_run_arena_db):
    """REGRESSION GUARD: the first manual run_arena.py validation crashed
    because `wire_source_health.last_fetch_attempt` is TIMESTAMPTZ
    (returned tz-aware by psycopg2) while `system_state.last_arena_boot_at`
    is TIMESTAMP (returned tz-naive). Comparing the two directly raises
    `TypeError: can't compare offset-naive and offset-aware datetimes`,
    which propagated up and exited the Arena with code 1 instead of the
    intended sys.exit(2). The verifier MUST normalize both sides before
    comparing.

    This test feeds the function exactly the shape the real Postgres
    schema returns: tz-aware latest_attempt, tz-naive boot_at. If the
    normalization is removed, the function raises TypeError instead of
    returning a bool, and this test fails."""
    run_arena = _import_run_arena()
    now = datetime.now(timezone.utc).replace(microsecond=0)
    # latest_attempt is tz-AWARE (TIMESTAMPTZ semantics)
    latest_attempt_aware = now
    # boot_at is tz-NAIVE (TIMESTAMP semantics) — matches what
    # `system_state.last_arena_boot_at` actually returns from psycopg2
    boot_at_naive = (now - timedelta(seconds=10)).replace(tzinfo=None)
    patch_run_arena_db((latest_attempt_aware, boot_at_naive))

    # Must return True (latest_attempt is fresher than boot_at) and must
    # NOT raise TypeError on the comparison.
    result = run_arena._verify_wire_scheduler_alive(timeout_seconds=5)
    assert result is True


def test_verify_wire_scheduler_alive_returns_false_when_db_raises(monkeypatch):
    """DB unreachable during the verification window — the function logs
    warnings and continues polling, returning False after timeout."""
    run_arena = _import_run_arena()
    import sqlalchemy

    def _raise(*args, **kwargs):
        raise RuntimeError("simulated DB unavailability")

    monkeypatch.setattr(sqlalchemy, "create_engine", _raise)

    t0 = time.time()
    result = run_arena._verify_wire_scheduler_alive(timeout_seconds=2)
    elapsed = time.time() - t0

    assert result is False
    assert elapsed >= 2.0
