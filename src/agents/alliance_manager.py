"""
Project Syndicate — Alliance Manager

Handles alliance proposals, acceptance, dissolution, and context injection.
Alliances are public, non-binding, and provide trust/relevance bonuses.
"""

__version__ = "0.1.0"

import logging
from datetime import datetime, timezone

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from src.common.config import config
from src.common.models import Agent, AgentAlliance

logger = logging.getLogger(__name__)


class AllianceManager:
    """Manages the alliance lifecycle between agents."""

    async def propose_alliance(
        self,
        proposer: Agent,
        target_name: str,
        offer: str,
        request: str,
        db_session: Session,
    ) -> dict:
        """Create an alliance proposal.

        Returns dict with success status and alliance info.
        """
        target = db_session.execute(
            select(Agent).where(Agent.name == target_name, Agent.status == "active")
        ).scalar_one_or_none()

        if not target:
            return {"success": False, "error": f"Agent '{target_name}' not found or not active"}

        if target.id == proposer.id:
            return {"success": False, "error": "Cannot ally with yourself"}

        # Check for existing alliance
        existing = db_session.execute(
            select(AgentAlliance).where(
                AgentAlliance.status.in_(["proposed", "active"]),
                or_(
                    (AgentAlliance.proposer_agent_id == proposer.id) & (AgentAlliance.target_agent_id == target.id),
                    (AgentAlliance.proposer_agent_id == target.id) & (AgentAlliance.target_agent_id == proposer.id),
                ),
            )
        ).scalar_one_or_none()

        if existing:
            return {"success": False, "error": "Alliance already exists or proposed between these agents"}

        alliance = AgentAlliance(
            proposer_agent_id=proposer.id,
            proposer_agent_name=proposer.name,
            target_agent_id=target.id,
            target_agent_name=target.name,
            proposer_offer=offer,
            proposer_request=request,
            status="proposed",
        )
        db_session.add(alliance)
        db_session.flush()

        return {"success": True, "alliance_id": alliance.id, "target": target.name}

    async def accept_alliance(
        self, accepting_agent: Agent, alliance_id: int, db_session: Session
    ) -> dict:
        """Accept a pending alliance proposal."""
        alliance = db_session.get(AgentAlliance, alliance_id)
        if not alliance:
            return {"success": False, "error": "Alliance not found"}

        if alliance.status != "proposed":
            return {"success": False, "error": f"Alliance status is '{alliance.status}', not proposed"}

        if alliance.target_agent_id != accepting_agent.id:
            return {"success": False, "error": "Only the target can accept an alliance"}

        alliance.status = "active"
        alliance.accepted_at = datetime.now(timezone.utc)
        db_session.flush()

        return {"success": True, "alliance_id": alliance.id}

    async def dissolve_alliance(
        self, dissolving_agent: Agent, alliance_id: int, reason: str, db_session: Session
    ) -> dict:
        """Dissolve an active alliance."""
        alliance = db_session.get(AgentAlliance, alliance_id)
        if not alliance:
            return {"success": False, "error": "Alliance not found"}

        if alliance.status != "active":
            return {"success": False, "error": f"Alliance status is '{alliance.status}', not active"}

        if dissolving_agent.id not in (alliance.proposer_agent_id, alliance.target_agent_id):
            return {"success": False, "error": "Only alliance members can dissolve"}

        alliance.status = "dissolved"
        alliance.dissolved_at = datetime.now(timezone.utc)
        alliance.dissolved_by = dissolving_agent.id
        alliance.dissolution_reason = reason
        db_session.flush()

        return {"success": True}

    async def auto_dissolve_on_death(self, dead_agent_id: int, db_session: Session) -> int:
        """Dissolve all active alliances involving a dead agent. Returns count."""
        alliances = list(
            db_session.execute(
                select(AgentAlliance).where(
                    AgentAlliance.status == "active",
                    or_(
                        AgentAlliance.proposer_agent_id == dead_agent_id,
                        AgentAlliance.target_agent_id == dead_agent_id,
                    ),
                )
            ).scalars().all()
        )

        for alliance in alliances:
            alliance.status = "dissolved"
            alliance.dissolved_at = datetime.now(timezone.utc)
            alliance.dissolved_by = dead_agent_id
            alliance.dissolution_reason = "Partner terminated"

        db_session.flush()
        return len(alliances)

    async def get_alliance_context(self, agent_id: int, db_session: Session) -> str:
        """Build alliance context for prompt injection."""
        # Active alliances
        active = list(
            db_session.execute(
                select(AgentAlliance).where(
                    AgentAlliance.status == "active",
                    or_(
                        AgentAlliance.proposer_agent_id == agent_id,
                        AgentAlliance.target_agent_id == agent_id,
                    ),
                )
            ).scalars().all()
        )

        # Pending proposals TO this agent
        proposals = list(
            db_session.execute(
                select(AgentAlliance).where(
                    AgentAlliance.status == "proposed",
                    AgentAlliance.target_agent_id == agent_id,
                )
            ).scalars().all()
        )

        if not active and not proposals:
            return ""

        lines = []
        if active:
            lines.append("ACTIVE ALLIANCES:")
            for a in active:
                partner_name = a.target_agent_name if a.proposer_agent_id == agent_id else a.proposer_agent_name
                partner_id = a.target_agent_id if a.proposer_agent_id == agent_id else a.proposer_agent_id
                partner = db_session.get(Agent, partner_id)
                if partner:
                    lines.append(
                        f"  - Allied with {partner_name} ({partner.type}). "
                        f"Rank: composite {partner.composite_score or 0:.2f}. P&L: ${partner.total_true_pnl or 0:.2f}."
                    )

        if proposals:
            lines.append("ALLIANCE PROPOSALS (awaiting your response):")
            for p in proposals:
                lines.append(
                    f"  - #{p.id} from {p.proposer_agent_name}: "
                    f"Offer: {p.proposer_offer[:100]}. Request: {p.proposer_request[:100]}."
                )

        return "\n".join(lines)

    async def get_alliance_trust_bonus(
        self, agent_id: int, target_id: int, db_session: Session
    ) -> float:
        """Returns trust bonus if agents are allied (+0.1), else 0.0."""
        alliance = db_session.execute(
            select(AgentAlliance).where(
                AgentAlliance.status == "active",
                or_(
                    (AgentAlliance.proposer_agent_id == agent_id) & (AgentAlliance.target_agent_id == target_id),
                    (AgentAlliance.proposer_agent_id == target_id) & (AgentAlliance.target_agent_id == agent_id),
                ),
            )
        ).scalar_one_or_none()

        return config.alliance_trust_bonus if alliance else 0.0
