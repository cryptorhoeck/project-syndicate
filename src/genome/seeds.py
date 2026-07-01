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

from sqlalchemy import select, text

from src.common.models import Agent, AgentGenome
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


def enable_genome_context(agent_id: int, db_session) -> None:
    """Turn ON the per-agent genome->prompt gate for ONE agent (Step 3c).

    Sets only the per-agent flag. The genome still does NOT reach the prompt unless
    the master switch `config.genome_context_enabled` is also True — so this is
    inert until the deliberate master flip. Use to designate the single seeded JJ
    Scout as the cohort-of-one. Offspring do NOT inherit this flag (see
    `genome_manager.create_genome`), so the cohort stays a cohort of one.
    """
    rec = db_session.execute(
        select(AgentGenome).where(AgentGenome.agent_id == agent_id)
    ).scalar_one_or_none()
    if rec is None:
        raise ValueError(f"no genome row for agent {agent_id}; seed it first")
    rec.context_enabled = True
    db_session.flush()
    logger.info("enabled genome->prompt gate for agent %s", agent_id)


def ensure_jj_scout(db_session, config) -> int | None:
    """Hands-off, self-healing JJ designation — keeps exactly one JJ scout alive.

    Called every Genesis cycle. If the master switch is ON and no active scout
    currently carries the JJ genome gate, this designates the longest-lived active
    scout as the JJ scout (seed JJ genome + enable its per-agent gate). It is:

    - **Master-switch-gated** — returns None (no-op) when
      ``config.genome_context_enabled`` is False, so the whole feature stays a
      single ``.env`` flip away from off.
    - **Cohort-of-one** — does nothing if an active JJ scout already exists, so it
      never enables a second one. Offspring don't inherit the flag
      (``genome_manager.create_genome``), so the cohort can't grow on its own.
    - **Self-healing** — if the JJ scout dies, the next cycle re-designates a fresh
      one, so the colony always has exactly one JJ scout without manual action.

    Returns the JJ scout's agent_id (existing or newly designated), or None.
    """
    if not getattr(config, "genome_context_enabled", False):
        return None
    # Defensive depth: never let JJ designation hang a Genesis cycle. Even with the
    # boot-sequence genome race fixed, a hard per-statement timeout means lock
    # contention aborts this step (caught + logged by the caller) rather than freezing
    # the colony, as it did on the maiden launch.
    try:
        db_session.execute(text("SET LOCAL statement_timeout = '5000'"))  # 5s (Postgres)
    except Exception:
        pass  # non-Postgres backend (e.g. SQLite in tests) — best effort
    # Already have a live JJ scout? Then we're done (cohort-of-one, idempotent).
    existing = db_session.execute(
        select(Agent.id)
        .join(AgentGenome, AgentGenome.agent_id == Agent.id)
        .where(
            Agent.type == "scout",
            Agent.status == "active",
            AgentGenome.context_enabled.is_(True),
        )
    ).first()
    if existing is not None:
        return existing[0]
    # Otherwise designate the longest-lived active scout as the JJ scout.
    scout = (
        db_session.execute(
            select(Agent)
            .where(Agent.type == "scout", Agent.status == "active")
            .order_by(Agent.id)
        )
        .scalars()
        .first()
    )
    if scout is None:
        return None  # no active scout yet (pre-spawn) — try again next cycle
    seed_agent_genome(scout.id, jj_scout_genome(), JJ_SEED_ROLE, db_session)
    enable_genome_context(scout.id, db_session)
    logger.info("ensure_jj_scout: auto-designated agent %s as the JJ scout", scout.id)
    return scout.id
