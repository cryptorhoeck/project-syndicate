"""
The Wire — SQLAlchemy ORM models.

Six tables:
    wire_sources          : static catalog (seeded by data migration)
    wire_raw_items        : raw payloads pre-digestion
    wire_events           : digested, structured, agent-consumable events
    wire_source_health    : per-source heartbeat
    wire_query_log        : Archive query audit (Tier 3)
    wire_treasury_ledger  : Genesis treasury spend on Wire infrastructure

All models share the project's existing Declarative Base from src.common.models
so Alembic autogen and the engine session work uniformly.
"""

from __future__ import annotations

__version__ = "0.1.0"

from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.common.models import Base

# SQLite cannot autoincrement plain BIGINT primary keys (autoincrement requires
# INTEGER PRIMARY KEY -> ROWID). On Postgres we want BIGINT for tables that may
# grow large. This variant gives us the right type per dialect.
BigIntPk = BigInteger().with_variant(Integer(), "sqlite")
BigIntFk = BigInteger().with_variant(Integer(), "sqlite")


# ---------------------------------------------------------------------------
# wire_sources
# ---------------------------------------------------------------------------

class WireSource(Base):
    """Static catalog of registered Wire sources."""
    __tablename__ = "wire_sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(128), nullable=False)
    tier: Mapped[str] = mapped_column(
        String(1),
        CheckConstraint("tier IN ('A','B','C')", name="ck_wire_sources_tier"),
        nullable=False,
    )
    fetch_interval_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    requires_api_key: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    api_key_env_var: Mapped[str | None] = mapped_column(String(64), nullable=True)
    base_url: Mapped[str] = mapped_column(Text, nullable=False)
    config_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    raw_items: Mapped[list["WireRawItem"]] = relationship(back_populates="source")
    health: Mapped["WireSourceHealth | None"] = relationship(
        back_populates="source", uselist=False
    )


# ---------------------------------------------------------------------------
# wire_raw_items
# ---------------------------------------------------------------------------

class WireRawItem(Base):
    """Raw items pulled from each source before digestion. Retain 30 days then prune."""
    __tablename__ = "wire_raw_items"
    __table_args__ = (
        UniqueConstraint("source_id", "external_id", name="uq_wire_raw_items_source_external"),
        Index("ix_wire_raw_items_status", "digestion_status", "fetched_at"),
        Index("ix_wire_raw_items_source_fetched", "source_id", "fetched_at"),
    )

    id: Mapped[int] = mapped_column(BigIntPk, primary_key=True, autoincrement=True)
    source_id: Mapped[int] = mapped_column(Integer, ForeignKey("wire_sources.id"), nullable=False)
    external_id: Mapped[str] = mapped_column(String(256), nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    occurred_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    raw_payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    digestion_status: Mapped[str] = mapped_column(
        String(16),
        CheckConstraint(
            "digestion_status IN ('pending','digested','rejected','dead_letter')",
            name="ck_wire_raw_items_digestion_status",
        ),
        nullable=False,
        default="pending",
        server_default="pending",
    )
    digestion_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )

    source: Mapped[WireSource] = relationship(back_populates="raw_items")
    events: Mapped[list["WireEvent"]] = relationship(back_populates="raw_item")


# ---------------------------------------------------------------------------
# wire_events
# ---------------------------------------------------------------------------

