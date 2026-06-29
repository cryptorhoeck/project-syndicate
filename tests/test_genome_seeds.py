"""Tests for preset genome seeds (Step 3a) — JJ Scout genome + clamp-on-seed guard.

INERT: a seeded genome influences nothing until the genome->prompt wiring (Step 3b)
is enabled for the agent. The load-bearing test here is THE GUARD: an out-of-range
hand-authored seed must be clamped before persist, not stored raw.
"""

import copy

from src.common.models import Agent, AgentGenome
from src.genome.genome_schema import validate_genome
from src.genome.seeds import JJ_SCOUT_GENOME, jj_scout_genome, seed_agent_genome


def _agent(session, name="JJ-Scout"):
    a = Agent(
        name=name, type="scout", status="active",
        capital_allocated=100.0, capital_current=100.0,
        thinking_budget_daily=0.5, thinking_budget_used_today=0.0,
        evaluation_count=0, profitable_evaluations=0,
    )
    session.add(a)
    session.commit()
    return a


def test_jj_scout_genome_is_in_bounds_and_carries_jj_values():
    g = jj_scout_genome()
    valid, violations = validate_genome(g, "scout")
    assert valid, violations
    sg = g["signal_generation"]
    assert sg["rsi_oversold"] == 30
    assert sg["rsi_overbought"] == 70
    assert sg["volume_spike_threshold"] == 2.0
    # JJ's native 0.3% momentum is below the genome floor (0.5) -> represented at floor.
    assert sg["momentum_threshold_pct"] == 0.5
    assert g["market_selection"]["volume_threshold_multiplier"] == 2.0


def test_seed_persists_clamped_genome(db_session_factory):
    session = db_session_factory()
    agent = _agent(session)
    stored = seed_agent_genome(agent.id, jj_scout_genome(), "scout", session)
    session.commit()
    rec = session.query(AgentGenome).filter_by(agent_id=agent.id).one()
    assert rec.genome_data["signal_generation"]["rsi_oversold"] == 30
    assert stored == rec.genome_data


def test_seed_updates_existing_row_in_place(db_session_factory):
    session = db_session_factory()
    agent = _agent(session)
    seed_agent_genome(agent.id, jj_scout_genome(), "scout", session)
    session.commit()
    seed_agent_genome(agent.id, jj_scout_genome(), "scout", session)  # again
    session.commit()
    rows = session.query(AgentGenome).filter_by(agent_id=agent.id).all()
    assert len(rows) == 1  # agent_id is unique — updated, not duplicated
    assert rows[0].genome_version == 2


def test_out_of_range_seed_is_clamped_not_stored_raw(db_session_factory):
    # THE GUARD: a hand-authored out-of-range seed must be clamped before persist.
    session = db_session_factory()
    agent = _agent(session)
    bad = copy.deepcopy(JJ_SCOUT_GENOME)
    bad["signal_generation"]["rsi_oversold"] = 999          # > max 40
    bad["signal_generation"]["momentum_threshold_pct"] = 0.1  # < floor 0.5
    bad["signal_generation"]["volume_spike_threshold"] = 99   # > max 5.0
    bad["market_selection"]["volatility_preference"] = -5.0    # < min 0.1

    seed_agent_genome(agent.id, bad, "scout", session)
    session.commit()

    sg = session.query(AgentGenome).filter_by(agent_id=agent.id).one().genome_data
    assert sg["signal_generation"]["rsi_oversold"] == 40          # clamped to max
    assert sg["signal_generation"]["momentum_threshold_pct"] == 0.5  # clamped to floor
    assert sg["signal_generation"]["volume_spike_threshold"] == 5.0  # clamped to max
    assert sg["market_selection"]["volatility_preference"] == 0.1    # clamped to min
    # And the persisted genome validates in-range (guard held).
    valid, violations = validate_genome(sg, "scout")
    assert valid, violations
