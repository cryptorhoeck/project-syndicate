"""
Project Syndicate — Run All

Starts Genesis, Warden, and Dead Man's Switch as separate subprocesses.
Monitors all three and restarts any that die.
This is the "turn the system on" script for development.

Usage: python scripts/run_all.py
"""

__version__ = "0.7.0"

import argparse
import os
import signal
import subprocess
import sys
import time

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
log = structlog.get_logger("run_all")

# Python executable from venv
PYTHON = os.path.join(PROJECT_ROOT, ".venv", "Scripts", "python.exe")
if not os.path.exists(PYTHON):
    PYTHON = sys.executable

# Process definitions
PROCESSES = {
    "genesis": [PYTHON, os.path.join(PROJECT_ROOT, "scripts", "run_genesis.py")],
    "warden": [PYTHON, os.path.join(PROJECT_ROOT, "scripts", "run_warden.py")],
    "heartbeat": [PYTHON, os.path.join(PROJECT_ROOT, "src", "risk", "heartbeat.py")],
}

_running = True
_children: dict[str, subprocess.Popen] = {}


def _handle_signal(signum: int, _frame) -> None:
    global _running
    log.info("shutdown_signal_received", signal=signum)
    _running = False


def start_process(name: str) -> subprocess.Popen:
    """Start a subprocess and return the Popen object."""
    cmd = PROCESSES[name]
    log.info("starting_process", name=name, cmd=" ".join(cmd))
    proc = subprocess.Popen(
        cmd,
        cwd=PROJECT_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    return proc


def main() -> None:
    global _running

    parser = argparse.ArgumentParser(description="Start all Syndicate processes")
    parser.add_argument("--with-web", action="store_true", help="Also start the web dashboard")
    parser.add_argument("--with-trading", action="store_true", help="Also start trading monitors")
    args = parser.parse_args()

    if args.with_web:
        PROCESSES["web"] = [PYTHON, os.path.join(PROJECT_ROOT, "scripts", "run_web.py")]
    if args.with_trading:
        PROCESSES["trading"] = [PYTHON, os.path.join(PROJECT_ROOT, "scripts", "run_trading.py")]

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    log.info("syndicate_starting", version=__version__, processes=list(PROCESSES.keys()))

    # Start all processes
    for name in PROCESSES:
        _children[name] = start_process(name)

    log.info("all_processes_started", pids={n: p.pid for n, p in _children.items()})

    # Monitor loop
    try:
        while _running:
            for name, proc in list(_children.items()):
                retcode = proc.poll()
                if retcode is not None:
                    log.warning(
                        "process_died",
                        name=name,
                        returncode=retcode,
                    )
                    if _running:
                        log.info("restarting_process", name=name)
                        _children[name] = start_process(name)
            time.sleep(5)
    except KeyboardInterrupt:
        pass

    # Shutdown
    log.info("shutting_down_all_processes")
    for name, proc in _children.items():
        if proc.poll() is None:
            log.info("terminating", name=name, pid=proc.pid)
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                log.warning("force_killing", name=name)
                proc.kill()

    log.info("syndicate_stopped")


if __name__ == "__main__":
    main()
