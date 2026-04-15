"""
Project Syndicate — CLI Launcher

One-click terminal menu for managing all Syndicate services.
Double-click syndicate.bat → this script runs.
"""

__version__ = "0.1.0"

import os
import subprocess
import sys
import webbrowser
from pathlib import Path

# Ensure project root is importable
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv(str(PROJECT_ROOT / ".env"), override=True)

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from scripts.syndicate_config import get_config
from scripts.syndicate_pids import cleanup_stale_pids
from scripts.syndicate_services import (
    check_arena, check_memurai, check_postgresql,
    clean_slate, get_system_status,
    launch_all, shutdown_all,
    start_arena, stop_arena,
    start_postgresql, stop_postgresql,
    start_memurai, stop_memurai,
)


def print_banner(console: Console) -> None:
    """Print the startup banner."""
    console.print()
    console.print(
        Panel(
            "[bold white]PROJECT SYNDICATE[/bold white] v" + __version__ + "\n"
            "[dim]Command & Control Center[/dim]",
            border_style="cyan",
            padding=(1, 4),
        )
    )


def display_menu(console: Console, status: dict) -> None:
    """Display the main menu with live status indicators."""

    def _dot(svc_status: str) -> str:
        if svc_status == "running":
            return "[green]⬤ ONLINE[/green] "
        return "[red]⬤ OFFLINE[/red]"

    pg = status.get("postgresql", {})
    mem = status.get("memurai", {})
    arena = status.get("arena", {})

    lines = [
        "",
        f"  PostgreSQL ····· {_dot(pg.get('status'))}    (port {pg.get('port', 5432)})",
        f"  Memurai ········ {_dot(mem.get('status'))}    (port {mem.get('port', 6379)})",
        f"  Arena ·········· {_dot(arena.get('status'))}    (port {arena.get('port', 8000)})",
        "",
    ]
    status_text = "\n".join(lines)

    menu_items = [
        "[bold cyan][1][/bold cyan] Launch All",
        "[bold cyan][2][/bold cyan] Shutdown All",
        "[bold cyan][3][/bold cyan] Open Dashboard",
        "[bold cyan][4][/bold cyan] System Status",
        "[bold cyan][5][/bold cyan] Backup Now",
        "[bold cyan][6][/bold cyan] View Logs",
        "[bold cyan][7][/bold cyan] Clean Slate",
        "[bold cyan][8][/bold cyan] Settings",
        "[bold cyan][9][/bold cyan] Services",
        "[bold cyan][S][/bold cyan] Smoke Test",
        "[bold cyan][0][/bold cyan] Exit",
    ]
    menu_text = "\n  ".join(menu_items)

    console.print(
        Panel(
            status_text + "\n  " + menu_text + "\n",
            border_style="dim",
            padding=(0, 2),
        )
    )


def menu_launch_all(config: dict, console: Console) -> None:
    """Launch all services sequentially with health gates."""
    launch_all(config, console)
    console.print()
    input("  Press Enter to return to menu...")


def menu_shutdown_all(config: dict, console: Console) -> None:
    """Shutdown services."""
    console.print()
    ans = input("  Shut down [A]ll services or [J]ust the Arena? ").strip().upper()
    if ans == "J":
        shutdown_all(config, console, scope="arena")
    elif ans == "A":
        shutdown_all(config, console, scope="all")
    else:
        console.print("  [dim]Cancelled.[/dim]")
    console.print()
    input("  Press Enter to return to menu...")


def menu_open_dashboard(config: dict, console: Console) -> None:
    """Open the dashboard in the browser."""
    if check_arena(config):
        host = config.get("dashboard", {}).get("host", "localhost")
        port = config.get("dashboard", {}).get("port", 8000)
        url = f"http://{host}:{port}"
        webbrowser.open(url)
        console.print(f"  Opened [cyan]{url}[/cyan]")
    else:
        console.print("  [yellow]Arena is not running.[/yellow]")
        ans = input("  Launch it now? [y/N]: ").strip().lower()
        if ans == "y":
            launch_all(config, console)
            if check_arena(config):
                host = config.get("dashboard", {}).get("host", "localhost")
                port = config.get("dashboard", {}).get("port", 8000)
                webbrowser.open(f"http://{host}:{port}")
    console.print()
    input("  Press Enter to return to menu...")


