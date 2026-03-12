"""
Project Syndicate — SQLAlchemy ORM Models

Defines the core database schema for the autonomous AI trading syndicate:
agents, transactions, messages (the Agora), evaluations, reputation,
Syndicate Improvement Proposals (SIPs), system state, lineage tracking,
inherited positions, market regimes, and daily reports.
"""

__version__ = "0.2.0"

import os
from datetime import date, datetime

from dotenv import load_dotenv
from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
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
    channel: Mapped[str] = mapped_column(String(50), nullable=False)  # general, strategy, risk, market_data, sips
    content: Mapped[str] = mapped_column(Text, nullable=False)
    metadata_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    # Relationships
    agent: Mapped["Agent | None"] = relationship("Agent", back_populates="messages")

    def __repr__(self) -> str:
        return f"<Message(id={self.id}, channel={self.channel!r}, agent_id={self.agent_id})>"


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
