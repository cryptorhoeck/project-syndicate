"""
Dead Man's Switch — independent system health monitor for Project Syndicate.

This script runs as its own standalone process, entirely outside any agent
framework.  Every CHECK_INTERVAL seconds it verifies:

    1. PostgreSQL is accessible (psycopg2 connection).
    2. Redis / Memurai is accessible (redis.Redis.ping()).
    3. The system_state table has been updated within the last
       HEARTBEAT_STALE_SECONDS seconds.

If any single check fails MAX_CONSECUTIVE_FAILURES times in a row, the monitor:

    - Logs a CRITICAL-level message via structlog.
    - Flags the issue in the system_state table (when the database is
      reachable).
    - (Placeholder) Future enhancements: email alert, exchange API kill switch.

On each fully successful check cycle the monitor updates
system_state.last_heartbeat_at to the current UTC time.

Usage:
    python src/risk/heartbeat.py
"""

__version__ = "0.1.0"

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
HEARTBEAT_STALE_SECONDS = 300  # 5 minutes

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
    "stale_heartbeat": 0,
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


def check_heartbeat_freshness() -> bool:
    """Return True if system_state.last_heartbeat_at is within the staleness window."""
    if not DATABASE_URL:
        log.error("DATABASE_URL not configured — cannot check heartbeat freshness")
        return False
    try:
        conn = psycopg2.connect(DATABASE_URL)
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT last_heartbeat_at FROM system_state "
                    "ORDER BY last_heartbeat_at DESC LIMIT 1"
                )
                row = cur.fetchone()
                if row is None:
                    log.warning("no_heartbeat_record_found")
                    return False
                last_heartbeat = row[0]
                if last_heartbeat is None:
                    # First run — no heartbeat yet, allow bootstrap
                    log.info("heartbeat_bootstrap", status="first_run")
                    return True
                if last_heartbeat.tzinfo is None:
                    last_heartbeat = last_heartbeat.replace(tzinfo=timezone.utc)
                age = (datetime.now(timezone.utc) - last_heartbeat).total_seconds()
                if age > HEARTBEAT_STALE_SECONDS:
                    log.warning("heartbeat_stale", age_seconds=age)
                    return False
                return True
        finally:
            conn.close()
    except Exception as exc:
        log.warning("heartbeat_freshness_check_failed", error=str(exc))
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
                    (f"red",),
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

    # Placeholder for future escalation actions:
    # - Send email alert
    # - Trigger exchange API kill switch


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
    """Execute all health checks. Returns True if every check passed."""
    checks: dict[str, callable] = {
        "postgres": check_postgres,
        "redis": check_redis,
        "stale_heartbeat": check_heartbeat_freshness,
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
