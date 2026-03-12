"""
Project Syndicate — Memory Manager

Three-tier memory system:
  Tier 1: Working Memory (context window, handled by ContextAssembler)
  Tier 2: Short-Term Memory (Redis, last 50 cycles)
  Tier 3: Long-Term Memory (PostgreSQL, persistent until death)

Handles memory promotion/demotion from reflection cycles,
memory inheritance for offspring, and memory retrieval for context assembly.
"""

__version__ = "0.8.0"

import json
import logging
from datetime import datetime, timezone

from sqlalchemy import desc
from sqlalchemy.orm import Session

from src.common.models import Agent, AgentLongTermMemory, AgentReflection

logger = logging.getLogger(__name__)


class MemoryManager:
    """Manages the three-tier agent memory system."""

    SHORT_TERM_SIZE = 50  # max cycles in Redis

    def __init__(self, db_session: Session, redis_client=None):
        self.db = db_session
        self.redis = redis_client

    # ──────────────────────────────────────────────
    # Tier 2: Short-Term Memory (Redis)
    # ──────────────────────────────────────────────

    def get_recent_cycles(self, agent_id: int, count: int = 10) -> list[dict]:
        """Read recent cycle summaries from Redis short-term memory.

        Args:
            agent_id: The agent ID.
            count: Number of recent cycles to retrieve.

        Returns:
            List of cycle summary dicts, newest first.
        """
        if not self.redis:
            return []

        try:
            key = f"agent:{agent_id}:recent_cycles"
            raw_entries = self.redis.lrange(key, 0, count - 1)
            return [json.loads(entry) for entry in raw_entries]
        except Exception as e:
            logger.debug(f"Redis read failed for agent {agent_id}: {e}")
            return []

    def push_cycle_to_short_term(self, agent_id: int, cycle_data: dict) -> None:
        """Push a cycle summary to Redis short-term memory.

        Args:
            agent_id: The agent ID.
            cycle_data: Cycle summary dict.
        """
        if not self.redis:
            return

        try:
            key = f"agent:{agent_id}:recent_cycles"
            self.redis.lpush(key, json.dumps(cycle_data))
            self.redis.ltrim(key, 0, self.SHORT_TERM_SIZE - 1)
        except Exception as e:
            logger.debug(f"Redis push failed for agent {agent_id}: {e}")

    def clear_short_term(self, agent_id: int) -> None:
        """Clear an agent's short-term memory (used on death/reset)."""
        if not self.redis:
            return
        try:
            self.redis.delete(f"agent:{agent_id}:recent_cycles")
        except Exception:
            pass

    # ──────────────────────────────────────────────
    # Tier 3: Long-Term Memory (PostgreSQL)
    # ──────────────────────────────────────────────

    def get_active_memories(
        self, agent_id: int, limit: int = 20
    ) -> list[AgentLongTermMemory]:
        """Get an agent's active long-term memories, sorted by confidence.

        Args:
            agent_id: The agent ID.
            limit: Maximum number of memories to return.

        Returns:
            List of active memory records.
        """
        return (
            self.db.query(AgentLongTermMemory)
            .filter(
                AgentLongTermMemory.agent_id == agent_id,
                AgentLongTermMemory.is_active == True,
            )
            .order_by(desc(AgentLongTermMemory.confidence))
            .limit(limit)
            .all()
        )

    def add_memory(
        self,
        agent_id: int,
        memory_type: str,
        content: str,
        confidence: float = 0.5,
        source: str = "self",
        source_cycle: int | None = None,
    ) -> AgentLongTermMemory:
        """Add a new long-term memory.

        Args:
            agent_id: The agent ID.
            memory_type: lesson, pattern, relationship, reflection, inherited.
            content: The memory content.
            confidence: How sure the agent is (0.0 to 1.0).
            source: self, parent, grandparent.
            source_cycle: Which cycle created this memory.

        Returns:
            The created memory record.
        """
        mem = AgentLongTermMemory(
            agent_id=agent_id,
            memory_type=memory_type,
            content=content,
            confidence=confidence,
            source=source,
            source_cycle=source_cycle,
        )
        self.db.add(mem)
        self.db.flush()
        logger.debug(f"Memory added for agent {agent_id}: {memory_type} — {content[:80]}")
        return mem

    def promote_memory(self, memory_id: int) -> None:
        """Promote a memory — increase confidence and confirmation count."""
        mem = self.db.query(AgentLongTermMemory).filter(
            AgentLongTermMemory.id == memory_id
        ).first()
        if mem:
            mem.times_confirmed += 1
            mem.confidence = min(1.0, mem.confidence + 0.1)
            mem.promoted_at = datetime.now(timezone.utc)
            self.db.add(mem)
            self.db.flush()

    def demote_memory(self, memory_id: int) -> None:
        """Demote a memory — decrease confidence. Deactivate if confidence drops to 0."""
        mem = self.db.query(AgentLongTermMemory).filter(
            AgentLongTermMemory.id == memory_id
        ).first()
        if mem:
            mem.times_contradicted += 1
            mem.confidence = max(0.0, mem.confidence - 0.15)
            mem.demoted_at = datetime.now(timezone.utc)
            if mem.confidence <= 0.0:
                mem.is_active = False
            self.db.add(mem)
            self.db.flush()

    def deactivate_memory(self, memory_id: int) -> None:
        """Fully deactivate a memory."""
        mem = self.db.query(AgentLongTermMemory).filter(
            AgentLongTermMemory.id == memory_id
        ).first()
        if mem:
            mem.is_active = False
            mem.demoted_at = datetime.now(timezone.utc)
            self.db.add(mem)
            self.db.flush()

    # ──────────────────────────────────────────────
    # Reflection Processing
    # ──────────────────────────────────────────────

    def process_reflection(
        self, agent_id: int, cycle_number: int, reflection: dict
    ) -> AgentReflection:
        """Process a reflection cycle output: store the reflection and
        handle memory promotions/demotions.

        Args:
            agent_id: The agent ID.
            cycle_number: The reflection cycle number.
            reflection: Parsed reflection output dict.

        Returns:
            The created AgentReflection record.
        """
        # Store the reflection
        ref = AgentReflection(
            agent_id=agent_id,
            cycle_number=cycle_number,
            what_worked=reflection.get("what_worked"),
            what_failed=reflection.get("what_failed"),
            pattern_detected=reflection.get("pattern_detected"),
            lesson=reflection.get("lesson"),
            confidence_trend=reflection.get("confidence_trend"),
            confidence_reason=reflection.get("confidence_reason"),
            strategy_note=reflection.get("strategy_note"),
            memory_promotions=reflection.get("memory_promotion", []),
            memory_demotions=reflection.get("memory_demotion", []),
        )
        self.db.add(ref)
        self.db.flush()

        # Add the lesson as a new long-term memory
        lesson = reflection.get("lesson")
        if lesson:
            self.add_memory(
                agent_id=agent_id,
                memory_type="lesson",
                content=lesson,
                confidence=0.6,
                source="self",
                source_cycle=cycle_number,
            )

        # Add detected pattern
        pattern = reflection.get("pattern_detected")
        if pattern and pattern.strip():
            self.add_memory(
                agent_id=agent_id,
                memory_type="pattern",
                content=pattern,
                confidence=0.5,
                source="self",
                source_cycle=cycle_number,
            )

        # Store reflection summary as a memory
        summary = reflection.get("strategy_note")
        if summary and summary.strip():
            self.add_memory(
                agent_id=agent_id,
                memory_type="reflection",
                content=summary,
                confidence=0.5,
                source="self",
                source_cycle=cycle_number,
            )

        # Process memory promotions (by content match)
        for promo_text in reflection.get("memory_promotion", []):
            self._promote_by_content(agent_id, promo_text)

        # Process memory demotions (by content match)
        for demo_text in reflection.get("memory_demotion", []):
            self._demote_by_content(agent_id, demo_text)

        logger.info(f"Reflection processed for agent {agent_id}, cycle {cycle_number}")
        return ref

    def _promote_by_content(self, agent_id: int, content_fragment: str) -> None:
        """Promote a memory by fuzzy content match."""
        mem = (
            self.db.query(AgentLongTermMemory)
            .filter(
                AgentLongTermMemory.agent_id == agent_id,
                AgentLongTermMemory.is_active == True,
                AgentLongTermMemory.content.contains(content_fragment[:100]),
            )
            .first()
        )
        if mem:
            self.promote_memory(mem.id)

    def _demote_by_content(self, agent_id: int, content_fragment: str) -> None:
        """Demote a memory by fuzzy content match."""
        mem = (
            self.db.query(AgentLongTermMemory)
            .filter(
                AgentLongTermMemory.agent_id == agent_id,
                AgentLongTermMemory.is_active == True,
                AgentLongTermMemory.content.contains(content_fragment[:100]),
            )
            .first()
        )
        if mem:
            self.demote_memory(mem.id)

    # ──────────────────────────────────────────────
    # Memory Inheritance
    # ──────────────────────────────────────────────

    def inherit_memories(self, parent_id: int, offspring_id: int) -> int:
        """Copy a parent's active long-term memories to an offspring.

        Used during reproduction to pass accumulated wisdom to next generation.

        Args:
            parent_id: The parent agent's ID.
            offspring_id: The offspring agent's ID.

        Returns:
            Number of memories inherited.
        """
        parent_memories = self.get_active_memories(parent_id, limit=50)
        count = 0

        for mem in parent_memories:
            # Reduce confidence slightly — inherited wisdom is less certain
            inherited_confidence = max(0.1, mem.confidence * 0.8)
            self.add_memory(
                agent_id=offspring_id,
                memory_type=mem.memory_type,
                content=mem.content,
                confidence=inherited_confidence,
                source="parent",
                source_cycle=mem.source_cycle,
            )
            count += 1

        # Also try to inherit grandparent memories (if they exist and are marked as parent-sourced)
        grandparent_memories = (
            self.db.query(AgentLongTermMemory)
            .filter(
                AgentLongTermMemory.agent_id == parent_id,
                AgentLongTermMemory.source == "parent",
                AgentLongTermMemory.is_active == True,
            )
            .all()
        )
        for mem in grandparent_memories:
            inherited_confidence = max(0.1, mem.confidence * 0.6)
            self.add_memory(
                agent_id=offspring_id,
                memory_type=mem.memory_type,
                content=mem.content,
                confidence=inherited_confidence,
                source="grandparent",
                source_cycle=mem.source_cycle,
            )
            count += 1

        logger.info(f"Inherited {count} memories from agent {parent_id} to {offspring_id}")
        return count
