"""
Project Syndicate — The Arena
Starts all system processes for the paper trading validation run.

Processes managed:
1. Genesis (5-minute cycle — the god node)
2. Warden (30-second cycle — risk enforcement)
3. Trading Monitors (position + limit order — 10-second cycles)
4. Dead Man's Switch (heartbeat monitoring)
5. FastAPI Dashboard (web interface on port 8000)

Usage: python scripts/run_arena.py
Stop: Ctrl+C (graceful shutdown of all processes)
"""

__version__ = "1.0.0"

import os
import signal
import subprocess
import sys
import time
from logging.handlers import RotatingFileHandler

import structlog
from dotenv import load_dotenv

# Ensure project root is on sys.path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)
load_dotenv(os.path.join(PROJECT_ROOT, ".env"), override=True)

# Logging
structlog.configure(
    processors=[
        structlog.stdlib.add_log_level,
        structlog.dev.ConsoleRenderer(),
    ],
    wrapper_class=structlog.stdlib.BoundLogger,
    context_class=dict,
    logger_factory=structlog.PrintLoggerFactory(),
)
log = structlog.get_logger("arena")

# Python executable from venv
PYTHON = os.path.join(PROJECT_ROOT, ".venv", "Scripts", "python.exe")
if not os.path.exists(PYTHON):
    PYTHON = sys.executable

# Process definitions — ordered by importance
PROCESSES = {
    "warden": {
        "cmd": [PYTHON, os.path.join(PROJECT_ROOT, "scripts", "run_warden.py")],
        "restart_delay": 0,  # Immediate restart — safety critical
        "critical": True,
    },
    "wire_scheduler": {
        # Closes WIRING_AUDIT_REPORT.md subsystem U: the Wire's data
        # acquisition layer was never started in production. Without this,
        # the Wire pipeline is empty — Scout `recent_signals`, severity-5
        # hooks, and Archive queries all become no-ops because nothing
        # populates wire_events. Starts AFTER Postgres/Memurai preflight
        # and BEFORE genesis/agents so Scout/Strategist/Critic see a live
        # ticker on their first cycle.
        "cmd": [PYTHON, "-m", "src.wire.cli", "run-scheduler", "--with-digest"],
        "restart_delay": 5,
        "critical": True,
    },
    "genesis": {
        "cmd": [PYTHON, os.path.join(PROJECT_ROOT, "scripts", "run_genesis.py")],
        "restart_delay": 30,  # Wait 30s before restarting Genesis
        "critical": True,
    },
    "agents": {
        "cmd": [PYTHON, os.path.join(PROJECT_ROOT, "scripts", "run_agents.py")],
        "restart_delay": 10,  # Wait before restarting — let agents recover
        "critical": True,
    },
    "price_fetcher": {
        "cmd": [PYTHON, os.path.join(PROJECT_ROOT, "scripts", "run_price_fetcher.py")],
        "restart_delay": 5,
        "critical": False,
    },
    "trading": {
        "cmd": [PYTHON, os.path.join(PROJECT_ROOT, "scripts", "run_trading.py")],
        "restart_delay": 5,
        "critical": False,
    },
    "heartbeat": {
        "cmd": [PYTHON, os.path.join(PROJECT_ROOT, "src", "risk", "heartbeat.py")],
        "restart_delay": 5,
        "critical": False,
    },
    "dashboard": {
        "cmd": [PYTHON, os.path.join(PROJECT_ROOT, "scripts", "run_web.py")],
        "restart_delay": 5,
        "critical": False,
    },
}

_running = True
_children: dict[str, subprocess.Popen] = {}
_restart_times: dict[str, float] = {}


def _handle_signal(signum: int, _frame) -> None:
    global _running
    log.info("shutdown_signal_received", signal=signum)
    _running = False


