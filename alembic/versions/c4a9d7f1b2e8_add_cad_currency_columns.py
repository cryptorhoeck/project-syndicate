"""add_cad_currency_columns

Revision ID: c4a9d7f1b2e8
Revises: 0e6578416d46
Create Date: 2026-03-23 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c4a9d7f1b2e8'
down_revision: Union[str, Sequence[str], None] = '0e6578416d46'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add CAD currency columns missing from currency layer work."""
    # system_state
    op.add_column('system_state', sa.Column('treasury_currency', sa.String(10), server_default='CAD', nullable=False))

    # agents
    op.add_column('agents', sa.Column('total_true_pnl_cad', sa.Float(), server_default='0.0', nullable=False))

    # evaluations
    op.add_column('evaluations', sa.Column('pnl_gross_cad', sa.Float(), server_default='0.0', nullable=False))
    op.add_column('evaluations', sa.Column('pnl_net_cad', sa.Float(), server_default='0.0', nullable=False))
    op.add_column('evaluations', sa.Column('api_cost_cad', sa.Float(), server_default='0.0', nullable=False))

    # daily_reports
    op.add_column('daily_reports', sa.Column('usdt_cad_rate', sa.Float(), nullable=True))


def downgrade() -> None:
    """Remove CAD currency columns."""
    op.drop_column('daily_reports', 'usdt_cad_rate')
    op.drop_column('evaluations', 'api_cost_cad')
    op.drop_column('evaluations', 'pnl_net_cad')
    op.drop_column('evaluations', 'pnl_gross_cad')
    op.drop_column('agents', 'total_true_pnl_cad')
    op.drop_column('system_state', 'treasury_currency')