def menu_system_status(config: dict, console: Console) -> None:
    """Show detailed system status table."""
    status = get_system_status(config)

    table = Table(title="System Status", border_style="dim")
    table.add_column("Service", style="bold")
    table.add_column("Status")
    table.add_column("Port", justify="right")

    for name in ["postgresql", "memurai", "arena"]:
        svc = status.get(name, {})
        st = svc.get("status", "unknown")
        st_display = f"[green]{st.upper()}[/green]" if st == "running" else f"[red]{st.upper()}[/red]"
        port = str(svc.get("port", "—"))
        table.add_row(name.title(), st_display, port)

    console.print()
    console.print(table)

    if status.get("arena", {}).get("status") == "running":
        host = config.get("dashboard", {}).get("host", "localhost")
        port = config.get("dashboard", {}).get("port", 8000)
        console.print(f"\n  Dashboard: [cyan]http://{host}:{port}[/cyan]")

    console.print()
    input("  Press Enter to return to menu...")


def menu_backup_now(config: dict, console: Console) -> None:
    """Run the backup script."""
    backup_script = config.get("backup_script")
    if not backup_script or not os.path.isfile(backup_script):
        console.print("  [red]Backup script not found.[/red]")
    else:
        console.print("  Running backup...")
        try:
            result = subprocess.run(
                [sys.executable, backup_script],
                cwd=str(PROJECT_ROOT),
                capture_output=True, text=True, timeout=120,
            )
            if result.returncode == 0:
                console.print("  [green]Backup complete.[/green]")
            else:
                console.print(f"  [yellow]Backup finished with warnings.[/yellow]")
            if result.stderr:
                # Show last few lines
                lines = result.stderr.strip().split("\n")
                for line in lines[-5:]:
                    console.print(f"  [dim]{line}[/dim]")
        except Exception as e:
            console.print(f"  [red]Backup failed: {e}[/red]")

    console.print()
    input("  Press Enter to return to menu...")


def menu_view_logs(config: dict, console: Console) -> None:
    """View log files."""
    logs_dir = PROJECT_ROOT / "logs"

    while True:
        console.print()
        console.print("  [bold]View Logs[/bold]")
        console.print("  [cyan][1][/cyan] Arena Log (last 50 lines)")
        console.print("  [cyan][2][/cyan] PostgreSQL Log (last 50 lines)")
        console.print("  [cyan][3][/cyan] Live tail Arena Log (Ctrl+C to stop)")
        console.print("  [cyan][0][/cyan] Back to main menu")

        choice = input("\n  Choice: ").strip()

        if choice == "1":
            _show_log(logs_dir / "arena.log", 50, console)
        elif choice == "2":
            _show_log(logs_dir / "postgresql.log", 50, console)
        elif choice == "3":
            _live_tail(logs_dir / "arena.log", console)
        elif choice == "0":
            break


def _show_log(log_path: Path, lines: int, console: Console) -> None:
    """Show last N lines of a log file."""
    if not log_path.exists():
        console.print(f"  [dim]No log file found at {log_path}[/dim]")
        return
    try:
        with open(log_path, "r", errors="replace") as f:
            all_lines = f.readlines()
        tail = all_lines[-lines:]
        console.print(f"\n  [dim]── {log_path.name} (last {len(tail)} lines) ──[/dim]")
        for line in tail:
            console.print(f"  {line.rstrip()}")
        console.print(f"  [dim]── end ──[/dim]")
    except Exception as e:
        console.print(f"  [red]Error reading log: {e}[/red]")
    input("\n  Press Enter to continue...")


