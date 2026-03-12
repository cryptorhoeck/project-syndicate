"""
Project Syndicate — Maintenance Tasks

Periodic housekeeping tasks:
  - Expire stale opportunities
  - Clean up abandoned plans
  - Reset daily thinking budgets
  - Prune short-term memory for terminated agents
"""

__version__ = "0.8.0"

import logging
from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.orm import sessionmaker

from src.common.models import Agent, Opportunity, Plan

logger = logging.getLogger(__name__)


class MaintenanceService:
    """Runs periodic maintenance tasks for the agent ecosystem."""

    def __init__(self, db_session_factory: sessionmaker):
        self.db_factory = db_session_factory

    async def run_all(self) -> dict:
        """Run all maintenance tasks.

        Returns:
            Dict with results of each task.
        """
        results = {}
        results["expired_opportunities"] = self.expire_stale_opportunities()
        results["stale_plans"] = self.cleanup_stale_plans()
        results["budget_resets"] = self.reset_daily_budgets()
        return results

    def expire_stale_opportunities(self) -> int:
        """Expire opportunities past their TTL.

        Returns:
            Number of expired opportunities.
        """
        now = datetime.now(timezone.utc)
        with self.db_factory() as session:
            stale = session.execute(
                select(Opportunity).where(
                    Opportunity.status == "new",
                    Opportunity.expires_at <= now,
                )
            ).scalars().all()

            for opp in stale:
                opp.status = "expired"
                session.add(opp)

            if stale:
                session.commit()
                logger.info(f"Expired {len(stale)} stale opportunities")

            return len(stale)

    def cleanup_stale_plans(self) -> int:
        """Clean up plans stuck in intermediate states.

        Plans submitted > 24h ago with no critic action → back to draft.
        Plans under_review > 12h → back to submitted.

        Returns:
            Number of plans cleaned up.
        """
        from datetime import timedelta
        now = datetime.now(timezone.utc)
        count = 0

        with self.db_factory() as session:
            # Submitted > 24h with no critic → back to draft
            stale_submitted = session.execute(
                select(Plan).where(
                    Plan.status == "submitted",
                    Plan.critic_agent_id == None,
                    Plan.submitted_at < now - timedelta(hours=24),
                )
            ).scalars().all()

            for plan in stale_submitted:
                plan.status = "draft"
                session.add(plan)
                count += 1

            # Under review > 12h → back to submitted (critic may have died)
            stale_review = session.execute(
                select(Plan).where(
                    Plan.status == "under_review",
                    Plan.reviewed_at == None,
                    Plan.submitted_at < now - timedelta(hours=12),
                )
            ).scalars().all()

            for plan in stale_review:
                plan.status = "submitted"
                plan.critic_agent_id = None
                plan.critic_agent_name = None
                session.add(plan)
                count += 1

            if count > 0:
                session.commit()
                logger.info(f"Cleaned up {count} stale plans")

            return count

    def reset_daily_budgets(self) -> int:
        """Reset thinking budgets for all agents.

        Called once per day (typically from Genesis hourly maintenance).

        Returns:
            Number of agents reset.
        """
        with self.db_factory() as session:
            result = session.execute(
                update(Agent)
                .where(Agent.status.in_(["active", "initializing"]))
                .values(thinking_budget_used_today=0.0)
            )
            count = result.rowcount
            session.commit()

            if count > 0:
                logger.info(f"Reset daily budgets for {count} agents")
            return count

    def prune_terminated_agent_memory(self, redis_client=None) -> int:
        """Clean up Redis short-term memory for terminated agents.

        Args:
            redis_client: Optional Redis client.

        Returns:
            Number of agents cleaned up.
        """
        if not redis_client:
            return 0

        with self.db_factory() as session:
            terminated = session.execute(
                select(Agent.id).where(Agent.status == "terminated")
            ).scalars().all()

        count = 0
        for agent_id in terminated:
            try:
                key = f"agent:{agent_id}:recent_cycles"
                if redis_client.exists(key):
                    redis_client.delete(key)
                    count += 1
            except Exception:
                pass

        if count > 0:
            logger.info(f"Pruned Redis memory for {count} terminated agents")
        return count
