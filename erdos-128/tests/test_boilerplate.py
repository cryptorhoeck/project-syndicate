"""
test_boilerplate.py — Phase 0 smoke test.

This is the gate that proves the environment is wired up correctly: the right
Python, all dependencies importable, backups working, the run log being written,
and the "don't run two copies" lock behaving. If this passes, Phase 0 is done.

Run from the project root with:   .venv/bin/python -m pytest tests/ -v
(On Windows CMD:                    .venv\\Scripts\\python.exe -m pytest tests\\ -v )
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# Make the project root importable so `from src.boilerplate import ...` works no
# matter which directory pytest is launched from. tests/ lives one level below
# the project root, so the root is the parent of this file's parent.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.boilerplate import (  # noqa: E402  (import after sys.path tweak — intentional)
    BoilerplateError,
    RUN_LOG_PATH,
    __version__,
    acquire_lock,
    check_packages,
    check_python_version,
    log_run,
    make_backup,
    release_lock,
    run_boilerplate,
)

# The dependencies the project actually relies on, by IMPORT name.
REQUIRED = ["networkx", "numpy", "igraph", "pulp"]


def test_python_version_ok():
    """The interpreter should satisfy our minimum and report a version string."""
    version = check_python_version()
    assert isinstance(version, str)
    # Should look like "3.11.15".
    assert version.count(".") == 2


def test_python_version_rejects_impossible_minimum():
    """An absurd minimum must raise — proves the check actually gates."""
    with pytest.raises(BoilerplateError):
        check_python_version(minimum=(99, 0))


def test_all_required_packages_present(capsys):
    """Every dependency imports, and we print the spec's confirmation line."""
    count = check_packages(REQUIRED)
    assert count == len(REQUIRED)
    captured = capsys.readouterr()
    assert f"All {len(REQUIRED)} required packages present." in captured.out


def test_missing_package_raises():
    """A bogus package name must fail the env check, not pass silently."""
    with pytest.raises(BoilerplateError):
        check_packages(["definitely_not_a_real_package_xyz"])


def test_make_backup_snapshots_a_file(tmp_path, capsys):
    """A real file handed to make_backup should be copied into backups/<stamp>/."""
    sample = tmp_path / "sample.txt"
    sample.write_text("hello")
    stamp = make_backup([sample])
    assert stamp is not None
    out = capsys.readouterr().out
    assert "Backup created" in out


def test_make_backup_handles_nothing():
    """Passing None must be a safe no-op (scripts that modify nothing call this)."""
    assert make_backup(None) is None


def test_log_run_appends_valid_json_line():
    """Each log_run call must add exactly one parseable JSON line."""
    before = RUN_LOG_PATH.read_text().count("\n") if RUN_LOG_PATH.exists() else 0
    log_run("smoke_test_log_check", {"phase": 0})
    after = RUN_LOG_PATH.read_text().count("\n")
    assert after == before + 1

    # The last line must be valid JSON with our standard fields.
    last_line = RUN_LOG_PATH.read_text().splitlines()[-1]
    record = json.loads(last_line)
    assert record["event"] == "smoke_test_log_check"
    assert record["boilerplate_version"] == __version__
    assert "timestamp" in record


def test_lock_detects_conflict():
    """A live lock must block a second acquire — the core 'no double run' guard.

    We acquire once (writes THIS process's PID, which is obviously alive), then a
    second acquire without force must raise. We always release afterwards so we
    don't leave a stale lock lying around for other tests.
    """
    lock = acquire_lock("smoke_conflict_test")
    try:
        with pytest.raises(BoilerplateError):
            acquire_lock("smoke_conflict_test")  # same PID, still alive -> conflict
        # force=True is explicit permission to reclaim, so it must NOT raise.
        relock = acquire_lock("smoke_conflict_test", force=True)
        release_lock(relock)
    finally:
        release_lock(lock)


def test_run_boilerplate_end_to_end():
    """The single entry point scripts use: all four steps run and a dict comes back."""
    ctx = run_boilerplate(
        script_name="smoke_end_to_end",
        script_version="0.0.1",
        required_packages=REQUIRED,
        backup_paths=None,
    )
    try:
        assert ctx["package_count"] == len(REQUIRED)
        assert isinstance(ctx["python"], str)
        # The run must have left a "boilerplate_ok" entry as the latest log line.
        last_line = RUN_LOG_PATH.read_text().splitlines()[-1]
        assert json.loads(last_line)["event"] == "boilerplate_ok"
    finally:
        release_lock(ctx["lockfile"])
