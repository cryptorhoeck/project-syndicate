"""Phase 10: wire_events.regime_review_status (subsystem H)

Revision ID: phase_10_wire_006
Revises: phase_10_wire_005
Create Date: 2026-05-04 00:00:00.000000

Closes WIRING_AUDIT_REPORT.md subsystem H. Severity-5 wire events fire
but no listener invoked Genesis regime review in production. Per War
Room iteration 1 directive on hotfix/genesis-regime-review-hook
(Option C): Postgres-as-queue is sufficient — the existing 5-minute
Genesis cycle latency is appropriate for regime review (halts handle
the immediate-action concern via fix I).

This migration adds the queue column. Producer (haiku_digester) sets
'pending' at INSERT for severity-5 events. Consumer (Genesis.run_cycle)
queries pending rows at top-of-cycle, marks 'reviewed' at end.

Column values:
  - 'skipped'  : default, non-severity-5 events
  - 'pending'  : severity-5 event awaiting Genesis consumption
  - 'reviewed' : Genesis consumed it during a cycle (terminal state)

Backfill: existing severity-5 rows are flipped to 'pending' so the
first cycle after deploy picks them up. Older Genesis cycles never
saw them (the receiver function did not exist), so flagging them
pending is the correct catch-up behavior. The bound on consumption
per cycle (50, set in `Genesis._consume_pending_regime_reviews`)
keeps catch-up from monopolising a cycle.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "phase_10_wire_006"
down_revision: Union[str, Sequence[str], None] = "phase_10_wire_005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()

    # 1. Add the column. server_default 'skipped' so the NOT NULL is
    # immediately satisfied for existing rows during the schema change.
    op.add_column(
        "wire_events",
        sa.Column(
            "regime_review_status",
            sa.String(length=16),
            nullable=False,
            server_default="skipped",
        ),
    )

    # 2. Backfill: severity-5 rows -> 'pending'. Genesis will consume
    # them on the first cycle after deploy.
    bind.execute(
        sa.text(
            "UPDATE wire_events SET regime_review_status = 'pending' "
            "WHERE severity = 5 AND duplicate_of IS NULL"
        )
    )

    # 3. Index for the consumption query
    # (regime_review_status='pending' ORDER BY severity DESC, occurred_at).
    op.create_index(
        "ix_wire_events_regime_review_status",
        "wire_events",
        ["regime_review_status", "severity"],
    )

    # 4. Constraint: status is one of the three allowed values. Mirrors
    # the wire_source_health.status check pattern.
    op.create_check_constraint(
        "ck_wire_events_regime_review_status",
        "wire_events",
        "regime_review_status IN ('pending','reviewed','skipped')",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_wire_events_regime_review_status", "wire_events", type_="check"
    )
    op.drop_index("ix_wire_events_regime_review_status", table_name="wire_events")
    op.drop_column("wire_events", "regime_review_status")
