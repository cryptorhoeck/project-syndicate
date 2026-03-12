"""Phase 2C: Economy tables — intel_signals, endorsements, review_requests, assignments, critic_accuracy, service_listings, gaming_flags

Revision ID: b8c4d2e3f5a6
Revises: a7b3c1d2e3f4
Create Date: 2026-03-12 12:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b8c4d2e3f5a6'
down_revision: str = 'a7b3c1d2e3f4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # intel_signals
    op.create_table(
        'intel_signals',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('message_id', sa.Integer(), sa.ForeignKey('messages.id'), nullable=False),
        sa.Column('scout_agent_id', sa.Integer(), sa.ForeignKey('agents.id'), nullable=False),
        sa.Column('scout_agent_name', sa.String(100), nullable=False),
        sa.Column('asset', sa.String(30), nullable=False),
        sa.Column('direction', sa.String(10), nullable=False),
        sa.Column('confidence_level', sa.Integer(), server_default='3', nullable=False),
        sa.Column('price_at_creation', sa.Float(), nullable=False),
        sa.Column('expires_at', sa.DateTime(), nullable=False),
        sa.Column('status', sa.String(20), server_default='active', nullable=False),
        sa.Column('total_endorsement_stake', sa.Float(), server_default='0', nullable=False),
        sa.Column('endorsement_count', sa.Integer(), server_default='0', nullable=False),
        sa.Column('settlement_price', sa.Float(), nullable=True),
        sa.Column('settlement_price_change_pct', sa.Float(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column('settled_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_intel_signals_status_expires', 'intel_signals', ['status', 'expires_at'])
    op.create_index('ix_intel_signals_scout', 'intel_signals', ['scout_agent_id'])

    # intel_endorsements
    op.create_table(
        'intel_endorsements',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('signal_id', sa.Integer(), sa.ForeignKey('intel_signals.id'), nullable=False),
        sa.Column('endorser_agent_id', sa.Integer(), sa.ForeignKey('agents.id'), nullable=False),
        sa.Column('endorser_agent_name', sa.String(100), nullable=False),
        sa.Column('stake_amount', sa.Float(), nullable=False),
        sa.Column('linked_trade_id', sa.Integer(), sa.ForeignKey('transactions.id'), nullable=True),
        sa.Column('settlement_status', sa.String(20), server_default='pending', nullable=False),
        sa.Column('settlement_pnl', sa.Float(), nullable=True),
        sa.Column('scout_reputation_change', sa.Float(), nullable=True),
        sa.Column('endorser_reputation_change', sa.Float(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column('settled_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('signal_id', 'endorser_agent_id', name='uq_endorsement_signal_agent'),
    )
    op.create_index('ix_intel_endorsements_signal', 'intel_endorsements', ['signal_id'])
    op.create_index('ix_intel_endorsements_endorser_status', 'intel_endorsements', ['endorser_agent_id', 'settlement_status'])

    # review_requests
    op.create_table(
        'review_requests',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('requester_agent_id', sa.Integer(), sa.ForeignKey('agents.id'), nullable=False),
        sa.Column('requester_agent_name', sa.String(100), nullable=False),
        sa.Column('proposal_message_id', sa.Integer(), sa.ForeignKey('messages.id'), nullable=False),
        sa.Column('proposal_summary', sa.Text(), nullable=True),
        sa.Column('budget_reputation', sa.Float(), nullable=False),
        sa.Column('requires_two_reviews', sa.Boolean(), server_default='false', nullable=False),
        sa.Column('status', sa.String(20), server_default='open', nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column('expires_at', sa.DateTime(), nullable=False),
        sa.Column('completed_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_review_requests_status_expires', 'review_requests', ['status', 'expires_at'])

    # review_assignments
    op.create_table(
        'review_assignments',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('review_request_id', sa.Integer(), sa.ForeignKey('review_requests.id'), nullable=False),
        sa.Column('critic_agent_id', sa.Integer(), sa.ForeignKey('agents.id'), nullable=False),
        sa.Column('critic_agent_name', sa.String(100), nullable=False),
        sa.Column('verdict', sa.String(20), nullable=True),
        sa.Column('reasoning', sa.Text(), nullable=True),
        sa.Column('risk_score', sa.Integer(), nullable=True),
        sa.Column('review_message_id', sa.Integer(), sa.ForeignKey('messages.id'), nullable=True),
        sa.Column('reputation_earned', sa.Float(), nullable=True),
        sa.Column('accepted_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column('completed_at', sa.DateTime(), nullable=True),
        sa.Column('deadline_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('review_request_id', 'critic_agent_id', name='uq_review_assignment_request_critic'),
    )
    op.create_index('ix_review_assignments_critic_completed', 'review_assignments', ['critic_agent_id', 'completed_at'])

    # critic_accuracy
    op.create_table(
        'critic_accuracy',
        sa.Column('critic_agent_id', sa.Integer(), sa.ForeignKey('agents.id'), nullable=False),
        sa.Column('total_reviews', sa.Integer(), server_default='0', nullable=False),
        sa.Column('accurate_reviews', sa.Integer(), server_default='0', nullable=False),
        sa.Column('accuracy_score', sa.Float(), server_default='0', nullable=False),
        sa.Column('approve_count', sa.Integer(), server_default='0', nullable=False),
        sa.Column('reject_count', sa.Integer(), server_default='0', nullable=False),
        sa.Column('conditional_count', sa.Integer(), server_default='0', nullable=False),
        sa.Column('avg_risk_score', sa.Float(), server_default='0', nullable=False),
        sa.Column('last_updated', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('critic_agent_id'),
    )

    # service_listings
    op.create_table(
        'service_listings',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('provider_agent_id', sa.Integer(), sa.ForeignKey('agents.id'), nullable=False),
        sa.Column('provider_agent_name', sa.String(100), nullable=False),
        sa.Column('title', sa.String(200), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('price_reputation', sa.Float(), nullable=False),
        sa.Column('status', sa.String(20), server_default='active', nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column('purchase_count', sa.Integer(), server_default='0', nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )

    # gaming_flags
    op.create_table(
        'gaming_flags',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('flag_type', sa.String(30), nullable=False),
        sa.Column('agent_ids', sa.JSON(), nullable=False),
        sa.Column('evidence', sa.Text(), nullable=False),
        sa.Column('severity', sa.String(10), nullable=False),
        sa.Column('penalty_applied', sa.Float(), nullable=True),
        sa.Column('detected_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column('reviewed_by', sa.String(20), nullable=True),
        sa.Column('resolved', sa.Boolean(), server_default='false', nullable=False),
        sa.Column('resolved_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_gaming_flags_resolved_detected', 'gaming_flags', ['resolved', 'detected_at'])


def downgrade() -> None:
    op.drop_table('gaming_flags')
    op.drop_table('service_listings')
    op.drop_table('critic_accuracy')
    op.drop_table('review_assignments')
    op.drop_table('review_requests')
    op.drop_table('intel_endorsements')
    op.drop_table('intel_signals')
