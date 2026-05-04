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


# BACKFILL_WINDOW_MINUTES = 30
#
# Derivation: matches `DEFAULT_AUTO_EXPIRE_MINUTES` in
# `src/wire/integration/operator_halt.py`. Sev-5 events older than the
# operator-halt TTL are presumed stale — if the underlying condition
# is still active, the upstream producer (Wire scheduler) will re-emit
# a fresh sev-5 event and the new digester run will queue it for
# review. Older sev-5 events should NOT retroactively trigger regime
# detection during the catch-up phase of first deploy; that would
# corrupt regime detection with stale signals.
#
# If the operator-halt TTL changes, update this constant in lockstep
# (the two windows must stay aligned — same operational rationale).
# The constant is kept here in the migration rather than imported
# from operator_halt to keep migrations self-contained and avoid
# coupling schema to runtime modules whose contents may evolve.
BACKFILL_WINDOW_MINUTES = 30


def backfill_pending_status(bind, cutoff) -> None:
    """Idempotent backfill UPDATE.

    Called from `upgrade()` and from
    `tests/test_genesis_regime_review_consumption.py::
    test_backfill_migration_idempotent` so the production code path
    and the test exercise the SAME SQL — no reimplementation drift
    (Critic iteration 4 follow-up 2).

    IDEMPOTENT (Critic iteration 3 Finding 1): the WHERE clause
    restricts to rows still at the server-default `'skipped'`. Re-runs
    of `alembic upgrade head` (a common deploy-script idempotency
    pattern) will not re-flip rows that the consumer has already
    processed to 'reviewed' / 'failed' / consumed-and-still-'pending'.
    This also bounds time-skew hazards — a CI-time cutoff applied via
    a backup restore won't retroactively re-queue rows that have
    since been classified by Genesis.
    """
    bind.execute(
        sa.text(
            "UPDATE wire_events SET regime_review_status = 'pending' "
            "WHERE severity = 5 AND duplicate_of IS NULL "
            "AND occurred_at >= :cutoff "
            "AND regime_review_status = 'skipped'"
        ),
        {"cutoff": cutoff},
    )


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

    # 3. Backfill via the shared helper so tests exercise the exact
    # same SQL the migration runs (Critic iteration 4 follow-up 2 —
    # tests must NOT reimplement the SQL).
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=BACKFILL_WINDOW_MINUTES)
    backfill_pending_status(bind, cutoff)

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
