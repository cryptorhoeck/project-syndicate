"""
Genesis regime-review consumption — single-phase e2e validation.

Closes WIRING_AUDIT_REPORT.md subsystem H production-runtime check.
Postgres-as-queue has well-understood semantics, so this validation
is single-phase (unlike fix I's three-phase Memurai-down dance):
exercise the queue once end-to-end with the real production code
paths and verify Genesis consumed it.

The validation:
  1. Real digester writes a sev-5 wire_events row → 'pending'
  2. Real GenesisAgent constructor (the same `genesis_runner.main`
     uses) runs run_cycle (collaborators that need live exchange data
     are stubbed; the queue read/write is live)
  3. Verify: row marked 'reviewed', `genesis_consuming_regime_review`
     log emitted with the correct event_id

Captures all output for the commit message. Prints `GREEN` when the
production path holds end-to-end.

Usage:
    .venv\\Scripts\\python.exe scripts\\validate_regime_review_consumption_e2e.py
"""

from __future__ import annotations

__version__ = "1.0.0"

import asyncio
import contextlib
import io
import os
import re
import sys
from datetime import datetime, timezone

# Ensure project root is on sys.path
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _PROJECT_ROOT)

from unittest.mock import AsyncMock, MagicMock

import redis as redis_lib
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.common.config import config
from src.common.models import Agent, Base, SystemState
from src.genesis.genesis import GenesisAgent
from src.wire.digest.haiku_digester import HaikuCallResult, HaikuDigester
import src.wire.models  # noqa: F401 — register Wire tables on Base.metadata
from src.wire.models import WireEvent, WireRawItem, WireSource, WireSourceHealth


GREEN = "\033[32m"
RED = "\033[31m"
BOLD = "\033[1m"
RESET = "\033[0m"
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


def _banner(title: str, color: str = BOLD) -> None:
    print()
    print(f"{color}{'=' * 78}{RESET}")
    print(f"{color}  {title}{RESET}")
    print(f"{color}{'=' * 78}{RESET}")


def _check(label: str, passed: bool, detail: str = "") -> bool:
    icon = f"{GREEN}OK   {RESET}" if passed else f"{RED}FAIL {RESET}"
    line = f"  {icon}  {label}"
    if detail:
        line += f"\n         {detail}"
    print(line)
    return passed


def _seed_world(factory):
    """SystemState + Genesis agents row + the kraken_announcements
    Wire source needed to ingest a deterministic sev-5 raw item."""
    with factory() as session:
        session.add(SystemState(
            total_treasury=1000.0, peak_treasury=1000.0,
            current_regime="bull", active_agent_count=0, alert_status="green",
        ))
        session.add(Agent(
            id=0, name="Genesis", type="genesis", status="active",
            generation=0, capital_allocated=0.0, capital_current=0.0,
            strategy_summary="Immortal God Node",
        ))
        src = WireSource(
            name="kraken_announcements",
            display_name="Kraken Announcements",
            tier="A", fetch_interval_seconds=300,
            enabled=True, requires_api_key=False, api_key_env_var=None,
            base_url="https://blog.kraken.com/feed/",
            config_json={"severity_floor": 3},
        )
        session.add(src)
        session.flush()
        session.add(WireSourceHealth(source_id=src.id, status="unknown"))
        session.commit()


def _make_raw_item(session, *, source_name: str, external_id: str,
                   deterministic_severity: int, deterministic_event_type: str):
    src = session.execute(
        select(WireSource).where(WireSource.name == source_name)
    ).scalar_one()
    raw = WireRawItem(
        source_id=src.id, external_id=external_id,
        raw_payload={
            "payload": {"foo": "bar"},
            "haiku_brief": "E2E validation — synthetic sev-5",
            "source_url": "https://example.com/x",
            "deterministic_severity": deterministic_severity,
            "deterministic_event_type": deterministic_event_type,
            "deterministic_coin": None,
            "deterministic_direction": None,
            "deterministic_is_macro": None,
        },
        occurred_at=datetime.now(timezone.utc),
    )
    session.add(raw)
    session.commit()
    return raw


def _fake_haiku_client(text):
    def _client(system_prompt, user_prompt):
        return HaikuCallResult(
            text=text, cost_usd=0.0, input_tokens=0, output_tokens=0,
        )
    return _client


def _make_genesis_with_mocks(db_factory) -> GenesisAgent:
    """Same shape as the test helper — production constructor, with the
    collaborators that would otherwise need live exchange data stubbed."""
    genesis = GenesisAgent(
        db_session_factory=db_factory,
        exchange_service=None, agora_service=None,
        library_service=None, economy_service=None,
    )
    genesis.treasury.update_peak_treasury = AsyncMock(return_value=None)
    genesis.treasury.close_inherited_positions = AsyncMock(return_value=None)
    genesis.treasury.get_treasury_balance = AsyncMock(
        return_value={"total": 1000.0, "available": 800.0, "reserved": 200.0}
    )
    genesis.regime_detector.detect_regime = AsyncMock(
        return_value={"regime": "bull", "changed": False, "previous_regime": "bull"}
    )
    genesis._check_agent_health = MagicMock(
        return_value={"active": 0, "due_for_evaluation": [], "stale": []}
    )
    genesis._run_evaluations = AsyncMock(return_value={})
    genesis._make_spawn_decisions = AsyncMock(return_value={})
    genesis._check_reproduction = AsyncMock(return_value={})
    genesis._monitor_agora = AsyncMock(return_value={})
    genesis._check_hibernation_wake = AsyncMock(return_value=None)
    genesis._maybe_run_hourly_maintenance = AsyncMock(return_value=None)
    genesis._maybe_run_boot_sequence = AsyncMock(return_value=None)
    genesis.post_to_agora = AsyncMock(return_value=None)
    genesis.accountant.generate_leaderboard = AsyncMock(return_value=[])
    return genesis


