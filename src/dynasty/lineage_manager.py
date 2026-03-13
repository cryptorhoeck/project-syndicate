"""
Project Syndicate — Lineage Manager (Phase 3F)

Creates and manages lineage records — the individual entries in a dynasty's family tree.
"""

__version__ = "1.2.0"

import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.common.models import (
    Agent, BehavioralProfile, Evaluation, Lineage,
)

logger = logging.getLogger(__name__)


class LineageManager:
    """Manages individual lineage records within dynasties."""

    def __init__(self) -> None:
        self.log = logger

    async def create_lineage_record(
        self,
        session: Session,
        agent: Agent,
        parent: Agent | None = None,
        mentor_package: dict | None = None,
        mutations: dict | None = None,
        founding_directive: str | None = None,
    ) -> Lineage:
        """Create or update lineage record for an agent."""
        # Check if record already exists (boot sequence may have created it)
        existing = session.get(Lineage, agent.id)
        if existing:
            # Update existing record with Phase 3F fields
            existing.agent_name = agent.name
            existing.dynasty_id = agent.dynasty_id
            existing.grandparent_id = parent.parent_id if parent else None
            existing.mutations_applied = mutations
            existing.founding_directive = founding_directive
            existing.posthumous_birth = agent.posthumous_birth
            if mentor_package:
                import json
                existing.mentor_package_json = json.dumps(mentor_package)
                existing.mentor_package_generated_at = datetime.now(timezone.utc)
            if parent:
                existing.inherited_temperature = agent.api_temperature
                existing.parent_composite_at_reproduction = parent.composite_score
                existing.parent_prestige_at_reproduction = parent.prestige_title
                existing.parent_profile_snapshot = self._get_profile_snapshot(session, parent.id)
            # Count inherited memories
            from src.common.models import AgentLongTermMemory
            inherited_count = session.execute(
                select(AgentLongTermMemory).where(
                    AgentLongTermMemory.agent_id == agent.id,
                    AgentLongTermMemory.source.in_(["parent", "grandparent"]),
                )
            ).scalars().all()
            existing.inherited_memories_count = len(inherited_count)
            session.add(existing)
            return existing

        # Build lineage path
        lineage_path = str(agent.id)
        if parent:
            parent_lineage = session.get(Lineage, parent.id)
            if parent_lineage and parent_lineage.lineage_path:
                lineage_path = f"{parent_lineage.lineage_path}/{agent.id}"

        lineage = Lineage(
            agent_id=agent.id,
            agent_name=agent.name,
            parent_id=parent.id if parent else None,
            grandparent_id=parent.parent_id if parent else None,
            dynasty_id=agent.dynasty_id,
            generation=agent.generation,
            lineage_path=lineage_path,
            mutations_applied=mutations,
            founding_directive=founding_directive,
            posthumous_birth=agent.posthumous_birth,
            inherited_temperature=agent.api_temperature if parent else None,
        )

        if parent:
            lineage.parent_composite_at_reproduction = parent.composite_score
            lineage.parent_prestige_at_reproduction = parent.prestige_title
            lineage.parent_profile_snapshot = self._get_profile_snapshot(session, parent.id)

        if mentor_package:
            import json
            lineage.mentor_package_json = json.dumps(mentor_package)
            lineage.mentor_package_generated_at = datetime.now(timezone.utc)

        session.add(lineage)
        session.flush()
        self.log.info(f"Lineage record created for {agent.name} (Gen {agent.generation})")
        return lineage

    async def record_death(
        self, session: Session, agent: Agent, evaluation: Evaluation | None = None,
    ) -> None:
        """Update lineage record when agent dies."""
        lineage = session.get(Lineage, agent.id)
        if not lineage:
            self.log.warning(f"No lineage record for agent {agent.id}")
            return

        now = datetime.now(timezone.utc)
        lineage.died_at = now
        lineage.cause_of_death = (
            agent.termination_reason
            or (evaluation.genesis_decision if evaluation else None)
            or "unknown"
        )
        if agent.created_at:
            created = agent.created_at
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            lineage.lifespan_days = (now - created).total_seconds() / 86400
        lineage.final_composite = agent.composite_score
        lineage.final_pnl = (agent.realized_pnl or 0) + (agent.unrealized_pnl or 0)
        lineage.final_prestige = agent.prestige_title
        session.add(lineage)

    async def get_family_tree(self, session: Session, dynasty_id: int) -> list[dict]:
        """Get full family tree for a dynasty as a hierarchical structure."""
        lineages = session.execute(
            select(Lineage).where(Lineage.dynasty_id == dynasty_id)
            .order_by(Lineage.generation)
        ).scalars().all()

        # Build lookup and tree
        by_id: dict[int, dict] = {}
        roots: list[dict] = []

        for lin in lineages:
            agent = session.get(Agent, lin.agent_id)
            node = {
                "agent_id": lin.agent_id,
                "agent_name": lin.agent_name or (agent.name if agent else f"Agent-{lin.agent_id}"),
                "generation": lin.generation,
                "status": agent.status if agent else "unknown",
                "composite_score": agent.composite_score if agent else None,
                "prestige": agent.prestige_title if agent else lin.final_prestige,
                "lifespan_days": lin.lifespan_days,
                "cause_of_death": lin.cause_of_death,
                "children": [],
            }
            by_id[lin.agent_id] = node

            if lin.parent_id and lin.parent_id in by_id:
                by_id[lin.parent_id]["children"].append(node)
            else:
                roots.append(node)

        return roots

    async def get_ancestors(
        self, session: Session, agent_id: int, depth: int = 3,
    ) -> list[Lineage]:
        """Get lineage chain: parent, grandparent, great-grandparent."""
        chain: list[Lineage] = []
        current = session.get(Lineage, agent_id)

        for _ in range(depth):
            if current and current.parent_id:
                parent_lineage = session.get(Lineage, current.parent_id)
                if parent_lineage:
                    chain.append(parent_lineage)
                    current = parent_lineage
                else:
                    break
            else:
                break

        return chain

    def _get_profile_snapshot(self, session: Session, agent_id: int) -> dict | None:
        """Get latest behavioral profile as a dict snapshot."""
        profile = session.execute(
            select(BehavioralProfile)
            .where(BehavioralProfile.agent_id == agent_id)
            .order_by(BehavioralProfile.created_at.desc())
            .limit(1)
        ).scalar_one_or_none()

        if not profile:
            return None

        return {
            "risk_appetite": profile.risk_appetite_score,
            "market_focus_entropy": profile.market_focus_entropy,
            "decision_style": profile.decision_style_score,
            "collaboration": profile.collaboration_score,
            "learning_velocity": profile.learning_velocity_score,
            "resilience": profile.resilience_score,
            "is_complete": profile.is_complete,
            "dominant_regime": profile.dominant_regime,
        }
