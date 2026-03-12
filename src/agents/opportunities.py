"""
Project Syndicate — Opportunities Manager

Manages the opportunity lifecycle in the Scout → Strategist pipeline:
  - Scouts create opportunities when they broadcast signals
  - Strategists claim opportunities to build plans
  - Opportunities expire after a TTL if unclaimed
  - Claimed opportunities convert to plans
"""

__version__ = "0.9.0"

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import desc
from sqlalchemy.orm import Session

from src.common.models import Agent, Message, Opportunity

logger = logging.getLogger(__name__)

# Default opportunity TTL
OPPORTUNITY_TTL_HOURS = 6


class OpportunityManager:
    """Manages the Scout → Strategist opportunity pipeline."""

    def __init__(self, db_session: Session):
        self.db = db_session

    def create_opportunity(
        self,
        scout: Agent,
        market: str,
        signal_type: str,
        details: str,
        urgency: str = "medium",
        confidence: int = 5,
        agora_message_id: int | None = None,
        ttl_hours: int = OPPORTUNITY_TTL_HOURS,
    ) -> Opportunity:
        """Create a new opportunity from a Scout broadcast.

        Args:
            scout: The Scout agent creating this opportunity.
            market: Trading pair (e.g., "SOL/USDT").
            signal_type: Type of signal detected.
            details: Description of the opportunity.
            urgency: low/medium/high.
            confidence: 1-10 confidence score.
            agora_message_id: Optional linked Agora message.
            ttl_hours: Hours until expiry if unclaimed.

        Returns:
            The created Opportunity record.
        """
        opp = Opportunity(
            scout_agent_id=scout.id,
            scout_agent_name=scout.name,
            market=market,
            signal_type=signal_type,
            details=details,
            urgency=urgency,
            confidence=confidence,
            status="new",
            agora_message_id=agora_message_id,
            expires_at=datetime.now(timezone.utc) + timedelta(hours=ttl_hours),
        )
        self.db.add(opp)
        self.db.flush()
        logger.info(f"Opportunity created: {market} ({signal_type}) by {scout.name}")
        return opp

    def claim_opportunity(
        self,
        opportunity_id: int,
        strategist: Agent,
    ) -> Opportunity | None:
        """Claim an opportunity for plan development.

        Args:
            opportunity_id: The opportunity to claim.
            strategist: The Strategist claiming it.

        Returns:
            The claimed Opportunity, or None if not available.
        """
        opp = self.db.query(Opportunity).filter(
            Opportunity.id == opportunity_id,
            Opportunity.status == "new",
        ).first()

        if not opp:
            return None

        opp.status = "claimed"
        opp.claimed_by_agent_id = strategist.id
        opp.claimed_at = datetime.now(timezone.utc)
        self.db.add(opp)
        self.db.flush()
        logger.info(f"Opportunity {opportunity_id} claimed by {strategist.name}")
        return opp

    def convert_to_plan(self, opportunity_id: int, plan_id: int) -> None:
        """Mark an opportunity as converted to a plan.

        Args:
            opportunity_id: The opportunity.
            plan_id: The resulting plan ID.
        """
        opp = self.db.query(Opportunity).filter(
            Opportunity.id == opportunity_id,
        ).first()
        if opp:
            opp.status = "converted"
            opp.converted_to_plan_id = plan_id
            self.db.add(opp)
            self.db.flush()

    def get_unclaimed(
        self,
        market: str | None = None,
        urgency: str | None = None,
        limit: int = 10,
    ) -> list[Opportunity]:
        """Get unclaimed, non-expired opportunities.

        Args:
            market: Optional market filter.
            urgency: Optional urgency filter.
            limit: Max results.

        Returns:
            List of available opportunities, newest first.
        """
        now = datetime.now(timezone.utc)
        query = self.db.query(Opportunity).filter(
            Opportunity.status == "new",
            Opportunity.expires_at > now,
        )

        if market:
            query = query.filter(Opportunity.market == market)
        if urgency:
            query = query.filter(Opportunity.urgency == urgency)

        return query.order_by(desc(Opportunity.created_at)).limit(limit).all()

    def get_by_scout(self, scout_id: int, limit: int = 20) -> list[Opportunity]:
        """Get opportunities created by a specific Scout.

        Args:
            scout_id: The Scout agent ID.
            limit: Max results.

        Returns:
            List of opportunities by this Scout.
        """
        return (
            self.db.query(Opportunity)
            .filter(Opportunity.scout_agent_id == scout_id)
            .order_by(desc(Opportunity.created_at))
            .limit(limit)
            .all()
        )

    def expire_stale(self) -> int:
        """Expire opportunities past their TTL.

        Returns:
            Number of expired opportunities.
        """
        now = datetime.now(timezone.utc)
        stale = (
            self.db.query(Opportunity)
            .filter(
                Opportunity.status == "new",
                Opportunity.expires_at <= now,
            )
            .all()
        )

        for opp in stale:
            opp.status = "expired"
            self.db.add(opp)

        if stale:
            self.db.flush()
            logger.info(f"Expired {len(stale)} stale opportunities")

        return len(stale)

    def format_for_context(self, opportunities: list[Opportunity]) -> str:
        """Format opportunities for inclusion in agent context.

        Args:
            opportunities: List of opportunities to format.

        Returns:
            Formatted string.
        """
        if not opportunities:
            return "No active opportunities."

        lines = ["=== ACTIVE OPPORTUNITIES ==="]
        for opp in opportunities:
            age_min = int((datetime.now(timezone.utc) - opp.created_at.replace(tzinfo=timezone.utc)).total_seconds() / 60)
            lines.append(
                f"  #{opp.id} [{opp.urgency}] {opp.market} — {opp.signal_type} "
                f"(confidence: {opp.confidence}/10, {age_min}m ago) by {opp.scout_agent_name}"
            )
            lines.append(f"    {opp.details[:150]}")

        return "\n".join(lines)
