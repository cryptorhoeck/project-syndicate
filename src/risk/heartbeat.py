"""
Dead Man's Switch — independent system health monitor for Project Syndicate.

This script runs as its own standalone process, entirely outside any agent
framework.  Every CHECK_INTERVAL seconds it verifies external dependencies
and, on success, advances `system_state.last_heartbeat_at` to NOW().  Other
processes (Genesis, Warden, the meta-monitor) read this column to detect
DMS liveness.

External health checks performed each cycle:

    1. PostgreSQL is accessible (psycopg2 connection).
    2. Redis / Memurai is accessible (redis.Redis.ping()).

If either check fails MAX_CONSECUTIVE_FAILURES times in a row, the monitor:

    - Logs a CRITICAL-level message via structlog.
    - Flags the issue in the system_state table (when the database is
      reachable).
    - Sends an emergency email if SMTP is configured.

A separate **meta-monitor** (`src/risk/dms_meta_monitor.py`) watches THIS
process from the outside by checking `last_heartbeat_at` freshness and
emitting `dead_mans_switch.silent_failure` to The Agora when stale.
The meta-monitor is what catches a dead DMS process — the DMS does not
self-watch, since that pattern is self-defeating (a dead process cannot
detect itself).

Usage:
    python src/risk/heartbeat.py
"""

__version__ = "0.2.0"

import os
import signal
import sys
import time
from datetime import datetime, timezone

import psycopg2
import redis
import structlog
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------
load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "")
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
CHECK_INTERVAL = 60  # seconds between check cycles
MAX_CONSECUTIVE_FAILURES = 3  # failures before escalation
# HEARTBEAT_STALE_SECONDS is consumed by the META-MONITOR (external observer).
# It is NOT used by the DMS to gate its own write; doing so creates a self-
# defeating loop where a stale heartbeat blocks the only writer that can
# refresh it. See PR notes / DEFERRED_ITEMS_TRACKER for the bug history.
HEARTBEAT_STALE_SECONDS = 300

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
structlog.configure(
    processors=[
        structlog.stdlib.add_log_level,
        structlog.dev.ConsoleRenderer(),
    ],
    wrapper_class=structlog.stdlib.BoundLogger,
    context_class=dict,
    logger_factory=structlog.PrintLoggerFactory(),
)
log = structlog.get_logger("heartbeat")

# ---------------------------------------------------------------------------
# Failure tracking
# ---------------------------------------------------------------------------
consecutive_failures: dict[str, int] = {
    "postgres": 0,
    "redis": 0,
}

# Graceful-shutdown flag
_running = True


def _handle_signal(signum: int, _frame) -> None:  # noqa: ANN001
    global _running
    log.info("shutdown_signal_received", signal=signum)
    _running = False


# ---------------------------------------------------------------------------
# Health checks
# ---------------------------------------------------------------------------

def check_postgres() -> bool:
    """Return True if we can connect to PostgreSQL and execute a trivial query."""
    if not DATABASE_URL:
        log.error("DATABASE_URL not configured")
        return False
    try:
        conn = psycopg2.connect(DATABASE_URL)
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
        finally:
            conn.close()
        return True
    except Exception as exc:
        log.warning("postgres_check_failed", error=str(exc))
        return False


def check_redis() -> bool:
    """Return True if Redis / Memurai responds to PING."""
    try:
        client = redis.Redis.from_url(REDIS_URL, socket_connect_timeout=5)
        client.ping()
        return True
    except Exception as exc:
        log.warning("redis_check_failed", error=str(exc))
        return False


# ---------------------------------------------------------------------------
# Escalation helpers
# ---------------------------------------------------------------------------

