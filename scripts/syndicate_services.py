"""
Project Syndicate — Service Manager

Starts, stops, and health-checks PostgreSQL, Memurai, and the Arena.
Uses health gates between sequential startups.
"""

__version__ = "0.1.0"

import os
import signal
import socket
import subprocess
import sys
import time
import webbrowser
from pathlib import Path
from urllib.request import urlopen
from urllib.error import URLError

from rich.console import Console

from scripts.syndicate_pids import (
    record_pid, remove_pid, is_process_alive, is_service_running,
    load_pids,
)

# Windows process creation flags
CREATE_NEW_PROCESS_GROUP = 0x00000200
DETACHED_PROCESS = 0x00000008

PROJECT_ROOT = Path(__file__).parent.parent


# ── Port / Health Checks ────────────────────────────────────

def _check_port(host: str, port: int, timeout: float = 2.0) -> bool:
    """Check if a port is accepting connections."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (socket.timeout, ConnectionRefusedError, OSError):
        return False


def check_postgresql(config: dict) -> bool:
    """Check if PostgreSQL is accepting connections."""
    port = config.get("postgresql", {}).get("port", 5432)
    return _check_port("localhost", port)


def check_memurai(config: dict) -> bool:
    """Check if Memurai/Redis is responding."""
    port = config.get("memurai", {}).get("port", 6379)
    return _check_port("localhost", port)


def check_arena(config: dict) -> bool:
    """Check if the Arena/Dashboard is responding on its HTTP port."""
    host = config.get("dashboard", {}).get("host", "localhost")
    port = config.get("dashboard", {}).get("port", 8000)
    try:
        urlopen(f"http://{host}:{port}/", timeout=3)
        return True
    except (URLError, OSError):
        return False


# ── PostgreSQL ──────────────────────────────────────────────

def start_postgresql(config: dict, console: Console) -> bool:
    """Start PostgreSQL with pg_ctl, wait for health gate."""
    if check_postgresql(config):
        console.print("  PostgreSQL [green]already running[/green]")
        return True

    pg = config.get("postgresql", {})
    bin_path = pg.get("bin_path")
    data_path = pg.get("data_path")

    if not bin_path or not data_path:
        console.print("  [red]PostgreSQL paths not configured.[/red] Run Settings to fix.")
        return False

    pg_ctl = os.path.join(bin_path, "pg_ctl.exe")
    if not os.path.isfile(pg_ctl):
        console.print(f"  [red]pg_ctl not found at {pg_ctl}[/red]")
        return False

    logs_dir = PROJECT_ROOT / "logs"
    logs_dir.mkdir(exist_ok=True)
    log_file = str(logs_dir / "postgresql.log")

    console.print("  Starting PostgreSQL...", end="")
    try:
        subprocess.Popen(
            [pg_ctl, "start", "-D", data_path, "-l", log_file],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except Exception as e:
        console.print(f" [red]FAILED: {e}[/red]")
        return False

    # Health gate: wait up to 30 seconds
    for _ in range(15):
        time.sleep(2)
        if check_postgresql(config):
            console.print(" [green]OK[/green]")
            record_pid("postgresql", None)  # pg_ctl manages its own PID
            return True

    console.print(" [red]TIMEOUT[/red] (30s)")
    return False


def stop_postgresql(config: dict, console: Console) -> bool:
    """Stop PostgreSQL with pg_ctl."""
    if not check_postgresql(config):
        console.print("  PostgreSQL [dim]already stopped[/dim]")
        remove_pid("postgresql")
        return True

    pg = config.get("postgresql", {})
    bin_path = pg.get("bin_path")
    data_path = pg.get("data_path")

    if not bin_path or not data_path:
        console.print("  [red]PostgreSQL paths not configured.[/red]")
        return False

    pg_ctl = os.path.join(bin_path, "pg_ctl.exe")
    console.print("  Stopping PostgreSQL...", end="")
    try:
        subprocess.run(
            [pg_ctl, "stop", "-D", data_path, "-m", "fast"],
            capture_output=True, timeout=15,
        )
    except Exception as e:
        console.print(f" [red]FAILED: {e}[/red]")
        return False

    # Wait for shutdown
    for _ in range(8):
        time.sleep(1)
        if not check_postgresql(config):
            console.print(" [green]OK[/green]")
            remove_pid("postgresql")
            return True

    console.print(" [yellow]may still be stopping[/yellow]")
    remove_pid("postgresql")
    return True


# ── Memurai ─────────────────────────────────────────────────

def start_memurai(config: dict, console: Console) -> bool:
    """Start Memurai (prefer Windows Service, fallback to exe)."""
    if check_memurai(config):
        console.print("  Memurai    [green]already running[/green]")
        return True

    mem = config.get("memurai", {})
    service_name = mem.get("service_name", "memurai")

    console.print("  Starting Memurai...", end="")

    # Try service first
    try:
        result = subprocess.run(
            ["net", "start", service_name],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode == 0 or "already been started" in result.stdout:
            # Health gate
            for _ in range(10):
                time.sleep(1)
                if check_memurai(config):
                    console.print(" [green]OK[/green] (service)")
                    record_pid("memurai", None, is_service=True)
                    return True
    except Exception:
        pass

    # Fallback: start exe directly
    exe_path = mem.get("exe_path")
    if exe_path and os.path.isfile(exe_path):
        try:
            proc = subprocess.Popen(
                [exe_path],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=CREATE_NEW_PROCESS_GROUP | DETACHED_PROCESS,
            )
            for _ in range(10):
                time.sleep(1)
                if check_memurai(config):
                    console.print(" [green]OK[/green] (exe)")
                    record_pid("memurai", proc.pid)
                    return True
        except Exception:
            pass

    console.print(" [red]FAILED[/red]")
    return False


def stop_memurai(config: dict, console: Console) -> bool:
    """Stop Memurai."""
    if not check_memurai(config):
        console.print("  Memurai    [dim]already stopped[/dim]")
        remove_pid("memurai")
        return True

    mem = config.get("memurai", {})
    service_name = mem.get("service_name", "memurai")
    console.print("  Stopping Memurai...", end="")

    # Try service stop
    try:
        subprocess.run(
            ["net", "stop", service_name],
            capture_output=True, timeout=15,
        )
        time.sleep(2)
        if not check_memurai(config):
            console.print(" [green]OK[/green]")
            remove_pid("memurai")
            return True
    except Exception:
        pass

    # Try killing PID
    pids = load_pids()
    mem_info = pids.get("memurai", {})
    pid = mem_info.get("pid")
    if pid and is_process_alive(pid):
        try:
            subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True, timeout=10)
        except Exception:
            pass

    remove_pid("memurai")
    console.print(" [green]OK[/green]")
    return True


# ── Arena ───────────────────────────────────────────────────

def start_arena(config: dict, console: Console) -> bool:
    """Start the Arena (includes Dashboard)."""
    if check_arena(config):
        console.print("  Arena      [green]already running[/green]")
        return True

    arena_script = config.get("arena_script")
    if not arena_script or not os.path.isfile(arena_script):
        console.print("  [red]Arena script not found.[/red]")
        return False

    logs_dir = PROJECT_ROOT / "logs"
    logs_dir.mkdir(exist_ok=True)
    log_file = logs_dir / "arena.log"

    console.print("  Starting Arena...", end="")
    try:
        log_fh = open(log_file, "a")
        proc = subprocess.Popen(
            [sys.executable, arena_script],
            stdout=log_fh, stderr=subprocess.STDOUT,
            cwd=str(PROJECT_ROOT),
            creationflags=CREATE_NEW_PROCESS_GROUP | DETACHED_PROCESS,
        )
    except Exception as e:
        console.print(f" [red]FAILED: {e}[/red]")
        return False

    record_pid("arena", proc.pid)

    # Health gate: wait up to 90 seconds (Arena takes time to boot)
    for i in range(30):
        time.sleep(3)
        if check_arena(config):
            console.print(f" [green]OK[/green] (pid {proc.pid})")
            return True
        # Check process didn't crash
        if proc.poll() is not None:
            console.print(f" [red]CRASHED[/red] (exit code {proc.returncode})")
            remove_pid("arena")
            return False

    console.print(f" [yellow]started (pid {proc.pid})[/yellow] — dashboard may still be loading")
    return True


def stop_arena(config: dict, console: Console) -> bool:
    """Stop the Arena gracefully."""
    pids = load_pids()
    arena_info = pids.get("arena", {})
    pid = arena_info.get("pid")

    if not pid or not is_process_alive(pid):
        if not check_arena(config):
            console.print("  Arena      [dim]already stopped[/dim]")
            remove_pid("arena")
            return True

    console.print("  Stopping Arena...", end="")

    if pid:
        # Try graceful: send CTRL_BREAK to process group
        try:
            os.kill(pid, signal.CTRL_BREAK_EVENT)
        except Exception:
            pass

        # Wait for graceful shutdown
        for _ in range(10):
            time.sleep(2)
            if not is_process_alive(pid):
                console.print(" [green]OK[/green]")
                remove_pid("arena")
                return True

        # Force kill with process tree
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                capture_output=True, timeout=10,
            )
        except Exception:
            pass

    remove_pid("arena")
    time.sleep(1)
    console.print(" [green]OK[/green]")
    return True


# ── Schema Migration ────────────────────────────────────────

def apply_schema_updates(config: dict, console: Console) -> bool:
    """Apply any missing database schema changes.

    Runs after PostgreSQL is confirmed healthy but before the Arena starts.
    Idempotent — safe to run every launch.
    """
    console.print("  Applying schema updates...", end="")

    pg = config.get("postgresql", {})
    db_url = (
        f"postgresql://{pg.get('user', 'postgres')}"
        f"@localhost:{pg.get('port', 5432)}"
        f"/{pg.get('database', 'syndicate')}"
    )

    try:
        from sqlalchemy import create_engine, text
        engine = create_engine(db_url)

        # Columns to add (idempotent — skips if exists)
        add_columns = [
            ("agents", "last_words", "TEXT"),
            ("agent_cycles", "model_used", "VARCHAR(60)"),
            ("agent_cycles", "model_reason", "VARCHAR(30)"),
        ]

        # Tables to create (IF NOT EXISTS — idempotent)
        create_tables = [
            """CREATE TABLE IF NOT EXISTS intel_accuracy_tracking (
                id SERIAL PRIMARY KEY,
                message_id INT NOT NULL,
                agent_id INT NOT NULL,
                agent_name VARCHAR(100) NOT NULL,
                market VARCHAR(50) NOT NULL,
                confidence_stated INT NOT NULL,
                content_summary TEXT NOT NULL,
                posted_at TIMESTAMP NOT NULL,
                outcome VARCHAR(20) DEFAULT 'pending',
                outcome_determined_at TIMESTAMP,
                outcome_evidence TEXT,
                reputation_change FLOAT,
                created_at TIMESTAMP DEFAULT NOW()
            )""",
            """CREATE TABLE IF NOT EXISTS intel_challenges (
                id SERIAL PRIMARY KEY,
                challenger_agent_id INT NOT NULL,
                challenger_agent_name VARCHAR(100) NOT NULL,
                target_message_id INT NOT NULL,
                target_agent_id INT NOT NULL,
                challenge_reason TEXT NOT NULL,
                counter_evidence TEXT NOT NULL,
                agora_message_id INT,
                outcome VARCHAR(20) DEFAULT 'pending',
                outcome_determined_at TIMESTAMP,
                challenger_reputation_change FLOAT,
                target_reputation_change FLOAT,
                created_at TIMESTAMP DEFAULT NOW()
            )""",
            """CREATE TABLE IF NOT EXISTS agent_alliances (
                id SERIAL PRIMARY KEY,
                proposer_agent_id INT NOT NULL,
                proposer_agent_name VARCHAR(100) NOT NULL,
                target_agent_id INT NOT NULL,
                target_agent_name VARCHAR(100) NOT NULL,
                proposer_offer TEXT NOT NULL,
                proposer_request TEXT NOT NULL,
                status VARCHAR(20) DEFAULT 'proposed',
                proposed_at TIMESTAMP DEFAULT NOW(),
                accepted_at TIMESTAMP,
                dissolved_at TIMESTAMP,
                dissolved_by INT,
                dissolution_reason TEXT
            )""",
            """CREATE TABLE IF NOT EXISTS system_improvement_proposals (
                id SERIAL PRIMARY KEY,
                proposer_agent_id INT NOT NULL,
                proposer_agent_name VARCHAR(100) NOT NULL,
                title VARCHAR(200) NOT NULL,
                category VARCHAR(50) NOT NULL,
                proposal TEXT NOT NULL,
                rationale TEXT NOT NULL,
                metrics_affected TEXT,
                agora_message_id INT,
                support_count INT DEFAULT 0,
                oppose_count INT DEFAULT 0,
                genesis_verdict VARCHAR(20),
                genesis_reasoning TEXT,
                owner_decision VARCHAR(20),
                owner_notes TEXT,
                status VARCHAR(20) DEFAULT 'proposed',
                proposed_at TIMESTAMP DEFAULT NOW(),
                resolved_at TIMESTAMP
            )""",
            """CREATE TABLE IF NOT EXISTS agent_tools (
                id SERIAL PRIMARY KEY,
                agent_id INT NOT NULL,
                tool_name VARCHAR(100) NOT NULL,
                description TEXT NOT NULL,
                script TEXT NOT NULL,
                script_hash VARCHAR(64) NOT NULL,
                version INT DEFAULT 1,
                times_executed INT DEFAULT 0,
                times_succeeded INT DEFAULT 0,
                times_failed INT DEFAULT 0,
                avg_execution_ms FLOAT,
                times_before_profitable INT DEFAULT 0,
                times_before_unprofitable INT DEFAULT 0,
                estimated_win_rate FLOAT,
                inherited_from_agent_id INT,
                original_author_id INT,
                generation_created INT DEFAULT 1,
                is_active BOOLEAN DEFAULT TRUE,
                deactivated_reason TEXT,
                created_at TIMESTAMP DEFAULT NOW(),
                updated_at TIMESTAMP DEFAULT NOW(),
                UNIQUE(agent_id, tool_name)
            )""",
            """CREATE TABLE IF NOT EXISTS sandbox_executions (
                id SERIAL PRIMARY KEY,
                agent_id INT NOT NULL,
                cycle_number INT NOT NULL,
                tool_name VARCHAR(100),
                script_hash VARCHAR(64) NOT NULL,
                script_length INT NOT NULL,
                success BOOLEAN NOT NULL,
                output TEXT,
                error TEXT,
                execution_time_ms INT NOT NULL,
                execution_cost_usd FLOAT NOT NULL,
                purpose TEXT,
                was_pre_compute BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMP DEFAULT NOW()
            )""",
            """CREATE TABLE IF NOT EXISTS boot_sequence_log (
                id SERIAL PRIMARY KEY,
                wave_number INT NOT NULL,
                event_type VARCHAR(50) NOT NULL,
                agent_id INT,
                agent_name VARCHAR(100),
                details TEXT,
                timestamp TIMESTAMP DEFAULT NOW()
            )""",
            """CREATE TABLE IF NOT EXISTS agent_genomes (
                id SERIAL PRIMARY KEY,
                agent_id INT NOT NULL UNIQUE,
                genome_version INT DEFAULT 1,
                genome_data JSONB NOT NULL,
                parent_genome_id INT,
                mutations_applied JSONB,
                fitness_score FLOAT,
                evaluations_with_genome INT DEFAULT 0,
                created_at TIMESTAMP DEFAULT NOW(),
                updated_at TIMESTAMP DEFAULT NOW()
            )""",
        ]

        with engine.connect() as conn:
            applied = 0

            # Add columns (skip if already exists)
            for table, col, col_type in add_columns:
                try:
                    conn.execute(text(
                        f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {col} {col_type}"
                    ))
                    applied += 1
                except Exception:
                    pass

            # Create tables
            for sql in create_tables:
                try:
                    conn.execute(text(sql))
                    applied += 1
                except Exception:
                    pass

            conn.commit()

        engine.dispose()
        console.print(f" [green]OK[/green] ({applied} checks)")
        return True

    except Exception as e:
        console.print(f" [yellow]skipped[/yellow] ({e})")
        return True  # Non-fatal — don't block launch


# ── Composite Operations ────────────────────────────────────

def launch_all(config: dict, console: Console) -> bool:
    """Sequential launch with health gates."""
    console.print()
    console.print("[bold]  LAUNCH SEQUENCE[/bold]")
    console.print("  " + "─" * 40)

    # Ensure logs directory
    (PROJECT_ROOT / "logs").mkdir(exist_ok=True)

    # 1. PostgreSQL
    pg_ok = start_postgresql(config, console)
    if not pg_ok:
        ans = input("  PostgreSQL failed. Continue anyway? [y/N]: ").strip().lower()
        if ans != "y":
            return False

    # 1.5. Schema updates (idempotent — runs every launch)
    if pg_ok:
        apply_schema_updates(config, console)

    # 2. Memurai
    mem_ok = start_memurai(config, console)
    if not mem_ok:
        ans = input("  Memurai failed. Continue anyway? [y/N]: ").strip().lower()
        if ans != "y":
            return False

    # 3. Arena
    arena_ok = start_arena(config, console)

    console.print("  " + "─" * 40)

    if pg_ok and mem_ok and arena_ok:
        console.print("  [green bold]ALL SYSTEMS GO[/green bold]")
        if config.get("open_browser_on_launch") and check_arena(config):
            host = config.get("dashboard", {}).get("host", "localhost")
            port = config.get("dashboard", {}).get("port", 8000)
            webbrowser.open(f"http://{host}:{port}")
        return True
    else:
        console.print("  [yellow]Partial launch — check status above[/yellow]")
        return False


def shutdown_all(config: dict, console: Console, scope: str = "all") -> bool:
    """Graceful shutdown. scope: 'all' or 'arena'."""
    console.print()
    console.print("[bold]  SHUTDOWN SEQUENCE[/bold]")
    console.print("  " + "─" * 40)

    # 1. Arena (always)
    stop_arena(config, console)

    if scope == "all":
        # 2. Memurai
        stop_memurai(config, console)
        # 3. PostgreSQL
        stop_postgresql(config, console)

    console.print("  " + "─" * 40)
    console.print("  [dim]Shutdown complete.[/dim]")
    return True


def get_system_status(config: dict) -> dict:
    """Check all services and return status dict."""
    return {
        "postgresql": {
            "status": "running" if check_postgresql(config) else "stopped",
            "port": config.get("postgresql", {}).get("port", 5432),
        },
        "memurai": {
            "status": "running" if check_memurai(config) else "stopped",
            "port": config.get("memurai", {}).get("port", 6379),
        },
        "arena": {
            "status": "running" if check_arena(config) else "stopped",
            "port": config.get("dashboard", {}).get("port", 8000),
        },
    }


def clean_slate(config: dict, console: Console) -> bool:
    """Reset database for a fresh Arena run.

    Each step runs in its own transaction so a failure in one
    (e.g. table doesn't exist) doesn't poison subsequent steps.
    """
    if check_arena(config):
        console.print("  [red]Arena is still running. Stop it first.[/red]")
        return False

    try:
        from sqlalchemy import create_engine, text
        from dotenv import load_dotenv
        load_dotenv(str(PROJECT_ROOT / ".env"))

        pg = config.get("postgresql", {})
        db_url = (
            f"postgresql://{pg.get('user', 'postgres')}"
            f"@localhost:{pg.get('port', 5432)}"
            f"/{pg.get('database', 'syndicate')}"
        )
        engine = create_engine(db_url)

        # All tables to truncate — FK-safe order (children before parents).
        # Includes Phase 8B/8C tables. Missing tables are silently skipped.
        tables = [
            # Phase 8C
            "sandbox_executions", "agent_tools", "agent_genomes",
            # Phase 8B
            "intel_accuracy_tracking", "intel_challenges",
            "agent_alliances", "system_improvement_proposals",
            # Phase 3E
            "behavioral_profiles", "agent_relationships",
            "divergence_scores", "study_history",
            # Phase 3D
            "rejection_tracking", "post_mortems",
            # Phase 3F
            "memorials", "lineages", "dynasties",
            # Phase 3C
            "positions", "orders", "limit_orders", "equity_snapshots",
            # Phase 3A
            "agent_cycles", "agent_long_term_memory", "agent_reflections",
            # Phase 3B
            "boot_sequence_log", "opportunities", "plans",
            # Phase 2C
            "intel_signals", "intel_endorsements",
            "review_requests", "reputation_transactions",
            "gaming_flags",
            # Phase 2B
            "library_contributions", "library_views",
            # Phase 2A
            "agora_read_receipts",
            # Core
            "messages", "evaluations", "transactions",
        ]

        truncated = 0
        skipped = 0

        # Step 1: Truncate each table in its own transaction
        for table in tables:
            try:
                with engine.connect() as conn:
                    conn.execute(text(f"TRUNCATE TABLE {table} CASCADE"))
                    conn.commit()
                    truncated += 1
            except Exception:
                skipped += 1

        console.print(f"  Truncated {truncated} tables ({skipped} skipped)")

        # Step 1b: Force-clear messages and read receipts (may have been skipped above)
        for tbl in ["agora_read_receipts", "messages"]:
            try:
                with engine.connect() as conn:
                    conn.execute(text(f"DELETE FROM {tbl}"))
                    conn.commit()
            except Exception:
                pass

        # Step 1c: Reset Agora channel message counts
        try:
            with engine.connect() as conn:
                conn.execute(text("UPDATE agora_channels SET message_count = 0"))
                conn.commit()
                console.print("  Agora channels reset")
        except Exception:
            pass

        # Step 2: Delete agents (keep Genesis id=0)
        try:
            with engine.connect() as conn:
                deleted = conn.execute(text("DELETE FROM agents WHERE id != 0"))
                conn.commit()
                console.print(f"  Deleted {deleted.rowcount} agents")
        except Exception as e:
            console.print(f"  [yellow]Agent cleanup: {e}[/yellow]")

        # Step 3: Reset system state
        try:
            with engine.connect() as conn:
                conn.execute(text("""
                    UPDATE system_state SET
                        total_treasury = 500.0,
                        peak_treasury = 500.0,
                        alert_status = 'green',
                        active_agent_count = 0,
                        current_regime = 'unknown'
                    WHERE id = 1
                """))
                conn.commit()
                console.print("  System state reset to $500 / GREEN")
        except Exception as e:
            console.print(f"  [yellow]System state reset: {e}[/yellow]")

        # Step 4: Reset sequences so new agent IDs start fresh
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT setval('agents_id_seq', 1, false)"))
                conn.commit()
        except Exception:
            pass

        engine.dispose()

        # Step 5: Flush Redis/Memurai
        if check_memurai(config):
            try:
                cli_path = config.get("memurai", {}).get("cli_path")
                if cli_path and os.path.isfile(cli_path):
                    subprocess.run([cli_path, "FLUSHDB"], capture_output=True, timeout=5)
                else:
                    subprocess.run(["redis-cli", "FLUSHDB"], capture_output=True, timeout=5)
                console.print("  Redis flushed")
            except Exception:
                console.print("  [dim]Redis flush skipped[/dim]")

        console.print("  [green]Clean slate complete.[/green] Ready for a fresh Arena run.")
        return True

    except Exception as e:
        console.print(f"  [red]Clean slate failed: {e}[/red]")
        return False