async def main() -> int:
    _banner("Genesis regime-review consumption — E2E validation", BOLD + GREEN)
    print(f"  python    : {sys.executable}")
    print(f"  redis_url : {config.redis_url}")

    # Memurai gate (Genesis constructor pings it).
    try:
        sanity = redis_lib.Redis.from_url(config.redis_url, decode_responses=True)
        sanity.ping()
        print(f"  {GREEN}Memurai reachable{RESET}")
    except Exception as exc:
        print(f"{RED}Memurai unreachable at {config.redis_url}: {exc}{RESET}")
        return 2

    # 1. Build a real SQLite world + factory.
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    _seed_world(factory)

    _banner("PHASE — Producer (HaikuDigester) writes pending row")
    with factory() as session:
        _make_raw_item(
            session, source_name="kraken_announcements",
            external_id="e2e-prod-sev5",
            deterministic_severity=5, deterministic_event_type="exchange_outage",
        )
        haiku = _fake_haiku_client(
            '{"coin":"BTC","is_macro":false,"event_type":"exchange_outage",'
            '"severity":3,"direction":"bearish","summary":"e2e validation"}'
        )
        digester = HaikuDigester(haiku_client=haiku, session=session)
        digester.digest_pending()
        evt = session.execute(select(WireEvent)).scalar_one()
        produced_event_id = evt.id
        produced_status = evt.regime_review_status

    p1 = _check(
        "real HaikuDigester wrote sev-5 row with regime_review_status='pending'",
        produced_status == "pending",
        detail=f"event_id={produced_event_id} status={produced_status!r}",
    )
    if not p1:
        print(f"\n{RED}Producer wiring broken — aborting validation.{RESET}")
        return 1

    _banner("PHASE — Consumer (GenesisAgent.run_cycle) consumes + marks reviewed")
    captured = io.StringIO()
    genesis = _make_genesis_with_mocks(factory)
    with contextlib.redirect_stdout(captured):
        report = await genesis.run_cycle()
    cycle_stdout = _strip_ansi(captured.getvalue())

    consume_lines = [
        line for line in cycle_stdout.splitlines()
        if "genesis_consuming_regime_review" in line
    ]
    log_match = any(
        f"event_id={produced_event_id}" in line for line in consume_lines
    )

    with factory() as session:
        evt = session.get(WireEvent, produced_event_id)
        post_status = evt.regime_review_status

    p2 = _check(
        "Genesis emitted genesis_consuming_regime_review log with correct event_id",
        log_match,
        detail=f"matching consume lines: {consume_lines!r}",
    )
    p3 = _check(
        "wire_events row marked 'reviewed' after run_cycle",
        post_status == "reviewed",
        detail=f"status={post_status!r}",
    )
    p4 = _check(
        "run_cycle reported regime_reviews_consumed=1 in cycle_report",
        report.get("regime_reviews_consumed") == 1,
        detail=f"cycle_report={report!r}",
    )

    if cycle_stdout:
        print()
        print(f"{BOLD}--- Genesis stdout (ANSI-stripped excerpt) ---{RESET}")
        for line in cycle_stdout.splitlines():
            if any(k in line for k in (
                "genesis_consuming_regime_review",
                "genesis_initialized",
                "genesis_cycle",
                "regime_review",
            )):
                print(f"  {line}")

    _banner("RESULT")
    overall = p1 and p2 and p3 and p4
    print(f"  Producer   (digester writes 'pending')          : "
          f"{GREEN if p1 else RED}{'PASS' if p1 else 'FAIL'}{RESET}")
    print(f"  Consume    (Genesis logs event_id)              : "
          f"{GREEN if p2 else RED}{'PASS' if p2 else 'FAIL'}{RESET}")
    print(f"  Mark       (row -> 'reviewed')                  : "
          f"{GREEN if p3 else RED}{'PASS' if p3 else 'FAIL'}{RESET}")
    print(f"  Report     (cycle_report.regime_reviews_consumed): "
          f"{GREEN if p4 else RED}{'PASS' if p4 else 'FAIL'}{RESET}")
    print()
    print(f"  Overall: {GREEN if overall else RED}"
          f"{'GREEN — subsystem H wired end-to-end' if overall else 'RED — DO NOT MERGE'}"
          f"{RESET}")
    return 0 if overall else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