def _preflight_checks() -> bool:
    """Verify all subsystems are operational before launch."""
    checks_passed = True

    # Database
    try:
        from sqlalchemy import create_engine, text
        from src.common.config import config
        engine = create_engine(config.database_url, pool_pre_ping=True)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        engine.dispose()
        log.info("preflight_db", status="OK")
    except Exception as e:
        log.error("preflight_db", status="FAIL", error=str(e))
        checks_passed = False

    # Redis
    try:
        import redis
        from src.common.config import config
        r = redis.Redis.from_url(config.redis_url, decode_responses=True)
        r.ping()
        r.close()
        log.info("preflight_redis", status="OK")
    except Exception as e:
        log.error("preflight_redis", status="FAIL", error=str(e))
        checks_passed = False

    # Anthropic API key present
    from src.common.config import config
    if not config.anthropic_api_key:
        log.error("preflight_anthropic", status="FAIL", error="ANTHROPIC_API_KEY not set")
        checks_passed = False
    else:
        log.info("preflight_anthropic", status="OK", key_length=len(config.anthropic_api_key))

    # Trading mode
    if config.trading_mode != "paper":
        log.error("preflight_trading_mode", status="FAIL",
                  error=f"trading_mode={config.trading_mode}, expected 'paper'")
        checks_passed = False
    else:
        log.info("preflight_trading_mode", status="OK", mode="paper")

    # Trading service factory wiring. Same risk shape as the DMS preflight:
    # if the factory returns None or raises, every Operator trade attempt
    # would fall through to the [NO SERVICE] log path and produce zero P&L.
    # Refuse Arena entry rather than spend another 3 days like 2026-04-14.
    try:
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from src.trading.execution_service import get_trading_service
        engine = create_engine(config.database_url, pool_pre_ping=True)
        db_factory = sessionmaker(bind=engine)
        trading_service = get_trading_service(db_session_factory=db_factory)
        engine.dispose()
        if trading_service is None:
            log.error("preflight_trading_service", status="FAIL",
                      error="get_trading_service returned None")
            checks_passed = False
        else:
            log.info("preflight_trading_service", status="OK",
                     impl=type(trading_service).__name__)
    except Exception as e:
        log.error("preflight_trading_service", status="FAIL", error=str(e))
        checks_passed = False

    # Phase 9A: Governance tables + parameter registry
    try:
        from sqlalchemy import create_engine, text
        from src.common.config import config as cfg
        engine = create_engine(cfg.database_url, pool_pre_ping=True)
        with engine.connect() as conn:
            for table in ["colony_maturity", "parameter_registry", "sip_votes",
                          "sip_debates", "parameter_change_log"]:
                conn.execute(text(f"SELECT 1 FROM {table} LIMIT 1"))

            # Check parameter registry is seeded
            param_count = conn.execute(
                text("SELECT COUNT(*) FROM parameter_registry")
            ).scalar()
            if param_count == 0:
                log.info("preflight_governance", status="SEEDING",
                         message="Parameter registry empty — seeding now")
                from scripts.seed_parameter_registry import seed
                seed(cfg.database_url)
                param_count = conn.execute(
                    text("SELECT COUNT(*) FROM parameter_registry")
                ).scalar()

        engine.dispose()
        log.info("preflight_governance", status="OK",
                 tables=5, parameters=param_count)
    except Exception as e:
        log.error("preflight_governance", status="FAIL", error=str(e))
        checks_passed = False

    return checks_passed


def _print_banner() -> None:
    """Print the Arena startup banner."""
    try:
        import ccxt
        k = ccxt.kraken()
        btc = k.fetch_ticker("BTC/USDT")["last"]
        btc_str = f"${btc:,.0f}"
    except Exception:
        btc_str = "unavailable"

    print()
    print("  +----------------------------------------------+")
    print("  |     PROJECT SYNDICATE -- THE ARENA           |")
    print("  |     Paper Trading Validation Run             |")
    print("  +----------------------------------------------+")
    print(f"  |  Treasury:    $500.00                        |")
    print(f"  |  Mode:        PAPER TRADING                  |")
    print(f"  |  Agents:      0 (boot sequence pending)      |")
    print(f"  |  Market:      BTC at {btc_str:<12s}           |")
    print(f"  |  Dashboard:   http://localhost:8000           |")
    print("  +----------------------------------------------+")
    print("  |  Processes:                                  |")
    print("  |    * Warden          (30 sec cycle)           |")
    print("  |    * Genesis         (5 min cycle)            |")
    print("  |    * Agent Runner    (OODA loops)             |")
    print("  |    * Trading Monitors (10 sec cycle)          |")
    print("  |    * Dead Man Switch (heartbeat)              |")
    print("  |    * Dashboard       (port 8000)              |")
    print("  +----------------------------------------------+")
    print("  |  Press Ctrl+C to shutdown gracefully          |")
    print("  +----------------------------------------------+")
    print()


_log_handles: dict[str, any] = {}


