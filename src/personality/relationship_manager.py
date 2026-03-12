"""
Project Syndicate — Relationship Manager (Phase 3E)

Formalized trust scoring between agents based on pipeline outcomes.
Trust forms automatically — agents don't choose who to trust.
"""

__version__ = "1.1.0"

import logging
import re
from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from src.common.config import config
from src.common.models import (
    Agent, AgentRelationship, Opportunity, Plan, Position,
)

logger = logging.getLogger(__name__)

# Simple positive/negative word sets for self-note sentiment
_POSITIVE_WORDS = frozenset({
    "good", "great", "reliable", "helpful", "accurate", "profitable",
    "trust", "solid", "strong", "quality", "excellent", "useful",
    "correct", "right", "valuable", "insightful", "precise",
})
_NEGATIVE_WORDS = frozenset({
    "bad", "wrong", "unreliable", "inaccurate", "loss", "poor",
    "risky", "misleading", "failed", "useless", "incorrect",
    "distrust", "costly", "harmful", "dangerous", "flawed",
})


class RelationshipManager:
    """Manages trust relationships between agents."""

    def __init__(self) -> None:
        self.log = logger

    async def record_interaction(
        self,
        session: Session,
        agent_id: int,
        target_agent_id: int,
        outcome: str,  # "positive" or "negative"
    ) -> AgentRelationship:
        """Record a single interaction and update trust score."""
        rel = self._get_or_create(session, agent_id, target_agent_id)

        now = datetime.now(timezone.utc)
        rel.interaction_count += 1
        rel.last_interaction_at = now

        if outcome == "positive":
            rel.positive_outcomes += 1
        else:
            rel.negative_outcomes += 1

        # Recalculate trust with time decay
        rel.trust_score = self._calculate_trust(
            rel.positive_outcomes,
            rel.negative_outcomes,
            rel.interaction_count,
            rel.last_interaction_at,
            rel.created_at,
        )

        session.add(rel)
        return rel

    async def update_from_pipeline_outcome(
        self,
        session: Session,
        position: Position,
    ) -> list[AgentRelationship]:
        """Update trust relationships when a trade closes."""
        updated = []
        if not position.realized_pnl:
            return updated

        is_profitable = position.realized_pnl > 0
        outcome = "positive" if is_profitable else "negative"
        operator_id = position.agent_id

        # Trace pipeline: position → plan → opportunity
        plan = None
        if position.source_plan_id:
            plan = session.get(Plan, position.source_plan_id)

        if plan:
            # Operator → Strategist trust
            if plan.strategist_agent_id and plan.strategist_agent_id != operator_id:
                rel = await self.record_interaction(
                    session, operator_id, plan.strategist_agent_id, outcome,
                )
                updated.append(rel)

            # Strategist → Scout trust (via opportunity)
            if plan.opportunity_id and plan.strategist_agent_id:
                opp = session.get(Opportunity, plan.opportunity_id)
                if opp and opp.scout_agent_id != plan.strategist_agent_id:
                    rel = await self.record_interaction(
                        session, plan.strategist_agent_id,
                        opp.scout_agent_id, outcome,
                    )
                    updated.append(rel)

            # Strategist → Critic trust
            if plan.critic_agent_id and plan.strategist_agent_id:
                if plan.critic_verdict == "approved":
                    # Critic approved: if profitable → positive, if loss → negative
                    rel = await self.record_interaction(
                        session, plan.strategist_agent_id,
                        plan.critic_agent_id, outcome,
                    )
                    updated.append(rel)

        return updated

    async def update_from_self_note(
        self,
        session: Session,
        agent_id: int,
        self_note_text: str,
    ) -> list[AgentRelationship]:
        """Extract agent mentions from self-notes and log sentiment."""
        if not self_note_text:
            return []

        updated = []

        # Get all active agent names
        agents = session.execute(
            select(Agent).where(
                Agent.id != agent_id,
                Agent.status.in_(["active", "frozen"]),
            )
        ).scalars().all()

        for target in agents:
            if target.name.lower() in self_note_text.lower():
                # Determine sentiment from surrounding context
                sentiment = self._classify_sentiment(self_note_text, target.name)
                if sentiment:
                    rel = await self.record_interaction(
                        session, agent_id, target.id, sentiment,
                    )
                    rel.last_assessment = self_note_text[:200]
                    session.add(rel)
                    updated.append(rel)

        return updated

    async def get_trust_summary(
        self,
        session: Session,
        agent_id: int,
    ) -> list[dict]:
        """Get formatted trust relationships for context injection."""
        relationships = session.execute(
            select(AgentRelationship).where(
                AgentRelationship.agent_id == agent_id,
                AgentRelationship.archived == False,
                AgentRelationship.interaction_count >= config.trust_min_interactions_to_show,
            ).order_by(AgentRelationship.trust_score.desc())
        ).scalars().all()

        # Filter: only include relationships where target is still active
        result = []
        for r in relationships:
            target = session.get(Agent, r.target_agent_id)
            if target and target.status in ("active", "frozen"):
                if r.trust_score > 0.65:
                    status = "trusted"
                elif r.trust_score > 0.35:
                    status = "neutral"
                else:
                    status = "distrusted"

                result.append({
                    "agent_name": r.target_agent_name,
                    "trust": round(r.trust_score, 2),
                    "positive": r.positive_outcomes,
                    "negative": r.negative_outcomes,
                    "status": status,
                })

        return result

    async def archive_dead_agent_relationships(
        self,
        session: Session,
        dead_agent_id: int,
    ) -> None:
        """Archive all relationships involving a dead agent."""
        now = datetime.now(timezone.utc)

        # Mark relationships where dead agent is the target
        session.execute(
            update(AgentRelationship).where(
                AgentRelationship.target_agent_id == dead_agent_id,
                AgentRelationship.archived == False,
            ).values(
                archived=True,
                archived_at=now,
                archive_reason="target_agent_terminated",
            )
        )

        # Mark relationships held by the dead agent
        session.execute(
            update(AgentRelationship).where(
                AgentRelationship.agent_id == dead_agent_id,
                AgentRelationship.archived == False,
            ).values(
                archived=True,
                archived_at=now,
                archive_reason="holder_agent_terminated",
            )
        )

        self.log.info(
            "relationships_archived",
            extra={"dead_agent_id": dead_agent_id},
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_or_create(
        self,
        session: Session,
        agent_id: int,
        target_agent_id: int,
    ) -> AgentRelationship:
        """Get or create a relationship record."""
        rel = session.execute(
            select(AgentRelationship).where(
                AgentRelationship.agent_id == agent_id,
                AgentRelationship.target_agent_id == target_agent_id,
            )
        ).scalar_one_or_none()

        if rel is None:
            target = session.get(Agent, target_agent_id)
            target_name = target.name if target else f"Agent-{target_agent_id}"
            rel = AgentRelationship(
                agent_id=agent_id,
                target_agent_id=target_agent_id,
                target_agent_name=target_name,
                trust_score=config.trust_prior,
            )
            session.add(rel)
            session.flush()

        return rel

    def _calculate_trust(
        self,
        positive: int,
        negative: int,
        interaction_count: int,
        last_interaction: datetime | None,
        created_at: datetime | None,
    ) -> float:
        """Bayesian trust with time decay."""
        if interaction_count == 0:
            return config.trust_prior

        decay = config.trust_decay_factor
        prior = config.trust_prior
        now = datetime.now(timezone.utc)

        # Simple weight: more recent interactions matter more
        # Use a simplified model since we track counts, not individual timestamps
        if last_interaction:
            days_since = max(0, (now - last_interaction).total_seconds() / 86400)
        else:
            days_since = 0

        recency_weight = decay ** days_since

        # Bayesian: prior pulls toward 0.5, evidence pulls toward reality
        prior_weight = 2  # Equivalent to 2 neutral observations
        weighted_positive = positive * recency_weight
        weighted_total = (positive + negative) * recency_weight

        trust = (weighted_positive + prior_weight * prior) / (weighted_total + prior_weight)
        return max(0.0, min(1.0, trust))

    def _classify_sentiment(self, text: str, agent_name: str) -> str | None:
        """Classify sentiment of text around an agent name mention."""
        text_lower = text.lower()
        name_lower = agent_name.lower()

        # Find position of agent name
        idx = text_lower.find(name_lower)
        if idx == -1:
            return None

        # Get context window around the name (50 chars each side)
        start = max(0, idx - 50)
        end = min(len(text_lower), idx + len(name_lower) + 50)
        context = text_lower[start:end]

        # Count positive/negative words in context
        words = set(re.findall(r'\b\w+\b', context))
        pos_count = len(words & _POSITIVE_WORDS)
        neg_count = len(words & _NEGATIVE_WORDS)

        if pos_count > neg_count:
            return "positive"
        elif neg_count > pos_count:
            return "negative"
        return None  # Neutral — don't record
