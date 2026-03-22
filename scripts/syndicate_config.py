"""
Project Syndicate — CLI Configuration Module

Auto-detects service paths, manages config persistence,
and runs first-run wizard when needed.
"""

__version__ = "0.1.0"

import glob
import json
import os
import shutil
import subprocess
from pathlib import Path

# Config file lives next to this script
SCRIPT_DIR = Path(__file__).parent
CONFIG_FILE = SCRIPT_DIR / "syndicate_config.json"
PROJECT_ROOT = SCRIPT_DIR.parent


def load_config() -> dict | None:
    """Load config from JSON file. Returns None if file doesn't exist."""
    if not CONFIG_FILE.exists():
        return None
    try:
        with open(CONFIG_FILE, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return None


def save_config(config: dict) -> None:
    """Save config to JSON file."""
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=4)


def _find_pg_ctl() -> str | None:
    """Try to find pg_ctl binary."""
    # 1. Check PATH
    pg_ctl = shutil.which("pg_ctl")
    if pg_ctl:
        return str(Path(pg_ctl).parent)

    # 2. Check known locations
    candidates = [
        r"C:\ProDesk\pgsql\bin\pg_ctl.exe",
    ]
    # 3. Glob for PostgreSQL installations
    for pattern in [
        r"C:\Program Files\PostgreSQL\*\bin\pg_ctl.exe",
        r"C:\PostgreSQL\*\bin\pg_ctl.exe",
    ]:
        candidates.extend(glob.glob(pattern))

    for path in candidates:
        if os.path.isfile(path):
            return str(Path(path).parent)

    return None


def _find_pg_data(bin_path: str) -> str | None:
    """Try to find PostgreSQL data directory near the bin path."""
    # Check sibling data/ directory
    bin_dir = Path(bin_path)
    data_dir = bin_dir.parent / "data"
    if data_dir.exists() and (data_dir / "postgresql.conf").exists():
        return str(data_dir)

    # Check common locations
    for path in [
        r"C:\ProDesk\pgsql\data",
        r"C:\Program Files\PostgreSQL\16\data",
        r"C:\Program Files\PostgreSQL\15\data",
        r"C:\Program Files\PostgreSQL\14\data",
    ]:
        p = Path(path)
        if p.exists() and (p / "postgresql.conf").exists():
            return str(p)

    return None


def _find_memurai() -> dict:
    """Try to find Memurai installation."""
    result = {"found": False, "service": False, "exe_path": None, "cli_path": None}

    # Check if registered as Windows Service
    try:
        r = subprocess.run(
            ["sc", "query", "memurai"],
            capture_output=True, text=True, timeout=5
        )
        if "SERVICE_NAME" in r.stdout:
            result["found"] = True
            result["service"] = True
    except Exception:
        pass

    # Check known paths
    for base in [
        r"C:\Program Files\Memurai",
        r"C:\Program Files (x86)\Memurai",
    ]:
        exe = os.path.join(base, "memurai-server.exe")
        cli = os.path.join(base, "memurai-cli.exe")
        if os.path.isfile(exe):
            result["found"] = True
            result["exe_path"] = exe
        if os.path.isfile(cli):
            result["cli_path"] = cli

    return result


