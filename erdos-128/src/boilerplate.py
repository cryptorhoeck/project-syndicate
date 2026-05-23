"""
boilerplate.py — the standard "prelude" every long-running Erdos-128 script runs
before it does any real work.

WHY this file exists
--------------------
Andrew's project discipline says: every script must, before touching anything
important, perform four checks in order:

    1. ENV CHECK   - is the Python version right? are required packages installed?
    2. VERSION NOTE - record which version of the script is running (for the log).
    3. BACKUP       - snapshot any files we're about to modify, so a bad run can
                      always be undone.
    4. PROCESS MGMT - make sure another copy of this script isn't already running
                      (two searches writing to the same files = corrupted results).

Centralising all four here means individual scripts (the graph generators, the
verifier, the search loops in later phases) just call `run_boilerplate(...)` once
at the top and inherit the whole safety standard for free.

A note on the OS
----------------
The original spec was written for Windows 11 / CMD. This module is deliberately
written to be *cross-platform* (it uses pathlib and the standard library only),
so it runs the same on Windows, Linux, or macOS. That matters because the search
may eventually run on a cloud machine, not just Andrew's laptop.
"""

from __future__ import annotations

import importlib
import json
import os
import platform
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

# Bump this whenever the boilerplate's behaviour changes. It gets written into the
# run log so we can always tell which safety logic a given run used.
__version__ = "0.1.0"

# ---------------------------------------------------------------------------
# Project layout helpers
# ---------------------------------------------------------------------------
# This file lives at  <project_root>/src/boilerplate.py
# so the project root is two levels up. We compute it once here instead of
# hard-coding a path, because hard-coded absolute paths (like "E:\...") break
# the moment the project is moved or run on a different machine.
PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent
BACKUPS_DIR: Path = PROJECT_ROOT / "backups"
RESULTS_DIR: Path = PROJECT_ROOT / "results"
RUN_LOG_PATH: Path = RESULTS_DIR / "run_log.jsonl"

# Minimum Python we are willing to run on. We target 3.12 in the spec, but this
# container provides 3.11, so we accept 3.11+. Lowering this is a conscious choice,
# not an accident — see CHANGELOG Phase 0.
MIN_PYTHON: tuple[int, int] = (3, 11)


class BoilerplateError(RuntimeError):
    """Raised when a pre-flight check fails hard enough that we must NOT continue.

    WHY a custom exception: it lets callers (and the smoke test) catch *only* our
    pre-flight failures, without accidentally swallowing unrelated bugs.
    """


# ---------------------------------------------------------------------------
# Step 1 — Environment check
# ---------------------------------------------------------------------------
def check_python_version(minimum: tuple[int, int] = MIN_PYTHON) -> str:
    """Confirm the running interpreter is new enough.

    WHY: type-hint syntax and stdlib features used across the project assume a
    modern Python. Failing fast here gives a clear message instead of a confusing
    crash deep inside some other module later.
    """
    current = sys.version_info[:2]
    if current < minimum:
        raise BoilerplateError(
            f"Python {minimum[0]}.{minimum[1]}+ required, "
            f"but running {current[0]}.{current[1]}."
        )
    return f"{current[0]}.{current[1]}.{sys.version_info[2]}"


def check_packages(required: list[str]) -> int:
    """Verify every required package can actually be imported.

    `required` holds IMPORT names (what you type after `import`), not PyPI names.
    For example the dependency installed as "python-igraph" is imported as
    "igraph" — so the caller passes "igraph" here. Getting this wrong is a common
    beginner trap, hence the explicit note.

    Returns the count of packages confirmed present so the caller can print the
    "All N required packages present" confirmation the spec asks for.
    """
    missing: list[str] = []
    for name in required:
        try:
            importlib.import_module(name)
        except ImportError:
            missing.append(name)

    if missing:
        raise BoilerplateError(
            "Missing required package(s): "
            + ", ".join(missing)
            + ". Activate the .venv and run: pip install -r requirements.txt"
        )

    print(f"All {len(required)} required packages present.")
    return len(required)


# ---------------------------------------------------------------------------
# Step 3 — Backup (run BEFORE any destructive change)
# ---------------------------------------------------------------------------
def make_backup(paths: list[Path] | None) -> str | None:
    """Copy the given files/dirs into a fresh timestamped folder under backups/.

    WHY timestamped folders: every run gets its own snapshot, so we never overwrite
    a previous backup. If a search run corrupts an output file, we can always walk
    back to the exact state before that run.

    Returns the backup folder name (e.g. "20260523_181500") or None if there was
    nothing to back up. Callers that aren't about to modify files can pass None.
    """
    if not paths:
        print("Backup: nothing to back up for this run.")
        return None

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest_root = BACKUPS_DIR / stamp
    dest_root.mkdir(parents=True, exist_ok=True)

    copied = 0
    for src in paths:
        src = Path(src)
        if not src.exists():
            # Skipping is fine: there's nothing yet to protect (e.g. first run).
            continue
        target = dest_root / src.name
        if src.is_dir():
            shutil.copytree(src, target, dirs_exist_ok=True)
        else:
            shutil.copy2(src, target)
        copied += 1

    print(f"Backup created: backups/{stamp} ({copied} item(s) copied).")
    return stamp


