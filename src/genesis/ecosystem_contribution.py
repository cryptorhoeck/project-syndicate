"""
Project Syndicate — Ecosystem Contribution Calculator

Single ecosystem-wide ranking metric for capital allocation:
  - Operators: true_pnl directly
  - Scouts: attributed_pnl × 0.25
  - Strategists: attributed_pnl × 0.25
  - Critics: estimated_money_saved × 0.50
"""

__version__ = "1.0.0"

import logging
from datetime import datetime

from sqlalchemy import select, func
from sqlalchemy.orm import Session

from src.common.config import config
from src.common.models import (
    Agent, AgentCycle, Opportunity, Plan, Position, RejectionTracking,
)

logger = logging.getLogger(__name__)


class EcosystemContributionCalculator:
    """Calculates ecosystem contribution score for capital allocation ranking."""

    async def calculate_all(
        self, session: Session,
        period_start: datetime, period_end: datetime,
    ) -> dict[int, float]:
        """Calculate ecosystem contribution for all active agents.

        Returns:
            Dict mapping agent_id → contribution score.
        """
        active_agents = session.execute(
            select(Agent).where(Agent.status == "active")
        ).scalars().all()

        contributions = {}
        for agent in active_agents:
            score = await self.calculate(session, agent, period_start, period_end)
            contributions[agent.id] = score

            # Update agent record
            agent.ecosystem_contribution = score
            session.add(agent)

        return contributions

    async def calculate(
        self, session: Session, agent: Agent,
        period_start: datetime, period_end: datetime,
    ) -> float:
        """Calculate ecosystem contribution for a single agent."""
        role = agent.type

        if role == "operator":
            return self._operator_contribution(session, agent, period_start, period_end)
        elif role == "scout":
            return self._scout_contribution(session, agent, period_start, period_end)
        elif role == "strategist":
            return self._strategist_contribution(session, agent, period_start, period_end)
        elif role == "critic":
            return self._critic_contribution(session, agent, period_start, period_end)
        else:
            return 0.0

    def _operator_contribution(
        self, session: Session, agent: Agent,
        period_start: datetime, period_end: datetime,
    ) -> float:
        """Operators: true_pnl directly."""
        api_cost = session.execute(
            select(func.sum(AgentCycle.api_cost_usd)).where(
                AgentCycle.agent_id == agent.id,
                AgentCycle.timestamp >= period_start,
                AgentCycle.timestamp <= period_end,
            )
        ).scalar() or 0.0

        return agent.realized_pnl + agent.unrealized_pnl - api_cost

    def _scout_contribution(
        self, session: Session, agent: Agent,
        period_start: datetime, period_end: datetime,
    ) -> float:
        """Scouts: attributed_pnl × attribution_scout_share."""
        opps = session.execute(
            select(Opportunity).where(
                Opportunity.scout_agent_id == agent.id,
                Opportunity.created_at >= period_start,
                Opportunity.created_at <= period_end,
            )
        ).scalars().all()

        opp_ids = [o.id for o in opps if o.converted_to_plan_id is not None]
        if not opp_ids:
            return 0.0

        positions = session.execute(
            select(Position).where(
                Position.source_opp_id.in_(opp_ids),
                Position.status != "open",
            )
        ).scalars().all()

        attributed_pnl = sum(
            p.realized_pnl for p in positions
            if p.realized_pnl is not None
        )

        return attributed_pnl * config.attribution_scout_share

    def _strategist_contribution(
        self, session: Session, agent: Agent,
        period_start: datetime, period_end: datetime,
    ) -> float:
        """Strategists: attributed_pnl × attribution_strategist_share."""
        plans = session.execute(
            select(Plan).where(
                Plan.strategist_agent_id == agent.id,
                Plan.created_at >= period_start,
                Plan.created_at <= period_end,
            )
        ).scalars().all()

        plan_ids = [p.id for p in plans]
        if not plan_ids:
            return 0.0

        positions = session.execute(
            select(Position).where(
                Position.source_plan_id.in_(plan_ids),
                Position.status != "open",
            )
        ).scalars().all()

        attributed_pnl = sum(
            p.realized_pnl for p in positions
            if p.realized_pnl is not None
        )

        return attributed_pnl * config.attribution_strategist_share

    def _critic_contribution(
        self, session: Session, agent: Agent,
        period_start: datetime, period_end: datetime,
    ) -> float:
        """Critics: estimated_money_saved × attribution_critic_share."""
        # Money saved = sum of losses avoided via correct rejections
        trackings = session.execute(
            select(RejectionTracking).where(
                RejectionTracking.critic_id == agent.id,
                RejectionTracking.status == "completed",
                RejectionTracking.critic_correct == True,
                RejectionTracking.completed_at >= period_start,
                RejectionTracking.completed_at <= period_end,
            )
        ).scalars().all()

        money_saved = 0.0
        for t in trackings:
            if t.outcome_pnl_pct is not None and t.outcome_pnl_pct < 0:
                # Estimate position size from plan
                plan = session.get(Plan, t.plan_id)
                if plan:
                    # Use agent's capital as proxy for position size
                    positions_size = plan.position_size_pct * 100  # rough estimate
                    money_saved += abs(t.outcome_pnl_pct / 100) * positions_size

        return money_saved * config.attribution_critic_share
