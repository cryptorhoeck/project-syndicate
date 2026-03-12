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

__version__ = "0.1.0"

import abc
import asyncio
import enum
from datetime import datetime, timezone
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker, Session

from src.common.models import Agent as AgentModel, Message as MessageModel

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
        A :class:`sqlalchemy.orm.sessionmaker` bound to the project database
        engine.  Used via the context-manager pattern::

            with self.db_session_factory() as session:
                ...
                session.commit()
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
    ) -> None:
        # Identity
        self.agent_id: int = agent_id
        self.name: str = name
        self.agent_type: str = agent_type

        # Lifecycle
        self.status: AgentStatus = AgentStatus.INITIALIZING

        # Database access
        self.db_session_factory: sessionmaker = db_session_factory

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
        """Permanently terminate the agent, recording the reason.

        Parameters
        ----------
        reason : str
            Human-readable explanation of why the agent was terminated.
        """
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
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Publish a message to an Agora channel.

        Parameters
        ----------
        channel : str
            Target channel name (e.g. ``"strategy"``, ``"risk-alerts"``).
        content : str
            The message body.
        metadata : dict | None
            Optional JSON-serialisable payload attached to the message.
        """
        with self.db_session_factory() as session:
            message = MessageModel(
                agent_id=self.agent_id,
                channel=channel,
                content=content,
                metadata_json=metadata,
            )
            session.add(message)
            session.commit()

        self.log.info("agora_post", channel=channel, content_length=len(content))

    async def read_agora(
        self,
        channel: str,
        since: datetime | None = None,
        limit: int = 50,
    ) -> list[MessageModel]:
        """Read recent messages from an Agora channel.

        Parameters
        ----------
        channel : str
            Channel to read from.
        since : datetime | None
            If provided, only return messages created after this timestamp.
        limit : int
            Maximum number of messages to return (default 50).

        Returns
        -------
        list[MessageModel]
            Messages ordered by creation time (oldest first).
        """
        with self.db_session_factory() as session:
            stmt = (
                select(MessageModel)
                .where(MessageModel.channel == channel)
            )
            if since is not None:
                stmt = stmt.where(MessageModel.timestamp > since)

            stmt = stmt.order_by(MessageModel.timestamp.asc()).limit(limit)
            results: list[MessageModel] = list(session.scalars(stmt).all())

        self.log.debug("agora_read", channel=channel, count=len(results))
        return results

    # ------------------------------------------------------------------
    # Thinking-tax / API cost tracking
    # ------------------------------------------------------------------

    def track_api_cost(self, cost: float) -> None:
        """Record an incremental API cost for this session.

        Parameters
        ----------
        cost : float
            Dollar amount to add (e.g. ``0.0023``).
        """
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
