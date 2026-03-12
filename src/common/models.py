"""
Project Syndicate — SQLAlchemy ORM Models

Defines the core database schema for the autonomous AI trading syndicate:
agents, transactions, messages (the Agora), evaluations, reputation,
Syndicate Improvement Proposals (SIPs), system state, lineage tracking,
inherited positions, market regimes, and daily reports.
"""

__version__ = "0.7.0"

import os
from datetime import date, datetime

from dotenv import load_dotenv
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    func,
)
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    mapped_column,
    relationship,
)

# ---------------------------------------------------------------------------
# Database URL configuration
# ---------------------------------------------------------------------------
load_dotenv()

DATABASE_URL: str = os.getenv(
    "DATABASE_URL",
    "postgresql://postgres@localhost:5432/syndicate",
)

# ---------------------------------------------------------------------------
# Declarative Base
# ---------------------------------------------------------------------------

class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------

class Agent(Base):
    __tablename__ = "agents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    type: Mapped[str] = mapped_column(String(50), nullable=False)  # genesis, scout, strategist, critic, operator
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="initializing")
    parent_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("agents.id"), nullable=True)
    generation: Mapped[int] = mapped_column(Integer, default=0)
    capital_allocated: Mapped[float] = mapped_column(Float, default=0.0)
    capital_current: Mapped[float] = mapped_column(Float, default=0.0)
    reputation_score: Mapped[float] = mapped_column(Float, default=100.0)
    prestige_title: Mapped[str | None] = mapped_column(String(50), nullable=True)
    survival_clock_start: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    survival_clock_end: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    survival_clock_paused: Mapped[bool] = mapped_column(Boolean, default=False)
    thinking_budget_daily: Mapped[float] = mapped_column(Float, default=1.0)  # dollars
    thinking_budget_used_today: Mapped[float] = mapped_column(Float, default=0.0)
    api_cost_total: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    terminated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    termination_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    strategy_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    config_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # Phase 1 additions
    composite_score: Mapped[float] = mapped_column(Float, default=0.0)
    hibernation_start: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    hibernation_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    total_api_cost: Mapped[float] = mapped_column(Float, default=0.0)
    total_gross_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    total_true_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    evaluation_count: Mapped[int] = mapped_column(Integer, default=0)
    profitable_evaluations: Mapped[int] = mapped_column(Integer, default=0)

    # Phase 3A additions — thinking cycle stats
    cycle_count: Mapped[int] = mapped_column(Integer, default=0)
    last_cycle_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    avg_cycle_cost: Mapped[float] = mapped_column(Float, default=0.0)
    avg_cycle_tokens: Mapped[int] = mapped_column(Integer, default=0)
    idle_rate: Mapped[float] = mapped_column(Float, default=0.0)
    validation_fail_rate: Mapped[float] = mapped_column(Float, default=0.0)
    warden_violation_count: Mapped[int] = mapped_column(Integer, default=0)
    current_context_mode: Mapped[str] = mapped_column(String(20), default="normal")
    api_temperature: Mapped[float | None] = mapped_column(Float, nullable=True)
    watched_markets: Mapped[dict | None] = mapped_column(JSON, default=list)

    # Relationships
    parent: Mapped["Agent | None"] = relationship(
        "Agent",
        remote_side=[id],
        back_populates="children",
    )
    children: Mapped[list["Agent"]] = relationship(
        "Agent",
        back_populates="parent",
    )
    transactions: Mapped[list["Transaction"]] = relationship(
        "Transaction",
        back_populates="agent",
    )
    messages: Mapped[list["Message"]] = relationship(
        "Message",
        back_populates="agent",
    )
    evaluations: Mapped[list["Evaluation"]] = relationship(
        "Evaluation",
        back_populates="agent",
    )

    def __repr__(self) -> str:
        return f"<Agent(id={self.id}, name={self.name!r}, type={self.type!r}, status={self.status!r})>"


# ---------------------------------------------------------------------------
# Transaction
# ---------------------------------------------------------------------------

