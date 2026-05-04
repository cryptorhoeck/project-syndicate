"""
Unit tests for `src.common.async_bridge.run_async_safely` —
the safe sync->async wrapper that closes WIRING_AUDIT_REPORT.md
subsystem P (the "fragile run_until_complete + bare except" pattern).

Five tests per the directive:
  - happy path with no running loop
  - happy path with a running loop (worker-thread dispatch)
  - exception is caught, returns failure indicator
  - KeyboardInterrupt and SystemExit propagate (narrow exception scope)
  - structured WARNING log on failure with `async_bridge_failure` field
"""

from __future__ import annotations

import asyncio
import logging
from unittest.mock import MagicMock

import pytest

from src.common.async_bridge import run_async_safely


@pytest.fixture(autouse=True)
def _restore_event_loop_after_test():
    """asyncio.run() closes the current loop. Other tests in the
    suite still use the deprecated `asyncio.get_event_loop()`
    pattern; restore a fresh loop after each test so suite ordering
    doesn't matter."""
    yield
    try:
        asyncio.set_event_loop(asyncio.new_event_loop())
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Tests 5–9 (numbered per directive)
# ---------------------------------------------------------------------------


def test_run_async_safely_with_no_running_loop_succeeds():
    """No loop on this thread → asyncio.run path. Coroutine completes
    cleanly, helper returns (True, None)."""
    out: dict = {}

    async def _coro():
        await asyncio.sleep(0)
        out["ran"] = True
        return "ignored"

    success, exc = run_async_safely(_coro())
    assert success is True
    assert exc is None
    assert out["ran"] is True


def test_run_async_safely_with_running_loop_succeeds():
    """Loop is running on this thread → worker-thread path. Coroutine
    completes via dispatched fresh loop, helper returns (True, None)."""
    out: dict = {}

    async def _outer():
        async def _inner():
            await asyncio.sleep(0)
            out["ran_in_worker"] = True

        # Sanity: confirm a loop IS running so we exercise the
        # worker-thread branch, not the asyncio.run branch.
        running = asyncio.get_running_loop()
        assert running is not None

        return run_async_safely(_inner())

    success, exc = asyncio.run(_outer())
    assert success is True
    assert exc is None
    assert out["ran_in_worker"] is True


def test_run_async_safely_catches_exception_returns_failure():
    """Coroutine raises Exception → helper catches, logs WARNING,
    returns (False, exc) with the exception preserved."""

    class MyError(Exception):
        pass

    async def _raises():
        raise MyError("boom")

    success, exc = run_async_safely(_raises())
    assert success is False
    assert isinstance(exc, MyError)
    assert "boom" in str(exc)


def test_run_async_safely_does_not_catch_keyboard_interrupt():
    """KeyboardInterrupt and SystemExit are BaseException subclasses
    that the narrow `except Exception` deliberately does NOT catch.
    The helper must propagate cooperative-cancellation signals."""

    async def _kbi():
        raise KeyboardInterrupt("user pressed ctrl-c")

    with pytest.raises(KeyboardInterrupt):
        run_async_safely(_kbi())

    async def _sysexit():
        raise SystemExit(0)

    with pytest.raises(SystemExit):
        run_async_safely(_sysexit())


def test_run_async_safely_logs_structured_failure_field(caplog):
    """The failure-path WARNING log must carry the
    `async_bridge_failure=True` extra field plus exception type and
    string. This is the contract observability dashboards rely on
    (the field is documented in the module docstring as the canonical
    failure marker)."""

    async def _raises():
        raise ValueError("structured-test")

    caplog.set_level(logging.WARNING)
    success, exc = run_async_safely(_raises())
    assert success is False
    assert isinstance(exc, ValueError)

    matching = [
        rec for rec in caplog.records
        if getattr(rec, "async_bridge_failure", None) is True
    ]
    assert matching, (
        f"Expected a WARNING log with async_bridge_failure=True. "
        f"Got: {[(r.levelname, r.getMessage()) for r in caplog.records]!r}"
    )
    rec = matching[0]
    assert rec.levelname == "WARNING"
    assert getattr(rec, "exception_type", None) == "ValueError"
    assert getattr(rec, "exception_str", None) == "structured-test"


def test_run_async_safely_accepts_custom_logger(caplog):
    """Optional `logger` argument routes the WARNING through the
    caller's logger instead of the module logger."""
    custom = logging.getLogger("test_eval_engine_custom_logger")

    async def _raises():
        raise RuntimeError("custom-logger-test")

    caplog.set_level(logging.WARNING, logger="test_eval_engine_custom_logger")
    success, _ = run_async_safely(_raises(), logger=custom)
    assert success is False

    custom_records = [
        r for r in caplog.records
        if r.name == "test_eval_engine_custom_logger"
    ]
    assert custom_records, (
        f"Failure log did not route through the supplied logger. "
        f"Records: {[(r.name, r.levelname) for r in caplog.records]!r}"
    )


def test_run_async_safely_with_running_loop_propagates_exception():
    """Exception inside the worker-thread path is unwrapped from the
    Future and surfaced as the original exception type, not as a
    `concurrent.futures.TimeoutError` or wrapping shim."""

    class MyWorkerError(Exception):
        pass

    async def _outer():
        async def _inner():
            raise MyWorkerError("from-worker")

        return run_async_safely(_inner())

    success, exc = asyncio.run(_outer())
    assert success is False
    assert isinstance(exc, MyWorkerError)
    assert "from-worker" in str(exc)
