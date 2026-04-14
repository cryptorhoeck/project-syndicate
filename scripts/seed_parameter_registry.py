"""
Seed the parameter registry with all SIP-modifiable system parameters.

Run once after migration. Idempotent -- skips existing entries.

Usage: python scripts/seed_parameter_registry.py
"""

__version__ = "0.1.0"

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from src.common.models import Base, ParameterRegistryEntry
from src.common.config import config

PARAMETERS = [
    # === TIER 1: OPEN PARAMETERS (standard 60% vote) ===

    # Evaluation weights
    {
        "parameter_key": "evaluation.sharpe_weight",
        "display_name": "Sharpe Ratio Weight",
        "description": "Weight of Sharpe ratio in operator composite evaluation score",
        "category": "evaluation",
        "current_value": 0.27,
        "default_value": 0.27,
        "min_value": 0.10,
        "max_value": 0.50,
        "tier": 1,
        "unit": "ratio",
    },
    {
        "parameter_key": "evaluation.pnl_weight",
        "display_name": "True P&L Weight",
        "description": "Weight of true P&L (after thinking tax) in operator composite score",
        "category": "evaluation",
        "current_value": 0.23,
        "default_value": 0.23,
        "min_value": 0.10,
        "max_value": 0.40,
        "tier": 1,
        "unit": "ratio",
    },
    {
        "parameter_key": "evaluation.win_rate_weight",
        "display_name": "Win Rate Weight",
        "description": "Weight of trade win rate in operator composite score",
        "category": "evaluation",
        "current_value": 0.18,
        "default_value": 0.18,
        "min_value": 0.05,
        "max_value": 0.35,
        "tier": 1,
        "unit": "ratio",
    },
    {
        "parameter_key": "evaluation.thinking_efficiency_weight",
        "display_name": "Thinking Efficiency Weight",
        "description": "Weight of API cost efficiency in operator composite score",
        "category": "evaluation",
        "current_value": 0.12,
        "default_value": 0.12,
        "min_value": 0.05,
        "max_value": 0.25,
        "tier": 1,
        "unit": "ratio",
    },
    {
        "parameter_key": "evaluation.reputation_weight",
        "display_name": "Reputation Weight",
        "description": "Weight of social reputation in composite evaluation score",
        "category": "evaluation",
        "current_value": 0.10,
        "default_value": 0.10,
        "min_value": 0.00,
        "max_value": 0.20,
        "tier": 1,
        "unit": "ratio",
    },
    # Lifecycle parameters
    {
        "parameter_key": "lifecycle.survival_clock_days",
        "display_name": "Survival Clock Duration",
        "description": "Days an agent has to prove itself before evaluation",
        "category": "lifecycle",
        "current_value": 14.0,
        "default_value": 14.0,
        "min_value": 7.0,
        "max_value": 30.0,
        "tier": 1,
        "unit": "days",
    },
    {
        "parameter_key": "lifecycle.hibernation_max_days",
        "display_name": "Max Hibernation Duration",
        "description": "Maximum days an agent can remain in strategic hibernation",
        "category": "lifecycle",
        "current_value": 7.0,
        "default_value": 7.0,
        "min_value": 1.0,
        "max_value": 14.0,
        "tier": 1,
        "unit": "days",
    },
    # Economics parameters
    {
        "parameter_key": "economics.starting_thinking_budget",
        "display_name": "Starting Thinking Budget",
        "description": "Daily thinking budget allocated to new agents (in dollars)",
        "category": "economics",
        "current_value": 2.50,
        "default_value": 2.50,
        "min_value": 0.50,
        "max_value": 10.00,
        "tier": 1,
        "unit": "dollars",
    },
    {
        "parameter_key": "economics.sip_thinking_tax_multiplier",
        "display_name": "SIP Thinking Tax Multiplier",
        "description": "Cost multiplier for proposing a SIP (normal cycle cost * this)",
        "category": "economics",
        "current_value": 2.0,
        "default_value": 2.0,
        "min_value": 1.0,
        "max_value": 5.0,
        "tier": 1,
        "unit": "multiplier",
    },
    {
        "parameter_key": "economics.intel_publication_delay_hours",
        "display_name": "Intel Publication Delay",
        "description": "Hours before successful strategy details are published to Library",
        "category": "economics",
        "current_value": 48.0,
        "default_value": 48.0,
        "min_value": 12.0,
        "max_value": 168.0,
        "tier": 1,
        "unit": "hours",
    },
    # Timing parameters
    {
        "parameter_key": "timing.scout_cycle_seconds",
        "display_name": "Scout Cycle Interval",
        "description": "Seconds between Scout agent thinking cycles",
        "category": "timing",
        "current_value": 300.0,
        "default_value": 300.0,
        "min_value": 60.0,
        "max_value": 900.0,
        "tier": 1,
        "unit": "seconds",
    },
    {
        "parameter_key": "timing.operator_cycle_seconds",
        "display_name": "Operator Cycle Interval",
        "description": "Seconds between Operator agent thinking cycles",
        "category": "timing",
        "current_value": 300.0,
        "default_value": 300.0,
        "min_value": 60.0,
        "max_value": 900.0,
        "tier": 1,
        "unit": "seconds",
    },
    {
        "parameter_key": "timing.reflection_cycle_interval",
        "display_name": "Reflection Cycle Interval",
        "description": "Run a reflection cycle every N normal cycles",
        "category": "timing",
        "current_value": 10.0,
        "default_value": 10.0,
        "min_value": 5.0,
        "max_value": 25.0,
        "tier": 1,
        "unit": "cycles",
    },

    # === TIER 2: STRUCTURAL PARAMETERS (75% supermajority + Genesis + owner) ===

    {
        "parameter_key": "lifecycle.boot_sequence_scouts",
        "display_name": "Boot Sequence Scout Count",
        "description": "Number of Scouts spawned during cold start",
        "category": "lifecycle",
        "current_value": 2.0,
        "default_value": 2.0,
        "min_value": 1.0,
        "max_value": 5.0,
        "tier": 2,
        "unit": "agents",
    },
    {
        "parameter_key": "lifecycle.boot_sequence_operators",
        "display_name": "Boot Sequence Operator Count",
        "description": "Number of Operators spawned during cold start",
        "category": "lifecycle",
        "current_value": 1.0,
        "default_value": 1.0,
        "min_value": 1.0,
        "max_value": 3.0,
        "tier": 2,
        "unit": "agents",
    },
    {
        "parameter_key": "evaluation.reputation_decay_rate",
        "display_name": "Reputation Decay Rate",
        "description": "Daily decay multiplier applied to reputation scores",
        "category": "evaluation",
        "current_value": 0.95,
        "default_value": 0.95,
        "min_value": 0.80,
        "max_value": 1.00,
        "tier": 2,
        "unit": "multiplier",
    },
    {
        "parameter_key": "lifecycle.reproduction_min_sharpe",
        "display_name": "Minimum Sharpe for Reproduction",
        "description": "Minimum Sharpe ratio required for an agent to reproduce",
        "category": "lifecycle",
        "current_value": 1.0,
        "default_value": 1.0,
        "min_value": 0.5,
        "max_value": 3.0,
        "tier": 2,
        "unit": "ratio",
    },

    # === TIER 3: FORBIDDEN PARAMETERS (no SIP can touch these) ===

    {
        "parameter_key": "risk.circuit_breaker_threshold",
        "display_name": "Circuit Breaker Threshold",
        "description": "Portfolio loss percentage from peak that triggers full shutdown",
        "category": "risk",
        "current_value": 0.75,
        "default_value": 0.75,
        "min_value": 0.75,
        "max_value": 0.75,
        "tier": 3,
        "unit": "percent",
    },
    {
        "parameter_key": "risk.per_agent_max_position_pct",
        "display_name": "Max Position Size Per Agent",
        "description": "Maximum position size as percentage of agent capital",
        "category": "risk",
        "current_value": 0.25,
        "default_value": 0.25,
        "min_value": 0.25,
        "max_value": 0.25,
        "tier": 3,
        "unit": "percent",
    },
    {
        "parameter_key": "risk.per_agent_max_loss_pct",
        "display_name": "Max Agent Loss Before Kill",
        "description": "Agent capital loss percentage that triggers instant termination",
        "category": "risk",
        "current_value": 0.50,
        "default_value": 0.50,
        "min_value": 0.50,
        "max_value": 0.50,
        "tier": 3,
        "unit": "percent",
    },
    {
        "parameter_key": "risk.treasury_reserve_pct",
        "display_name": "Treasury Reserve Requirement",
        "description": "Minimum percentage of treasury held in reserve",
        "category": "risk",
        "current_value": 0.20,
        "default_value": 0.20,
        "min_value": 0.20,
        "max_value": 0.20,
        "tier": 3,
        "unit": "percent",
    },
    {
        "parameter_key": "governance.vote_pass_threshold",
        "display_name": "SIP Vote Pass Threshold",
        "description": "Percentage of weighted votes needed to pass a SIP",
        "category": "governance",
        "current_value": 0.60,
        "default_value": 0.60,
        "min_value": 0.60,
        "max_value": 0.60,
        "tier": 3,
        "unit": "percent",
    },
    {
        "parameter_key": "governance.structural_supermajority",
        "display_name": "Structural SIP Supermajority",
        "description": "Percentage of weighted votes needed for Tier 2 structural SIPs",
        "category": "governance",
        "current_value": 0.75,
        "default_value": 0.75,
        "min_value": 0.75,
        "max_value": 0.75,
        "tier": 3,
        "unit": "percent",
    },
]


def seed(db_url: str = None):
    """Insert parameter registry entries. Idempotent."""
    url = db_url or config.database_url
    engine = create_engine(url)

    inserted = 0
    skipped = 0

    with Session(engine) as session:
        for p in PARAMETERS:
            existing = session.execute(
                select(ParameterRegistryEntry).where(
                    ParameterRegistryEntry.parameter_key == p["parameter_key"]
                )
            ).scalar_one_or_none()

            if existing:
                skipped += 1
                continue

            entry = ParameterRegistryEntry(**p)
            session.add(entry)
            inserted += 1

        session.commit()

    print(f"Parameter registry seeded: {inserted} inserted, {skipped} skipped (already exist)")
    return inserted, skipped


if __name__ == "__main__":
    seed()
