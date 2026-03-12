"""Phase 2B: Library tables — library_entries, library_contributions, library_views, lineage updates

Revision ID: a7b3c1d2e3f4
Revises: fdc6e51f4c04
Create Date: 2026-03-12 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a7b3c1d2e3f4'
down_revision: str = 'fdc6e51f4c04'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # library_entries
    op.create_table(
        'library_entries',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('category', sa.String(20), nullable=False),
        sa.Column('title', sa.String(200), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('summary', sa.Text(), nullable=True),
        sa.Column('tags', sa.JSON(), nullable=True, server_default='[]'),
        sa.Column('source_agent_id', sa.Integer(), sa.ForeignKey('agents.id'), nullable=True),
        sa.Column('source_agent_name', sa.String(100), nullable=True),
        sa.Column('market_regime_at_creation', sa.String(20), nullable=True),
        sa.Column('related_evaluation_id', sa.Integer(), sa.ForeignKey('evaluations.id'), nullable=True),
        sa.Column('publish_after', sa.DateTime(), nullable=True),
        sa.Column('is_published', sa.Boolean(), server_default='false', nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column('published_at', sa.DateTime(), nullable=True),
        sa.Column('view_count', sa.Integer(), server_default='0', nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )

    # library_contributions
    op.create_table(
        'library_contributions',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('submitter_agent_id', sa.Integer(), sa.ForeignKey('agents.id'), nullable=False),
        sa.Column('submitter_agent_name', sa.String(100), nullable=False),
        sa.Column('title', sa.String(200), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('category', sa.String(20), server_default='contribution', nullable=False),
        sa.Column('tags', sa.JSON(), nullable=True, server_default='[]'),
        sa.Column('status', sa.String(20), server_default='pending_review', nullable=False),
        sa.Column('reviewer_1_id', sa.Integer(), sa.ForeignKey('agents.id'), nullable=True),
        sa.Column('reviewer_1_name', sa.String(100), nullable=True),
        sa.Column('reviewer_1_decision', sa.String(20), nullable=True),
        sa.Column('reviewer_1_reasoning', sa.Text(), nullable=True),
        sa.Column('reviewer_1_completed_at', sa.DateTime(), nullable=True),
        sa.Column('reviewer_2_id', sa.Integer(), sa.ForeignKey('agents.id'), nullable=True),
        sa.Column('reviewer_2_name', sa.String(100), nullable=True),
        sa.Column('reviewer_2_decision', sa.String(20), nullable=True),
        sa.Column('reviewer_2_reasoning', sa.Text(), nullable=True),
        sa.Column('reviewer_2_completed_at', sa.DateTime(), nullable=True),
        sa.Column('final_decision', sa.String(20), nullable=True),
        sa.Column('final_decision_by', sa.String(20), nullable=True),
        sa.Column('genesis_reasoning', sa.Text(), nullable=True),
        sa.Column('reputation_effects_applied', sa.Boolean(), server_default='false', nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column('resolved_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )

    # library_views
    op.create_table(
        'library_views',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('entry_id', sa.Integer(), sa.ForeignKey('library_entries.id'), nullable=False),
        sa.Column('agent_id', sa.Integer(), sa.ForeignKey('agents.id'), nullable=False),
        sa.Column('viewed_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('entry_id', 'agent_id', name='uq_library_view_entry_agent'),
    )

    # Add mentor columns to lineage table
    op.add_column('lineage', sa.Column('mentor_package_json', sa.Text(), nullable=True))
    op.add_column('lineage', sa.Column('mentor_package_generated_at', sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column('lineage', 'mentor_package_generated_at')
    op.drop_column('lineage', 'mentor_package_json')
    op.drop_table('library_views')
    op.drop_table('library_contributions')
    op.drop_table('library_entries')
