"""Preset genome seeds — author specific (non-random) genomes for spawning.

Syndicate's genome creation is random / warm-start / inherited+mutated; there is no
preset path. A seed lets us start an agent biased toward a known style (e.g. JJ's
VWAP/RSI/momentum/volume thresholds). Every seed is clamped + validated to
GENOME_BOUNDS before it is persisted — a hand-authored out-of-range value is
corrected, never stored raw.

INERT by itself: a seeded genome influences nothing until the genome→prompt wiring
(Step 3b, default-OFF) is enabled for that specific agent. See STEP3_PLAN.md.
"""

from __future__ import annotations

import logging

from sqlalchemy import select

from src.common.models import AgentGenome
from src.genome.genome_schema import clamp_genome, validate_genome

logger = logging.getLogger(__name__)

JJ_SEED_ROLE = "scout"

# JJ "Gorilla" Scout genome — VWAP-deviation / RSI mean-reversion / volume style,
# mapped from jj-bot's thresholds. Scout sections only (market_selection,
# signal_generation, behavioral). All values within GENOME_BOUNDS.
JJ_SCOUT_GENOME = {
    "market_selection": {
        "volatility_preference": 0.5,
        "volume_threshold_multiplier": 2.0,
        "max_concurrent_markets": 3,
        "regime_weights": {"bull": 0.6, "bear": 0.6, "crab": 0.9, "volatile": 0.5},
    },
    "signal_generation": {
        "min_confidence_to_broadcast": 5,
        "momentum_threshold_pct": 0.5,   # JJ's native 0.3% is below the genome floor (0.5)
        "volume_spike_threshold": 2.0,   # JJ 2x volume spike
        "rsi_oversold": 30,              # JJ RSI 30 / 70 bands
        "rsi_overbought": 70,
        "contrarian_bias": 0.3,          # VWAP mean-reversion = contrarian lean
    },
    "behavioral": {
        "idle_tolerance_cycles": 5,
        "intel_sharing_generosity": 0.5,
        "alliance_willingness": 0.4,
        "sip_propensity": 0.1,
        "hibernate_threshold_budget_pct": 10.0,
        "tool_execution_frequency": 0.6,  # JJ leans on its own analysis (e.g. consult_tool)
        "communication_expressiveness": 0.6,
    },
}


def jj_scout_genome() -> dict:
    """Return the JJ Scout genome, clamped to bounds (safe to persist)."""
    return clamp_genome(JJ_SCOUT_GENOME)


def seed_agent_genome(agent_id: int, genome_data: dict, role: str, db_session) -> dict:
    """Persist a preset genome for an agent — CLAMPED + validated first.

    Always clamps to GENOME_BOUNDS before writing, so a hand-authored out-of-range
    value is corrected, never stored raw. Creates the AgentGenome row, or updates an
    existing one (agent_id is UNIQUE). Returns the clamped genome that was stored.
    """
    clamped = clamp_genome(genome_data)
    valid, violations = validate_genome(clamped, role)
    if not valid:  # clamp should guarantee this; refuse rather than store bad data
        raise ValueError(f"seed genome still invalid after clamp: {violations}")

    existing = db_session.execute(
        select(AgentGenome).where(AgentGenome.agent_id == agent_id)
    ).scalar_one_or_none()
    if existing is not None:
        existing.genome_data = clamped
        existing.genome_version = (existing.genome_version or 1) + 1
    else:
        db_session.add(
            AgentGenome(agent_id=agent_id, genome_version=1, genome_data=clamped)
        )
    db_session.flush()
    logger.info("seeded preset genome for agent %s (role=%s)", agent_id, role)
    return clamped