class WireEvent(Base):
    """Digested, structured, agent-consumable events. The Archive."""
    __tablename__ = "wire_events"
    __table_args__ = (
        CheckConstraint("severity BETWEEN 1 AND 5", name="ck_wire_events_severity"),
        CheckConstraint(
            "direction IN ('bullish','bearish','neutral')",
            name="ck_wire_events_direction",
        ),
        CheckConstraint(
            "regime_review_status IN ('pending','reviewed','skipped')",
            name="ck_wire_events_regime_review_status",
        ),
        Index("ix_wire_events_coin_severity", "coin", "severity", "occurred_at"),
        Index("ix_wire_events_severity_recent", "severity", "occurred_at"),
        Index("ix_wire_events_canonical", "canonical_hash"),
        Index("ix_wire_events_macro", "is_macro", "occurred_at"),
        Index("ix_wire_events_regime_review_status", "regime_review_status", "severity"),
    )

    id: Mapped[int] = mapped_column(BigIntPk, primary_key=True, autoincrement=True)
    raw_item_id: Mapped[int | None] = mapped_column(
        BigIntFk, ForeignKey("wire_raw_items.id"), nullable=True
    )
    canonical_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    coin: Mapped[str | None] = mapped_column(String(32), nullable=True)
    is_macro: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    severity: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    direction: Mapped[str | None] = mapped_column(String(16), nullable=True)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    digested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    haiku_cost_usd: Mapped[float] = mapped_column(
        Numeric(10, 6), nullable=False, default=0, server_default="0"
    )
    duplicate_of: Mapped[int | None] = mapped_column(
        BigIntFk, ForeignKey("wire_events.id"), nullable=True
    )
    published_to_ticker: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    # Genesis regime-review queue marker (subsystem H, Option C). Sev-5
    # events get 'pending' at INSERT; Genesis.run_cycle() consumes them
    # at top-of-cycle and flips to 'reviewed' at end-of-cycle. Non-sev-5
    # events default to 'skipped' and are never re-touched.
    regime_review_status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="skipped", server_default="skipped"
    )

    raw_item: Mapped[WireRawItem | None] = relationship(back_populates="events")


# ---------------------------------------------------------------------------
# wire_source_health
# ---------------------------------------------------------------------------

class WireSourceHealth(Base):
    """Per-source heartbeat. One row per source, updated each cycle."""
    __tablename__ = "wire_source_health"
    __table_args__ = (
        CheckConstraint(
            "status IN ('healthy','degraded','failing','disabled','unknown')",
            name="ck_wire_source_health_status",
        ),
    )

    source_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("wire_sources.id"), primary_key=True
    )
    last_fetch_attempt: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_fetch_success: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_fetch_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    consecutive_failures: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    items_last_24h: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="unknown", server_default="unknown"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    source: Mapped[WireSource] = relationship(back_populates="health")


# ---------------------------------------------------------------------------
# wire_query_log
# ---------------------------------------------------------------------------

class WireQueryLog(Base):
    """Tracks Archive queries by agents. Used for cost accounting and abuse detection."""
    __tablename__ = "wire_query_log"
    __table_args__ = (
        Index("ix_wire_query_log_agent_time", "agent_id", "queried_at"),
    )

    id: Mapped[int] = mapped_column(BigIntPk, primary_key=True, autoincrement=True)
    agent_id: Mapped[int] = mapped_column(Integer, ForeignKey("agents.id"), nullable=False)
    query_params: Mapped[dict] = mapped_column(JSON, nullable=False)
    results_count: Mapped[int] = mapped_column(Integer, nullable=False)
    token_cost: Mapped[int] = mapped_column(Integer, nullable=False)
    queried_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


# ---------------------------------------------------------------------------
# wire_treasury_ledger
# ---------------------------------------------------------------------------

class WireTreasuryLedger(Base):
    """Tracks Genesis treasury spend on Wire infrastructure. Separate from agent thinking tax."""
    __tablename__ = "wire_treasury_ledger"
    __table_args__ = (
        Index("ix_wire_treasury_ledger_time", "incurred_at"),
    )

    id: Mapped[int] = mapped_column(BigIntPk, primary_key=True, autoincrement=True)
    cost_category: Mapped[str] = mapped_column(String(32), nullable=False)
    cost_usd: Mapped[float] = mapped_column(Numeric(10, 6), nullable=False)
    related_event_id: Mapped[int | None] = mapped_column(
        BigIntFk, ForeignKey("wire_events.id"), nullable=True
    )
    incurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