def _live_tail(log_path: Path, console: Console) -> None:
    """Live tail a log file. Ctrl+C to stop."""
    if not log_path.exists():
        console.print(f"  [dim]No log file found at {log_path}[/dim]")
        return
    console.print(f"  [dim]Tailing {log_path.name}... (Ctrl+C to stop)[/dim]")
    try:
        with open(log_path, "r", errors="replace") as f:
            # Seek to end
            f.seek(0, 2)
            while True:
                line = f.readline()
                if line:
                    console.print(f"  {line.rstrip()}")
                else:
                    import time
                    time.sleep(0.5)
    except KeyboardInterrupt:
        console.print("\n  [dim]Stopped tailing.[/dim]")


def menu_clean_slate(config: dict, console: Console) -> None:
    """Reset database for a fresh Arena run."""
    console.print()
    console.print("  [bold red]⚠  CLEAN SLATE[/bold red]")
    console.print("  This will DELETE all agent data and reset the treasury to $500.")
    console.print("  [red]This cannot be undone.[/red]")
    console.print()

    confirm = input("  Type YES to confirm: ").strip()
    if confirm != "YES":
        console.print("  [dim]Cancelled.[/dim]")
        input("\n  Press Enter to return to menu...")
        return

    if check_arena(config):
        console.print("  [yellow]Arena is still running.[/yellow]")
        ans = input("  Shut it down first? [y/N]: ").strip().lower()
        if ans == "y":
            stop_arena(config, console)
        else:
            console.print("  [dim]Cancelled — stop the Arena first.[/dim]")
            input("\n  Press Enter to return to menu...")
            return

    clean_slate(config, console)
    console.print()
    input("  Press Enter to return to menu...")


def menu_settings(config: dict, console: Console) -> dict:
    """Show and edit settings."""
    from scripts.syndicate_config import save_config, detect_paths, run_first_time_wizard

    while True:
        table = Table(title="Current Configuration", border_style="dim")
        table.add_column("Setting", style="bold")
        table.add_column("Value")

        settings = [
            ("Project Path", config.get("project_path", "—")),
            ("PostgreSQL bin", config.get("postgresql", {}).get("bin_path", "—")),
            ("PostgreSQL data", config.get("postgresql", {}).get("data_path", "—")),
            ("PostgreSQL port", str(config.get("postgresql", {}).get("port", 5432))),
            ("Memurai service", config.get("memurai", {}).get("service_name", "—")),
            ("Memurai port", str(config.get("memurai", {}).get("port", 6379))),
            ("Dashboard port", str(config.get("dashboard", {}).get("port", 8000))),
            ("Open browser", "Yes" if config.get("open_browser_on_launch") else "No"),
        ]
        for name, val in settings:
            table.add_row(name, val)

        console.print()
        console.print(table)
        console.print()
        console.print("  [cyan][1][/cyan] Re-run auto-detection")
        console.print("  [cyan][2][/cyan] Toggle browser-on-launch")
        console.print("  [cyan][3][/cyan] Reset to defaults (re-run wizard)")
        console.print("  [cyan][0][/cyan] Back to main menu")

        choice = input("\n  Choice: ").strip()
        if choice == "1":
            detected = detect_paths()
            # Merge detected into current config (don't overwrite user overrides for existing values)
            if detected.get("postgresql", {}).get("bin_path"):
                config["postgresql"]["bin_path"] = detected["postgresql"]["bin_path"]
            if detected.get("postgresql", {}).get("data_path"):
                config["postgresql"]["data_path"] = detected["postgresql"]["data_path"]
            # Clean internal flags
            for key in list(detected.keys()):
                if key.startswith("_"):
                    del detected[key]
            save_config(config)
            console.print("  [green]Auto-detection complete. Config updated.[/green]")
        elif choice == "2":
            config["open_browser_on_launch"] = not config.get("open_browser_on_launch", True)
            save_config(config)
            val = "ON" if config["open_browser_on_launch"] else "OFF"
            console.print(f"  Browser-on-launch: [cyan]{val}[/cyan]")
        elif choice == "3":
            config = run_first_time_wizard()
        elif choice == "0":
            return config

    return config


