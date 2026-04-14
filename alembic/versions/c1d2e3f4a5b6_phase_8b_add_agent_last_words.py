"""phase_8b_add_agent_last_words

Revision ID: c1d2e3f4a5b6
Revises: 0e6578416d46
Create Date: 2026-03-24 00:00:00.000000

Adds agents.last_words column introduced in Phase 8B (Survival Instinct).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c1d2e3f4a5b6'
down_revision: Union[str, Sequence[str], None] = 'c4a9d7f1b2e8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add agents.last_words column (Phase 8B). Safe to run if column already exists."""
    op.execute(
        "ALTER TABLE agents ADD COLUMN IF NOT EXISTS last_words TEXT"
    )


def downgrade() -> None:
    """Remove agents.last_words column."""
    op.drop_column('agents', 'last_words')