class Transaction(Base):
    __tablename__ = "transactions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    agent_id: Mapped[int] = mapped_column(Integer, ForeignKey("agents.id"), nullable=False)
    type: Mapped[str] = mapped_column(String(20), nullable=False)  # spot, futures, defi, transfer, api_cost
    exchange: Mapped[str | None] = mapped_column(String(50), nullable=True)
    symbol: Mapped[str | None] = mapped_column(String(20), nullable=True)
    side: Mapped[str | None] = mapped_column(String(10), nullable=True)  # buy, sell
    amount: Mapped[float] = mapped_column(Float, nullable=False)
    price: Mapped[float | None] = mapped_column(Float, nullable=True)
    fee: Mapped[float] = mapped_column(Float, default=0.0)
    pnl: Mapped[float] = mapped_column(Float, default=0.0)
    timestamp: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    # Relationships
    agent: Mapped["Agent"] = relationship("Agent", back_populates="transactions")

    def __repr__(self) -> str:
        return f"<Transaction(id={self.id}, agent_id={self.agent_id}, type={self.type!r}, symbol={self.symbol!r})>"


# ---------------------------------------------------------------------------
# Message — The Agora
# ---------------------------------------------------------------------------

class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    agent_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("agents.id"), nullable=True)
    channel: Mapped[str] = mapped_column(String(50), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    metadata_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    # Phase 2A additions
    message_type: Mapped[str] = mapped_column(String(20), default="chat")  # thought, proposal, signal, alert, chat, system, evaluation, trade, economy
    agent_name: Mapped[str | None] = mapped_column(String(100), nullable=True)  # denormalized for fast display
    parent_message_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("messages.id"), nullable=True)
    importance: Mapped[int] = mapped_column(Integer, default=0)  # 0=normal, 1=important, 2=critical
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # Relationships
    agent: Mapped["Agent | None"] = relationship("Agent", back_populates="messages")
    parent_message: Mapped["Message | None"] = relationship("Message", remote_side=[id])

    def __repr__(self) -> str:
        return f"<Message(id={self.id}, channel={self.channel!r}, type={self.message_type!r}, agent_id={self.agent_id})>"


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------

class Evaluation(Base):
    __tablename__ = "evaluations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    agent_id: Mapped[int] = mapped_column(Integer, ForeignKey("agents.id"), nullable=False)
    evaluation_type: Mapped[str] = mapped_column(String(30), nullable=False)  # survival_check, weekly_review, tournament
    pnl_gross: Mapped[float] = mapped_column(Float, default=0.0)
    pnl_net: Mapped[float] = mapped_column(Float, default=0.0)
    api_cost: Mapped[float] = mapped_column(Float, default=0.0)
    sharpe_ratio: Mapped[float | None] = mapped_column(Float, nullable=True)
    reputation_change: Mapped[float] = mapped_column(Float, default=0.0)
    result: Mapped[str | None] = mapped_column(String(20), nullable=True)  # survived, terminated, promoted, hibernated
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    # Relationships
    agent: Mapped["Agent"] = relationship("Agent", back_populates="evaluations")

    def __repr__(self) -> str:
        return f"<Evaluation(id={self.id}, agent_id={self.agent_id}, type={self.evaluation_type!r}, result={self.result!r})>"


# ---------------------------------------------------------------------------
# ReputationTransaction
# ---------------------------------------------------------------------------

class ReputationTransaction(Base):
    __tablename__ = "reputation_transactions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    from_agent_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("agents.id"), nullable=True)
    to_agent_id: Mapped[int] = mapped_column(Integer, ForeignKey("agents.id"), nullable=False)
    amount: Mapped[float] = mapped_column(Float, nullable=False)
    reason: Mapped[str | None] = mapped_column(String(200), nullable=True)
    related_trade_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("transactions.id"), nullable=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    # Relationships
    from_agent: Mapped["Agent | None"] = relationship("Agent", foreign_keys=[from_agent_id])
    to_agent: Mapped["Agent"] = relationship("Agent", foreign_keys=[to_agent_id])
    related_trade: Mapped["Transaction | None"] = relationship("Transaction", foreign_keys=[related_trade_id])

    def __repr__(self) -> str:
        return f"<ReputationTransaction(id={self.id}, from={self.from_agent_id}, to={self.to_agent_id}, amount={self.amount})>"


