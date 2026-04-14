"""Phase 9A: SIP voting and colony maturity

Revision ID: e5f6a7b8c9d0
Revises: c1d2e3f4a5b6
Create Date: 2026-04-13 00:00:00.000000

Adds colony_maturity, parameter_registry, parameter_change_log,
sip_votes, sip_debates tables.
Adds lifecycle columns to system_improvement_proposals.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'e5f6a7b8c9d0'
down_revision: Union[str, Sequence[str], None] = 'c1d2e3f4a5b6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create Phase 9A tables and add SIP lifecycle columns."""

    # Colony maturity singleton
    op.create_table(
        'colony_maturity',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('stage', sa.String(20), nullable=False, server_default='nascent'),
        sa.Column('colony_age_days', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('max_generation', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('total_sips_passed', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('active_agent_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('last_stage_transition_at', sa.DateTime(), nullable=True),
        sa.Column('last_computed_at', sa.DateTime(), server_default=sa.func.now()),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()),
    )
    # Seed the singleton row
    op.execute("INSERT INTO colony_maturity (stage) VALUES ('nascent')")

    # Parameter registry
    op.create_table(
        'parameter_registry',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('parameter_key', sa.String(100), nullable=False, unique=True),
        sa.Column('display_name', sa.String(200), nullable=False),
        sa.Column('description', sa.Text(), nullable=False),
        sa.Column('category', sa.String(50), nullable=False),
        sa.Column('current_value', sa.Float(), nullable=False),
        sa.Column('default_value', sa.Float(), nullable=False),
        sa.Column('min_value', sa.Float(), nullable=False),
        sa.Column('max_value', sa.Float(), nullable=False),
        sa.Column('tier', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('unit', sa.String(30), nullable=True),
        sa.Column('last_modified_by_sip_id', sa.Integer(),
                  sa.ForeignKey('system_improvement_proposals.id'), nullable=True),
        sa.Column('last_modified_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()),
    )

    # Parameter change log
    op.create_table(
        'parameter_change_log',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('parameter_key', sa.String(100), nullable=False),
        sa.Column('old_value', sa.Float(), nullable=False),
        sa.Column('new_value', sa.Float(), nullable=False),
        sa.Column('changed_by_sip_id', sa.Integer(),
                  sa.ForeignKey('system_improvement_proposals.id'), nullable=False),
        sa.Column('changed_at', sa.DateTime(), server_default=sa.func.now()),
        sa.Column('drift_direction', sa.String(10), nullable=False),
    )

    # SIP votes
    op.create_table(
        'sip_votes',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('sip_id', sa.Integer(),
                  sa.ForeignKey('system_improvement_proposals.id'), nullable=False),
        sa.Column('agent_id', sa.Integer(), sa.ForeignKey('agents.id'), nullable=False),
        sa.Column('agent_name', sa.String(100), nullable=False),
        sa.Column('vote', sa.String(10), nullable=False),
        sa.Column('vote_weight', sa.Float(), nullable=False, server_default='1.0'),
        sa.Column('agora_message_id', sa.Integer(), nullable=True),
        sa.Column('voted_at', sa.DateTime(), server_default=sa.func.now()),
        sa.UniqueConstraint('sip_id', 'agent_id', name='uq_sip_vote_agent'),
        sa.CheckConstraint("vote IN ('support', 'oppose', 'abstain')"),
    )

    # SIP debates
    op.create_table(
        'sip_debates',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('sip_id', sa.Integer(),
                  sa.ForeignKey('system_improvement_proposals.id'), nullable=False),
        sa.Column('agent_id', sa.Integer(), sa.ForeignKey('agents.id'), nullable=False),
        sa.Column('agent_name', sa.String(100), nullable=False),
        sa.Column('position', sa.String(10), nullable=False),
        sa.Column('argument', sa.Text(), nullable=False),
        sa.Column('agora_message_id', sa.Integer(), nullable=True),
        sa.Column('posted_at', sa.DateTime(), server_default=sa.func.now()),
        sa.CheckConstraint("position IN ('for', 'against', 'neutral')"),
    )

    # Add lifecycle columns to system_improvement_proposals
    cols = [
        ('lifecycle_status', sa.String(30), 'debate'),
        ('debate_ends_at', sa.DateTime(), None),
        ('voting_ends_at', sa.DateTime(), None),
        ('tallied_at', sa.DateTime(), None),
        ('genesis_reviewed_at', sa.DateTime(), None),
        ('implemented_at', sa.DateTime(), None),
        ('target_parameter_key', sa.String(100), None),
        ('proposed_value', sa.Float(), None),
        ('weighted_support', sa.Float(), '0.0'),
        ('weighted_oppose', sa.Float(), '0.0'),
        ('weighted_total_cast', sa.Float(), '0.0'),
        ('vote_pass_percentage', sa.Float(), None),
        ('parameter_tier', sa.Integer(), None),
        ('colony_maturity_at_proposal', sa.String(20), None),
        ('genesis_veto_used', sa.Boolean(), 'false'),
        ('cosponsor_agent_id', sa.Integer(), None),
    ]
    for name, col_type, default in cols:
        kw = {}
        if default is not None:
            kw['server_default'] = default
        op.execute(
            f"ALTER TABLE system_improvement_proposals "
            f"ADD COLUMN IF NOT EXISTS {name} "
            f"{col_type.compile(dialect=op.get_bind().dialect) if hasattr(col_type, 'compile') else col_type} "
            f"{'DEFAULT ' + repr(default) if default is not None else 'NULL'}"
        )


def downgrade() -> None:
    """Remove Phase 9A tables and columns."""
    op.drop_table('sip_debates')
    op.drop_table('sip_votes')
    op.drop_table('parameter_change_log')
    op.drop_table('parameter_registry')
    op.drop_table('colony_maturity')

    cols = [
        'lifecycle_status', 'debate_ends_at', 'voting_ends_at', 'tallied_at',
        'genesis_reviewed_at', 'implemented_at', 'target_parameter_key',
        'proposed_value', 'weighted_support', 'weighted_oppose',
        'weighted_total_cast', 'vote_pass_percentage', 'parameter_tier',
        'colony_maturity_at_proposal', 'genesis_veto_used', 'cosponsor_agent_id',
    ]
    for col in cols:
        op.drop_column('system_improvement_proposals', col)
