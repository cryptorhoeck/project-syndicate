"""
Async/sync bridge — fix for the fragile `run_until_complete + bare except`
pattern surfaced by WIRING_AUDIT_REPORT.md subsystem P.

PROBLEM (closed by this module)
-------------------------------
Code shaped like ::

    try:
        asyncio.get_event_loop().run_until_complete(some_coro())
    except Exception:
        pass

is a silent-failure landmine when the calling thread already has a
running event loop. ``run_until_complete`` raises
``RuntimeError: This event loop is already running`` and the bare
``except: pass`` swallows the failure with no log line, no metric,
no alert. Side-effects of ``some_coro()`` — API cost tracking, fitness
updates, alert posts — are silently dropped under contended event-loop
state.

WHEN TO USE THIS HELPER
-----------------------
Call this helper from sync code that needs to run a coroutine to
completion AND may itself be invoked from an async context (i.e.,
the calling thread already has a running loop). Concretely: a sync
method on an async-heavy class, called as part of a larger ``async``
flow, that needs to dispatch a one-off coroutine.

WHEN NOT TO USE
---------------
- The caller is itself ``async def``: just ``await`` the coroutine.
  The helper still works (it offloads to a worker thread), but
  blocks the loop while the worker runs.
- The caller is pure sync top-level (CLI, test runner, script): just
  use ``asyncio.run(coro)`` directly. The helper still works (no loop
  detected, falls back to ``asyncio.run``).

EXECUTION PATHS
---------------
1. **No running loop on this thread** → the helper calls
   ``asyncio.run(coro)`` directly. Fast, no thread overhead.
2. **Running loop on this thread** → the helper offloads via
   ``concurrent.futures.ThreadPoolExecutor`` (max_workers=1) and runs
   the coroutine on a fresh loop in the worker thread. The calling
   thread blocks on ``Future.result(timeout=...)``. This avoids the
   "loop already running" reentry trap and works correctly even when
   embedded deep inside an async call chain.

THREAD SAFETY NOTE FOR CALLERS
------------------------------
When the worker-thread path is taken (running loop on this thread),
the coroutine runs on a DIFFERENT thread than the caller. SQLAlchemy
``Session`` objects are NOT thread-safe; if the coroutine needs DB
access, it should create its own session via ``db_session_factory()``
inside the coroutine body — do NOT pass a session from the calling
thread into a coroutine you hand to this helper.

FAILURE HANDLING
----------------
The helper catches ``Exception`` (NOT ``BaseException``) — so
``KeyboardInterrupt`` and ``SystemExit`` propagate. On any caught
exception, the helper logs WARNING with a structured field
``async_bridge_failure=True`` plus the exception type and string,
and returns ``(False, exc)``. The caller is expected to track the
failure via its own counter and emit an escalation alert when
threshold is reached (see, e.g., ``EvaluationEngine`` for the
canonical consumer pattern).

The helper does NOT raise on failure; that policy is locked-in for
non-safety-critical paths where a transient async-bridge failure
must not abort the caller's broader work. Callers that need
fail-loud semantics should inspect the returned tuple and raise from
there.
"""

from __future__ import annotations

__version__ = "0.1.0"

import asyncio
import concurrent.futures
import logging
from typing import Coroutine, Optional, Tuple

logger = logging.getLogger(__name__)


# Default timeout for the worker-thread path. Long enough for normal
# DB roundtrips on Postgres; short enough that a stalled worker
# doesn't park the calling async flow indefinitely.
DEFAULT_BRIDGE_TIMEOUT_SECONDS = 30.0


def run_async_safely(
    coro: Coroutine,
    *,
    logger: Optional[logging.Logger] = None,
    timeout: float = DEFAULT_BRIDGE_TIMEOUT_SECONDS,
) -> Tuple[bool, Optional[Exception]]:
    """Run an async coroutine to completion from sync code.

    Detects whether an event loop is already running on the current
    thread and dispatches accordingly. See module docstring for the
    full contract.

    Args:
        coro: A coroutine object (the result of calling ``async_func()``,
            NOT the function itself).
        logger: Optional logger for the WARNING failure line. Falls
            back to this module's logger if not provided.
        timeout: Max wall-clock seconds to wait for the worker-thread
            path. Ignored on the no-running-loop path.

    Returns:
        ``(True, None)`` on success.
        ``(False, exc)`` on failure (any caught ``Exception``).

    Never raises except for ``KeyboardInterrupt`` / ``SystemExit``,
    which propagate.
    """
    log = logger or globals()["logger"]

    try:
        try:
            asyncio.get_running_loop()
            loop_running = True
        except RuntimeError:
            loop_running = False

        if loop_running:
            # Worker-thread path. The fresh loop in the worker is
            # created and torn down by ``asyncio.run`` per call.
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(asyncio.run, coro)
                future.result(timeout=timeout)
        else:
            # No running loop. Direct asyncio.run — fast, no thread.
            asyncio.run(coro)

        return (True, None)

    except (KeyboardInterrupt, SystemExit):
        # Narrow exception scope: cooperative cancellation must
        # propagate. Never swallow these.
        raise
    except Exception as exc:
        log.warning(
            "async_bridge_failure: %s: %s",
            type(exc).__name__, exc,
            extra={
                "async_bridge_failure": True,
                "exception_type": type(exc).__name__,
                "exception_str": str(exc),
            },
        )
        return (False, exc)
