"""Phase 10 Tier 2: enable remaining 5 Wire sources

Revision ID: phase_10_wire_003
Revises: phase_10_wire_002
Create Date: 2026-05-01 02:00:00.000000

Flips `enabled` to TRUE for the five Tier 2 sources whose implementations
landed in this build tier:
    etherscan_transfers, funding_rates, fred, trading_economics, fear_greed
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "phase_10_wire_003"
down_revision: Union[str, Sequence[str], None] = "phase_10_wire_002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_TIER2_NAMES = (
    "etherscan_transfers",
    "funding_rates",
    "fred",
    "trading_economics",
    "fear_greed",
)


def upgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text(
            "UPDATE wire_sources SET enabled = TRUE WHERE name = ANY(:names)"
        ),
        {"names": list(_TIER2_NAMES)},
    )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text(
            "UPDATE wire_sources SET enabled = FALSE WHERE name = ANY(:names)"
        ),
        {"names": list(_TIER2_NAMES)},
    )
