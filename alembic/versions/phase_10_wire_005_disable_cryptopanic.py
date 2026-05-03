"""Phase 10: drop cryptopanic from launch set (free tier discontinued)

Revision ID: phase_10_wire_005
Revises: phase_10_wire_004
Create Date: 2026-05-02 23:55:00.000000

CryptoPanic discontinued its free public tier in 2024-2025; paid-only at
$25/mo+ as of validation date. Decision: drop from Phase 10 launch set,
build a free RSS-based replacement in Phase 10.5 (CoinTelegraph, CoinDesk,
Decrypt, The Block, Reddit r/CryptoCurrency JSON).

Source code and table row are intentionally preserved — flipping enabled
back to TRUE is the only change required if Andrew ever buys a key.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "phase_10_wire_005"
down_revision: Union[str, Sequence[str], None] = "phase_10_wire_004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.get_bind().execute(
        sa.text("UPDATE wire_sources SET enabled = FALSE WHERE name = 'cryptopanic'")
    )


def downgrade() -> None:
    op.get_bind().execute(
        sa.text("UPDATE wire_sources SET enabled = TRUE WHERE name = 'cryptopanic'")
    )
