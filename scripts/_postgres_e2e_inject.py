"""One-shot validation injector for Finding 4 (e2e against real Postgres).

Usage:
    .venv\\Scripts\\python.exe scripts\\_postgres_e2e_inject.py

This script runs ONCE against the dev Postgres after the
phase_10_wire_006 migration has been applied. It:
  1. Inserts a synthetic wire_raw_item (kraken_announcements,
     deterministic sev-5 + exchange_outage).
  2. Invokes the real HaikuDigester to produce a wire_events row.
  3. Asserts the row landed with regime_review_status='pending'.
  4. Builds a real GenesisAgent (collaborators that need live exchange
     data are stubbed) and runs run_cycle.
  5. Asserts the row was consumed (log emitted, attempt_count=1) and
     marked 'reviewed'.
  6. Cleans up the synthetic raw_item + event so the dev DB returns
     to its pre-injection counts.

Output is a structured report suitable for pasting into the commit.

NOT a test fixture — single-shot diagnostic script kept under scripts/
so it doesn't get picked up by pytest. Underscore prefix marks it
as such.
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import os
import re
import sys
from datetime import datetime, timezone

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _PROJECT_ROOT)

from unittest.mock import AsyncMock, MagicMock

from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker

from src.common.config import config
from src.genesis.genesis import GenesisAgent
from src.wire.digest.haiku_digester import HaikuCallResult, HaikuDigester
import src.wire.models  # noqa: F401
from src.wire.models import WireEvent, WireRawItem, WireSource


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _strip_ansi(s: str) -> str:
    return _ANSI_RE.sub("", s)


def _make_genesis_with_mocks(factory) -> GenesisAgent:
    g = GenesisAgent(
        db_session_factory=factory,
        exchange_service=None, agora_service=None,
        library_service=None, economy_service=None,
    )
    g.treasury.update_peak_treasury = AsyncMock(return_value=None)
    g.treasury.close_inherited_positions = AsyncMock(return_value=None)
    g.treasury.get_treasury_balance = AsyncMock(
        return_value={"total": 1000.0, "available": 800.0, "reserved": 200.0}
    )
    g.regime_detector.detect_regime = AsyncMock(
        return_value={"regime": "bull", "changed": False, "previous_regime": "bull"}
    )
    g._check_agent_health = MagicMock(
        return_value={"active": 0, "due_for_evaluation": [], "stale": []}
    )
    g._run_evaluations = AsyncMock(return_value={})
    g._make_spawn_decisions = AsyncMock(return_value={})
    g._check_reproduction = AsyncMock(return_value={})
    g._monitor_agora = AsyncMock(return_value={})
    g._check_hibernation_wake = AsyncMock(return_value=None)
    g._maybe_run_hourly_maintenance = AsyncMock(return_value=None)
    g._maybe_run_boot_sequence = AsyncMock(return_value=None)
    g.post_to_agora = AsyncMock(return_value=None)
    g.accountant.generate_leaderboard = AsyncMock(return_value=[])
    return g


async def main() -> int:
    engine = create_engine(config.database_url)
    factory = sessionmaker(bind=engine)

    print("=" * 78)
    print("Postgres e2e — Finding 4")
    print("=" * 78)
    print(f"  database_url: {config.database_url}")

    # PRE-state
    with engine.connect() as c:
        head = c.execute(text("SELECT version_num FROM alembic_version")).scalar()
        print(f"  alembic head: {head}")
        pre = c.execute(text(
            "SELECT regime_review_status, COUNT(*) FROM wire_events "
            "GROUP BY regime_review_status ORDER BY regime_review_status"
        )).fetchall()
        print(f"  pre-injection wire_events by status: {pre}")
        pre_total = c.execute(text("SELECT COUNT(*) FROM wire_events")).scalar()
        print(f"  pre-injection wire_events total:     {pre_total}")

    # INJECT a synthetic raw item that the digester will turn into a
    # sev-5 wire_event.
    with factory() as session:
        src = session.execute(
            select(WireSource).where(WireSource.name == "kraken_announcements")
        ).scalar_one()
        external_id = f"e2e-finding-4-{int(datetime.now(timezone.utc).timestamp())}"
        raw = WireRawItem(
            source_id=src.id,
            external_id=external_id,
            raw_payload={
                "payload": {"foo": "bar"},
                "haiku_brief": "E2E Finding 4 — synthetic sev-5 against real Postgres",
                "source_url": "https://example.com/finding-4",
                "deterministic_severity": 5,
                "deterministic_event_type": "exchange_outage",
                "deterministic_coin": None,
                "deterministic_direction": None,
                "deterministic_is_macro": None,
            },
            occurred_at=datetime.now(timezone.utc),
        )
        session.add(raw)
        session.commit()
        raw_id = raw.id
        print(f"  injected wire_raw_item id={raw_id} external_id={external_id!r}")

    # DIGEST — real HaikuDigester. Synthetic Haiku response (the
    # deterministic flags override severity/event_type so the response
    # only needs to satisfy the schema).
    def _fake_haiku(system_prompt, user_prompt):
        return HaikuCallResult(
            text=(
                '{"coin":"BTC","is_macro":false,"event_type":"exchange_outage",'
                '"severity":3,"direction":"bearish","summary":"Finding 4 e2e"}'
            ),
            cost_usd=0.0, input_tokens=0, output_tokens=0,
        )

    with factory() as session:
        digester = HaikuDigester(haiku_client=_fake_haiku, session=session)
        results = digester.digest_pending(limit=10)
        results_for_us = [r for r in results if r.raw_item_id == raw_id]
        if not results_for_us:
            print("  FAIL — digester did not process our raw_item")
            return 1
        evt_id = results_for_us[0].event_id
        print(f"  digester produced wire_event id={evt_id}")

    # PHASE 1 ASSERT — row should be 'pending' with attempt_count=0.
    with engine.connect() as c:
        row = c.execute(
            text(
                "SELECT id, severity, regime_review_status, attempt_count, last_error "
                "FROM wire_events WHERE id = :id"
            ),
            {"id": evt_id},
        ).first()
        if row is None:
            print(f"  FAIL — wire_event id={evt_id} not found")
            return 1
        print(f"  wire_event after digest: severity={row.severity} "
              f"status={row.regime_review_status} attempt_count={row.attempt_count} "
              f"last_error={row.last_error!r}")
        if row.regime_review_status != "pending":
            print(f"  FAIL — expected status='pending', got {row.regime_review_status!r}")
            return 1
        if row.attempt_count != 0:
            print(f"  FAIL — expected attempt_count=0, got {row.attempt_count}")
            return 1

    # CONSUME via real GenesisAgent.run_cycle. Capture stdout for the
    # consumption log line.
    captured = io.StringIO()
    genesis = _make_genesis_with_mocks(factory)
    with contextlib.redirect_stdout(captured):
        report = await genesis.run_cycle()
    cycle_stdout = _strip_ansi(captured.getvalue())
    consume_lines = [
        line for line in cycle_stdout.splitlines()
        if "genesis_consuming_regime_review" in line
        and f"event_id={evt_id}" in line
    ]
    print(f"  cycle_report.regime_reviews_consumed = "
          f"{report.get('regime_reviews_consumed')}")
    if consume_lines:
        print(f"  consume log line: {consume_lines[0]}")
    else:
        print(f"  FAIL — no consume log line for event_id={evt_id}")
        print(f"  cycle stdout:\n{cycle_stdout}")
        return 1

    # PHASE 2 ASSERT — row should be 'reviewed' with attempt_count=1.
    with engine.connect() as c:
        row = c.execute(
            text(
                "SELECT regime_review_status, attempt_count, last_error "
                "FROM wire_events WHERE id = :id"
            ),
            {"id": evt_id},
        ).first()
        print(f"  wire_event after run_cycle: status={row.regime_review_status} "
              f"attempt_count={row.attempt_count} last_error={row.last_error!r}")
        if row.regime_review_status != "reviewed":
            print(f"  FAIL — expected status='reviewed', got {row.regime_review_status!r}")
            return 1
        if row.attempt_count != 1:
            print(f"  FAIL — expected attempt_count=1, got {row.attempt_count}")
            return 1

    # POST-state count.
    with engine.connect() as c:
        post = c.execute(text(
            "SELECT regime_review_status, COUNT(*) FROM wire_events "
            "GROUP BY regime_review_status ORDER BY regime_review_status"
        )).fetchall()
        print(f"  post-validation wire_events by status: {post}")

    # CLEANUP — remove the synthetic event + raw_item so dev DB returns
    # to its pre-injection counts.
    with engine.connect() as c:
        with c.begin():
            # Treasury ledger references event_id; drop those first.
            c.execute(
                text("DELETE FROM wire_treasury_ledger WHERE related_event_id = :id"),
                {"id": evt_id},
            )
            c.execute(text("DELETE FROM wire_events WHERE id = :id"), {"id": evt_id})
            c.execute(text("DELETE FROM wire_raw_items WHERE id = :id"), {"id": raw_id})

    with engine.connect() as c:
        post_cleanup = c.execute(text(
            "SELECT regime_review_status, COUNT(*) FROM wire_events "
            "GROUP BY regime_review_status ORDER BY regime_review_status"
        )).fetchall()
        post_cleanup_total = c.execute(
            text("SELECT COUNT(*) FROM wire_events")
        ).scalar()
        print(f"  post-cleanup wire_events by status:    {post_cleanup}")
        print(f"  post-cleanup wire_events total:        {post_cleanup_total}")

    print()
    print("RESULT: GREEN — Postgres e2e validates subsystem H end-to-end")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