# ---------------------------------------------------------------------------
# SIP — Syndicate Improvement Proposal
# ---------------------------------------------------------------------------

class SIP(Base):
    __tablename__ = "sips"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    proposing_agent_id: Mapped[int] = mapped_column(Integer, ForeignKey("agents.id"), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="proposed")  # proposed, voting, approved, rejected, implemented
    votes_for: Mapped[int] = mapped_column(Integer, default=0)
    votes_against: Mapped[int] = mapped_column(Integer, default=0)
    owner_decision: Mapped[str | None] = mapped_column(String(20), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # Relationships
    proposing_agent: Mapped["Agent"] = relationship("Agent", foreign_keys=[proposing_agent_id])

    def __repr__(self) -> str:
        return f"<SIP(id={self.id}, title={self.title!r}, status={self.status!r})>"


# ---------------------------------------------------------------------------
# SystemState
# ---------------------------------------------------------------------------

class SystemState(Base):
    __tablename__ = "system_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    total_treasury: Mapped[float] = mapped_column(Float, default=0.0)
    peak_treasury: Mapped[float] = mapped_column(Float, default=0.0)
    current_regime: Mapped[str] = mapped_column(String(20), default="unknown")  # bull, bear, crab, volatile, unknown
    active_agent_count: Mapped[int] = mapped_column(Integer, default=0)
    alert_status: Mapped[str] = mapped_column(String(20), default="green")  # green, yellow, red, circuit_breaker
    last_backup_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    def __repr__(self) -> str:
        return f"<SystemState(id={self.id}, treasury={self.total_treasury}, regime={self.current_regime!r})>"


# ---------------------------------------------------------------------------
# Lineage
# ---------------------------------------------------------------------------

class Lineage(Base):
    __tablename__ = "lineage"

    agent_id: Mapped[int] = mapped_column(Integer, ForeignKey("agents.id"), primary_key=True)
    parent_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("agents.id"), nullable=True)
    generation: Mapped[int] = mapped_column(Integer, default=0)
    lineage_path: Mapped[str | None] = mapped_column(String(500), nullable=True)  # e.g. "1/3/7/15"
    strategy_heritage_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # Relationships
    agent: Mapped["Agent"] = relationship("Agent", foreign_keys=[agent_id])
    parent: Mapped["Agent | None"] = relationship("Agent", foreign_keys=[parent_id])

    # Phase 2B additions
    mentor_package_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    mentor_package_generated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    def __repr__(self) -> str:
        return f"<Lineage(agent_id={self.agent_id}, generation={self.generation}, path={self.lineage_path!r})>"


# ---------------------------------------------------------------------------
# InheritedPosition — Phase 1
# ---------------------------------------------------------------------------

class InheritedPosition(Base):
    __tablename__ = "inherited_positions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    original_agent_id: Mapped[int] = mapped_column(Integer, ForeignKey("agents.id"), nullable=False)
    inherited_by: Mapped[str] = mapped_column(String(50), nullable=False)  # 'genesis' or agent_id
    exchange: Mapped[str] = mapped_column(String(50), nullable=False)
    symbol: Mapped[str] = mapped_column(String(20), nullable=False)
    side: Mapped[str] = mapped_column(String(10), nullable=False)  # long, short
    amount: Mapped[float] = mapped_column(Float, nullable=False)
    entry_price: Mapped[float] = mapped_column(Float, nullable=False)
    inherited_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    closed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    close_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    pnl: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="open")  # open, closed

    # Relationships
    original_agent: Mapped["Agent"] = relationship("Agent", foreign_keys=[original_agent_id])

    def __repr__(self) -> str:
        return f"<InheritedPosition(id={self.id}, symbol={self.symbol!r}, status={self.status!r})>"


# ---------------------------------------------------------------------------
# MarketRegime — Phase 1
# ---------------------------------------------------------------------------

