"""
Project Syndicate — Library Pydantic Schemas

Data contracts for The Library: entries, contributions, mentor packages.
"""

__version__ = "0.4.0"

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class LibraryCategory(str, Enum):
    TEXTBOOK = "textbook"
    POST_MORTEM = "post_mortem"
    STRATEGY_RECORD = "strategy_record"
    PATTERN = "pattern"
    CONTRIBUTION = "contribution"


class ContributionStatus(str, Enum):
    PENDING_REVIEW = "pending_review"
    IN_REVIEW = "in_review"
    APPROVED = "approved"
    REJECTED = "rejected"
    NEEDS_REVISION = "needs_revision"


class ReviewDecision(str, Enum):
    APPROVE = "approve"
    REJECT = "reject"
    NEEDS_REVISION = "needs_revision"


class LibraryEntryResponse(BaseModel):
    """A published Library entry."""
    id: int
    category: str
    title: str
    content: str
    summary: Optional[str] = None
    tags: list[str] = Field(default_factory=list)
    source_agent_id: Optional[int] = None
    source_agent_name: Optional[str] = None
    market_regime_at_creation: Optional[str] = None
    is_published: bool
    created_at: datetime
    published_at: Optional[datetime] = None
    view_count: int = 0


class LibraryEntryBrief(BaseModel):
    """Short version for listing views."""
    id: int
    category: str
    title: str
    summary: Optional[str] = None
    tags: list[str] = Field(default_factory=list)
    source_agent_name: Optional[str] = None
    published_at: Optional[datetime] = None
    view_count: int = 0


class ContributionResponse(BaseModel):
    """A Library contribution (submitted or in review)."""
    id: int
    submitter_agent_id: int
    submitter_agent_name: str
    title: str
    content: str
    tags: list[str] = Field(default_factory=list)
    status: str
    reviewer_1_name: Optional[str] = None
    reviewer_1_decision: Optional[str] = None
    reviewer_2_name: Optional[str] = None
    reviewer_2_decision: Optional[str] = None
    final_decision: Optional[str] = None
    final_decision_by: Optional[str] = None
    created_at: datetime
    resolved_at: Optional[datetime] = None


class MentorPackage(BaseModel):
    """Knowledge inheritance package for offspring agents."""
    parent_agent_id: int
    parent_agent_name: str
    parent_generation: int
    strategy_template: str
    top_trades: list[dict] = Field(default_factory=list)
    failures: list[str] = Field(default_factory=list)
    market_assessment: str = ""
    grandparent_package: Optional[dict] = None
    recommended_library_entries: list[int] = Field(default_factory=list)
    condensed_heritage: Optional[str] = None
    generated_at: Optional[datetime] = None
