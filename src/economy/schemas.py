"""
Project Syndicate — Economy Pydantic Schemas

Data contracts for the Internal Economy: intel signals, endorsements,
review requests, assignments, critic accuracy, service listings, gaming flags.
"""

__version__ = "0.5.0"

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class SignalDirection(str, Enum):
    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"


class SignalStatus(str, Enum):
    ACTIVE = "active"
    SETTLED_PROFITABLE = "settled_profitable"
    SETTLED_UNPROFITABLE = "settled_unprofitable"
    EXPIRED_NO_ENDORSEMENTS = "expired_no_endorsements"


class EndorsementStatus(str, Enum):
    PENDING = "pending"
    SETTLED_WIN = "settled_win"
    SETTLED_LOSS = "settled_loss"
    EXPIRED_REFUND = "expired_refund"


class ReviewRequestStatus(str, Enum):
    OPEN = "open"
    ASSIGNED = "assigned"
    COMPLETED = "completed"
    EXPIRED = "expired"


class ReviewVerdict(str, Enum):
    APPROVE = "approve"
    REJECT = "reject"
    CONDITIONAL_APPROVE = "conditional_approve"


class GamingFlagType(str, Enum):
    WASH_TRADING = "wash_trading"
    RUBBER_STAMP = "rubber_stamp"
    INTEL_SPAM = "intel_spam"
    COLLUSION = "collusion"


class GamingFlagSeverity(str, Enum):
    WARNING = "warning"
    PENALTY = "penalty"
    CRITICAL = "critical"


class IntelSignalResponse(BaseModel):
    """An intel signal."""
    id: int
    message_id: int
    scout_agent_id: int
    scout_agent_name: str
    asset: str
    direction: str
    confidence_level: int
    price_at_creation: float
    expires_at: datetime
    status: str
    total_endorsement_stake: float
    endorsement_count: int
    settlement_price: Optional[float] = None
    settlement_price_change_pct: Optional[float] = None
    created_at: datetime
    settled_at: Optional[datetime] = None


class IntelEndorsementResponse(BaseModel):
    """An intel endorsement."""
    id: int
    signal_id: int
    endorser_agent_id: int
    endorser_agent_name: str
    stake_amount: float
    linked_trade_id: Optional[int] = None
    settlement_status: str
    settlement_pnl: Optional[float] = None
    scout_reputation_change: Optional[float] = None
    endorser_reputation_change: Optional[float] = None
    created_at: datetime
    settled_at: Optional[datetime] = None


class ReviewRequestResponse(BaseModel):
    """A review request."""
    id: int
    requester_agent_id: int
    requester_agent_name: str
    proposal_message_id: int
    proposal_summary: Optional[str] = None
    budget_reputation: float
    requires_two_reviews: bool
    status: str
    created_at: datetime
    expires_at: datetime
    completed_at: Optional[datetime] = None


class ReviewAssignmentResponse(BaseModel):
    """A review assignment."""
    id: int
    review_request_id: int
    critic_agent_id: int
    critic_agent_name: str
    verdict: Optional[str] = None
    reasoning: Optional[str] = None
    risk_score: Optional[int] = None
    reputation_earned: Optional[float] = None
    accepted_at: datetime
    completed_at: Optional[datetime] = None
    deadline_at: datetime


class CriticAccuracyResponse(BaseModel):
    """Critic accuracy statistics."""
    critic_agent_id: int
    total_reviews: int
    accurate_reviews: int
    accuracy_score: float
    approve_count: int
    reject_count: int
    conditional_count: int
    avg_risk_score: float


class ServiceListingResponse(BaseModel):
    """A service listing."""
    id: int
    provider_agent_id: int
    provider_agent_name: str
    title: str
    description: Optional[str] = None
    price_reputation: float
    status: str
    created_at: datetime
    purchase_count: int


class GamingFlagResponse(BaseModel):
    """A gaming detection flag."""
    id: int
    flag_type: str
    agent_ids: list[int]
    evidence: str
    severity: str
    penalty_applied: Optional[float] = None
    detected_at: datetime
    reviewed_by: Optional[str] = None
    resolved: bool
    resolved_at: Optional[datetime] = None


class EconomyStats(BaseModel):
    """Aggregate economy statistics for the daily report."""
    total_reputation_in_circulation: float
    total_reputation_in_escrow: float
    active_intel_signals: int
    total_endorsements_24h: int
    total_endorsement_stake_24h: float
    signals_settled_24h: int
    profitable_signals_24h: int
    unprofitable_signals_24h: int
    open_review_requests: int
    reviews_completed_24h: int
    gaming_flags_unresolved: int
    top_reputation_agents: list[dict] = Field(default_factory=list)
