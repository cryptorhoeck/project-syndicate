"""Weave Step 2b: tool_consult_results queue table (first-party tool round-trip)

Revision ID: weave_2b_001
Revises: phase_9b_tier_a_001
Create Date: 2026-06-28 00:00:00.000000

ADDITIVE-ONLY: one new table, zero ALTERs to existing tables (safest migration
class — no path to data loss). Revises the current single head, keeping the
Alembic chain linear.

Single-cycle round-trip queue for the `consult_tool` action (DB-as-queue, mirrors
archive_query_results / phase_10_wire_007). Producer writes status='pending';
ContextAssembler consumes on the agent's NEXT cycle, renders into context, and
marks 'delivered' (surfaced once, then gone). Rows are scoped to
requesting_agent_id; stale 'pending' and old 'delivered' rows are pruned by age
on the maintenance pass so a dead agent's request can never linger.

Indexes:
  - (requesting_agent_id, status): the consumer's "fetch my pending" query.
  - (status, requested_at): age-based prune of delivered rows AND expiry of
    stale pending rows.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "weave_2b_001"
down_revision: Union[str, Sequence[str], None] = "phase_9b_tier_a_001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "tool_consult_results",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "requesting_agent_id",
            sa.Integer(),
            sa.ForeignKey("agents.id"),
            nullable=False,
        ),
        sa.Column("tool_name", sa.String(length=64), nullable=False),
        sa.Column("market", sa.String(length=32), nullable=False),
        sa.Column("result_payload", sa.JSON(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "requested_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending', 'delivered', 'failed')",
            name="ck_tool_consult_results_status",
        ),
    )
    op.create_index(
        "ix_tool_consult_results_agent_status",
        "tool_consult_results",
        ["requesting_agent_id", "status"],
    )
    op.create_index(
        "ix_tool_consult_results_status_requested",
        "tool_consult_results",
        ["status", "requested_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_tool_consult_results_status_requested",
        table_name="tool_consult_results",
    )
    op.drop_index(
        "ix_tool_consult_results_agent_status",
        table_name="tool_consult_results",
    )
    op.drop_table("tool_consult_results")
