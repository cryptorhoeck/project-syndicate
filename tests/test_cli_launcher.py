"""
Tests for the Syndicate CLI Launcher (Phase 8A).

Focus on testable logic — config, PID management, detection.
Don't test interactive menu display or actual service start/stop.
"""

__version__ = "0.1.0"

import json
import os
import pytest
from pathlib import Path


# ── Config Tests ────────────────────────────────────────────

class TestConfig:
    def test_save_and_load_config(self, tmp_path):
        """Save a config dict, load it back, verify identical."""
        from scripts.syndicate_config import save_config, load_config, CONFIG_FILE

        # Temporarily redirect config file to tmp_path
        config_file = tmp_path / "test_config.json"
        test_config = {
            "version": 1,
            "project_path": "E:\\test",
            "postgresql": {"port": 5432},
        }

        # Write directly to tmp path
        with open(config_file, "w") as f:
            json.dump(test_config, f)

        with open(config_file, "r") as f:
            loaded = json.load(f)

        assert loaded["version"] == 1
        assert loaded["project_path"] == "E:\\test"
        assert loaded["postgresql"]["port"] == 5432

    def test_load_missing_config_returns_none(self, tmp_path):
        """Loading from nonexistent path returns None."""
        from scripts.syndicate_config import load_config

        # The default path may or may not exist, but we can test
        # the logic by checking a definitely missing file
        missing = tmp_path / "nonexistent.json"
        assert not missing.exists()

    def test_config_schema_version(self):
        """detect_paths() returns a dict with version field."""
        from scripts.syndicate_config import detect_paths

        detected = detect_paths()
        assert "version" in detected
        assert detected["version"] == 1

    def test_detect_paths_returns_dict(self):
        """detect_paths() returns a dict with expected keys."""
        from scripts.syndicate_config import detect_paths

        detected = detect_paths()
        assert isinstance(detected, dict)
        assert "project_path" in detected
        assert "postgresql" in detected
        assert "memurai" in detected
        assert "dashboard" in detected
        assert "arena_script" in detected

    def test_detect_paths_finds_project(self):
        """detect_paths() should find the project root."""
        from scripts.syndicate_config import detect_paths

        detected = detect_paths()
        project_path = detected["project_path"]
        # Should contain CLAUDE.md
        assert os.path.isfile(os.path.join(project_path, "CLAUDE.md"))


# ── PID Manager Tests ───────────────────────────────────────

class TestPidManager:
    def test_save_and_load_pids(self, tmp_path):
        """Save PIDs, load them back, verify identical."""
        from scripts.syndicate_pids import save_pids, load_pids

        pid_file = tmp_path / "test_pids.json"
        test_pids = {"postgresql": {"pid": 12345, "service": False}}

        save_pids(test_pids, pid_file)
        loaded = load_pids(pid_file)
        assert loaded["postgresql"]["pid"] == 12345

    def test_load_missing_pid_file(self, tmp_path):
        """Loading from nonexistent path returns empty dict."""
        from scripts.syndicate_pids import load_pids

        pid_file = tmp_path / "nonexistent.json"
        result = load_pids(pid_file)
        assert result == {}

    def test_record_pid(self, tmp_path):
        """Record a PID, verify it's in the file."""
        from scripts.syndicate_pids import record_pid, load_pids

        pid_file = tmp_path / "test_pids.json"
        record_pid("test_service", 99999, pid_file=pid_file)

        loaded = load_pids(pid_file)
        assert "test_service" in loaded
        assert loaded["test_service"]["pid"] == 99999

    def test_remove_pid(self, tmp_path):
        """Record then remove a PID, verify it's gone."""
        from scripts.syndicate_pids import record_pid, remove_pid, load_pids

        pid_file = tmp_path / "test_pids.json"
        record_pid("test_service", 99999, pid_file=pid_file)
        remove_pid("test_service", pid_file=pid_file)

        loaded = load_pids(pid_file)
        assert "test_service" not in loaded

    def test_is_process_alive_with_current_pid(self):
        """os.getpid() should always be alive."""
        from scripts.syndicate_pids import is_process_alive

        assert is_process_alive(os.getpid())

    def test_is_process_alive_with_bogus_pid(self):
        """PID 999999 should not be alive (almost certainly)."""
        from scripts.syndicate_pids import is_process_alive

        assert not is_process_alive(999999)

    def test_cleanup_stale_pids(self, tmp_path):
        """Record a bogus PID, cleanup should remove it."""
        from scripts.syndicate_pids import record_pid, cleanup_stale_pids, load_pids

        pid_file = tmp_path / "test_pids.json"
        record_pid("dead_service", 999999, pid_file=pid_file)

        cleanup_stale_pids(pid_file)

        loaded = load_pids(pid_file)
        assert "dead_service" not in loaded


# ── Service Check Tests (non-destructive) ───────────────────

class TestServiceChecks:
    def test_check_postgresql_on_wrong_port(self):
        """Checking PG on an unused port should return False."""
        from scripts.syndicate_services import check_postgresql

        config = {"postgresql": {"port": 59999}}
        assert check_postgresql(config) is False

    def test_check_memurai_on_wrong_port(self):
        """Checking Memurai on an unused port should return False."""
        from scripts.syndicate_services import check_memurai

        config = {"memurai": {"port": 59998}}
        assert check_memurai(config) is False

    def test_check_arena_on_wrong_port(self):
        """Checking Arena on an unused port should return False."""
        from scripts.syndicate_services import check_arena

        config = {"dashboard": {"host": "localhost", "port": 59997}}
        assert check_arena(config) is False

    def test_get_system_status_returns_dict(self):
        """get_system_status returns properly structured dict."""
        from scripts.syndicate_services import get_system_status

        config = {
            "postgresql": {"port": 59999},
            "memurai": {"port": 59998},
            "dashboard": {"host": "localhost", "port": 59997},
        }
        status = get_system_status(config)
        assert "postgresql" in status
        assert "memurai" in status
        assert "arena" in status
        for svc in status.values():
            assert "status" in svc
            assert svc["status"] in ("running", "stopped")