def _verify_wire_scheduler_alive(timeout_seconds: int = 60) -> bool:
    """Block until at least one enabled wire_source has a `last_fetch_attempt`
    fresher than the Arena's boot stamp, OR the timeout elapses.

    Returns True only if the Wire scheduler actually attempted a fetch
    within the window. The scheduler ticks all enabled sources on its
    first iteration, so this should fire within a few seconds of boot.

    Failure means the intelligence layer is dead: the Arena would be
    running with empty `wire_events`, severity-5 hooks would never fire,
    Scout `recent_signals` would always be empty, and the Wire dashboard
    would show stale or no data. The Arena MUST refuse to enter the
    monitor loop in that state, same risk class as the DMS preflight.

    Implementation note: we rely on `wire_source_health.last_fetch_attempt`
    rather than `wire_events` count because (1) it updates on every fetch
    attempt regardless of whether the source returned items, and (2) it
    does not depend on Haiku digestion succeeding — the scheduler may be
    fetching healthily even when ANTHROPIC_API_KEY is rejected, and we
    want to verify the scheduler, not the digester.

    Tz handling: `wire_source_health.last_fetch_attempt` is TIMESTAMPTZ
    while `system_state.last_arena_boot_at` is TIMESTAMP (naive) for
    historical schema reasons. Postgres returns one tz-aware and one
    tz-naive; comparing them directly raises TypeError. We normalize
    both to naive UTC before the comparison.
    """
    try:
        from datetime import timezone
        from sqlalchemy import create_engine, text
        from src.common.config import config
    except Exception as exc:
        log.error("wire_scheduler_verify_import_failed", error=str(exc))
        return False

    def _to_naive_utc(dt):
        """Coerce any datetime to naive UTC for comparison."""
        if dt is None:
            return None
        if dt.tzinfo is not None:
            return dt.astimezone(timezone.utc).replace(tzinfo=None)
        return dt

    deadline = time.time() + timeout_seconds
    poll_seconds = 2.0

    while time.time() < deadline:
        try:
            engine = create_engine(config.database_url, pool_pre_ping=True)
            with engine.connect() as conn:
                row = conn.execute(text(
                    "SELECT MAX(h.last_fetch_attempt) AS latest_attempt, "
                    "       (SELECT last_arena_boot_at FROM system_state LIMIT 1) AS boot_at "
                    "FROM wire_source_health h "
                    "JOIN wire_sources s ON s.id = h.source_id "
                    "WHERE s.enabled = TRUE"
                )).fetchone()
            engine.dispose()
        except Exception as exc:
            log.warning("wire_scheduler_verify_db_failed", error=str(exc))
            time.sleep(poll_seconds)
            continue

        if row is None:
            time.sleep(poll_seconds)
            continue

        latest_attempt = _to_naive_utc(row[0])
        boot_at = _to_naive_utc(row[1])
        if latest_attempt is not None and boot_at is not None and latest_attempt >= boot_at:
            log.info(
                "wire_scheduler_alive",
                latest_fetch_attempt=str(latest_attempt),
                boot_at=str(boot_at),
            )
            return True
        time.sleep(poll_seconds)

    return False


def _verify_dead_mans_switch_alive(timeout_seconds: int = 60) -> bool:
    """Block until system_state.last_heartbeat_at is fresher than the Arena's
    boot stamp, OR the timeout elapses.

    Returns True only if the DMS produced a beat within the window. Any other
    outcome — DB unreachable, heartbeat never advanced, ambiguous state —
    returns False.

    Called from the post-start path in main(), AFTER `last_arena_boot_at` was
    stamped. The DMS, on its initial cycle, runs `_update_heartbeat()` which
    must therefore produce a `last_heartbeat_at >= last_arena_boot_at`.
    """
    try:
        from sqlalchemy import create_engine, text
        from src.common.config import config
    except Exception as exc:
        log.error("dms_verify_import_failed", error=str(exc))
        return False

    deadline = time.time() + timeout_seconds
    poll_seconds = 2.0
    saw_beat = False

    while time.time() < deadline:
        try:
            engine = create_engine(config.database_url, pool_pre_ping=True)
            with engine.connect() as conn:
                row = conn.execute(text(
                    "SELECT last_heartbeat_at, last_arena_boot_at "
                    "FROM system_state LIMIT 1"
                )).fetchone()
            engine.dispose()
        except Exception as exc:
            log.warning("dms_verify_db_failed", error=str(exc))
            time.sleep(poll_seconds)
            continue

        if row is None:
            time.sleep(poll_seconds)
            continue

        heartbeat_at = row[0]
        boot_at = row[1]
        if heartbeat_at is not None and boot_at is not None and heartbeat_at >= boot_at:
            log.info(
                "dead_mans_switch_alive",
                heartbeat_at=str(heartbeat_at),
                boot_at=str(boot_at),
            )
            saw_beat = True
            break
        time.sleep(poll_seconds)

    return saw_beat


def start_process(name: str) -> subprocess.Popen:
    """Start a subprocess and return the Popen object."""
    proc_def = PROCESSES[name]
    cmd = proc_def["cmd"]
    log.info("starting_process", name=name)

    # Close previous log handle for this process if it exists
    old_handle = _log_handles.pop(name, None)
    if old_handle:
        try:
            old_handle.close()
        except Exception:
            pass

    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    for key in ["ANTHROPIC_API_KEY", "EXCHANGE_API_KEY", "EXCHANGE_API_SECRET"]:
        env.pop(key, None)

    log_dir = os.path.join(PROJECT_ROOT, "logs")
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, f"{name}.log")

    # Truncate if over 50MB to prevent unbounded growth
    try:
        if os.path.exists(log_path) and os.path.getsize(log_path) > 50 * 1024 * 1024:
            # Rename old log, start fresh
            backup = log_path + ".prev"
            if os.path.exists(backup):
                os.remove(backup)
            os.rename(log_path, backup)
    except Exception:
        pass

    log_file = open(log_path, "a", encoding="utf-8")
    _log_handles[name] = log_file

    proc = subprocess.Popen(
        cmd,
        cwd=PROJECT_ROOT,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        env=env,
    )
    return proc


