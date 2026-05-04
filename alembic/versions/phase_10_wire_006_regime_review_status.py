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

This migration adds the queue columns. Producer (haiku_digester) sets
'pending' at INSERT for severity-5 events. Consumer (Genesis.run_cycle)
queries pending rows at top-of-cycle, marks 'reviewed' at end-of-cycle.

Columns:
  - regime_review_status (VARCHAR(16) NOT NULL DEFAULT 'skipped')
      values: 'skipped' | 'pending' | 'reviewed' | 'failed'
  - attempt_count (INTEGER NOT NULL DEFAULT 0)
      incremented before each consumption attempt; cap at 3
      (poison-pill guard, Critic iteration 2 Finding 1)
  - last_error (TEXT NULL)
      populated when run_cycle's top-level except writes to all rows
      consumed in the failing cycle; preserved when the cap fires and
      the row is flipped to 'failed'

Backfill rule (Critic iteration 2 Finding 3): only sev-5 rows
occurring within the last 30 minutes are flipped to 'pending'. Older
sev-5 events are left 'skipped' to prevent stale-event replay
corrupting regime detection on first deploy. The 30-minute window
matches the operator-halt auto-expiry TTL — genuinely active sev-5
conditions will get re-published by their producers; historical
events stay historical.
"""
from datetime import datetime, timedelta, timezone
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "phase_10_wire_006"
down_revision: Union[str, Sequence[str], None] = "phase_10_wire_005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# 30 minutes — matches operator-halt auto-expire TTL. Centralized so the
# backfill cutoff and any future tooling agree on the boundary.
BACKFILL_WINDOW_MINUTES = 30


def upgrade() -> None:
    bind = op.get_bind()

    # 1. Add the status column. server_default 'skipped' immediately
    # satisfies the NOT NULL constraint for existing rows.
    op.add_column(
        "wire_events",
        sa.Column(
            "regime_review_status",
            sa.String(length=16),
            nullable=False,
            server_default="skipped",
        ),
    )

    # 2. Retry cap columns (Finding 1).
    op.add_column(
        "wire_events",
        sa.Column(
            "attempt_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "wire_events",
        sa.Column("last_error", sa.Text(), nullable=True),
    )

    # 3. Backfill: severity-5 rows in the last 30 minutes -> 'pending'.
    # Older rows stay 'skipped' (server_default). Genuinely active
    # conditions will re-publish; historical events stay historical.
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=BACKFILL_WINDOW_MINUTES)
    bind.execute(
        sa.text(
            "UPDATE wire_events SET regime_review_status = 'pending' "
            "WHERE severity = 5 AND duplicate_of IS NULL "
            "AND occurred_at >= :cutoff"
        ),
        {"cutoff": cutoff},
    )

    # 4. Index for the consumption query
    # (regime_review_status='pending' ORDER BY severity DESC, occurred_at).
    op.create_index(
        "ix_wire_events_regime_review_status",
        "wire_events",
        ["regime_review_status", "severity"],
    )

    # 5. Constraint: status is one of the four allowed values. 'failed'
    # is the terminal state when attempt_count exceeds the retry cap.
    op.create_check_constraint(
        "ck_wire_events_regime_review_status",
        "wire_events",
        "regime_review_status IN ('pending','reviewed','skipped','failed')",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_wire_events_regime_review_status", "wire_events", type_="check"
    )
    op.drop_index("ix_wire_events_regime_review_status", table_name="wire_events")
    op.drop_column("wire_events", "last_error")
    op.drop_column("wire_events", "attempt_count")
    op.drop_column("wire_events", "regime_review_status")
