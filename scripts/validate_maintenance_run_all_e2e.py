"""
Subsystem T-subset fix — MaintenanceService.run_all e2e validation.

Three phases against the dev Postgres:

  1. PRE-STATE: count stale opportunities, stale plans, terminated-
     agent Redis keys, and capture sample agents'
     `thinking_budget_used_today` values.
  2. INVOKE: call `genesis._maybe_run_hourly_maintenance()` once with a
     fresh GenesisAgent constructed against the dev Postgres.
  3. POST-STATE: same counts as Phase 1 with deltas.
     CRITICALLY assert `thinking_budget_used_today` is UNCHANGED —
     this proves run_all() does NOT trigger budget reset (Option B
     contract).

The script seeds throwaway stale rows so the deltas are visible
even on a clean dev DB, and cleans them up afterwards.

Usage:
    .venv\\Scripts\\python.exe scripts\\validate_maintenance_run_all_e2e.py
"""

from __future__ import annotations

__version__ = "1.0.0"

import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _PROJECT_ROOT)

import redis as redis_lib
from sqlalchemy import create_engine, select, text as sql_text
from sqlalchemy.orm import sessionmaker

from src.common.config import config
from src.common.models import Agent, Opportunity, Plan, SystemState


GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"
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


def _seed_synthetic(factory) -> dict:
    """Insert throwaway stale opportunities, stale plans, and a
    terminated agent + Redis key. Returns the seeded ids for cleanup."""
    now = datetime.now(timezone.utc)
    seeded = {"opp_ids": [], "plan_ids": [], "agent_id": None}

    # Need a scout/strategist agent to satisfy FK. Pick the first
    # non-Genesis active agent in the DB, or skip seeding if none.
    with factory() as session:
        scout = session.execute(
            select(Agent).where(Agent.type == "scout").limit(1)
        ).scalar_one_or_none()
        strategist = session.execute(
            select(Agent).where(Agent.type == "strategist").limit(1)
        ).scalar_one_or_none()

        if scout is None or strategist is None:
            # Fall back to the Genesis row at id=0 — it's not really
            # a scout/strategist, but the FK is satisfied for the
            # synthetic test rows.
            genesis_row = session.get(Agent, 0)
            scout = scout or genesis_row
            strategist = strategist or genesis_row

        # 5 stale opportunities (past expires_at).
        for i in range(5):
            opp = Opportunity(
                scout_agent_id=scout.id,
                scout_agent_name=scout.name,
                market="BTC/USDT", signal_type="volume_breakout",
                details=f"e2e-stale-{i}",
                status="new",
                expires_at=now - timedelta(hours=1),
            )
            session.add(opp)
            session.flush()
            seeded["opp_ids"].append(opp.id)

        # 3 stale plans (submitted >24h ago, no critic).
        for i in range(3):
            plan = Plan(
                strategist_agent_id=strategist.id,
                strategist_agent_name=strategist.name,
                plan_name=f"e2e-stale-plan-{i}", market="BTC/USDT",
                direction="long", entry_conditions="x", exit_conditions="y",
                thesis="e2e validation", status="submitted",
                submitted_at=now - timedelta(hours=25),
            )
            session.add(plan)
            session.flush()
            seeded["plan_ids"].append(plan.id)

        # 1 terminated agent for memory pruning.
        ts = int(datetime.now(timezone.utc).timestamp())
        terminated = Agent(
            name=f"E2E-Terminated-{ts}",
            type="operator", status="terminated",
            generation=1, capital_allocated=0.0, capital_current=0.0,
            cash_balance=0.0, total_equity=0.0,
        )
        session.add(terminated)
        session.flush()
        seeded["agent_id"] = terminated.id

        session.commit()

    return seeded


