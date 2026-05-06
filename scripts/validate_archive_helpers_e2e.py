"""
Subsystems F + G — Strategist/Critic Archive helpers e2e validation.

Five phases against the dev Postgres:

  1. PRE-STATE: count archive_query_results rows by status; print
     the first available Strategist + Critic in the DB (or fail
     loudly if absent — empty DB is a real signal, NOT a thing
     to auto-create).
  2. STRATEGIST CONTEXT ASSEMBLY: invoke ContextAssembler for the
     real Strategist; verify the Wire Archive prefetch slice
     appears in priority context.
  3. STRATEGIST DEEP-DIVE: emit a `query_archive` action via
     ActionExecutor; verify a pending row is written and the
     helper charged.
  4. NEXT-CYCLE DELIVERY: invoke ContextAssembler again for the
     same Strategist; verify the pending row is now rendered AND
     marked 'delivered'.
  5. CRITIC 3-FREE FLOW: invoke 3 query_archive actions for a
     Critic in succession; verify all 3 free; invoke a 4th and
     verify the charge fires.

The script seeds a few synthetic WireEvents and one synthetic
query row so the deltas are visible against any pre-existing dev
DB state. Cleanup at the end removes everything synthetic so the
dev DB returns to its pre-injection counts (mirrors the regime-
review e2e cleanup pattern).

Usage:
    .venv\\Scripts\\python.exe scripts\\validate_archive_helpers_e2e.py
"""

from __future__ import annotations

__version__ = "1.0.0"

import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _PROJECT_ROOT)

from sqlalchemy import create_engine, select, text as sql_text
from sqlalchemy.orm import sessionmaker

from src.common.config import config
from src.common.models import Agent


GREEN = "\033[32m"
RED = "\033[31m"
BOLD = "\033[1m"
RESET = "\033[0m"


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


def _seed_wire_events(session, *, fixed_now: datetime) -> list[int]:
    """Insert a few synthetic severity-3+ events covering BTC/ETH/macro
    so the prefetch slice has something to surface. Returns the
    inserted event ids for cleanup."""
    from src.wire.models import WireEvent

    ids: list[int] = []
    rows = [
        ("e2e-archive-BTC-1", "BTC", 4, "funding_extreme", 30),
        ("e2e-archive-ETH-1", "ETH", 4, "tvl_drop", 60),
        ("e2e-archive-MACRO-1", None, 3, "macro_calendar", 90),
    ]
    for canonical, coin, sev, etype, mins_ago in rows:
        ev = WireEvent(
            canonical_hash=canonical,
            coin=coin,
            event_type=etype,
            severity=sev,
            summary=f"e2e validation event for {coin or 'macro'}",
            occurred_at=fixed_now - timedelta(minutes=mins_ago),
            digested_at=fixed_now - timedelta(minutes=mins_ago),
            published_to_ticker=True,
        )
        session.add(ev)
        session.flush()
        ids.append(ev.id)
    session.commit()
    return ids


def _find_or_seed_role(session, role: str, ts: int) -> tuple[Agent, bool]:
    """Find the first active agent of the given role; if none exists,
    seed a synthetic one with an `e2e-archive-` name prefix and
    return it alongside a `seeded` bool so the caller can clean up.

    Unlike the maintenance e2e (which can validate against any
    agent), this validation specifically requires Strategist + Critic
    roles. A dev DB without those agents is a known starting state,
    not a misconfig — auto-seeding is consistent with the synthetic
    WireEvents seeded below.
    """
    a = session.execute(
        select(Agent).where(Agent.type == role, Agent.status == "active").limit(1)
    ).scalar_one_or_none()
    if a is not None:
        return a, False
    seeded = Agent(
        name=f"e2e-archive-{role}-{ts}",
        type=role, status="active", generation=1,
        capital_allocated=200.0, capital_current=200.0,
        cash_balance=200.0, total_equity=200.0,
        watched_markets=["BTC", "ETH"],
        thinking_budget_daily=0.5,
    )
    session.add(seeded)
    session.commit()
    return seeded, True