def main() -> None:
    global _running

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    log.info("arena_preflight_starting")
    if not _preflight_checks():
        log.error("arena_preflight_failed", message="Fix issues above before launching")
        sys.exit(1)

    _print_banner()

    # Stamp boot time for cold start grace period
    try:
        from sqlalchemy import create_engine, text
        from src.common.config import config
        engine = create_engine(config.database_url)
        with engine.connect() as conn:
            conn.execute(text(
                "UPDATE system_state SET last_arena_boot_at = NOW() WHERE id = 1"
            ))
            conn.commit()
        engine.dispose()
        log.info("arena_boot_timestamp", status="stamped")
    except Exception as e:
        log.warning("arena_boot_timestamp_failed", error=str(e))

    log.info("arena_starting", version=__version__, processes=list(PROCESSES.keys()))

    # Start all processes — Warden first (safety critical)
    for name in PROCESSES:
        _children[name] = start_process(name)
        _restart_times[name] = 0.0

    log.info("all_processes_started", pids={n: p.pid for n, p in _children.items()})

    # Hard verification: the Dead Man's Switch must produce a fresh heartbeat
    # within 60 seconds of the Arena coming up. If it doesn't, the failsafe
    # is silently dead and the Arena MUST NOT trade. We refuse to enter the
    # monitor loop and shut everything back down. Same risk class as the
    # Library reflection bug — silent failures here cost real money.
    if not _verify_dead_mans_switch_alive(timeout_seconds=60):
        log.error(
            "dead_mans_switch_did_not_beat",
            message="Heartbeat did not advance within 60s of Arena boot. "
                    "Refusing to enter monitor loop. Investigate "
                    "src/risk/heartbeat.py and the heartbeat process logs.",
        )
        for name, proc in _children.items():
            if proc.poll() is None:
                log.info("aborting_terminate", name=name, pid=proc.pid)
                proc.terminate()
        sys.exit(2)

    # Hard verification: the Wire scheduler must produce at least one fetch
    # attempt within 60 seconds of boot. If it doesn't, the intelligence
    # layer is dead — wire_events stay empty, Scout recent_signals is
    # always empty, severity-5 hooks never fire. Refuse to enter monitor
    # loop. Same wiring contract as DMS / Warden / TradeExecutionService:
    # construct, verify, abort if verification fails. Closes
    # WIRING_AUDIT_REPORT.md subsystem U.
    if not _verify_wire_scheduler_alive(timeout_seconds=60):
        log.error(
            "wire_scheduler_did_not_fetch",
            message="No enabled Wire source produced a fetch_attempt within "
                    "60s of Arena boot. Refusing to enter monitor loop. "
                    "Investigate src/wire/cli.py run-scheduler logs and "
                    "wire_source_health.",
        )
        for name, proc in _children.items():
            if proc.poll() is None:
                log.info("aborting_terminate", name=name, pid=proc.pid)
                proc.terminate()
        sys.exit(2)

    # Monitor loop
    try:
        while _running:
            for name, proc in list(_children.items()):
                retcode = proc.poll()
                if retcode is not None and _running:
                    proc_def = PROCESSES[name]
                    delay = proc_def["restart_delay"]

                    log.warning("process_died", name=name, returncode=retcode)

                    # Respect restart delay
                    now = time.time()
                    last_restart = _restart_times.get(name, 0)
                    if now - last_restart < delay:
                        continue

                    log.info("restarting_process", name=name, delay=delay)
                    if delay > 0:
                        time.sleep(delay)
                    _children[name] = start_process(name)
                    _restart_times[name] = time.time()

            time.sleep(10)
    except KeyboardInterrupt:
        pass

    # Graceful shutdown — reverse order of criticality
    log.info("arena_shutting_down")
    shutdown_order = ["genesis", "agents", "trading", "wire_scheduler", "dashboard", "heartbeat", "warden"]
    for name in shutdown_order:
        proc = _children.get(name)
        if proc and proc.poll() is None:
            log.info("terminating", name=name, pid=proc.pid)
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                log.warning("force_killing", name=name)
                proc.kill()

    # Close all log file handles
    for name, handle in _log_handles.items():
        try:
            handle.close()
        except Exception:
            pass

    log.info("arena_stopped")


if __name__ == "__main__":
    main()
