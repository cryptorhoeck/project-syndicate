"""Phase 9B Tier A — seed parameter_registry with five proof-of-concept parameters

Revision ID: phase_9b_tier_a_001
Revises: phase_10_wire_007
Create Date: 2026-05-08 00:00:00.000000

Seeds five rows into parameter_registry to make the read-path proof-of-concept
runnable. One numeric Tier 1 (probation_grace_cycles) is migrated to get_param
in this same commit; the others are seeded for Tier B continuity and rejection
tests.

Tier mapping:
- Tier 1 (permissive, 60% pass): evaluation.probation_grace_cycles, evaluation.first_eval_leniency
- Tier 2 (structural, 75% supermajority): colony.min_spawn_capital, colony.max_agents
- Tier 3 (forbidden, immutable): colony.darwin_pressure_enabled

The Tier 3 row exists ONLY as a rejection target. No production code reads it.
Disabling Darwinian selection would invalidate the experiment's premise.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "phase_9b_tier_a_001"
down_revision: Union[str, Sequence[str], None] = "phase_10_wire_007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# (parameter_key, display_name, description, category, default_value,
#  min_value, max_value, tier, unit)
SEED_ROWS = [
    (
        "evaluation.probation_grace_cycles",
        "Probation Grace Cycles",
        (
            "Cycles an underperforming agent gets before the survival clock "
            "expires. Initial value assigned when an agent enters probation. "
            "Distinct from Agent.probation_grace_cycles, which is the "
            "per-agent countdown that decrements each cycle."
        ),
        "evaluation",
        3.0,
        1.0,
        10.0,
        1,
        "cycles",
    ),
    (
        "evaluation.first_eval_leniency",
        "First Evaluation Leniency",
        (
            "If 1, new agents get leniency on their first evaluation (will not "
            "be terminated, even if pre-filter says terminate). Boolean stored "
            "as 0/1. Seeded for Tier B; not yet consumed in Tier A."
        ),
        "evaluation",
        1.0,
        0.0,
        1.0,
        1,
        None,
    ),
    (
        "colony.min_spawn_capital",
        "Minimum Spawn Capital",
        (
            "Minimum treasury capital (USDT) required before Genesis can spawn "
            "a new agent. Tier 2 — structural, requires 75% supermajority."
        ),
        "colony",
        50.0,
        25.0,
        200.0,
        2,
        "USDT",
    ),
    (
        "colony.max_agents",
        "Maximum Active Agents",
        (
            "Hard cap on simultaneous active agents. Tier 2 — structural, "
            "requires 75% supermajority."
        ),
        "colony",
        8.0,
        3.0,
        20.0,
        2,
        "agents",
    ),
    (
        "colony.darwin_pressure_enabled",
        "Darwinian Selection Pressure Enabled",
        (
            "If 1, natural selection / agent termination is active. "
            "TIER 3 FORBIDDEN. Disabling this breaks the experiment's core "
            "premise (agents must face survival pressure to evolve). Seeded "
            "purely as a rejection target — no production code reads this row."
        ),
        "colony",
        1.0,
        0.0,
        1.0,
        3,
        None,
    ),
]


def upgrade() -> None:
    bind = op.get_bind()
    for (
        parameter_key,
        display_name,
        description,
        category,
        default_value,
        min_value,
        max_value,
        tier,
        unit,
    ) in SEED_ROWS:
        bind.execute(
            sa.text(
                """
                INSERT INTO parameter_registry (
                    parameter_key, display_name, description, category,
                    current_value, default_value, min_value, max_value,
                    tier, unit
                )
                VALUES (
                    :parameter_key, :display_name, :description, :category,
                    :current_value, :default_value, :min_value, :max_value,
                    :tier, :unit
                )
                """
            ),
            {
                "parameter_key": parameter_key,
                "display_name": display_name,
                "description": description,
                "category": category,
                "current_value": default_value,
                "default_value": default_value,
                "min_value": min_value,
                "max_value": max_value,
                "tier": tier,
                "unit": unit,
            },
        )


def downgrade() -> None:
    bind = op.get_bind()
    keys = [row[0] for row in SEED_ROWS]
    bind.execute(
        sa.text(
            "DELETE FROM parameter_registry WHERE parameter_key IN :keys"
        ).bindparams(sa.bindparam("keys", expanding=True)),
        {"keys": keys},
    )
