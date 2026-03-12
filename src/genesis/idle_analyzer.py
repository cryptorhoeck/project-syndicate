"""
Project Syndicate — Idle Analyzer

Classifies idle cycles into actionable categories:
  1. post_loss_caution — had a loss in last 3 cycles
  2. no_input — pipeline had no work for this agent
  3. strategic_patience — reasoning mentions waiting for conditions
  4. paralysis (default) — pipeline had work, no good excuse
"""

__version__ = "1.0.0"

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.common.models import Agent, AgentCycle, Opportunity, Plan

logger = logging.getLogger(__name__)

# Keywords indicating strategic patience
PATIENCE_KEYWORDS = [
    "wait", "waiting", "patience", "patient", "hold",
    "not yet", "too early", "premature", "conditions",
    "signal", "confirmation", "consolidat", "sideways",
]


@dataclass
class IdleBreakdown:
    """Breakdown of idle cycles by category."""
    total_idle: int = 0
    total_cycles: int = 0
    idle_rate: float = 0.0
    breakdown: dict = field(default_factory=lambda: {
        "post_loss_caution": 0,
        "no_input": 0,
        "strategic_patience": 0,
        "paralysis": 0,
    })
    breakdown_pct: dict = field(default_factory=lambda: {
        "post_loss_caution": 0.0,
        "no_input": 0.0,
        "strategic_patience": 0.0,
        "paralysis": 0.0,
    })


class IdleAnalyzer:
    """Classifies idle cycles into actionable categories."""

    async def analyze_idle_periods(
        self, session: Session, agent_id: int,
        period_start: datetime, period_end: datetime,
    ) -> IdleBreakdown:
        """Analyze idle periods for an agent.

        Args:
            session: DB session.
            agent_id: Agent to analyze.
            period_start: Start of evaluation period.
            period_end: End of evaluation period.

        Returns:
            IdleBreakdown with categorized idle cycles.
        """
        agent = session.get(Agent, agent_id)
        if not agent:
            return IdleBreakdown()

        cycles = session.execute(
            select(AgentCycle)
            .where(
                AgentCycle.agent_id == agent_id,
                AgentCycle.timestamp >= period_start,
                AgentCycle.timestamp <= period_end,
            )
            .order_by(AgentCycle.cycle_number)
        ).scalars().all()

        result = IdleBreakdown()
        result.total_cycles = len(cycles)

        if not cycles:
            return result

        # Identify idle cycles
        idle_cycles = [
            c for c in cycles
            if c.action_type in (None, "wait", "observe", "idle")
        ]
        result.total_idle = len(idle_cycles)
        result.idle_rate = result.total_idle / result.total_cycles if result.total_cycles > 0 else 0.0

        # Check pipeline availability based on agent role
        has_pipeline_work = self._check_pipeline_availability(
            session, agent, period_start, period_end
        )

        for idle_cycle in idle_cycles:
            category = self._classify_idle(
                session, agent, idle_cycle, cycles, has_pipeline_work
            )
            result.breakdown[category] += 1

        # Calculate percentages
        if result.total_idle > 0:
            for cat in result.breakdown:
                result.breakdown_pct[cat] = result.breakdown[cat] / result.total_idle

        return result

    def _classify_idle(
        self, session: Session, agent: Agent,
        idle_cycle: AgentCycle, all_cycles: list,
        has_pipeline_work: bool,
    ) -> str:
        """Classify a single idle cycle (priority order).

        1. post_loss_caution — had a loss in last 3 cycles
        2. no_input — pipeline had no work
        3. strategic_patience — reasoning mentions waiting
        4. paralysis — default
        """
        # 1. Post-loss caution: check last 3 cycles before this one
        cycle_idx = next(
            (i for i, c in enumerate(all_cycles) if c.id == idle_cycle.id), -1
        )
        if cycle_idx > 0:
            lookback = all_cycles[max(0, cycle_idx - 3):cycle_idx]
            for prev in lookback:
                if prev.outcome_pnl is not None and prev.outcome_pnl < 0:
                    return "post_loss_caution"

        # 2. No input: pipeline had no work for this agent
        if not has_pipeline_work:
            return "no_input"

        # 3. Strategic patience: reasoning mentions waiting for conditions
        reasoning = (idle_cycle.reasoning or "") + " " + (idle_cycle.situation or "")
        reasoning_lower = reasoning.lower()
        for keyword in PATIENCE_KEYWORDS:
            if keyword in reasoning_lower:
                return "strategic_patience"

        # 4. Default: paralysis
        return "paralysis"

    def _check_pipeline_availability(
        self, session: Session, agent: Agent,
        period_start: datetime, period_end: datetime,
    ) -> bool:
        """Check if the pipeline had work available for this agent's role."""
        role = agent.type

        if role == "scout":
            # Scouts always have potential work (can look for opportunities)
            return True

        elif role == "strategist":
            # Check for unclaimed opportunities
            unclaimed = session.execute(
                select(Opportunity).where(
                    Opportunity.status == "new",
                    Opportunity.created_at >= period_start,
                    Opportunity.created_at <= period_end,
                )
            ).scalars().first()
            return unclaimed is not None

        elif role == "critic":
            # Check for plans needing review
            pending_review = session.execute(
                select(Plan).where(
                    Plan.status.in_(["submitted", "under_review"]),
                    Plan.created_at >= period_start,
                    Plan.created_at <= period_end,
                )
            ).scalars().first()
            return pending_review is not None

        elif role == "operator":
            # Check for approved plans needing execution
            approved = session.execute(
                select(Plan).where(
                    Plan.status == "approved",
                    Plan.created_at >= period_start,
                    Plan.created_at <= period_end,
                )
            ).scalars().first()
            return approved is not None

        return True  # Default: assume work is available
