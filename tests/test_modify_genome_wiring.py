"""modify_genome action — real-backend wiring, honest feedback, per-period cap.

Before this fix the action routed to ``_handle_broadcast``: it posted a chat
message and returned FAKE success while the genome never changed — corrupting
agents' self-models. These tests lock in that it now:

  1. routes to the REAL GenomeManager backend (not broadcast),
  2. actually PERSISTS a valid change — verified by reloading in a FRESH session
     (the old broadcast path AND an untracked-JSON write would both fail this),
  3. reports failures HONESTLY with the genome left UNCHANGED (no fake success),
  4. enforces the promised cap of 2 modifications per evaluation period.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.agents.action_executor import ActionExecutor
from src.common.models import Agent, AgentGenome, Base
from src.genome.genome_manager import GenomeManager
from src.genome.genome_schema import GENOME_BOUNDS

PATH = "signal_generation.rsi_oversold"
LOW, HIGH = GENOME_BOUNDS[PATH]
OUT_OF_RANGE = HIGH + 100  # clearly above the upper bound


@pytest.fixture
def db_factory():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


async def _seed_agent_with_genome(db_factory, evaluation_count: int = 0) -> int:
    with db_factory() as s:
        a = Agent(
            name="JJ-Scout", type="scout", status="active", generation=1,
            capital_allocated=100.0, capital_current=100.0,
            thinking_budget_daily=0.5, thinking_budget_used_today=0.0,
            evaluation_count=evaluation_count,
        )
        s.add(a)
        s.commit()
        await GenomeManager().create_genome(a.id, "scout", db_session=s)
        s.commit()
        return a.id


def _action(value, path: str = PATH, evidence: str = "backtest shows edge", confidence: int = 8):
    return {"action": {"type": "modify_genome", "params": {
        "parameter_path": path, "new_value": value,
        "evidence": evidence, "confidence": confidence,
    }}}


def _genome_value(db_factory, agent_id: int, path: str = PATH):
    """Read a genome parameter from a FRESH session (proves DB persistence)."""
    with db_factory() as s:
        rec = s.query(AgentGenome).filter_by(agent_id=agent_id).one()
        node = rec.genome_data
        for part in path.split("."):
            node = node[part]
        return node


def test_action_routes_to_real_handler_not_broadcast(db_factory):
    # Regression guard: the silent no-op broadcast route must be gone.
    with db_factory() as s:
        ex = ActionExecutor(db_session=s)
        handler = ex._get_handler("modify_genome")
        assert handler == ex._handle_modify_genome
        assert handler != ex._handle_broadcast


@pytest.mark.asyncio
async def test_valid_mod_succeeds_persists_and_records_evidence(db_factory):
    agent_id = await _seed_agent_with_genome(db_factory)
    before = _genome_value(db_factory, agent_id)
    target = LOW if before != LOW else HIGH  # guaranteed different + in-bounds

    with db_factory() as s:
        ex = ActionExecutor(db_session=s)
        agent = s.get(Agent, agent_id)
        result = await ex.execute(agent, _action(target))
        s.commit()

    assert result.success is True
    assert PATH in result.details and str(target) in result.details

    # The honest part: reload in a FRESH session — the genome ACTUALLY changed.
    assert _genome_value(db_factory, agent_id) == target

    # The stated evidence is the whole point — it must be recorded.
    with db_factory() as s:
        muts = s.query(AgentGenome).filter_by(agent_id=agent_id).one().mutations_applied
    agent_mods = [v for k, v in muts.items() if k.startswith("agent_mod_")]
    assert any(v.get("evidence") == "backtest shows edge" for v in agent_mods)


@pytest.mark.asyncio
async def test_out_of_bounds_fails_and_genome_unchanged(db_factory):
    agent_id = await _seed_agent_with_genome(db_factory)
    before = _genome_value(db_factory, agent_id)

    with db_factory() as s:
        ex = ActionExecutor(db_session=s)
        agent = s.get(Agent, agent_id)
        result = await ex.execute(agent, _action(OUT_OF_RANGE))
        s.commit()

    assert result.success is False
    assert "out of range" in result.details
    # No fake success: the genome is UNCHANGED on reload.
    assert _genome_value(db_factory, agent_id) == before


@pytest.mark.asyncio
async def test_unknown_parameter_fails_honestly(db_factory):
    agent_id = await _seed_agent_with_genome(db_factory)

    with db_factory() as s:
        ex = ActionExecutor(db_session=s)
        agent = s.get(Agent, agent_id)
        result = await ex.execute(agent, _action(5, path="signal_generation.not_a_real_param"))
        s.commit()

    assert result.success is False
    assert "unknown parameter" in result.details


@pytest.mark.asyncio
async def test_cap_blocks_third_in_period_then_resets_next_period(db_factory):
    agent_id = await _seed_agent_with_genome(db_factory, evaluation_count=0)

    with db_factory() as s:
        ex = ActionExecutor(db_session=s)
        agent = s.get(Agent, agent_id)

        r1 = await ex.execute(agent, _action(LOW)); s.commit()
        r2 = await ex.execute(agent, _action(LOW)); s.commit()
        r3 = await ex.execute(agent, _action(LOW)); s.commit()
        assert r1.success is True
        assert r2.success is True
        assert r3.success is False
        assert "limit reached this evaluation period" in r3.details

        # A new evaluation period (counter bumped) frees the cap again.
        agent.evaluation_count = 1
        s.commit()
        r4 = await ex.execute(agent, _action(HIGH)); s.commit()
        assert r4.success is True
