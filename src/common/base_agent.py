"""
base_agent.py — Abstract base class for all agents in Project Syndicate.

Every agent in the system (council members, operators, risk monitors, etc.)
inherits from BaseAgent. It provides:

  - A standard lifecycle: INITIALIZING -> ACTIVE -> HIBERNATING/EVALUATING -> TERMINATED
  - Agora messaging (post and read from shared communication channels)
  - Thinking-tax cost tracking (API spend per session, flushed to the DB)
  - Persistence helpers that load/save agent state via SQLAlchemy

Subclasses MUST implement the three abstract coroutines:
    initialize(), run(), evaluate()
"""

from __future__ import annotations

__version__ = "0.5.0"

import abc
import asyncio
import enum
from datetime import datetime, timezone
from typing import Any, Optional, TYPE_CHECKING

import structlog
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker, Session

from src.common.models import Agent as AgentModel, Message as MessageModel

if TYPE_CHECKING:
    from src.agora.agora_service import AgoraService
    from src.agora.schemas import AgoraMessageResponse, MessageType
    from src.economy.economy_service import EconomyService
    from src.library.library_service import LibraryService

logger = structlog.get_logger()


# ---------------------------------------------------------------------------
# Agent lifecycle states
# ---------------------------------------------------------------------------

class AgentStatus(enum.Enum):
    """Possible runtime states for any agent."""

    INITIALIZING = "initializing"
    ACTIVE = "active"
    HIBERNATING = "hibernating"
    EVALUATING = "evaluating"
    TERMINATED = "terminated"


# ---------------------------------------------------------------------------
# Abstract base agent
# ---------------------------------------------------------------------------

