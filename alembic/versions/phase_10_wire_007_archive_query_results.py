"""Phase 10: archive_query_results queue table (subsystems F + G)

Revision ID: phase_10_wire_007
Revises: phase_10_wire_006
Create Date: 2026-05-04 00:00:00.000000

Closes WIRING_AUDIT_REPORT.md subsystems F (Strategist Archive helper)
and G (Critic Archive helper). Both helpers existed and were tested in
isolation but were NEVER constructed in production — Strategists and
Critics had no path to the Wire Archive.

This migration adds the queue table for the deep-dive `query_archive`
action's one-cycle-latency delivery. Producer: agent emits
`query_archive` → action handler invokes the helper → writes a row
with `status='pending'`. Consumer: ContextAssembler on the agent's
NEXT cycle reads pending rows for that agent_id, formats them into
priority context, marks 'delivered'. Same DB-as-queue shape as
subsystem H's regime-review consumption (with at-least-once and the
attempt_count cap).

Columns:
  - id (PK, BIGINT autoincrement — query results can grow large
    fast if archive use becomes routine)
  - requesting_agent_id (FK agents.id)
  - query_text (the agent's free-text query description, audit trail)
  - lookback_hours, max_results (action params)
  - result_payload (JSON; serialized ArchiveQueryResult.events list)
  - status ('pending' | 'delivered' | 'failed')
  - attempt_count (consumer-side retry counter, mirrors regime_review
    poison-pill cap)
  - last_error (TEXT, populated on per-row consumption failure)
  - requested_at, delivered_at

Indexes:
  - (requesting_agent_id, status) for the consumer's per-agent
    "fetch my pending" query
  - (status, requested_at) for any future audit/dashboard sweep
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "phase_10_wire_007"
down_revision: Union[str, Sequence[str], None] = "phase_10_wire_006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "archive_query_results",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "requesting_agent_id",
            sa.Integer(),
            sa.ForeignKey("agents.id"),
            nullable=False,
        ),
        sa.Column("query_text", sa.Text(), nullable=False),
        sa.Column(
            "lookback_hours",
            sa.Integer(),
            nullable=False,
            server_default="24",
        ),
        sa.Column(
            "max_results",
            sa.Integer(),
            nullable=False,
            server_default="10",
        ),
        sa.Column("result_payload", sa.JSON(), nullable=True),
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default="pending",
        ),
        sa.Column(
            "attempt_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "requested_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "delivered_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'delivered', 'failed')",
            name="ck_archive_query_results_status",
        ),
    )

    op.create_index(
        "ix_archive_query_results_agent_status",
        "archive_query_results",
        ["requesting_agent_id", "status"],
    )
    op.create_index(
        "ix_archive_query_results_status_requested",
        "archive_query_results",
        ["status", "requested_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_archive_query_results_status_requested",
        table_name="archive_query_results",
    )
    op.drop_index(
        "ix_archive_query_results_agent_status",
        table_name="archive_query_results",
    )
    op.drop_table("archive_query_results")
