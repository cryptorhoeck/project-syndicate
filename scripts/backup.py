"""
Backup system for Project Syndicate.

Handles automated database (PostgreSQL) and configuration directory backups.
Each backup is saved to a timestamped directory under backups/ with the format
backup_YYYYMMDD_HHMMSS/. Inside each backup directory:

    syndicate.sql   — full pg_dump of the syndicate database
    config/         — mirror of the project's config/ directory

Rotation policy:
    - Keeps the last 7 daily backups.
    - Keeps the last 4 weekly backups (Sunday snapshots).
    - All other backups are purged by cleanup_old_backups().

Usage:
    python scripts/backup.py          # run directly
    from scripts.backup import main   # import and call
"""

__version__ = "0.1.0"

import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

import structlog
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
BACKUPS_DIR = PROJECT_ROOT / "backups"
CONFIG_DIR = PROJECT_ROOT / "config"

# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------
load_dotenv(PROJECT_ROOT / ".env", override=True)

DATABASE_URL = os.getenv("DATABASE_URL", "")
PG_DUMP_PATH = os.getenv("PG_DUMP_PATH", "pg_dump")

# ---------------------------------------------------------------------------
# Rotation limits
# ---------------------------------------------------------------------------
DAILY_KEEP = 7
WEEKLY_KEEP = 4

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
log = structlog.get_logger("backup")

# ---------------------------------------------------------------------------
# Backup timestamp directory pattern
# ---------------------------------------------------------------------------
BACKUP_DIR_RE = re.compile(r"^backup_(\d{8}_\d{6})$")
TIMESTAMP_FMT = "%Y%m%d_%H%M%S"


def _parse_backup_timestamp(name: str) -> datetime | None:
    """Return the datetime encoded in a backup directory name, or None."""
    match = BACKUP_DIR_RE.match(name)
    if match:
        try:
            return datetime.strptime(match.group(1), TIMESTAMP_FMT)
        except ValueError:
            return None
    return None


def backup_database(dest: Path) -> bool:
    """Run pg_dump and write the output to *dest*/syndicate.sql.

    Returns True on success, False on failure.
    """
    if not DATABASE_URL:
        log.error("DATABASE_URL is not set — skipping database backup")
        return False

    dump_file = dest / "syndicate.sql"
    cmd = [PG_DUMP_PATH, f"--dbname={DATABASE_URL}", "-f", str(dump_file)]
    log.info("running_pg_dump", command=" ".join(cmd))

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=600,
        )
        if result.returncode != 0:
            log.error(
                "pg_dump_failed",
                returncode=result.returncode,
                stderr=result.stderr.strip(),
            )
            return False
        log.info("database_backup_complete", file=str(dump_file))
        return True
    except FileNotFoundError:
        log.error("pg_dump_not_found", path=PG_DUMP_PATH)
        return False
    except subprocess.TimeoutExpired:
        log.error("pg_dump_timeout")
        return False


def backup_config(dest: Path) -> bool:
    """Copy the config/ directory into *dest*/config/.

    Returns True on success, False on failure.
    """
    if not CONFIG_DIR.is_dir():
        log.warning("config_dir_missing", path=str(CONFIG_DIR))
        return False

    target = dest / "config"
    try:
        shutil.copytree(CONFIG_DIR, target)
        log.info("config_backup_complete", target=str(target))
        return True
    except Exception as exc:
        log.error("config_backup_failed", error=str(exc))
        return False


def cleanup_old_backups() -> None:
    """Enforce the rotation policy.

    Keeps the most recent *DAILY_KEEP* daily backups and the most recent
    *WEEKLY_KEEP* weekly (Sunday) backups.  Everything else is removed.
    """
    if not BACKUPS_DIR.is_dir():
        return

    # Collect all valid backup dirs with their timestamps.
    entries: list[tuple[Path, datetime]] = []
    for child in BACKUPS_DIR.iterdir():
        if child.is_dir():
            ts = _parse_backup_timestamp(child.name)
            if ts is not None:
                entries.append((child, ts))

    if not entries:
        return

    # Sort newest-first.
    entries.sort(key=lambda x: x[1], reverse=True)

    # Determine which backups to keep.
    keep: set[Path] = set()

    # --- daily: keep the latest DAILY_KEEP ---
    for path, _ in entries[:DAILY_KEEP]:
        keep.add(path)

    # --- weekly (Sunday): keep the latest WEEKLY_KEEP Sunday backups ---
    weekly_count = 0
    for path, ts in entries:
        if ts.weekday() == 6:  # Sunday
            keep.add(path)
            weekly_count += 1
            if weekly_count >= WEEKLY_KEEP:
                break

    # Remove everything not in the keep set.
    for path, ts in entries:
        if path not in keep:
            log.info("removing_old_backup", path=str(path), timestamp=str(ts))
            shutil.rmtree(path)


def run_backup() -> bool:
    """Execute a full backup cycle (database + config).

    Returns True if both steps succeed.
    """
    timestamp = datetime.now().strftime(TIMESTAMP_FMT)
    backup_dir = BACKUPS_DIR / f"backup_{timestamp}"

    log.info("starting_backup", target=str(backup_dir))
    backup_dir.mkdir(parents=True, exist_ok=True)

    db_ok = backup_database(backup_dir)
    cfg_ok = backup_config(backup_dir)

    if db_ok and cfg_ok:
        log.info("backup_complete", directory=str(backup_dir))
    else:
        log.warning(
            "backup_partial",
            database=db_ok,
            config=cfg_ok,
            directory=str(backup_dir),
        )

    return db_ok and cfg_ok


def main() -> None:
    """Entry point: run a backup then enforce the rotation policy."""
    log.info("backup_started", version=__version__)
    success = run_backup()
    cleanup_old_backups()
    log.info("backup_finished", success=success)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