class BaseAgent(abc.ABC):
    """Abstract base that every Project Syndicate agent inherits from.

    Parameters
    ----------
    agent_id : int
        Unique database primary-key for the agent.
    name : str
        Human-readable agent name (e.g. ``"Strategist-7"``).
    agent_type : str
        Category string such as ``"council"``, ``"operator"``, ``"risk"``.
    db_session_factory : sessionmaker
        A :class:`sqlalchemy.orm.sessionmaker` bound to the project database engine.
    agora_service : AgoraService | None
        The Agora communication service. If None, Agora methods are no-ops.
    """

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def __init__(
        self,
        agent_id: int,
        name: str,
        agent_type: str,
        db_session_factory: sessionmaker,
        agora_service: Optional["AgoraService"] = None,
        library_service: Optional["LibraryService"] = None,
        economy_service: Optional["EconomyService"] = None,
    ) -> None:
        # Identity
        self.agent_id: int = agent_id
        self.name: str = name
        self.agent_type: str = agent_type

        # Lifecycle
        self.status: AgentStatus = AgentStatus.INITIALIZING

        # Database access
        self.db_session_factory: sessionmaker = db_session_factory

        # Agora communication
        self.agora: Optional["AgoraService"] = agora_service

        # Library access
        self.library: Optional["LibraryService"] = library_service

        # Economy access
        self.economy: Optional["EconomyService"] = economy_service

        # Structured logger bound to this agent
        self.log: structlog.stdlib.BoundLogger = logger.bind(
            agent_id=self.agent_id,
            agent_name=self.name,
        )

        # Lineage & reputation — populated by load_from_db()
        self.generation: int = 0
        self.parent_id: int | None = None
        self.reputation: float = 0.0

        # Cost tracking for the current runtime session
        self.thinking_cost_session: float = 0.0

    # ------------------------------------------------------------------
    # Abstract interface — subclasses MUST implement these
    # ------------------------------------------------------------------

    @abc.abstractmethod
    async def initialize(self) -> None:
        """Set up agent-specific resources (models, tools, memory, etc.)."""
        ...

    @abc.abstractmethod
    async def run(self) -> None:
        """Main agent loop — called after ``initialize()``."""
        ...

    @abc.abstractmethod
    async def evaluate(self) -> dict[str, Any]:
        """Self-evaluation.  Returns a dict of performance metrics."""
        ...

    # ------------------------------------------------------------------
    # Lifecycle helpers
    # ------------------------------------------------------------------

    async def hibernate(self) -> None:
        """Transition the agent into a low-power hibernation state."""
        self.status = AgentStatus.HIBERNATING
        self._update_status_in_db(AgentStatus.HIBERNATING)
        self.log.info("agent_hibernated")

    async def wake(self) -> None:
        """Wake the agent from hibernation and mark it active."""
        self.status = AgentStatus.ACTIVE
        self._update_status_in_db(AgentStatus.ACTIVE)
        self.log.info("agent_woken")

    async def terminate(self, reason: str) -> None:
        """Permanently terminate the agent, recording the reason."""
        self.status = AgentStatus.TERMINATED
        now = datetime.now(timezone.utc)

        with self.db_session_factory() as session:
            agent_row: AgentModel | None = session.get(AgentModel, self.agent_id)
            if agent_row is not None:
                agent_row.status = AgentStatus.TERMINATED.value
                agent_row.termination_reason = reason
                agent_row.terminated_at = now
                session.commit()

        self.log.info("agent_terminated", reason=reason, terminated_at=now.isoformat())

    # ------------------------------------------------------------------
    # Agora integration (message bus)
    # ------------------------------------------------------------------

    async def post_to_agora(
        self,
        channel: str,
        content: str,
        message_type: Optional["MessageType"] = None,
        metadata: dict[str, Any] | None = None,
        importance: int = 0,
        expires_at: Optional[datetime] = None,
    ) -> Optional["AgoraMessageResponse"]:
        """Post a message to The Agora.

        Parameters
        ----------
        channel : str
            Target channel name.
        content : str
            The message body.
        message_type : MessageType | None
            Message classification. Defaults to CHAT.
        metadata : dict | None
            Optional JSON-serializable payload.
        importance : int
            0=normal, 1=important, 2=critical.
        expires_at : datetime | None
            When this message expires (for time-sensitive signals).
        """
        if self.agora is not None:
            from src.agora.schemas import AgoraMessage, MessageType as MT
            msg = AgoraMessage(
                agent_id=self.agent_id,
                agent_name=self.name,
                channel=channel,
                content=content,
                message_type=message_type or MT.CHAT,
                metadata=metadata or {},
                importance=importance,
                expires_at=expires_at,
            )
            return await self.agora.post_message(msg)

        # Fallback: direct DB write (no AgoraService available)
        with self.db_session_factory() as session:
            message = MessageModel(
                agent_id=self.agent_id,
                channel=channel,
                content=content,
                metadata_json=metadata,
                agent_name=self.name,
                message_type=message_type.value if message_type else "chat",
                importance=importance,
                expires_at=expires_at,
            )
            session.add(message)
            session.commit()

        self.log.info("agora_post", channel=channel, content_length=len(content))
        return None

    async def read_agora(
        self,
        channel: str,
        since: datetime | None = None,
        limit: int = 50,
        message_types: Optional[list] = None,
        only_unread: bool = False,
    ) -> list:
        """Read messages from The Agora.

        Parameters
        ----------
        channel : str
            Channel to read from.
        since : datetime | None
            If provided, only return messages after this timestamp.
        limit : int
            Maximum messages to return.
        message_types : list[MessageType] | None
            Filter by specific message types.
        only_unread : bool
            If True, read only messages since last mark_read().
        """
        if self.agora is not None:
            if only_unread:
                return await self.agora.read_channel_since_last_read(
                    agent_id=self.agent_id, channel=channel, limit=limit
                )
            return await self.agora.read_channel(
                channel=channel, since=since, limit=limit,
                message_types=message_types,
            )

        # Fallback: direct DB query
        with self.db_session_factory() as session:
            stmt = select(MessageModel).where(MessageModel.channel == channel)
            if since is not None:
                stmt = stmt.where(MessageModel.timestamp > since)
            stmt = stmt.order_by(MessageModel.timestamp.asc()).limit(limit)
            results: list[MessageModel] = list(session.scalars(stmt).all())

        self.log.debug("agora_read", channel=channel, count=len(results))
        return results

    async def mark_agora_read(
        self,
        channel: str,
        up_to_message_id: Optional[int] = None,
    ) -> None:
        """Mark a channel as read. Call after processing messages."""
        if self.agora is None:
            return
        await self.agora.mark_read(
            agent_id=self.agent_id, channel=channel,
            up_to_message_id=up_to_message_id,
        )

    async def get_agora_unread(self) -> dict[str, int]:
        """Check how many unread messages per channel."""
        if self.agora is None:
            return {}
        return await self.agora.get_unread_counts(agent_id=self.agent_id)

    async def broadcast(self, content: str, importance: int = 1) -> Optional["AgoraMessageResponse"]:
        """Post an important message to agent-chat visible to everyone."""
        from src.agora.schemas import MessageType as MT
        return await self.post_to_agora(
            channel="agent-chat",
            content=content,
            message_type=MT.CHAT,
            importance=importance,
        )

    # ------------------------------------------------------------------
    # Library access
    # ------------------------------------------------------------------

    def read_textbook(self, topic: str) -> Optional[str]:
        """Read a textbook. File I/O, not an API call."""
        if self.library is None:
            return None
        return self.library.get_textbook(topic)

    async def search_library(
        self, query: str, category=None, limit: int = 10
    ) -> list:
        """Search the Library."""
        if self.library is None:
            return []
        return await self.library.search_entries(query=query, category=category, limit=limit)

    async def submit_to_library(
        self, title: str, content: str, tags: list[str] | None = None
    ):
        """Submit a contribution for peer review."""
        if self.library is None:
            return None
        return await self.library.submit_contribution(
            agent_id=self.agent_id,
            agent_name=self.name,
            title=title,
            content=content,
            tags=tags or [],
        )

    async def get_my_pending_reviews(self) -> list:
        """Check for assigned Library reviews."""
        if self.library is None:
            return []
        return await self.library.get_pending_reviews(self.agent_id)

    # ------------------------------------------------------------------
    # Economy access
    # ------------------------------------------------------------------

    async def create_intel_signal(
        self, asset: str, direction: str, confidence: int, expires_hours: int = 48,
    ) -> Any:
        """Post an intel signal to the market."""
        if self.economy is None:
            return None

        from src.agora.schemas import MessageType as MT
        msg = await self.post_to_agora(
            channel="market-intel",
            content=f"Signal: {asset} {direction} (confidence {confidence}/5)",
            message_type=MT.SIGNAL,
            metadata={"asset": asset, "direction": direction, "confidence": confidence},
        )
        if msg is None:
            return None

        from datetime import timedelta
        expires_at = datetime.now(timezone.utc) + timedelta(hours=expires_hours)

        # Get current price (best-effort)
        price = 0.0

        return await self.economy.create_intel_signal(
            scout_agent_id=self.agent_id,
            scout_agent_name=self.name,
            message_id=msg.id,
            asset=asset,
            direction=direction,
            confidence_level=confidence,
            price_at_creation=price,
            expires_at=expires_at,
        )

    async def endorse_intel(self, signal_id: int, stake: float) -> Any:
        """Endorse someone else's intel signal."""
        if self.economy is None:
            return None
        return await self.economy.endorse_intel(
            signal_id=signal_id,
            endorser_agent_id=self.agent_id,
            endorser_agent_name=self.name,
            stake_amount=stake,
        )

    async def request_strategy_review(
        self,
        proposal_message_id: int,
        summary: str,
        budget: float,
        capital_pct: float = 0.0,
    ) -> Any:
        """Request a Critic to review a strategy proposal."""
        if self.economy is None:
            return None
        return await self.economy.request_review(
            requester_agent_id=self.agent_id,
            requester_agent_name=self.name,
            proposal_message_id=proposal_message_id,
            proposal_summary=summary,
            budget_reputation=budget,
            capital_percentage=capital_pct,
        )

    async def accept_and_submit_review(
        self,
        request_id: int,
        verdict: str,
        reasoning: str,
        risk_score: int,
    ) -> Any:
        """Accept and complete a review request (for Critic agents)."""
        if self.economy is None:
            return None

        assignment = await self.economy.accept_review(
            request_id=request_id,
            critic_agent_id=self.agent_id,
            critic_agent_name=self.name,
        )
        if assignment is None:
            return None

        from src.agora.schemas import MessageType as MT
        review_msg = await self.post_to_agora(
            channel="strategy-debate",
            content=f"Review of proposal: {verdict}. Risk: {risk_score}/10. {reasoning}",
            message_type=MT.EVALUATION,
        )

        return await self.economy.submit_review(
            assignment_id=assignment.id,
            verdict=verdict,
            reasoning=reasoning,
            risk_score=risk_score,
            review_message_id=review_msg.id if review_msg else None,
        )

    async def get_my_reputation(self) -> float:
        """Get current reputation balance."""
        if self.economy is None:
            return 0.0
        return await self.economy.get_balance(self.agent_id)

    # ------------------------------------------------------------------
    # Thinking-tax / API cost tracking
    # ------------------------------------------------------------------

    def track_api_cost(self, cost: float) -> None:
        """Record an incremental API cost for this session."""
        self.thinking_cost_session += cost
        self.log.debug(
            "api_cost_tracked",
            cost=cost,
            session_total=self.thinking_cost_session,
        )

    async def flush_costs_to_db(self) -> None:
        """Persist accumulated session costs to the agent's database record."""
        if self.thinking_cost_session == 0.0:
            return

        with self.db_session_factory() as session:
            agent_row: AgentModel | None = session.get(AgentModel, self.agent_id)
            if agent_row is not None:
                agent_row.api_cost_total = (
                    (agent_row.api_cost_total or 0.0) + self.thinking_cost_session
                )
                session.commit()

        self.log.info(
            "costs_flushed",
            session_cost=self.thinking_cost_session,
        )
        self.thinking_cost_session = 0.0

    # ------------------------------------------------------------------
    # Database persistence helpers
    # ------------------------------------------------------------------

    async def load_from_db(self) -> None:
        """Load agent metadata from the database by ``self.agent_id``."""
        with self.db_session_factory() as session:
            agent_row: AgentModel | None = session.get(AgentModel, self.agent_id)
            if agent_row is None:
                self.log.warning("agent_not_found_in_db", agent_id=self.agent_id)
                return

            self.name = agent_row.name
            self.agent_type = agent_row.type
            self.generation = agent_row.generation or 0
            self.parent_id = agent_row.parent_id
            self.reputation = agent_row.reputation_score or 0.0
            self.status = AgentStatus(agent_row.status)

        self.log.info("loaded_from_db")

    async def save_to_db(self) -> None:
        """Persist the agent's current in-memory state to the database."""
        with self.db_session_factory() as session:
            agent_row: AgentModel | None = session.get(AgentModel, self.agent_id)
            if agent_row is None:
                agent_row = AgentModel(id=self.agent_id)
                session.add(agent_row)

            agent_row.name = self.name
            agent_row.type = self.agent_type
            agent_row.generation = self.generation
            agent_row.parent_id = self.parent_id
            agent_row.reputation_score = self.reputation
            agent_row.status = self.status.value

            session.commit()

        self.log.info("saved_to_db")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _update_status_in_db(self, new_status: AgentStatus) -> None:
        """Write a status change to the database (sync helper)."""
        with self.db_session_factory() as session:
            agent_row: AgentModel | None = session.get(AgentModel, self.agent_id)
            if agent_row is not None:
                agent_row.status = new_status.value
                session.commit()

    # ------------------------------------------------------------------
    # Dunder helpers
    # ------------------------------------------------------------------

    def __repr__(self) -> str:
        return (
            f"<{self.__class__.__name__}"
            f"(id={self.agent_id}, name={self.name!r}, "
            f"type={self.agent_type!r}, status={self.status.name})>"
        )
