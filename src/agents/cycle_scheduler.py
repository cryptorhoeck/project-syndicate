"""
Project Syndicate — Cycle Scheduler

Manages agent thinking cycle scheduling:
  - Base frequency per role
  - Interrupt triggers from Agora events
  - Cooldown enforcement (60s minimum between cycles)
  - Priority queue in Redis
  - Sequential cycle processing (one at a time for Phase 3A)
"""

__version__ = "0.9.0"

import enum
import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from src.common.models import Agent
from src.agents.roles import get_role

logger = logging.getLogger(__name__)


class CyclePriority(enum.IntEnum):
    """Priority levels for the cycle queue."""
    IDLE = 0
    SCHEDULED = 1
    INTERRUPT = 2
    CRITICAL = 3


@dataclass
class ScheduleResult:
    """Result of a scheduling check."""
    queued: bool
    reason: str
    priority: CyclePriority = CyclePriority.SCHEDULED
    next_eligible_time: float | None = None


# Interrupt trigger mapping: Agora event type → which roles to wake
INTERRUPT_TRIGGERS: dict[str, list[str]] = {
    "opportunity_broadcast": ["strategist"],
    "opportunity_created": ["strategist"],  # Phase 3B: pipeline-aware
    "plan_submitted": ["critic"],
    "plan_approved": ["operator"],
    "warden_alert": ["scout", "strategist", "critic", "operator"],
    "agent_mentioned": [],  # special: wakes the mentioned agent regardless of role
}