class MarketRegime(Base):
    __tablename__ = "market_regimes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    regime: Mapped[str] = mapped_column(String(20), nullable=False)  # bull, bear, crab, volatile
    btc_price: Mapped[float] = mapped_column(Float, nullable=False)
    btc_ma_20: Mapped[float] = mapped_column(Float, nullable=False)
    btc_ma_50: Mapped[float] = mapped_column(Float, nullable=False)
    btc_volatility_30d: Mapped[float] = mapped_column(Float, nullable=False)
    btc_dominance: Mapped[float] = mapped_column(Float, nullable=False)
    total_market_cap: Mapped[float] = mapped_column(Float, nullable=False)
    detected_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    def __repr__(self) -> str:
        return f"<MarketRegime(id={self.id}, regime={self.regime!r}, btc_price={self.btc_price})>"


# ---------------------------------------------------------------------------
# DailyReport — Phase 1
# ---------------------------------------------------------------------------

class DailyReport(Base):
    __tablename__ = "daily_reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    report_date: Mapped[date] = mapped_column(Date, unique=True, nullable=False)
    treasury_balance: Mapped[float] = mapped_column(Float, nullable=False)
    treasury_change_24h: Mapped[float] = mapped_column(Float, nullable=False)
    active_agents: Mapped[int] = mapped_column(Integer, nullable=False)
    agents_born: Mapped[int] = mapped_column(Integer, default=0)
    agents_died: Mapped[int] = mapped_column(Integer, default=0)
    agents_hibernating: Mapped[int] = mapped_column(Integer, default=0)
    top_performer_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("agents.id"), nullable=True)
    worst_performer_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("agents.id"), nullable=True)
    market_regime: Mapped[str] = mapped_column(String(20), nullable=False)
    alert_status: Mapped[str] = mapped_column(String(20), nullable=False)  # green, yellow, red, circuit_breaker
    total_api_cost_24h: Mapped[float] = mapped_column(Float, default=0.0)
    report_content: Mapped[str] = mapped_column(Text, nullable=False)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # Relationships
    top_performer: Mapped["Agent | None"] = relationship("Agent", foreign_keys=[top_performer_id])
    worst_performer: Mapped["Agent | None"] = relationship("Agent", foreign_keys=[worst_performer_id])

    def __repr__(self) -> str:
        return f"<DailyReport(id={self.id}, date={self.report_date}, regime={self.market_regime!r})>"


# ---------------------------------------------------------------------------
# AgoraChannel — Phase 2A
# ---------------------------------------------------------------------------

class AgoraChannel(Base):
    __tablename__ = "agora_channels"

    name: Mapped[str] = mapped_column(String(50), primary_key=True)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    is_system: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    message_count: Mapped[int] = mapped_column(Integer, default=0)

    def __repr__(self) -> str:
        return f"<AgoraChannel(name={self.name!r}, system={self.is_system}, messages={self.message_count})>"


# ---------------------------------------------------------------------------
# AgoraReadReceipt — Phase 2A
# ---------------------------------------------------------------------------

class AgoraReadReceipt(Base):
    __tablename__ = "agora_read_receipts"
    __table_args__ = (
        UniqueConstraint("agent_id", "channel", name="uq_read_receipt_agent_channel"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    agent_id: Mapped[int] = mapped_column(Integer, ForeignKey("agents.id"), nullable=False)
    channel: Mapped[str] = mapped_column(String(50), nullable=False)
    last_read_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
    last_read_message_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("messages.id"), nullable=True)

    # Relationships
    agent: Mapped["Agent"] = relationship("Agent", foreign_keys=[agent_id])
    last_read_message: Mapped["Message | None"] = relationship("Message", foreign_keys=[last_read_message_id])

    def __repr__(self) -> str:
        return f"<AgoraReadReceipt(agent_id={self.agent_id}, channel={self.channel!r})>"


# ---------------------------------------------------------------------------
# LibraryEntry — Phase 2B
# ---------------------------------------------------------------------------

class LibraryEntry(Base):
    __tablename__ = "library_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    category: Mapped[str] = mapped_column(String(20), nullable=False)  # textbook, post_mortem, strategy_record, pattern, contribution
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    tags: Mapped[dict | None] = mapped_column(JSON, default=list)
    source_agent_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("agents.id"), nullable=True)
    source_agent_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    market_regime_at_creation: Mapped[str | None] = mapped_column(String(20), nullable=True)
    related_evaluation_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("evaluations.id"), nullable=True)
    publish_after: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    is_published: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    view_count: Mapped[int] = mapped_column(Integer, default=0)

    # Relationships
    source_agent: Mapped["Agent | None"] = relationship("Agent", foreign_keys=[source_agent_id])
    related_evaluation: Mapped["Evaluation | None"] = relationship("Evaluation", foreign_keys=[related_evaluation_id])

    def __repr__(self) -> str:
        return f"<LibraryEntry(id={self.id}, category={self.category!r}, title={self.title!r})>"


