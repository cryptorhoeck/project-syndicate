"""
Project Syndicate — PID Manager

Tracks running service processes via a JSON file.
Handles stale PID cleanup and service status detection.
"""

__version__ = "0.1.0"

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
DEFAULT_PID_FILE = SCRIPT_DIR / ".syndicate_pids.json"


def load_pids(pid_file: str | Path | None = None) -> dict:
    """Load PID file. Returns empty dict if file doesn't exist."""
    path = Path(pid_file) if pid_file else DEFAULT_PID_FILE
    if not path.exists():
        return {}
    try:
        with open(path, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return {}


def save_pids(pids: dict, pid_file: str | Path | None = None) -> None:
    """Save PID file."""
    path = Path(pid_file) if pid_file else DEFAULT_PID_FILE
    with open(path, "w") as f:
        json.dump(pids, f, indent=2)


def record_pid(
    service_name: str,
    pid: int | None,
    is_service: bool = False,
    pid_file: str | Path | None = None,
) -> None:
    """Record a running process."""
    pids = load_pids(pid_file)
    pids[service_name] = {
        "pid": pid,
        "service": is_service,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "started_by": "cli",
    }
    save_pids(pids, pid_file)


def remove_pid(service_name: str, pid_file: str | Path | None = None) -> None:
    """Remove a service entry from PID file."""
    pids = load_pids(pid_file)
    pids.pop(service_name, None)
    save_pids(pids, pid_file)


def is_process_alive(pid: int) -> bool:
    """Check if a PID is still running on Windows."""
    if pid is None:
        return False
    try:
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
            capture_output=True, text=True, timeout=5
        )
        return str(pid) in result.stdout
    except Exception:
        return False


def is_service_running(service_name: str) -> bool:
    """Check if a Windows Service is in RUNNING state."""
    try:
        result = subprocess.run(
            ["sc", "query", service_name],
            capture_output=True, text=True, timeout=5
        )
        return "RUNNING" in result.stdout
    except Exception:
        return False


def get_live_status(pid_file: str | Path | None = None) -> dict:
    """Check actual status of all tracked services.

    Returns dict like:
    {
        "postgresql": {"status": "running", "pid": 12345},
        "memurai": {"status": "running", "service": True},
        "arena": {"status": "stopped", "pid": None}
    }
    """
    pids = load_pids(pid_file)
    status = {}

    for name, info in pids.items():
        if info.get("service"):
            svc_name = info.get("service_name", name)
            if is_service_running(svc_name):
                status[name] = {"status": "running", "service": True, "started_at": info.get("started_at")}
            else:
                status[name] = {"status": "stopped", "service": True}
        else:
            pid = info.get("pid")
            if pid and is_process_alive(pid):
                status[name] = {"status": "running", "pid": pid, "started_at": info.get("started_at")}
            else:
                status[name] = {"status": "stopped", "pid": None}

    return status


def cleanup_stale_pids(pid_file: str | Path | None = None) -> None:
    """Remove entries for processes that are no longer running."""
    pids = load_pids(pid_file)
    to_remove = []

    for name, info in pids.items():
        if info.get("service"):
            svc_name = info.get("service_name", name)
            if not is_service_running(svc_name):
                to_remove.append(name)
        else:
            pid = info.get("pid")
            if not pid or not is_process_alive(pid):
                to_remove.append(name)

    for name in to_remove:
        del pids[name]

    save_pids(pids, pid_file)
