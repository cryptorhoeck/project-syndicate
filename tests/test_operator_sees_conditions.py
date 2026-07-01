"""#5(b) — the operator must SEE an approved plan's entry conditions and know how to act.

The #5 trace found the operator was structurally blind to the conditions it was asked to
execute (context_assembler showed approved plans without Entry/Exit), and its mandate gave
no rule for mapping a non-price condition onto its real tools (market vs limit). "Drop the
fiction" (b) fixes both: surface the conditions, and tell the operator an approval IS the
timing decision — a price-ish condition -> limit order at that price; a non-price signal it
can't monitor (volume/VWAP/RSI) -> execute at market now.

The behavioral proof (operators actually fire on approval) is LLM behavior, confirmed at the
end-of-cleanup re-fly. What's unit-testable — proven here fail-before/pass-after — is that
the operator's assembled context now CONTAINS the conditions and the mapping guidance.
"""

from __future__ import annotations

from src.agents.context_assembler import ContextAssembler
from src.common.models import Agent, Plan


def _agent(name: str, atype: str) -> Agent:
    return Agent(
        name=name, type=atype, status="active", generation=1,
        capital_allocated=100.0, capital_current=100.0,
    )


def test_operator_sees_approved_plan_conditions_and_mapping(db_session_factory):
    with db_session_factory() as s:
        strat = _agent("Strat-5b", "strategist")
        op = _agent("Op-5b", "operator")
        s.add_all([strat, op])
        s.commit()
        s.refresh(strat)
        s.refresh(op)

        s.add(Plan(
            strategist_agent_id=strat.id, strategist_agent_name=strat.name,
            plan_name="Vol breakout", market="BTC/USDT", direction="long",
            entry_conditions="enter on a 1.5x volume spike above VWAP",
            exit_conditions="exit at +2% or on VWAP loss",
            position_size_pct=5.0, thesis="volume-led breakout", status="approved",
        ))
        s.commit()

        ctx = ContextAssembler(s).assemble(op)
        full = ctx.system_prompt + "\n" + ctx.user_prompt

        # 1. Blindness fixed: the operator now SEES the entry condition it must execute.
        assert "1.5x volume spike above VWAP" in full, "operator still blind to entry conditions"
        # 2. Coherence: the mandate tells it HOW to map a condition onto its real tools.
        assert "LIMIT order" in full and "MARKET" in full, "condition->order mapping missing"
        assert "approval IS the timing decision" in full


def test_no_approved_plans_section_without_an_approved_plan(db_session_factory):
    """Guard: the Entry/Exit lines are tied to approved plans, not spuriously emitted."""
    with db_session_factory() as s:
        op = _agent("Op-5b2", "operator")
        s.add(op)
        s.commit()
        s.refresh(op)

        ctx = ContextAssembler(s).assemble(op)
        full = ctx.system_prompt + "\n" + ctx.user_prompt
        assert "APPROVED PLANS (READY TO EXECUTE)" not in full