# ---------------------------------------------------------------------------
# LibraryContribution — Phase 2B
# ---------------------------------------------------------------------------

class LibraryContribution(Base):
    __tablename__ = "library_contributions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    submitter_agent_id: Mapped[int] = mapped_column(Integer, ForeignKey("agents.id"), nullable=False)
    submitter_agent_name: Mapped[str] = mapped_column(String(100), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(String(20), default="contribution")
    tags: Mapped[dict | None] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(20), default="pending_review")  # pending_review, in_review, approved, rejected, needs_revision
    reviewer_1_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("agents.id"), nullable=True)
    reviewer_1_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    reviewer_1_decision: Mapped[str | None] = mapped_column(String(20), nullable=True)
    reviewer_1_reasoning: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewer_1_completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    reviewer_2_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("agents.id"), nullable=True)
    reviewer_2_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    reviewer_2_decision: Mapped[str | None] = mapped_column(String(20), nullable=True)
    reviewer_2_reasoning: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewer_2_completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    final_decision: Mapped[str | None] = mapped_column(String(20), nullable=True)
    final_decision_by: Mapped[str | None] = mapped_column(String(20), nullable=True)  # consensus, genesis_tiebreaker, genesis_solo
    genesis_reasoning: Mapped[str | None] = mapped_column(Text, nullable=True)
    reputation_effects_applied: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # Relationships
    submitter: Mapped["Agent"] = relationship("Agent", foreign_keys=[submitter_agent_id])
    reviewer_1: Mapped["Agent | None"] = relationship("Agent", foreign_keys=[reviewer_1_id])
    reviewer_2: Mapped["Agent | None"] = relationship("Agent", foreign_keys=[reviewer_2_id])

    def __repr__(self) -> str:
        return f"<LibraryContribution(id={self.id}, title={self.title!r}, status={self.status!r})>"


# ---------------------------------------------------------------------------
# LibraryView — Phase 2B
# ---------------------------------------------------------------------------