def _flag_issue_in_db(check_name: str) -> None:
    """Write a failure flag into system_state (best-effort)."""
    if not DATABASE_URL:
        return
    try:
        conn = psycopg2.connect(DATABASE_URL)
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE system_state SET alert_status = %s "
                    "WHERE id = (SELECT id FROM system_state LIMIT 1)",
                    ("red",),
                )
            conn.commit()
        finally:
            conn.close()
        log.info("flagged_issue_in_db", check=check_name)
    except Exception as exc:
        log.error("failed_to_flag_issue_in_db", check=check_name, error=str(exc))


def _escalate(check_name: str) -> None:
    """Handle a check that has exceeded the consecutive-failure threshold."""
    log.critical(
        "health_check_critical",
        check=check_name,
        consecutive_failures=MAX_CONSECUTIVE_FAILURES,
    )
    _flag_issue_in_db(check_name)

    # Email alert (sends if SMTP is configured, no-op otherwise)
    try:
        from src.reports.email_service import EmailService
        import asyncio
        email = EmailService()
        loop = asyncio.new_event_loop()
        loop.run_until_complete(email.send_emergency(
            f"CRITICAL: Health check '{check_name}' failed "
            f"{MAX_CONSECUTIVE_FAILURES} consecutive times. "
            f"Immediate investigation required."
        ))
        loop.close()
    except Exception as exc:
        log.error("escalation_email_failed", check=check_name, error=str(exc))


def _update_heartbeat() -> None:
    """Set system_state.last_heartbeat_at to NOW() (best-effort)."""
    if not DATABASE_URL:
        return
    try:
        conn = psycopg2.connect(DATABASE_URL)
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE system_state SET last_heartbeat_at = "
                    "(NOW() AT TIME ZONE 'UTC') "
                    "WHERE id = (SELECT id FROM system_state LIMIT 1)"
                )
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:
        log.error("failed_to_update_heartbeat", error=str(exc))


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def _run_checks() -> bool:
    """Execute external health checks. Returns True if every check passed.

    Critical: this no longer includes a self-freshness check on
    `last_heartbeat_at`. That check was self-defeating — a stale heartbeat
    blocked the very writer that could refresh it (the DMS is the sole
    writer of the column). Liveness watching is now the META-MONITOR's job
    (`src/risk/dms_meta_monitor.py`), which runs in a different process.
    """
    checks: dict[str, callable] = {
        "postgres": check_postgres,
        "redis": check_redis,
    }

    all_passed = True
    for name, fn in checks.items():
        if fn():
            consecutive_failures[name] = 0
        else:
            consecutive_failures[name] += 1
            all_passed = False
            log.warning(
                "check_failed",
                check=name,
                consecutive=consecutive_failures[name],
            )
            if consecutive_failures[name] >= MAX_CONSECUTIVE_FAILURES:
                _escalate(name)

    return all_passed


def main() -> None:
    """Entry point: run the health-check loop until interrupted."""
    global _running

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    log.info(
        "heartbeat_monitor_started",
        version=__version__,
        check_interval=CHECK_INTERVAL,
        max_failures=MAX_CONSECUTIVE_FAILURES,
        stale_seconds=HEARTBEAT_STALE_SECONDS,
    )

    # Beat once on startup so external observers immediately see liveness,
    # rather than waiting CHECK_INTERVAL for the first cycle. Postgres-fail
    # at boot still skips the write because _update_heartbeat() catches the
    # exception and returns; we'd hit a normal cycle on the next iteration.
    if check_postgres():
        _update_heartbeat()
        log.info("heartbeat_initial_beat")

    try:
        while _running:
            all_ok = _run_checks()
            if all_ok:
                _update_heartbeat()
                log.info("all_checks_passed")
            else:
                log.warning("some_checks_failed", failures=dict(consecutive_failures))

            # Sleep in small increments so we can respond to shutdown promptly.
            for _ in range(CHECK_INTERVAL):
                if not _running:
                    break
                time.sleep(1)
    except KeyboardInterrupt:
        pass

    log.info("heartbeat_monitor_stopped")
    sys.exit(0)


if __name__ == "__main__":
    main()