def menu_services(config: dict, console: Console) -> None:
    """Individual service start/stop submenu."""
    while True:
        pg_up = check_postgresql(config)
        mem_up = check_memurai(config)
        arena_up = check_arena(config)

        def _dot(up: bool) -> str:
            return "[green]ONLINE[/green] " if up else "[red]OFFLINE[/red]"

        console.print()
        console.print("  [bold]Services[/bold]")
        console.print(f"  PostgreSQL: {_dot(pg_up)}   Memurai: {_dot(mem_up)}   Arena: {_dot(arena_up)}")
        console.print()
        console.print(f"  [cyan][1][/cyan] {'Stop' if pg_up else 'Start'} PostgreSQL")
        console.print(f"  [cyan][2][/cyan] {'Stop' if mem_up else 'Start'} Memurai")
        console.print(f"  [cyan][3][/cyan] {'Stop' if arena_up else 'Start'} Arena")
        console.print("  [cyan][0][/cyan] Back")

        choice = input("\n  Choice: ").strip()
        if choice == "1":
            if pg_up:
                stop_postgresql(config, console)
            else:
                start_postgresql(config, console)
        elif choice == "2":
            if mem_up:
                stop_memurai(config, console)
            else:
                start_memurai(config, console)
        elif choice == "3":
            if arena_up:
                stop_arena(config, console)
            else:
                start_arena(config, console)
        elif choice == "0":
            return


def menu_smoke_test(config: dict, console: Console) -> None:
    """Run the pre-launch smoke test."""
    console.print()
    console.print("[bold]  SMOKE TEST[/bold]")
    console.print("  " + "─" * 40)
    try:
        result = subprocess.run(
            [sys.executable, str(PROJECT_ROOT / "scripts" / "smoke_test.py")],
            cwd=str(PROJECT_ROOT),
            timeout=60,
        )
        if result.returncode == 0:
            console.print("  [green bold]GREEN — ready to launch[/green bold]")
        elif result.returncode == 2:
            console.print("  [yellow bold]YELLOW — non-critical warnings[/yellow bold]")
        else:
            console.print("  [red bold]RED — blocking issues found[/red bold]")
    except Exception as e:
        console.print(f"  [red]Smoke test failed: {e}[/red]")
    console.print()
    input("  Press Enter to return to menu...")


def menu_exit(config: dict, console: Console) -> bool:
    """Handle exit. Returns True if should exit, False to stay in menu."""
    status = get_system_status(config)
    running = [name for name, svc in status.items() if svc.get("status") == "running"]

    if running:
        console.print()
        console.print(f"  Services still running: [cyan]{', '.join(running)}[/cyan]")
        console.print("  [cyan][1][/cyan] Leave them running (exit CLI only)")
        console.print("  [cyan][2][/cyan] Shut everything down, then exit")
        console.print("  [cyan][3][/cyan] Cancel (go back to menu)")
        choice = input("\n  Choice: ").strip()
        if choice == "1":
            console.print("  [dim]CLI closed. Services still running.[/dim]")
            return True
        elif choice == "2":
            shutdown_all(config, console, scope="all")
            console.print("  [dim]All services stopped. Goodbye.[/dim]")
            return True
        else:
            return False
    else:
        console.print("\n  [dim]Goodbye.[/dim]")
        return True


def main():
    console = Console()

    print_banner(console)

    config = get_config()
    cleanup_stale_pids()

    while True:
        status = get_system_status(config)
        display_menu(console, status)

        choice = input("\n  Enter choice: ").strip()

        if choice == "1":
            menu_launch_all(config, console)
        elif choice == "2":
            menu_shutdown_all(config, console)
        elif choice == "3":
            menu_open_dashboard(config, console)
        elif choice == "4":
            menu_system_status(config, console)
        elif choice == "5":
            menu_backup_now(config, console)
        elif choice == "6":
            menu_view_logs(config, console)
        elif choice == "7":
            menu_clean_slate(config, console)
        elif choice == "8":
            config = menu_settings(config, console)
        elif choice == "9":
            menu_services(config, console)
        elif choice.upper() == "S":
            menu_smoke_test(config, console)
        elif choice == "0":
            if menu_exit(config, console):
                break
        else:
            console.print("  [yellow]Invalid choice. Try again.[/yellow]")


if __name__ == "__main__":
    main()