def detect_paths() -> dict:
    """Auto-detect all paths. Returns a config dict with found/not-found status."""
    detected = {
        "version": 1,
        "project_path": str(PROJECT_ROOT),
        "venv_path": str(PROJECT_ROOT / ".venv"),
        "postgresql": {
            "bin_path": None,
            "data_path": None,
            "port": 5432,
            "user": "postgres",
            "database": "syndicate",
        },
        "memurai": {
            "service_name": "memurai",
            "exe_path": None,
            "cli_path": None,
            "port": 6379,
        },
        "dashboard": {
            "host": "localhost",
            "port": 8000,
        },
        "arena_script": str(SCRIPT_DIR / "run_arena.py"),
        "backup_script": str(SCRIPT_DIR / "backup.py"),
        "pid_file": str(SCRIPT_DIR / ".syndicate_pids.json"),
        "open_browser_on_launch": True,
    }

    # PostgreSQL
    pg_bin = _find_pg_ctl()
    if pg_bin:
        detected["postgresql"]["bin_path"] = pg_bin
        pg_data = _find_pg_data(pg_bin)
        if pg_data:
            detected["postgresql"]["data_path"] = pg_data

    # Memurai
    mem = _find_memurai()
    if mem["found"]:
        if mem["exe_path"]:
            detected["memurai"]["exe_path"] = mem["exe_path"]
        if mem["cli_path"]:
            detected["memurai"]["cli_path"] = mem["cli_path"]

    # Venv check
    venv_python = Path(detected["venv_path"]) / "Scripts" / "python.exe"
    detected["_venv_found"] = venv_python.exists()

    # Arena script check
    detected["_arena_found"] = os.path.isfile(detected["arena_script"])
    detected["_backup_found"] = os.path.isfile(detected["backup_script"])
    detected["_pg_found"] = detected["postgresql"]["bin_path"] is not None
    detected["_pg_data_found"] = detected["postgresql"]["data_path"] is not None
    detected["_memurai_found"] = mem["found"]
    detected["_memurai_service"] = mem["service"]

    return detected


def run_first_time_wizard() -> dict:
    """Interactive wizard that detects paths, asks user for missing ones, saves config."""
    print()
    print("=" * 50)
    print("  PROJECT SYNDICATE — FIRST RUN SETUP")
    print("=" * 50)
    print()
    print("  Detecting your environment...")
    print()

    detected = detect_paths()

    items = [
        ("PostgreSQL", detected["_pg_found"], detected["postgresql"].get("bin_path", "not found")),
        ("PG Data Dir", detected["_pg_data_found"], detected["postgresql"].get("data_path", "not found")),
        ("Memurai", detected["_memurai_found"],
         "Windows Service" if detected["_memurai_service"] else (detected["memurai"].get("exe_path") or "not found")),
        ("Project Path", True, detected["project_path"]),
        ("Virtual Env", detected["_venv_found"], detected["venv_path"]),
        ("Arena Script", detected["_arena_found"], detected["arena_script"]),
        ("Backup Script", detected["_backup_found"], detected["backup_script"]),
    ]

    for name, found, path in items:
        icon = "OK" if found else "!!"
        print(f"  {name:.<20} {icon}  {path}")

    print()

    # Ask for missing paths
    if not detected["_pg_found"]:
        path = input("  PostgreSQL bin path (e.g., C:\\PostgreSQL\\16\\bin): ").strip()
        if path and os.path.isdir(path):
            detected["postgresql"]["bin_path"] = path
            pg_data = _find_pg_data(path)
            if pg_data:
                detected["postgresql"]["data_path"] = pg_data

    if not detected["_pg_data_found"] and detected["postgresql"]["bin_path"]:
        path = input("  PostgreSQL data path (e.g., C:\\PostgreSQL\\16\\data): ").strip()
        if path and os.path.isdir(path):
            detected["postgresql"]["data_path"] = path

    if not detected["_memurai_found"]:
        path = input("  Memurai exe path (e.g., C:\\Program Files\\Memurai\\memurai-server.exe): ").strip()
        if path and os.path.isfile(path):
            detected["memurai"]["exe_path"] = path

    # Clean up internal detection flags
    for key in list(detected.keys()):
        if key.startswith("_"):
            del detected[key]

    save_config(detected)
    print()
    print(f"  Configuration saved to: {CONFIG_FILE}")
    print()
    print("  To create a desktop shortcut:")
    print(f'    1. Right-click Desktop → New → Shortcut')
    print(f'    2. Location: "{PROJECT_ROOT / "syndicate.bat"}"')
    print(f'    3. Name: Project Syndicate')
    print()
    input("  Press Enter to continue to main menu...")

    return detected


def get_config() -> dict:
    """Main entry point. Loads config if exists, runs wizard if not."""
    config = load_config()
    if config is not None:
        return config
    return run_first_time_wizard()