async def main() -> int:
    _banner("Archive helpers (subsystems F + G) — e2e validation",
            BOLD + GREEN)
    print(f"  database_url: {config.database_url}")

    engine = create_engine(config.database_url)
    factory = sessionmaker(bind=engine)

    try:
        with engine.connect() as c:
            c.execute(sql_text("SELECT 1"))
        print(f"  {GREEN}Postgres reachable{RESET}")
    except Exception as exc:
        print(f"{RED}Postgres unreachable: {exc}{RESET}")
        return 2

    overall_ok = True
    seeded_event_ids: list[int] = []
    synthetic_query_ids: list[int] = []
    seeded_agent_ids: list[int] = []

    try:
        from src.agents.action_executor import ActionExecutor
        from src.agents.context_assembler import ContextAssembler
        from src.wire.integration.agent_context import (
            build_critic_archive_helper,
            build_strategist_archive_helper,
        )
        from src.wire.models import (
            ArchiveQueryResult as ArchiveQueryResultRow,
        )

        # ---------------- PHASE 1: PRE-STATE ----------------
        _banner("PHASE 1 — PRE-STATE")
        with factory() as session:
            pre_counts = {
                row.status: row.cnt for row in session.execute(sql_text(
                    "SELECT status, COUNT(*) AS cnt FROM archive_query_results "
                    "GROUP BY status ORDER BY status"
                )).all()
            }
            ts = int(datetime.now(timezone.utc).timestamp())
            strategist, s_seeded = _find_or_seed_role(session, "strategist", ts)
            critic, c_seeded = _find_or_seed_role(session, "critic", ts)
            strategist_id = strategist.id
            critic_id = critic.id
            if s_seeded:
                seeded_agent_ids.append(strategist_id)
            if c_seeded:
                seeded_agent_ids.append(critic_id)
            print(f"  archive_query_results by status: {pre_counts or '(empty)'}")
            print(f"  Strategist: id={strategist_id} name={strategist.name!r} "
                  f"watched_markets={strategist.watched_markets!r} "
                  f"{'(seeded)' if s_seeded else '(existing)'}")
            print(f"  Critic:     id={critic_id} name={critic.name!r} "
                  f"watched_markets={critic.watched_markets!r} "
                  f"{'(seeded)' if c_seeded else '(existing)'}")

            fixed_now = datetime.now(timezone.utc)
            seeded_event_ids = _seed_wire_events(session, fixed_now=fixed_now)
            print(f"  seeded {len(seeded_event_ids)} synthetic WireEvents "
                  f"(ids {seeded_event_ids})")

        # ---------------- PHASE 2: STRATEGIST CONTEXT ASSEMBLY ----
        _banner("PHASE 2 — Strategist context assembly (prefetch slice)")
        with factory() as session:
            assembler = ContextAssembler(session, token_budget=3000)
            agent = session.get(Agent, strategist_id)
            text = assembler._build_priority_context(agent, token_budget=3000)
            present = "RECENT WIRE EVENTS (last 24h, severity 3+)" in text
            ok_a = _check(
                "Wire Archive prefetch slice present in priority context",
                present,
                detail=(
                    "(found)" if present
                    else f"missing — head:\n{text[:600]}"
                ),
            )
            overall_ok &= ok_a

        # ---------------- PHASE 3: STRATEGIST DEEP-DIVE ----------
        _banner("PHASE 3 — Strategist query_archive action -> pending row")
        with factory() as session:
            agent = session.get(Agent, strategist_id)
            helper = build_strategist_archive_helper(
                session, agent_id=int(agent.id),
            )
            executor = ActionExecutor(session)
            executor.archive_helper = helper

            parsed = {
                "action": {
                    "type": "query_archive",
                    "params": {
                        "query": "e2e validation: BTC funding rate week",
                        "lookback_hours": 168,
                        "max_results": 10,
                    },
                }
            }
            result = await executor.execute(agent, parsed)
            ok_b = _check(
                "ActionExecutor handled query_archive successfully",
                result.success,
                detail=(
                    f"cost={result.cost} details={result.details!r}"
                ),
            )
            ok_c = _check(
                "Strategist query was charged (cost > 0)",
                result.cost > 0,
                detail=f"cost={result.cost}",
            )

            row = session.execute(
                select(ArchiveQueryResultRow).where(
                    ArchiveQueryResultRow.requesting_agent_id == strategist_id,
                    ArchiveQueryResultRow.query_text.like("e2e validation:%"),
                )
            ).scalar_one()
            synthetic_query_ids.append(row.id)
            ok_d = _check(
                "archive_query_results row written with status='pending'",
                row.status == "pending",
                detail=f"row_id={row.id} status={row.status!r}",
            )
            overall_ok &= (ok_b and ok_c and ok_d)
            pending_row_id = row.id

        # ---------------- PHASE 4: NEXT-CYCLE DELIVERY -----------
        _banner("PHASE 4 — Next-cycle ContextAssembler consumes pending row")
        with factory() as session:
            agent = session.get(Agent, strategist_id)
            assembler = ContextAssembler(session, token_budget=3000)
            text = assembler._build_priority_context(agent, token_budget=3000)
            ok_e = _check(
                "Pending row rendered into priority context",
                "e2e validation: BTC funding rate week" in text,
                detail=("(found)" if "e2e validation: BTC funding rate week" in text
                        else f"missing — head:\n{text[:600]}"),
            )

            session.expire_all()
            after = session.get(ArchiveQueryResultRow, pending_row_id)
            ok_f = _check(
                "Row marked 'delivered' after consumption",
                after.status == "delivered" and after.delivered_at is not None,
                detail=f"status={after.status!r} delivered_at={after.delivered_at!r}",
            )
            overall_ok &= (ok_e and ok_f)

        # ---------------- PHASE 5: CRITIC 3-FREE FLOW ----------
        _banner("PHASE 5 — Critic 3 free + 1 charged")
        critic_costs: list[float] = []
        with factory() as session:
            critic = session.get(Agent, critic_id)
            helper = build_critic_archive_helper(
                session, agent_id=int(critic.id), free_budget=3,
            )
            executor = ActionExecutor(session)
            executor.archive_helper = helper

            for i in range(4):
                parsed = {
                    "action": {
                        "type": "query_archive",
                        "params": {
                            "query": f"e2e validation: critic {i+1}",
                            "lookback_hours": 24,
                            "max_results": 5,
                        },
                    }
                }
                result = await executor.execute(critic, parsed)
                critic_costs.append(result.cost)
                row = session.execute(
                    select(ArchiveQueryResultRow).where(
                        ArchiveQueryResultRow.query_text == parsed["action"]["params"]["query"]
                    )
                ).scalar_one()
                synthetic_query_ids.append(row.id)

            ok_g = _check(
                "Critic queries 1-3 free, query 4 charged",
                critic_costs[0] == 0
                and critic_costs[1] == 0
                and critic_costs[2] == 0
                and critic_costs[3] > 0,
                detail=f"costs={critic_costs!r}",
            )
            overall_ok &= ok_g

    finally:
        # CLEANUP — remove synthetic rows. Order matters: queries
        # FK-reference agents, query-log rows reference agents, so
        # synthetic agents go LAST.
        #
        # Critic iteration 2 Finding 5: archive_query_results rows
        # are deleted by `requesting_agent_id` reference, NOT by the
        # tracked `synthetic_query_ids` list. Phase 2's
        # ContextAssembler invocation can land deeper rows the test
        # didn't explicitly track (e.g., a future ContextAssembler
        # write); deleting by agent FK catches all orphaned rows
        # whose agent is about to be deleted. Two passes:
        #   (1) seeded_query_ids — for orphans whose agent is real
        #       (existing-Critic case), delete by tracked id.
        #   (2) synthetic-agent FKs — for synthetic agents, delete
        #       all rows referencing them, including any not in the
        #       tracked list.
        if seeded_event_ids or synthetic_query_ids or seeded_agent_ids:
            with engine.connect() as c:
                with c.begin():
                    if synthetic_query_ids:
                        c.execute(
                            sql_text("DELETE FROM archive_query_results "
                                     "WHERE id = ANY(:ids)"),
                            {"ids": synthetic_query_ids},
                        )
                    if seeded_event_ids:
                        c.execute(
                            sql_text("DELETE FROM wire_events "
                                     "WHERE id = ANY(:ids)"),
                            {"ids": seeded_event_ids},
                        )
                    if seeded_agent_ids:
                        # Catch any rows the test didn't explicitly
                        # track but whose agent is about to be
                        # deleted — prevents FK orphans on subsequent
                        # runs (Critic iteration 2 Finding 5).
                        c.execute(
                            sql_text(
                                "DELETE FROM archive_query_results "
                                "WHERE requesting_agent_id = ANY(:ids)"
                            ),
                            {"ids": seeded_agent_ids},
                        )
                        # wire_query_log rows reference agents — must
                        # delete those first or FK constraint fires.
                        c.execute(
                            sql_text("DELETE FROM wire_query_log "
                                     "WHERE agent_id = ANY(:ids)"),
                            {"ids": seeded_agent_ids},
                        )
                        c.execute(
                            sql_text("DELETE FROM agents "
                                     "WHERE id = ANY(:ids)"),
                            {"ids": seeded_agent_ids},
                        )

    _banner("RESULT")
    print(f"  Overall: {GREEN if overall_ok else RED}"
          f"{'GREEN — subsystems F+G wired end-to-end' if overall_ok else 'RED — DO NOT MERGE'}"
          f"{RESET}")
    return 0 if overall_ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
