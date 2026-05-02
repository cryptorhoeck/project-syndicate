"""Phase 10: The Wire — schema (6 tables)

Revision ID: phase_10_wire_001
Revises: e5f6a7b8c9d0
Create Date: 2026-05-01 00:00:00.000000

Creates wire_sources, wire_raw_items, wire_events, wire_source_health,
wire_query_log, wire_treasury_ledger.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "phase_10_wire_001"
down_revision: Union[str, Sequence[str], None] = "e5f6a7b8c9d0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # wire_sources
    # ------------------------------------------------------------------
    op.create_table(
        "wire_sources",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(64), nullable=False, unique=True),
        sa.Column("display_name", sa.String(128), nullable=False),
        sa.Column("tier", sa.String(1), nullable=False),
        sa.Column("fetch_interval_seconds", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("requires_api_key", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("api_key_env_var", sa.String(64), nullable=True),
        sa.Column("base_url", sa.Text(), nullable=False),
        sa.Column("config_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.CheckConstraint("tier IN ('A','B','C')", name="ck_wire_sources_tier"),
    )

    # ------------------------------------------------------------------
    # wire_raw_items
    # ------------------------------------------------------------------
    op.create_table(
        "wire_raw_items",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("source_id", sa.Integer(), sa.ForeignKey("wire_sources.id"), nullable=False),
        sa.Column("external_id", sa.String(256), nullable=False),
        sa.Column(
            "fetched_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("raw_payload", sa.JSON(), nullable=False),
        sa.Column(
            "digestion_status",
            sa.String(16),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("digestion_attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.UniqueConstraint("source_id", "external_id", name="uq_wire_raw_items_source_external"),
        sa.CheckConstraint(
            "digestion_status IN ('pending','digested','rejected','dead_letter')",
            name="ck_wire_raw_items_digestion_status",
        ),
    )
    op.create_index(
        "ix_wire_raw_items_status",
        "wire_raw_items",
        ["digestion_status", "fetched_at"],
    )
    op.create_index(
        "ix_wire_raw_items_source_fetched",
        "wire_raw_items",
        ["source_id", "fetched_at"],
    )

    # ------------------------------------------------------------------
    # wire_events
    # ------------------------------------------------------------------
    op.create_table(
        "wire_events",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "raw_item_id",
            sa.BigInteger(),
            sa.ForeignKey("wire_raw_items.id"),
            nullable=True,
        ),
        sa.Column("canonical_hash", sa.String(64), nullable=False),
        sa.Column("coin", sa.String(32), nullable=True),
        sa.Column("is_macro", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("severity", sa.SmallInteger(), nullable=False),
        sa.Column("direction", sa.String(16), nullable=True),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column(
            "digested_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("haiku_cost_usd", sa.Numeric(10, 6), nullable=False, server_default="0"),
        sa.Column(
            "duplicate_of",
            sa.BigInteger(),
            sa.ForeignKey("wire_events.id"),
            nullable=True,
        ),
        sa.Column(
            "published_to_ticker",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.CheckConstraint("severity BETWEEN 1 AND 5", name="ck_wire_events_severity"),
        sa.CheckConstraint(
            "direction IN ('bullish','bearish','neutral')",
            name="ck_wire_events_direction",
        ),
    )
    op.create_index(
        "ix_wire_events_coin_severity",
        "wire_events",
        ["coin", "severity", "occurred_at"],
    )
    op.create_index(
        "ix_wire_events_severity_recent",
        "wire_events",
        ["severity", "occurred_at"],
    )
    op.create_index("ix_wire_events_canonical", "wire_events", ["canonical_hash"])
    op.create_index("ix_wire_events_macro", "wire_events", ["is_macro", "occurred_at"])

    # ------------------------------------------------------------------
    # wire_source_health
    # ------------------------------------------------------------------
    op.create_table(
        "wire_source_health",
        sa.Column(
            "source_id",
            sa.Integer(),
            sa.ForeignKey("wire_sources.id"),
            primary_key=True,
        ),
        sa.Column("last_fetch_attempt", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_fetch_success", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_fetch_error", sa.Text(), nullable=True),
        sa.Column("consecutive_failures", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("items_last_24h", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "status",
            sa.String(16),
            nullable=False,
            server_default="unknown",
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "status IN ('healthy','degraded','failing','disabled','unknown')",
            name="ck_wire_source_health_status",
        ),
    )

    # ------------------------------------------------------------------
    # wire_query_log
    # ------------------------------------------------------------------
    op.create_table(
        "wire_query_log",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("agent_id", sa.Integer(), sa.ForeignKey("agents.id"), nullable=False),
        sa.Column("query_params", sa.JSON(), nullable=False),
        sa.Column("results_count", sa.Integer(), nullable=False),
        sa.Column("token_cost", sa.Integer(), nullable=False),
        sa.Column(
            "queried_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_wire_query_log_agent_time",
        "wire_query_log",
        ["agent_id", "queried_at"],
    )

    # ------------------------------------------------------------------
    # wire_treasury_ledger
    # ------------------------------------------------------------------
    op.create_table(
        "wire_treasury_ledger",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("cost_category", sa.String(32), nullable=False),
        sa.Column("cost_usd", sa.Numeric(10, 6), nullable=False),
        sa.Column(
            "related_event_id",
            sa.BigInteger(),
            sa.ForeignKey("wire_events.id"),
            nullable=True,
        ),
        sa.Column(
            "incurred_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_wire_treasury_ledger_time",
        "wire_treasury_ledger",
        ["incurred_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_wire_treasury_ledger_time", table_name="wire_treasury_ledger")
    op.drop_table("wire_treasury_ledger")
    op.drop_index("ix_wire_query_log_agent_time", table_name="wire_query_log")
    op.drop_table("wire_query_log")
    op.drop_table("wire_source_health")
    op.drop_index("ix_wire_events_macro", table_name="wire_events")
    op.drop_index("ix_wire_events_canonical", table_name="wire_events")
    op.drop_index("ix_wire_events_severity_recent", table_name="wire_events")
    op.drop_index("ix_wire_events_coin_severity", table_name="wire_events")
    op.drop_table("wire_events")
    op.drop_index("ix_wire_raw_items_source_fetched", table_name="wire_raw_items")
    op.drop_index("ix_wire_raw_items_status", table_name="wire_raw_items")
    op.drop_table("wire_raw_items")
    op.drop_table("wire_sources")
