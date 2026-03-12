"""
Project Syndicate — Agora Message Schemas

Pydantic data contracts for all Agora communication.
Every agent uses these models to post and read messages.
"""

__version__ = "0.3.0"

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class MessageType(str, Enum):
    """All message types in The Agora."""

    THOUGHT = "thought"          # Internal reasoning an agent chose to share
    PROPOSAL = "proposal"        # Formal submission (strategy, SIP, etc.)
    SIGNAL = "signal"            # Actionable intel (trade signal, opportunity)
    ALERT = "alert"              # System-level alert (Warden, circuit breaker)
    CHAT = "chat"                # Informal discussion
    SYSTEM = "system"            # System events (agent born, agent died, regime change)
    EVALUATION = "evaluation"    # Evaluation results
    TRADE = "trade"              # Trade execution reports
    ECONOMY = "economy"          # Internal economy transactions (intel purchase, review, etc.)


class AgoraMessage(BaseModel):
    """The standard message format for posting to The Agora."""

    agent_id: int
    agent_name: str
    channel: str
    content: str
    message_type: MessageType = MessageType.CHAT
    metadata: dict = Field(default_factory=dict)
    importance: int = Field(default=0, ge=0, le=2)  # 0=normal, 1=important, 2=critical
    parent_message_id: Optional[int] = None
    expires_at: Optional[datetime] = None


class AgoraMessageResponse(BaseModel):
    """What gets returned when reading messages."""

    id: int
    agent_id: int
    agent_name: str
    channel: str
    content: str
    message_type: str
    metadata: dict
    importance: int
    parent_message_id: Optional[int]
    timestamp: datetime
    expires_at: Optional[datetime] = None


class ChannelInfo(BaseModel):
    """Channel metadata."""

    name: str
    description: str
    is_system: bool
    message_count: int
    latest_message_at: Optional[datetime] = None


class ReadReceipt(BaseModel):
    """Tracks where an agent has read up to in a channel."""

    agent_id: int
    channel: str
    last_read_at: datetime
    last_read_message_id: Optional[int]
