"""
Project Syndicate — Memorial Manager (Phase 3F)

Creates memorial records for "The Fallen" — dead agents preserved for the dashboard.
"""

__version__ = "1.2.0"

import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from src.common.models import Agent, Dynasty, Evaluation, Memorial

logger = logging.getLogger(__name__)


class MemorialManager:
    """Creates memorial records for terminated agents."""

    def __init__(self) -> None:
        self.log = logger

    async def create_memorial(
        self,
        session: Session,
        agent: Agent,
        evaluation: Evaluation | None = None,
        epitaph: str | None = None,
    ) -> Memorial:
        """Create a memorial record for The Fallen."""
        now = datetime.now(timezone.utc)

        # Get dynasty name
        dynasty_name = "No Dynasty"
        if agent.dynasty_id:
            dynasty = session.get(Dynasty, agent.dynasty_id)
            if dynasty:
                dynasty_name = dynasty.dynasty_name

        # Find best and worst metrics from evaluation
        best_name, best_val = None, None
        worst_name, worst_val = None, None

        if evaluation and evaluation.metric_breakdown:
            metrics = evaluation.metric_breakdown
            for name, data in metrics.items():
                if isinstance(data, dict):
                    norm = data.get("normalized", data.get("raw"))
                else:
                    norm = data
                if norm is None:
                    continue
                if best_val is None or norm > best_val:
                    best_val = norm
                    best_name = name
                if worst_val is None or norm < worst_val:
                    worst_val = norm
                    worst_name = name

        # Calculate lifespan (handle both naive and aware datetimes)
        lifespan = 0.0
        if agent.created_at:
            created = agent.created_at
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            lifespan = (now - created).total_seconds() / 86400

        # Determine notable achievement
        achievement = self._determine_achievement(agent)

        # Determine cause of death
        cause = (
            agent.termination_reason
            or (evaluation.genesis_decision if evaluation else None)
            or "evaluation_failed"
        )

        memorial = Memorial(
            agent_id=agent.id,
            agent_name=agent.name,
            agent_role=agent.type,
            dynasty_name=dynasty_name,
            generation=agent.generation,
            lifespan_days=lifespan,
            cause_of_death=cause[:200],
            total_cycles=agent.cycle_count or 0,
            final_prestige=agent.prestige_title,
            best_metric_name=best_name,
            best_metric_value=best_val,
            worst_metric_name=worst_name,
            worst_metric_value=worst_val,
            notable_achievement=achievement,
            final_pnl=(agent.realized_pnl or 0) + (agent.unrealized_pnl or 0),
            epitaph=epitaph,
        )
        session.add(memorial)
        session.flush()

        self.log.info(f"Memorial created for {agent.name} ({dynasty_name})")
        return memorial

    def _determine_achievement(self, agent: Agent) -> str | None:
        """Determine notable achievement for the agent."""
        achievements = []

        if agent.prestige_title:
            achievements.append(f"Reached {agent.prestige_title} prestige")

        pnl = (agent.realized_pnl or 0) + (agent.unrealized_pnl or 0)
        if pnl > 0:
            achievements.append(f"Net positive P&L: ${pnl:.2f}")

        if agent.evaluation_count and agent.evaluation_count >= 5:
            achievements.append(f"Survived {agent.evaluation_count} evaluations")

        if agent.offspring_count and agent.offspring_count > 0:
            achievements.append(f"Spawned {agent.offspring_count} offspring")

        if not achievements:
            return None

        return "; ".join(achievements[:2])
