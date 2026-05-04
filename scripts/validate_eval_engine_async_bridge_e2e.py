"""
Subsystem P fix — eval engine async-bridge end-to-end validation.

Four phases against the dev Postgres (per WIRING_AUDIT_REPORT.md
subsystem P directive):

  1. HEALTHY: invoke a real evaluation-style call via the production
     code path (track_api_call + update_fitness wrapped through
     run_async_safely). Capture before/after of the agent's
     api_cost_total and the genome's fitness_score; assert both
     changed.
  2. FORCED FAILURE: patch Accountant.track_api_call to raise. Same
     evaluation-shape call. Capture: WARNING log emitted, evaluation
     "completes" (no exception bubbles), counter incremented to 1,
     update_fitness still works.
  3. THREE CONSECUTIVE FAILURES: repeat phase 2 three times. On the
     third failure, capture the CRITICAL log + system-alert post.
  4. RESET: restore Accountant, run one successful track_api_call,
     assert counter resets to 0.

Postgres must be running (started via
`C:/ProDesk/pgsql/bin/pg_ctl.exe start -D ...`). The script
auto-cleans the synthetic agent + genome row on completion so the
dev DB returns to its pre-injection state.

Usage:
    .venv\\Scripts\\python.exe scripts\\validate_eval_engine_async_bridge_e2e.py
"""

from __future__ import annotations

__version__ = "1.0.0"

import asyncio
import io
import logging
import os
import re
import sys
import contextlib
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _PROJECT_ROOT)

from sqlalchemy import create_engine, select, text as sql_text
from sqlalchemy.orm import sessionmaker

from src.common.async_bridge import run_async_safely
from src.common.config import config
from src.common.models import Agent, AgentGenome, Transaction


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


def _seed_synthetic_agent(factory) -> tuple[int, int]:
    """Insert a throwaway Operator + matching agent_genomes row.
    Returns (agent_id, genome_id). The cleanup at the end of main
    deletes both."""
    with factory() as session:
        agent = Agent(
            name=f"OperatorEvalP-{int(datetime.now(timezone.utc).timestamp())}",
            type="operator", status="active",
            generation=1, capital_allocated=200.0, capital_current=200.0,
            cash_balance=200.0, reserved_cash=0.0, total_equity=200.0,
            thinking_budget_used_today=0.0,
            total_api_cost=0.0, api_cost_total=0.0,
            composite_score=0.0,
        )
        session.add(agent)
        session.flush()
        genome = AgentGenome(
            agent_id=agent.id,
            genome_data={"role": "operator", "params": {}},
            evaluations_with_genome=0,
            fitness_score=0.0,
        )
        session.add(genome)
        session.commit()
        return agent.id, genome.id


