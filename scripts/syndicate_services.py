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
    """Reset database for a fresh Arena run."""
    if check_arena(config):
        console.print("  [red]Arena is still running. Stop it first.[/red]")
        return False

    try:
        from sqlalchemy import create_engine, text
        from dotenv import load_dotenv
        load_dotenv(str(PROJECT_ROOT / ".env"))

        pg = config.get("postgresql", {})
        db_url = f"postgresql://{pg.get('user', 'postgres')}@localhost:{pg.get('port', 5432)}/{pg.get('database', 'syndicate')}"
        engine = create_engine(db_url)

        # Tables to truncate in FK-safe order
        tables = [
            "agent_cycles", "agent_long_term_memory", "agent_reflections",
            "agora_read_receipts", "messages",
            "positions", "orders", "limit_orders", "equity_snapshots",
            "transactions", "evaluations", "post_mortems",
            "opportunities", "plans",
            "intel_signals", "intel_endorsements",
            "review_requests", "reputation_transactions",
            "gaming_flags",
            "behavioral_profiles", "agent_relationships",
            "divergence_scores", "study_history",
            "rejection_tracking",
            "memorials", "lineages", "dynasties",
            "library_contributions", "library_views",
        ]

        with engine.connect() as conn:
            for table in tables:
                try:
                    conn.execute(text(f"TRUNCATE TABLE {table} CASCADE"))
                except Exception:
                    pass  # Table may not exist

            # Reset agents (keep Genesis)
            conn.execute(text("DELETE FROM agents WHERE id != 0"))

            # Reset system state
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

        engine.dispose()

        # Flush Redis/Memurai
        if check_memurai(config):
            cli_path = config.get("memurai", {}).get("cli_path")
            if cli_path and os.path.isfile(cli_path):
                subprocess.run([cli_path, "FLUSHDB"], capture_output=True, timeout=5)
            else:
                # Try default redis-cli
                try:
                    subprocess.run(["redis-cli", "FLUSHDB"], capture_output=True, timeout=5)
                except Exception:
                    pass

        console.print("  [green]Clean slate complete.[/green] Ready for a fresh Arena run.")
        return True

    except Exception as e:
        console.print(f"  [red]Clean slate failed: {e}[/red]")
        return False
