"""Phase 2A: Agora columns on messages, agora_channels, agora_read_receipts

Revision ID: fdc6e51f4c04
Revises: d24866bf2818
Create Date: 2026-03-12 11:12:09.175076

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'fdc6e51f4c04'
down_revision: Union[str, Sequence[str], None] = 'd24866bf2818'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # --- New tables ---
    op.create_table('agora_channels',
        sa.Column('name', sa.String(length=50), nullable=False),
        sa.Column('description', sa.Text(), nullable=False),
        sa.Column('is_system', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.Column('message_count', sa.Integer(), nullable=False, server_default='0'),
        sa.PrimaryKeyConstraint('name')
    )
    op.create_table('agora_read_receipts',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('agent_id', sa.Integer(), nullable=False),
        sa.Column('channel', sa.String(length=50), nullable=False),
        sa.Column('last_read_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.Column('last_read_message_id', sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(['agent_id'], ['agents.id']),
        sa.ForeignKeyConstraint(['last_read_message_id'], ['messages.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('agent_id', 'channel', name='uq_read_receipt_agent_channel')
    )

    # --- New columns on messages (with server defaults for existing rows) ---
    op.add_column('messages', sa.Column('message_type', sa.String(length=20), server_default='chat', nullable=False))
    op.add_column('messages', sa.Column('agent_name', sa.String(length=100), nullable=True))
    op.add_column('messages', sa.Column('parent_message_id', sa.Integer(), nullable=True))
    op.add_column('messages', sa.Column('importance', sa.Integer(), server_default='0', nullable=False))
    op.add_column('messages', sa.Column('expires_at', sa.DateTime(), nullable=True))
    op.create_foreign_key('fk_messages_parent', 'messages', 'messages', ['parent_message_id'], ['id'])

    # --- Seed default channels ---
    channels = sa.table('agora_channels',
        sa.column('name', sa.String),
        sa.column('description', sa.Text),
        sa.column('is_system', sa.Boolean),
    )
    op.bulk_insert(channels, [
        {"name": "market-intel", "description": "Market discoveries, price movements, opportunities", "is_system": False},
        {"name": "strategy-proposals", "description": "Formal strategy proposals for debate", "is_system": False},
        {"name": "strategy-debate", "description": "Critiques, counter-arguments, stress tests", "is_system": False},
        {"name": "trade-signals", "description": "Pre-trade announcements: I'm about to trade X because Y", "is_system": False},
        {"name": "trade-results", "description": "Post-trade outcomes, P&L updates", "is_system": False},
        {"name": "system-alerts", "description": "Warden alerts, Dead Man's Switch, circuit breaker events", "is_system": True},
        {"name": "genesis-log", "description": "Genesis spawn/kill/evaluate decisions, capital allocation", "is_system": True},
        {"name": "agent-chat", "description": "Free-form agent discussion, ideas, collaboration", "is_system": False},
        {"name": "sip-proposals", "description": "System Improvement Proposals", "is_system": False},
        {"name": "daily-report", "description": "Genesis daily narrative report", "is_system": True},
    ])

    # --- Backfill agent_name='Genesis' on existing messages from agent_id=0 ---
    op.execute("UPDATE messages SET agent_name = 'Genesis' WHERE agent_id = 0")


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint('fk_messages_parent', 'messages', type_='foreignkey')
    op.drop_column('messages', 'expires_at')
    op.drop_column('messages', 'importance')
    op.drop_column('messages', 'parent_message_id')
    op.drop_column('messages', 'agent_name')
    op.drop_column('messages', 'message_type')
    op.drop_table('agora_read_receipts')
    op.drop_table('agora_channels')
