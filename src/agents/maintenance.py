"""
Project Syndicate — Maintenance Tasks

Periodic housekeeping tasks:
  - Expire stale opportunities
  - Clean up abandoned plans
  - Reset daily thinking budgets
  - Prune short-term memory for terminated agents
"""

__version__ = "0.9.0"

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

    async def run_all(self, redis_client=None) -> dict:
        """Run the three hourly-safe maintenance tasks.

        Subsystem T-subset fix: this orchestrator deliberately does
        NOT call `reset_daily_budgets`. The budget reset is daily-only
        (resetting agents' `thinking_budget_used_today` more often
        than once per day would let agents consume up to 24x their
        intended daily budget). The daily gate lives at the call site
        in `Genesis._maybe_run_hourly_maintenance`; budget reset is
        invoked separately from there.

        Each method is wrapped in its own try/except so a single
        task failure does not prevent the others from running. On
        per-task failure, log WARNING with the task name and return
        0 in the result dict for that task.

        Args:
            redis_client: Optional Redis client, threaded through to
                `prune_terminated_agent_memory`. If None, that task
                is a no-op (returns 0).

        Returns:
            Dict with three counts: opportunities_expired,
            plans_cleaned, memory_pruned. Each task's count is the
            number of rows it touched (or 0 on failure).
        """
        results = {
            "opportunities_expired": 0,
            "plans_cleaned": 0,
            "memory_pruned": 0,
        }

        try:
            results["opportunities_expired"] = self.expire_stale_opportunities()
        except Exception as exc:
            logger.warning(
                "maintenance_task_failed",
                extra={
                    "task": "expire_stale_opportunities",
                    "error": str(exc),
                    "error_type": type(exc).__name__,
                },
            )

        try:
            results["plans_cleaned"] = self.cleanup_stale_plans()
        except Exception as exc:
            logger.warning(
                "maintenance_task_failed",
                extra={
                    "task": "cleanup_stale_plans",
                    "error": str(exc),
                    "error_type": type(exc).__name__,
                },
            )

        try:
            results["memory_pruned"] = self.prune_terminated_agent_memory(
                redis_client=redis_client,
            )
        except Exception as exc:
            logger.warning(
                "maintenance_task_failed",
                extra={
                    "task": "prune_terminated_agent_memory",
                    "error": str(exc),
                    "error_type": type(exc).__name__,
                },
            )

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
