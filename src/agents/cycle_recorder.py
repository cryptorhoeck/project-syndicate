"""
Project Syndicate — Cycle Recorder

Phase 5 (RECORD) of the OODA loop — the Black Box.
Writes everything from a cycle to permanent storage:
  1. PostgreSQL agent_cycles table
  2. Agora activity channel
  3. Redis short-term memory
  4. Agent running stats
"""

__version__ = "0.8.0"

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from src.common.models import Agent, AgentCycle, Message

logger = logging.getLogger(__name__)

SHORT_TERM_MEMORY_SIZE = 50  # keep last 50 cycles in Redis


@dataclass
class CycleData:
    """All data from a single thinking cycle."""
    agent_id: int
    agent_name: str
    generation: int
    cycle_number: int
    cycle_type: str  # normal, reflection, survival
    context_mode: str
    context_tokens: int
    situation: str | None = None
    confidence_score: int | None = None
    confidence_reason: str | None = None
    recent_pattern: str | None = None
    action_type: str | None = None
    action_params: dict | None = None
    reasoning: str | None = None
    self_note: str | None = None
    validation_passed: bool = True
    validation_retries: int = 0
    warden_flags: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    api_cost_usd: float = 0.0
    cycle_duration_ms: int = 0
    api_latency_ms: int = 0


class CycleRecorder:
    """Records every thinking cycle to the permanent black box."""

    def __init__(self, db_session: Session, redis_client=None, agora_service=None):
        self.db = db_session
        self.redis = redis_client
        self.agora = agora_service

    def record(self, data: CycleData) -> AgentCycle:
        """Record a complete cycle to all storage tiers.

        Args:
            data: Full cycle data.

        Returns:
            The created AgentCycle record.
        """
        # 1. Write to PostgreSQL
        cycle = AgentCycle(
            agent_id=data.agent_id,
            cycle_number=data.cycle_number,
            cycle_type=data.cycle_type,
            context_mode=data.context_mode,
            context_tokens=data.context_tokens,
            situation=data.situation,
            confidence_score=data.confidence_score,
            confidence_reason=data.confidence_reason,
            recent_pattern=data.recent_pattern,
            action_type=data.action_type,
            action_params=data.action_params,
            reasoning=data.reasoning,
            self_note=data.self_note,
            validation_passed=data.validation_passed,
            validation_retries=data.validation_retries,
            warden_flags=data.warden_flags,
            input_tokens=data.input_tokens,
            output_tokens=data.output_tokens,
            api_cost_usd=data.api_cost_usd,
            cycle_duration_ms=data.cycle_duration_ms,
            api_latency_ms=data.api_latency_ms,
        )
        self.db.add(cycle)
        self.db.flush()

        # 2. Post summary to Agora
        self._post_to_agora(data)

        # 3. Update short-term memory in Redis
        self._update_short_term_memory(data)

        # 4. Update agent running stats
        self._update_agent_stats(data)

        logger.info(
            "cycle_recorded",
            extra={
                "agent_id": data.agent_id,
                "cycle": data.cycle_number,
                "type": data.cycle_type,
                "action": data.action_type,
                "cost": data.api_cost_usd,
            },
        )

        return cycle

    def record_failed(self, data: CycleData) -> AgentCycle:
        """Record a failed cycle (validation failed, no action taken).

        Args:
            data: Partial cycle data (validation_passed should be False).

        Returns:
            The created AgentCycle record.
        """
        data.validation_passed = False
        data.action_type = None
        data.action_params = None
        return self.record(data)

    def _post_to_agora(self, data: CycleData) -> None:
        """Post a cycle summary to the Agora agent-activity channel."""
        action = data.action_type or "no_action"
        if data.cycle_type == "reflection":
            summary = f"{data.agent_name} completed reflection cycle #{data.cycle_number}"
        elif not data.validation_passed:
            summary = f"{data.agent_name} cycle #{data.cycle_number} failed validation"
        else:
            summary = f"{data.agent_name}: {action}"
            if data.situation:
                summary += f" — {data.situation[:100]}"

        if self.agora:
            try:
                self.agora.post_system_message(
                    channel="agent-activity",
                    content=summary,
                    metadata={
                        "agent_id": data.agent_id,
                        "cycle": data.cycle_number,
                        "action": data.action_type,
                        "cycle_type": data.cycle_type,
                    },
                )
            except Exception as e:
                logger.debug(f"Agora post failed: {e}")
        else:
            # Write directly if no Agora service
            try:
                msg = Message(
                    agent_id=data.agent_id,
                    agent_name=data.agent_name,
                    channel="agent-activity",
                    content=summary,
                    message_type="system",
                    metadata_json={
                        "cycle": data.cycle_number,
                        "action": data.action_type,
                    },
                )
                self.db.add(msg)
                self.db.flush()
            except Exception as e:
                logger.debug(f"Direct Agora write failed: {e}")

    def _update_short_term_memory(self, data: CycleData) -> None:
        """Push cycle summary to Redis short-term memory."""
        if not self.redis:
            return

        try:
            key = f"agent:{data.agent_id}:recent_cycles"
            cycle_summary = json.dumps({
                "cycle_number": data.cycle_number,
                "cycle_type": data.cycle_type,
                "action_type": data.action_type,
                "confidence_score": data.confidence_score,
                "situation": data.situation[:300] if data.situation else None,
                "self_note": data.self_note,
                "api_cost_usd": data.api_cost_usd,
                "validation_passed": data.validation_passed,
            })
            self.redis.lpush(key, cycle_summary)
            self.redis.ltrim(key, 0, SHORT_TERM_MEMORY_SIZE - 1)
        except Exception as e:
            logger.debug(f"Redis short-term memory update failed: {e}")

    def _update_agent_stats(self, data: CycleData) -> None:
        """Update the agent's running statistics."""
        agent = self.db.query(Agent).filter(Agent.id == data.agent_id).first()
        if not agent:
            return

        agent.cycle_count = data.cycle_number + 1
        agent.last_cycle_at = datetime.now(timezone.utc)
        agent.total_api_cost += data.api_cost_usd
        agent.thinking_budget_used_today += data.api_cost_usd
        agent.current_context_mode = data.context_mode

        # Update rolling averages
        n = agent.cycle_count
        if n > 0:
            # Rolling avg cost
            agent.avg_cycle_cost = (
                (agent.avg_cycle_cost * (n - 1) + data.api_cost_usd) / n
            )
            # Rolling avg tokens
            total_tokens = data.input_tokens + data.output_tokens
            agent.avg_cycle_tokens = int(
                (agent.avg_cycle_tokens * (n - 1) + total_tokens) / n
            )

        # Update idle rate
        if data.action_type == "go_idle":
            # Incremental idle rate
            idle_count = agent.idle_rate * (n - 1) + 1
            agent.idle_rate = idle_count / n
        elif n > 1:
            idle_count = agent.idle_rate * (n - 1)
            agent.idle_rate = idle_count / n

        # Update validation fail rate
        if not data.validation_passed:
            fail_count = agent.validation_fail_rate * (n - 1) + 1
            agent.validation_fail_rate = fail_count / n
        elif n > 1:
            fail_count = agent.validation_fail_rate * (n - 1)
            agent.validation_fail_rate = fail_count / n

        self.db.add(agent)
        self.db.flush()