class LibraryView(Base):
    __tablename__ = "library_views"
    __table_args__ = (
        UniqueConstraint("entry_id", "agent_id", name="uq_library_view_entry_agent"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    entry_id: Mapped[int] = mapped_column(Integer, ForeignKey("library_entries.id"), nullable=False)
    agent_id: Mapped[int] = mapped_column(Integer, ForeignKey("agents.id"), nullable=False)
    viewed_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    # Relationships
    entry: Mapped["LibraryEntry"] = relationship("LibraryEntry", foreign_keys=[entry_id])
    agent: Mapped["Agent"] = relationship("Agent", foreign_keys=[agent_id])

    def __repr__(self) -> str:
        return f"<LibraryView(entry_id={self.entry_id}, agent_id={self.agent_id})>"


# ---------------------------------------------------------------------------
# IntelSignal — Phase 2C
# ---------------------------------------------------------------------------

class IntelSignal(Base):
    __tablename__ = "intel_signals"
    __table_args__ = (
        Index("ix_intel_signals_status_expires", "status", "expires_at"),
        Index("ix_intel_signals_scout", "scout_agent_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    message_id: Mapped[int] = mapped_column(Integer, ForeignKey("messages.id"), nullable=False)
    scout_agent_id: Mapped[int] = mapped_column(Integer, ForeignKey("agents.id"), nullable=False)
    scout_agent_name: Mapped[str] = mapped_column(String(100), nullable=False)
    asset: Mapped[str] = mapped_column(String(30), nullable=False)
    direction: Mapped[str] = mapped_column(String(10), nullable=False)  # bullish, bearish, neutral
    confidence_level: Mapped[int] = mapped_column(Integer, default=3)
    price_at_creation: Mapped[float] = mapped_column(Float, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="active")
    total_endorsement_stake: Mapped[float] = mapped_column(Float, default=0.0)
    endorsement_count: Mapped[int] = mapped_column(Integer, default=0)
    settlement_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    settlement_price_change_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    settled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # Relationships
    scout_agent: Mapped["Agent"] = relationship("Agent", foreign_keys=[scout_agent_id])
    message: Mapped["Message"] = relationship("Message", foreign_keys=[message_id])

    def __repr__(self) -> str:
        return f"<IntelSignal(id={self.id}, asset={self.asset!r}, direction={self.direction!r}, status={self.status!r})>"


# ---------------------------------------------------------------------------
# IntelEndorsement — Phase 2C
# ---------------------------------------------------------------------------

class IntelEndorsement(Base):
    __tablename__ = "intel_endorsements"
    __table_args__ = (
        UniqueConstraint("signal_id", "endorser_agent_id", name="uq_endorsement_signal_agent"),
        Index("ix_intel_endorsements_signal", "signal_id"),
        Index("ix_intel_endorsements_endorser_status", "endorser_agent_id", "settlement_status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    signal_id: Mapped[int] = mapped_column(Integer, ForeignKey("intel_signals.id"), nullable=False)
    endorser_agent_id: Mapped[int] = mapped_column(Integer, ForeignKey("agents.id"), nullable=False)
    endorser_agent_name: Mapped[str] = mapped_column(String(100), nullable=False)
    stake_amount: Mapped[float] = mapped_column(Float, nullable=False)
    linked_trade_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("transactions.id"), nullable=True)
    settlement_status: Mapped[str] = mapped_column(String(20), default="pending")
    settlement_pnl: Mapped[float | None] = mapped_column(Float, nullable=True)
    scout_reputation_change: Mapped[float | None] = mapped_column(Float, nullable=True)
    endorser_reputation_change: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    settled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # Relationships
    signal: Mapped["IntelSignal"] = relationship("IntelSignal", foreign_keys=[signal_id])
    endorser_agent: Mapped["Agent"] = relationship("Agent", foreign_keys=[endorser_agent_id])
    linked_trade: Mapped["Transaction | None"] = relationship("Transaction", foreign_keys=[linked_trade_id])

    def __repr__(self) -> str:
        return f"<IntelEndorsement(id={self.id}, signal_id={self.signal_id}, endorser={self.endorser_agent_name!r})>"


# ---------------------------------------------------------------------------
# ReviewRequest — Phase 2C
# ---------------------------------------------------------------------------

class ReviewRequest(Base):
    __tablename__ = "review_requests"
    __table_args__ = (
        Index("ix_review_requests_status_expires", "status", "expires_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    requester_agent_id: Mapped[int] = mapped_column(Integer, ForeignKey("agents.id"), nullable=False)
    requester_agent_name: Mapped[str] = mapped_column(String(100), nullable=False)
    proposal_message_id: Mapped[int] = mapped_column(Integer, ForeignKey("messages.id"), nullable=False)
    proposal_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    budget_reputation: Mapped[float] = mapped_column(Float, nullable=False)
    requires_two_reviews: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(20), default="open")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # Relationships
    requester_agent: Mapped["Agent"] = relationship("Agent", foreign_keys=[requester_agent_id])
    proposal_message: Mapped["Message"] = relationship("Message", foreign_keys=[proposal_message_id])

    def __repr__(self) -> str:
        return f"<ReviewRequest(id={self.id}, requester={self.requester_agent_name!r}, status={self.status!r})>"


# ---------------------------------------------------------------------------
# ReviewAssignment — Phase 2C
# ---------------------------------------------------------------------------

class ReviewAssignment(Base):
    __tablename__ = "review_assignments"
    __table_args__ = (
        UniqueConstraint("review_request_id", "critic_agent_id", name="uq_review_assignment_request_critic"),
        Index("ix_review_assignments_critic_completed", "critic_agent_id", "completed_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    review_request_id: Mapped[int] = mapped_column(Integer, ForeignKey("review_requests.id"), nullable=False)
    critic_agent_id: Mapped[int] = mapped_column(Integer, ForeignKey("agents.id"), nullable=False)
    critic_agent_name: Mapped[str] = mapped_column(String(100), nullable=False)
    verdict: Mapped[str | None] = mapped_column(String(20), nullable=True)
    reasoning: Mapped[str | None] = mapped_column(Text, nullable=True)
    risk_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    review_message_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("messages.id"), nullable=True)
    reputation_earned: Mapped[float | None] = mapped_column(Float, nullable=True)
    accepted_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    deadline_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    # Relationships
    review_request: Mapped["ReviewRequest"] = relationship("ReviewRequest", foreign_keys=[review_request_id])
    critic_agent: Mapped["Agent"] = relationship("Agent", foreign_keys=[critic_agent_id])
    review_message: Mapped["Message | None"] = relationship("Message", foreign_keys=[review_message_id])

    def __repr__(self) -> str:
        return f"<ReviewAssignment(id={self.id}, critic={self.critic_agent_name!r}, verdict={self.verdict!r})>"


# ---------------------------------------------------------------------------
# CriticAccuracy — Phase 2C
# ---------------------------------------------------------------------------

class CriticAccuracy(Base):
    __tablename__ = "critic_accuracy"

    critic_agent_id: Mapped[int] = mapped_column(Integer, ForeignKey("agents.id"), primary_key=True)
    total_reviews: Mapped[int] = mapped_column(Integer, default=0)
    accurate_reviews: Mapped[int] = mapped_column(Integer, default=0)
    accuracy_score: Mapped[float] = mapped_column(Float, default=0.0)
    approve_count: Mapped[int] = mapped_column(Integer, default=0)
    reject_count: Mapped[int] = mapped_column(Integer, default=0)
    conditional_count: Mapped[int] = mapped_column(Integer, default=0)
    avg_risk_score: Mapped[float] = mapped_column(Float, default=0.0)
    last_updated: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    # Relationships
    critic_agent: Mapped["Agent"] = relationship("Agent", foreign_keys=[critic_agent_id])

    def __repr__(self) -> str:
        return f"<CriticAccuracy(critic_id={self.critic_agent_id}, accuracy={self.accuracy_score:.2f})>"


# ---------------------------------------------------------------------------
# ServiceListing — Phase 2C (framework only)
# ---------------------------------------------------------------------------

class ServiceListing(Base):
    __tablename__ = "service_listings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    provider_agent_id: Mapped[int] = mapped_column(Integer, ForeignKey("agents.id"), nullable=False)
    provider_agent_name: Mapped[str] = mapped_column(String(100), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    price_reputation: Mapped[float] = mapped_column(Float, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    purchase_count: Mapped[int] = mapped_column(Integer, default=0)

    # Relationships
    provider_agent: Mapped["Agent"] = relationship("Agent", foreign_keys=[provider_agent_id])

    def __repr__(self) -> str:
        return f"<ServiceListing(id={self.id}, title={self.title!r}, status={self.status!r})>"


# ---------------------------------------------------------------------------
# GamingFlag — Phase 2C
# ---------------------------------------------------------------------------

class GamingFlag(Base):
    __tablename__ = "gaming_flags"
    __table_args__ = (
        Index("ix_gaming_flags_resolved_detected", "resolved", "detected_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    flag_type: Mapped[str] = mapped_column(String(30), nullable=False)
    agent_ids: Mapped[dict] = mapped_column(JSON, nullable=False)
    evidence: Mapped[str] = mapped_column(Text, nullable=False)
    severity: Mapped[str] = mapped_column(String(10), nullable=False)
    penalty_applied: Mapped[float | None] = mapped_column(Float, nullable=True)
    detected_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    reviewed_by: Mapped[str | None] = mapped_column(String(20), nullable=True)
    resolved: Mapped[bool] = mapped_column(Boolean, default=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    def __repr__(self) -> str:
        return f"<GamingFlag(id={self.id}, type={self.flag_type!r}, severity={self.severity!r})>"


# ---------------------------------------------------------------------------
# AgentCycle — Phase 3A (Thinking Cycle Black Box)
# ---------------------------------------------------------------------------

class AgentCycle(Base):
    __tablename__ = "agent_cycles"
    __table_args__ = (
        Index("ix_agent_cycles_agent_id_cycle", "agent_id", "cycle_number"),
        Index("ix_agent_cycles_agent_id_timestamp", "agent_id", "timestamp"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    agent_id: Mapped[int] = mapped_column(Integer, ForeignKey("agents.id"), nullable=False)
    cycle_number: Mapped[int] = mapped_column(Integer, nullable=False)
    cycle_type: Mapped[str] = mapped_column(String(20), nullable=False, default="normal")  # normal, reflection, survival
    timestamp: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    context_mode: Mapped[str] = mapped_column(String(20), default="normal")  # normal, crisis, hunting, survival
    context_tokens: Mapped[int] = mapped_column(Integer, default=0)
    situation: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    confidence_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    recent_pattern: Mapped[str | None] = mapped_column(Text, nullable=True)
    action_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    action_params: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    reasoning: Mapped[str | None] = mapped_column(Text, nullable=True)
    self_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    validation_passed: Mapped[bool] = mapped_column(Boolean, default=True)
    validation_retries: Mapped[int] = mapped_column(Integer, default=0)
    warden_flags: Mapped[int] = mapped_column(Integer, default=0)
    outcome: Mapped[str | None] = mapped_column(Text, nullable=True)
    outcome_pnl: Mapped[float | None] = mapped_column(Float, nullable=True)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    api_cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    cycle_duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    api_latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    # Relationships
    agent: Mapped["Agent"] = relationship("Agent", foreign_keys=[agent_id])

    def __repr__(self) -> str:
        return f"<AgentCycle(id={self.id}, agent_id={self.agent_id}, cycle={self.cycle_number}, type={self.cycle_type!r})>"


# ---------------------------------------------------------------------------
# AgentLongTermMemory — Phase 3A
# ---------------------------------------------------------------------------

class AgentLongTermMemory(Base):
    __tablename__ = "agent_long_term_memory"
    __table_args__ = (
        Index("ix_agent_ltm_agent_active", "agent_id", "is_active"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    agent_id: Mapped[int] = mapped_column(Integer, ForeignKey("agents.id"), nullable=False)
    memory_type: Mapped[str] = mapped_column(String(20), nullable=False)  # lesson, pattern, relationship, reflection, inherited
    content: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, default=0.5)
    source: Mapped[str] = mapped_column(String(20), default="self")  # self, parent, grandparent
    source_cycle: Mapped[int | None] = mapped_column(Integer, nullable=True)
    times_confirmed: Mapped[int] = mapped_column(Integer, default=0)
    times_contradicted: Mapped[int] = mapped_column(Integer, default=0)
    promoted_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    demoted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    # Relationships
    agent: Mapped["Agent"] = relationship("Agent", foreign_keys=[agent_id])

    def __repr__(self) -> str:
        return f"<AgentLongTermMemory(id={self.id}, agent_id={self.agent_id}, type={self.memory_type!r}, active={self.is_active})>"


# ---------------------------------------------------------------------------
# AgentReflection — Phase 3A
# ---------------------------------------------------------------------------

class AgentReflection(Base):
    __tablename__ = "agent_reflections"
    __table_args__ = (
        Index("ix_agent_reflections_agent_id", "agent_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    agent_id: Mapped[int] = mapped_column(Integer, ForeignKey("agents.id"), nullable=False)
    cycle_number: Mapped[int] = mapped_column(Integer, nullable=False)
    what_worked: Mapped[str | None] = mapped_column(Text, nullable=True)
    what_failed: Mapped[str | None] = mapped_column(Text, nullable=True)
    pattern_detected: Mapped[str | None] = mapped_column(Text, nullable=True)
    lesson: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence_trend: Mapped[str | None] = mapped_column(String(20), nullable=True)  # improving, stable, declining
    confidence_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    strategy_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    memory_promotions: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    memory_demotions: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    # Relationships
    agent: Mapped["Agent"] = relationship("Agent", foreign_keys=[agent_id])

    def __repr__(self) -> str:
        return f"<AgentReflection(id={self.id}, agent_id={self.agent_id}, cycle={self.cycle_number})>"