class CycleScheduler:
    """Schedules and queues agent thinking cycles."""

    QUEUE_KEY = "syndicate:cycle_queue"
    COOLDOWN_SECONDS = 60  # minimum gap between cycles per agent

    def __init__(self, db_session: Session, redis_client=None):
        self.db = db_session
        self.redis = redis_client
        self._local_queue: list[tuple[int, float]] = []

    def schedule_cycle(
        self,
        agent: Agent,
        priority: CyclePriority = CyclePriority.SCHEDULED,
        trigger_reason: str = "scheduled",
    ) -> ScheduleResult:
        """Check if an agent can be scheduled and enqueue if so.

        Args:
            agent: The agent to schedule.
            priority: Queue priority level.
            trigger_reason: Why this cycle is being triggered.

        Returns:
            ScheduleResult indicating whether the cycle was queued.
        """
        # Check cooldown
        if agent.last_cycle_at:
            last_ts = agent.last_cycle_at.replace(tzinfo=timezone.utc)
            elapsed = (datetime.now(timezone.utc) - last_ts).total_seconds()
            if elapsed < self.COOLDOWN_SECONDS:
                next_eligible = time.time() + (self.COOLDOWN_SECONDS - elapsed)
                return ScheduleResult(
                    queued=False,
                    reason=f"cooldown ({elapsed:.0f}s < {self.COOLDOWN_SECONDS}s)",
                    next_eligible_time=next_eligible,
                )

        # Check agent status
        if agent.status not in ("active", "initializing"):
            return ScheduleResult(
                queued=False,
                reason=f"agent status is '{agent.status}', not active",
            )

        # Enqueue
        self._enqueue(agent.id, priority, trigger_reason)

        return ScheduleResult(
            queued=True,
            reason=trigger_reason,
            priority=priority,
        )

    def handle_interrupt(
        self,
        event_type: str,
        target_agent_id: int | None = None,
    ) -> list[int]:
        """Process an interrupt event and wake appropriate agents.

        Args:
            event_type: The Agora event type (e.g., "plan_submitted").
            target_agent_id: For "agent_mentioned", the specific agent to wake.

        Returns:
            List of agent IDs that were queued.
        """
        queued_ids = []

        if event_type == "agent_mentioned" and target_agent_id:
            agent = self.db.query(Agent).filter(Agent.id == target_agent_id).first()
            if agent:
                result = self.schedule_cycle(
                    agent,
                    priority=CyclePriority.INTERRUPT,
                    trigger_reason=f"mentioned",
                )
                if result.queued:
                    queued_ids.append(agent.id)
            return queued_ids

        # Wake agents by role
        roles_to_wake = INTERRUPT_TRIGGERS.get(event_type, [])
        if not roles_to_wake:
            return queued_ids

        agents = (
            self.db.query(Agent)
            .filter(
                Agent.type.in_(roles_to_wake),
                Agent.status == "active",
            )
            .all()
        )

        priority = CyclePriority.CRITICAL if event_type == "warden_alert" else CyclePriority.INTERRUPT

        for agent in agents:
            result = self.schedule_cycle(
                agent,
                priority=priority,
                trigger_reason=f"interrupt:{event_type}",
            )
            if result.queued:
                queued_ids.append(agent.id)

        return queued_ids

    def get_next(self) -> int | None:
        """Pop the highest-priority agent from the queue.

        Returns:
            Agent ID of the next agent to process, or None if empty.
        """
        if not self.redis:
            return self._pop_from_local_queue()

        try:
            # zpopmax returns [(member, score)] or empty list
            result = self.redis.zpopmax(self.QUEUE_KEY, count=1)
            if result:
                agent_id_str, score = result[0]
                return int(agent_id_str)
        except Exception as e:
            logger.warning(f"Redis queue pop failed: {e}")

        return None

    def queue_size(self) -> int:
        """Get the number of agents in the queue."""
        if not self.redis:
            return len(self._local_queue)

        try:
            return self.redis.zcard(self.QUEUE_KEY)
        except Exception:
            return 0

    def get_cycle_interval(self, agent: Agent) -> int:
        """Get the appropriate cycle interval for an agent.

        Args:
            agent: The agent.

        Returns:
            Cycle interval in seconds.
        """
        role = get_role(agent.type)

        # Operators: shorter interval when they have active positions
        if agent.type == "operator" and role.active_cycle_interval_seconds:
            # Check if operator has active positions (simplified check)
            if agent.capital_current > 0 and agent.capital_current != agent.capital_allocated:
                return role.active_cycle_interval_seconds

        return role.cycle_interval_seconds

    def schedule_all_active(self) -> list[int]:
        """Schedule cycles for all active agents based on their timers.

        Returns:
            List of agent IDs that were queued.
        """
        agents = (
            self.db.query(Agent)
            .filter(Agent.status == "active")
            .all()
        )

        queued = []
        now = datetime.now(timezone.utc)

        for agent in agents:
            # Phase 3B: skip agents not yet oriented
            if not agent.orientation_completed and agent.generation > 0:
                continue

            interval = self.get_cycle_interval(agent)
            if interval <= 0:  # on-demand only (critics)
                continue

            # Check if enough time has passed since last cycle
            if agent.last_cycle_at:
                last_ts = agent.last_cycle_at.replace(tzinfo=timezone.utc)
                elapsed = (now - last_ts).total_seconds()
                if elapsed < interval:
                    continue

            result = self.schedule_cycle(agent, trigger_reason="timer")
            if result.queued:
                queued.append(agent.id)

        return queued

    # ──────────────────────────────────────────────
    # Internal queue management
    # ──────────────────────────────────────────────

    def _enqueue(self, agent_id: int, priority: CyclePriority, reason: str) -> None:
        """Add an agent to the cycle queue."""
        # Score: priority * 1e9 + timestamp for tie-breaking
        score = priority.value * 1_000_000_000 + time.time()

        if self.redis:
            try:
                self.redis.zadd(self.QUEUE_KEY, {str(agent_id): score})
                logger.debug(f"Enqueued agent {agent_id} (priority={priority.name}, reason={reason})")
                return
            except Exception as e:
                logger.warning(f"Redis enqueue failed: {e}")

        # Fallback to local queue
        self._local_queue.append((agent_id, score))
        self._local_queue.sort(key=lambda x: x[1], reverse=True)

    def _pop_from_local_queue(self) -> int | None:
        """Pop from the local fallback queue."""
        if self._local_queue:
            agent_id, _ = self._local_queue.pop(0)
            return agent_id
        return None

    def clear_queue(self) -> None:
        """Clear the entire cycle queue."""
        if self.redis:
            try:
                self.redis.delete(self.QUEUE_KEY)
            except Exception:
                pass
        self._local_queue.clear()