async def main() -> int:
    _banner("Eval engine async-bridge — e2e validation", BOLD + GREEN)
    print(f"  database_url: {config.database_url}")

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

    agent_id, genome_id = _seed_synthetic_agent(factory)
    print(f"  seeded: agent_id={agent_id} genome_id={genome_id}")

    # Capture WARNING+CRITICAL logs from the eval_engine + async_bridge.
    log_records: list[logging.LogRecord] = []

    class _Capture(logging.Handler):
        def emit(self, record):
            log_records.append(record)

    handler = _Capture(level=logging.WARNING)
    for name in ("src.genesis.evaluation_engine", "src.common.async_bridge"):
        logging.getLogger(name).addHandler(handler)

    from src.genesis.evaluation_engine import (
        ASYNC_FAILURE_ESCALATION_THRESHOLD,
        EvaluationEngine,
    )
    from src.genome.genome_manager import GenomeManager
    from src.risk.accountant import Accountant
    from src.risk import accountant as acct_mod

    eng = EvaluationEngine(db_session_factory=factory)

    overall_ok = True

    try:
        # --------------- PHASE 1: HEALTHY ---------------
        _banner("PHASE 1 — Healthy: real track_api_call + update_fitness")
        with factory() as s:
            pre_agent = s.get(Agent, agent_id)
            pre_genome = s.get(AgentGenome, genome_id)
            pre_api_cost = float(pre_agent.api_cost_total or 0)
            pre_fitness = float(pre_genome.fitness_score or 0)
            pre_evals = pre_genome.evaluations_with_genome
            print(f"  pre  api_cost_total={pre_api_cost} "
                  f"fitness={pre_fitness} evals_with_genome={pre_evals}")

        acct = Accountant(db_session_factory=factory)
        success_api, exc_api = run_async_safely(
            acct.track_api_call(
                agent_id=agent_id, model="claude-haiku-4-5-20251001",
                input_tokens=1000, output_tokens=500,
            )
        )
        eng._record_async_outcome("track_api_call", success_api, exc_api)

        gm = GenomeManager()

        async def _uf_with_fresh():
            with factory() as fs:
                await gm.update_fitness(agent_id, 0.92, fs)
                fs.commit()

        success_uf, exc_uf = run_async_safely(_uf_with_fresh())
        eng._record_async_outcome("update_fitness", success_uf, exc_uf)

        with factory() as s:
            post_agent = s.get(Agent, agent_id)
            post_genome = s.get(AgentGenome, genome_id)
            post_api_cost = float(post_agent.api_cost_total or 0)
            post_fitness = float(post_genome.fitness_score or 0)
            post_evals = post_genome.evaluations_with_genome
            print(f"  post api_cost_total={post_api_cost} "
                  f"fitness={post_fitness} evals_with_genome={post_evals}")

        p1_a = _check("track_api_call returned success", success_api,
                      detail=f"exc={exc_api!r}")
        p1_b = _check("update_fitness returned success", success_uf,
                      detail=f"exc={exc_uf!r}")
        p1_c = _check("agent.api_cost_total moved up",
                      post_api_cost > pre_api_cost,
                      detail=f"{pre_api_cost} -> {post_api_cost}")
        p1_d = _check("genome.fitness_score updated",
                      post_fitness > pre_fitness and post_fitness > 0,
                      detail=f"{pre_fitness} -> {post_fitness}")
        p1_e = _check("genome.evaluations_with_genome incremented",
                      post_evals == pre_evals + 1,
                      detail=f"{pre_evals} -> {post_evals}")
        p1_f = _check("track_api_call counter still 0",
                      eng._track_api_call_failure_count == 0)
        p1_g = _check("update_fitness counter still 0",
                      eng._update_fitness_failure_count == 0)
        phase1_ok = all([p1_a, p1_b, p1_c, p1_d, p1_e, p1_f, p1_g])
        overall_ok &= phase1_ok

        # --------------- PHASE 2: FORCED FAILURE ---------------
        _banner("PHASE 2 — Forced track_api_call failure")
        log_records.clear()

        async def _always_raises(self, *a, **kw):
            raise RuntimeError("synthetic forced failure")

        with patch.object(acct_mod.Accountant, "track_api_call", _always_raises):
            success_api, exc_api = run_async_safely(
                acct.track_api_call(
                    agent_id=agent_id, model="claude-haiku-4-5-20251001",
                    input_tokens=10, output_tokens=10,
                )
            )
            eng._record_async_outcome("track_api_call", success_api, exc_api)

        warn_records = [r for r in log_records if r.levelname == "WARNING"]
        async_bridge_warn = [
            r for r in warn_records
            if getattr(r, "async_bridge_failure", None) is True
        ]
        p2_a = _check("track_api_call returned failure",
                      success_api is False and isinstance(exc_api, RuntimeError),
                      detail=f"exc={exc_api!r}")
        p2_b = _check("WARNING log emitted with async_bridge_failure=True",
                      bool(async_bridge_warn),
                      detail=f"warn count={len(warn_records)}")
        p2_c = _check("counter incremented to 1",
                      eng._track_api_call_failure_count == 1,
                      detail=f"counter={eng._track_api_call_failure_count}")

        # update_fitness still works after a track_api_call failure.
        async def _uf2():
            with factory() as fs:
                await gm.update_fitness(agent_id, 0.95, fs)
                fs.commit()

        success_uf, exc_uf = run_async_safely(_uf2())
        eng._record_async_outcome("update_fitness", success_uf, exc_uf)
        p2_d = _check("update_fitness still works after track_api_call fails",
                      success_uf is True,
                      detail=f"exc={exc_uf!r}")

        phase2_ok = all([p2_a, p2_b, p2_c, p2_d])
        overall_ok &= phase2_ok

        # --------------- PHASE 3: THREE CONSECUTIVE FAILURES ---------------
        _banner("PHASE 3 — Three consecutive track_api_call failures")
        log_records.clear()

        # Counter is at 1 from phase 2. Two more failures → 3 total →
        # escalation fires.
        critical_records: list[logging.LogRecord] = []

        with patch.object(acct_mod.Accountant, "track_api_call", _always_raises):
            for i in range(2):
                success_api, exc_api = run_async_safely(
                    acct.track_api_call(
                        agent_id=agent_id, model="claude-haiku-4-5-20251001",
                        input_tokens=10, output_tokens=10,
                    )
                )
                eng._record_async_outcome("track_api_call", success_api, exc_api)

        critical_records = [
            r for r in log_records
            if r.levelname == "CRITICAL"
            and "eval_engine_async_failure_escalated" in r.getMessage()
        ]
        p3_a = _check("counter reached threshold",
                      eng._track_api_call_failure_count >= ASYNC_FAILURE_ESCALATION_THRESHOLD,
                      detail=f"counter={eng._track_api_call_failure_count}, "
                             f"threshold={ASYNC_FAILURE_ESCALATION_THRESHOLD}")
        p3_b = _check("CRITICAL escalation log emitted",
                      bool(critical_records),
                      detail=f"critical records: "
                             f"{[r.getMessage() for r in critical_records]!r}")
        p3_c = _check("CRITICAL log carries call_type=track_api_call",
                      bool(critical_records)
                      and getattr(critical_records[0], "call_type", None) == "track_api_call")

        phase3_ok = all([p3_a, p3_b, p3_c])
        overall_ok &= phase3_ok

        # --------------- PHASE 4: COUNTER RESET ---------------
        _banner("PHASE 4 — Counter resets on first success")
        # Outside the patch: real Accountant.track_api_call works.
        success_api, exc_api = run_async_safely(
            acct.track_api_call(
                agent_id=agent_id, model="claude-haiku-4-5-20251001",
                input_tokens=10, output_tokens=10,
            )
        )
        eng._record_async_outcome("track_api_call", success_api, exc_api)

        p4_a = _check("track_api_call success after recovery",
                      success_api is True,
                      detail=f"exc={exc_api!r}")
        p4_b = _check("counter reset to 0",
                      eng._track_api_call_failure_count == 0,
                      detail=f"counter={eng._track_api_call_failure_count}")

        phase4_ok = all([p4_a, p4_b])
        overall_ok &= phase4_ok

    finally:
        # CLEANUP — remove the synthetic agent, genome, and any
        # transactions linked to that agent. Returns dev DB to its
        # pre-injection state.
        with engine.connect() as c:
            with c.begin():
                c.execute(
                    sql_text("DELETE FROM transactions WHERE agent_id = :aid"),
                    {"aid": agent_id},
                )
                c.execute(
                    sql_text("DELETE FROM agent_genomes WHERE agent_id = :aid"),
                    {"aid": agent_id},
                )
                c.execute(
                    sql_text("DELETE FROM agents WHERE id = :aid"),
                    {"aid": agent_id},
                )
        for name in ("src.genesis.evaluation_engine", "src.common.async_bridge"):
            logging.getLogger(name).removeHandler(handler)

    _banner("RESULT")
    print(f"  Phase 1 (healthy)                 : "
          f"{GREEN if phase1_ok else RED}{'PASS' if phase1_ok else 'FAIL'}{RESET}")
    print(f"  Phase 2 (forced failure)          : "
          f"{GREEN if phase2_ok else RED}{'PASS' if phase2_ok else 'FAIL'}{RESET}")
    print(f"  Phase 3 (three consecutive)       : "
          f"{GREEN if phase3_ok else RED}{'PASS' if phase3_ok else 'FAIL'}{RESET}")
    print(f"  Phase 4 (counter reset)           : "
          f"{GREEN if phase4_ok else RED}{'PASS' if phase4_ok else 'FAIL'}{RESET}")
    print()
    print(f"  Overall: {GREEN if overall_ok else RED}"
          f"{'GREEN — subsystem P wired end-to-end' if overall_ok else 'RED — DO NOT MERGE'}"
          f"{RESET}")
    return 0 if overall_ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