async def main() -> int:
    _banner("Maintenance run_all() — e2e validation", BOLD + GREEN)
    print(f"  database_url: {config.database_url}")
    print(f"  redis_url:    {config.redis_url}")

    engine = create_engine(config.database_url)
    factory = sessionmaker(bind=engine)

    # Sanity ping the DB.
    try:
        with engine.connect() as c:
            c.execute(sql_text("SELECT 1"))
        print(f"  {GREEN}Postgres reachable{RESET}")
    except Exception as exc:
        print(f"{RED}Postgres unreachable: {exc}{RESET}")
        return 2

    # Sanity ping Redis.
    r = redis_lib.Redis.from_url(config.redis_url, decode_responses=True)
    try:
        r.ping()
        print(f"  {GREEN}Memurai reachable{RESET}")
    except Exception as exc:
        print(f"{RED}Memurai unreachable: {exc}{RESET}")
        return 2

    # Seed synthetic stale rows + Redis key.
    seeded = _seed_synthetic(factory)
    terminated_id = seeded["agent_id"]
    redis_key = f"agent:{terminated_id}:recent_cycles"
    r.set(redis_key, "synthetic e2e memory entry")

    overall_ok = True

    try:
        # ---------------- PHASE 1: PRE-STATE ----------------
        _banner("PHASE 1 — PRE-STATE")
        with factory() as session:
            pre_stale_opps = session.execute(
                select(Opportunity).where(
                    Opportunity.id.in_(seeded["opp_ids"]),
                    Opportunity.status == "new",
                )
            ).scalars().all()
            pre_stale_plans = session.execute(
                select(Plan).where(
                    Plan.id.in_(seeded["plan_ids"]),
                    Plan.status == "submitted",
                )
            ).scalars().all()
            pre_redis_key_exists = bool(r.exists(redis_key))

            sample_agents = session.execute(
                select(Agent).where(
                    Agent.status.in_(["active", "initializing"]),
                ).limit(5)
            ).scalars().all()
            pre_budgets = {
                a.id: float(a.thinking_budget_used_today or 0)
                for a in sample_agents
            }

        print(f"  stale opportunities (synthetic, status=new): {len(pre_stale_opps)}")
        print(f"  stale plans         (synthetic, status=submitted): {len(pre_stale_plans)}")
        print(f"  terminated-agent Redis key exists:  {pre_redis_key_exists}")
        print(f"  sample agent budgets (id -> thinking_budget_used_today):")
        for aid, bud in sorted(pre_budgets.items()):
            print(f"      agent_id={aid}: {bud}")

        # ---------------- PHASE 2: INVOKE ----------------
        _banner("PHASE 2 — INVOKE genesis._maybe_run_hourly_maintenance()")
        from src.genesis.genesis import GenesisAgent

        g = GenesisAgent(
            db_session_factory=factory,
            exchange_service=None, agora_service=None,
            library_service=None, economy_service=None,
        )
        # Force the daily-gate closed by setting today as the last
        # reset date — this is the production-norm scenario (the
        # gate is closed for ~23 hours of every day).
        g._last_budget_reset_date = datetime.now(timezone.utc).date()

        await g._maybe_run_hourly_maintenance()
        print(f"  {GREEN}_maybe_run_hourly_maintenance returned{RESET}")

        # ---------------- PHASE 3: POST-STATE ----------------
        _banner("PHASE 3 — POST-STATE + DELTAS")
        with factory() as session:
            post_stale_opps = session.execute(
                select(Opportunity).where(
                    Opportunity.id.in_(seeded["opp_ids"]),
                    Opportunity.status == "new",
                )
            ).scalars().all()
            expired_opps = session.execute(
                select(Opportunity).where(
                    Opportunity.id.in_(seeded["opp_ids"]),
                    Opportunity.status == "expired",
                )
            ).scalars().all()
            post_stale_plans = session.execute(
                select(Plan).where(
                    Plan.id.in_(seeded["plan_ids"]),
                    Plan.status == "submitted",
                )
            ).scalars().all()
            cleaned_plans = session.execute(
                select(Plan).where(
                    Plan.id.in_(seeded["plan_ids"]),
                    Plan.status == "draft",
                )
            ).scalars().all()
            post_redis_key_exists = bool(r.exists(redis_key))

            sample_agents = session.execute(
                select(Agent).where(
                    Agent.id.in_(list(pre_budgets.keys())),
                )
            ).scalars().all()
            post_budgets = {
                a.id: float(a.thinking_budget_used_today or 0)
                for a in sample_agents
            }

        delta_stale_opps = len(post_stale_opps) - len(pre_stale_opps)
        delta_stale_plans = len(post_stale_plans) - len(pre_stale_plans)

        print(f"  stale opportunities (synthetic, status=new): "
              f"{len(post_stale_opps)} (delta {delta_stale_opps})")
        print(f"  expired (synthetic): {len(expired_opps)}")
        print(f"  stale plans         (synthetic, status=submitted): "
              f"{len(post_stale_plans)} (delta {delta_stale_plans})")
        print(f"  cleaned plans       (synthetic, status=draft): {len(cleaned_plans)}")
        print(f"  terminated-agent Redis key exists:  {post_redis_key_exists}")

        ok_a = _check(
            "all 5 synthetic stale opportunities flipped to 'expired'",
            len(expired_opps) == 5 and len(post_stale_opps) == 0,
            detail=f"expired={len(expired_opps)} still_new={len(post_stale_opps)}",
        )
        ok_b = _check(
            "all 3 synthetic stale plans flipped to 'draft'",
            len(cleaned_plans) == 3 and len(post_stale_plans) == 0,
            detail=f"cleaned={len(cleaned_plans)} still_submitted={len(post_stale_plans)}",
        )
        ok_c = _check(
            "terminated-agent Redis key was pruned",
            pre_redis_key_exists and not post_redis_key_exists,
            detail=f"pre={pre_redis_key_exists} post={post_redis_key_exists}",
        )

        # CRITICAL: budget cadence preserved.
        budgets_unchanged = post_budgets == pre_budgets
        ok_d = _check(
            "thinking_budget_used_today UNCHANGED (Option B contract)",
            budgets_unchanged,
            detail=(
                f"pre={pre_budgets!r}\n         post={post_budgets!r}"
                if not budgets_unchanged
                else "agents' daily budgets preserved"
            ),
        )

        overall_ok &= (ok_a and ok_b and ok_c and ok_d)

    finally:
        # CLEANUP — remove synthetic rows + Redis key.
        with engine.connect() as c:
            with c.begin():
                if seeded["opp_ids"]:
                    c.execute(
                        sql_text("DELETE FROM opportunities WHERE id = ANY(:ids)"),
                        {"ids": seeded["opp_ids"]},
                    )
                if seeded["plan_ids"]:
                    c.execute(
                        sql_text("DELETE FROM plans WHERE id = ANY(:ids)"),
                        {"ids": seeded["plan_ids"]},
                    )
                if seeded["agent_id"] is not None:
                    c.execute(
                        sql_text("DELETE FROM agents WHERE id = :aid"),
                        {"aid": seeded["agent_id"]},
                    )
        try:
            r.delete(redis_key)
        except Exception:
            pass

    _banner("RESULT")
    print(f"  Overall: {GREEN if overall_ok else RED}"
          f"{'GREEN — subsystem T-subset wired end-to-end' if overall_ok else 'RED — DO NOT MERGE'}"
          f"{RESET}")
    return 0 if overall_ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