# ---------------------------------------------------------------------------
# Step 4 — Process management (don't run two copies at once)
# ---------------------------------------------------------------------------
def _pid_is_alive(pid: int) -> bool:
    """Best-effort check of whether a process id is still running.

    WHY this is tricky: there's no single cross-platform stdlib call. On POSIX we
    send signal 0 (which checks existence without actually signalling). On Windows
    we fall back to a tasklist query. If we can't tell, we err on the side of
    "alive" so we never silently stomp on a real running search.
    """
    if pid <= 0:
        return False
    if os.name == "posix":
        try:
            os.kill(pid, 0)  # signal 0 = "does this process exist & can I touch it?"
        except ProcessLookupError:
            return False
        except PermissionError:
            return True  # exists but owned by someone else
        return True
    # Windows fallback.
    try:
        out = os.popen(f'tasklist /FI "PID eq {pid}"').read()
        return str(pid) in out
    except Exception:
        return True


def acquire_lock(script_name: str, force: bool = False) -> Path:
    """Take an exclusive run-lock for `script_name` via a PID lockfile.

    Behaviour:
      * No lockfile            -> create one, we're good.
      * Lockfile, dead PID     -> stale leftover from a crash; reclaim it.
      * Lockfile, LIVE PID     -> a real conflicting run exists. We ABORT.

    WHY we abort instead of auto-killing: the spec says "handle conflicting
    processes", and killing another process blindly is destructive — it could be
    a legitimate multi-day search the user is intentionally running. Refusing to
    start (and saying so clearly) is the safe default. A caller who truly wants to
    take over can pass force=True, which we treat as explicit permission to reclaim
    the lock. We still never SIGKILL another process from here.
    """
    lockfile = RESULTS_DIR / f"{script_name}.lock"
    if lockfile.exists():
        try:
            old_pid = int(lockfile.read_text().strip())
        except (ValueError, OSError):
            old_pid = -1  # unreadable lock = treat as stale

        if _pid_is_alive(old_pid) and not force:
            raise BoilerplateError(
                f"Another instance of '{script_name}' appears to be running "
                f"(PID {old_pid}). Refusing to start a second copy. "
                f"If that PID is dead, delete {lockfile} or pass force=True."
            )
        # Stale lock (or force) -> fall through and overwrite it.

    lockfile.write_text(str(os.getpid()))
    return lockfile


def release_lock(lockfile: Path) -> None:
    """Remove our lockfile. Safe to call even if it's already gone."""
    try:
        lockfile.unlink(missing_ok=True)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Run log
# ---------------------------------------------------------------------------
def log_run(event: str, details: dict | None = None) -> None:
    """Append one JSON object per line to results/run_log.jsonl.

    WHY JSONL (one JSON object per line) instead of a single big JSON array:
    we can append to it forever without re-reading/rewriting the whole file, and
    a half-written final line never corrupts the earlier, valid lines. Perfect for
    an append-only audit trail of every run.
    """
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event": event,
        "boilerplate_version": __version__,
        "details": details or {},
    }
    with RUN_LOG_PATH.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record) + "\n")


# ---------------------------------------------------------------------------
# The one function scripts actually call
# ---------------------------------------------------------------------------
def run_boilerplate(
    script_name: str,
    script_version: str,
    required_packages: list[str],
    backup_paths: list[Path] | None = None,
    force: bool = False,
) -> dict:
    """Run all four pre-flight steps in order and record the result.

    Returns a small dict with the things a caller might want afterwards:
        {"python": "3.11.15", "package_count": 5,
         "backup": "20260523_181500" | None, "lockfile": Path}

    Typical use at the top of a real script:

        from src.boilerplate import run_boilerplate, release_lock
        ctx = run_boilerplate(
            script_name="search_b",
            script_version="1.0.0",
            required_packages=["networkx", "numpy", "igraph", "pulp"],
            backup_paths=[RESULTS_DIR / "candidates.json"],
        )
        try:
            ... real work ...
        finally:
            release_lock(ctx["lockfile"])
    """
    print(f"=== {script_name} v{script_version} (boilerplate v{__version__}) ===")
    print(f"Platform: {platform.system()} {platform.release()}")

    # 1. ENV CHECK
    py = check_python_version()
    pkg_count = check_packages(required_packages)

    # 2. VERSION NOTE (already printed above; also recorded in the log below).

    # 3. BACKUP — before anything destructive.
    backup_id = make_backup(backup_paths)

    # 4. PROCESS MANAGEMENT — last, so we don't hold a lock if an earlier check fails.
    lockfile = acquire_lock(script_name, force=force)

    log_run(
        "boilerplate_ok",
        {
            "script": script_name,
            "script_version": script_version,
            "python": py,
            "package_count": pkg_count,
            "backup": backup_id,
            "platform": f"{platform.system()} {platform.release()}",
        },
    )

    return {
        "python": py,
        "package_count": pkg_count,
        "backup": backup_id,
        "lockfile": lockfile,
    }


if __name__ == "__main__":
    # Running this module directly performs a self-check using the real project
    # dependencies. Handy for "does my environment work?" without writing code.
    ctx = run_boilerplate(
        script_name="boilerplate_selfcheck",
        script_version=__version__,
        required_packages=["networkx", "numpy", "igraph", "pulp"],
        backup_paths=None,
    )
    release_lock(ctx["lockfile"])
    print("Self-check complete.")
