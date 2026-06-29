"""Weave Step 3b: agent_genomes.context_enabled (per-agent genome->prompt gate)

Revision ID: weave_3b_001
Revises: weave_2b_001
Create Date: 2026-06-28 00:00:00.000000

ADDITIVE column on an existing table. Backfills every existing row to FALSE
(server_default='false', NOT NULL), so when the master switch
`config.genome_context_enabled` is flipped ON, the existing/drifted population
stays dark — only a deliberately-enabled agent (per-agent flag True) gets
genome-in-prompt. Fail-safe: NULL/missing reads as disabled.

Revises the current single head (weave_2b_001); keeps the chain linear.
Real tested downgrade (drops the column).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "weave_3b_001"
down_revision: Union[str, Sequence[str], None] = "weave_2b_001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "agent_genomes",
        sa.Column(
            "context_enabled",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("agent_genomes", "context_enabled")
