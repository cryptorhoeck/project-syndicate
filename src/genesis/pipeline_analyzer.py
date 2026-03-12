"""
Project Syndicate — Pipeline Analyzer

Analyzes the Scout → Strategist → Critic → Operator pipeline
to identify conversion rates and bottlenecks at each stage.
"""

__version__ = "1.0.0"

import logging
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select, func
from sqlalchemy.orm import Session

from src.common.models import Opportunity, Plan, Position

logger = logging.getLogger(__name__)


@dataclass
class PipelineReport:
    """Pipeline conversion analysis for an evaluation period."""
    total_opportunities: int = 0
    claimed_opportunities: int = 0
    total_plans: int = 0
    approved_plans: int = 0
    rejected_plans: int = 0
    executed_plans: int = 0
    stage_rates: dict = field(default_factory=dict)
    bottleneck: str = "none"


class PipelineAnalyzer:
    """Analyzes pipeline conversion rates and identifies bottlenecks."""

    async def analyze(
        self, session: Session,
        period_start: datetime, period_end: datetime,
    ) -> PipelineReport:
        """Analyze the full pipeline for a given period."""
        report = PipelineReport()

        # Stage 1: Scout → Strategist (opportunities → claimed)
        opps = session.execute(
            select(Opportunity).where(
                Opportunity.created_at >= period_start,
                Opportunity.created_at <= period_end,
            )
        ).scalars().all()

        report.total_opportunities = len(opps)
        report.claimed_opportunities = sum(
            1 for o in opps if o.claimed_by_agent_id is not None
        )

        # Stage 2: Strategist → Critic (plans submitted)
        plans = session.execute(
            select(Plan).where(
                Plan.created_at >= period_start,
                Plan.created_at <= period_end,
            )
        ).scalars().all()

        report.total_plans = len(plans)
        report.approved_plans = sum(
            1 for p in plans if p.critic_verdict == "approved"
        )
        report.rejected_plans = sum(
            1 for p in plans if p.critic_verdict == "rejected"
        )

        # Stage 3: Critic → Operator (approved plans → executed)
        approved_plan_ids = [p.id for p in plans if p.critic_verdict == "approved"]
        if approved_plan_ids:
            executed_count = session.execute(
                select(func.count())
                .select_from(Position)
                .where(Position.source_plan_id.in_(approved_plan_ids))
            ).scalar()
            report.executed_plans = executed_count or 0
        else:
            report.executed_plans = 0

        # Calculate conversion rates
        scout_to_strategist = (
            report.claimed_opportunities / report.total_opportunities
            if report.total_opportunities > 0 else 0.0
        )
        strategist_to_critic = (
            report.approved_plans / report.total_plans
            if report.total_plans > 0 else 0.0
        )
        critic_to_operator = (
            report.executed_plans / report.approved_plans
            if report.approved_plans > 0 else 0.0
        )

        report.stage_rates = {
            "scout_to_strategist": scout_to_strategist,
            "strategist_to_critic": strategist_to_critic,
            "critic_to_operator": critic_to_operator,
        }

        # Identify bottleneck
        if report.total_opportunities == 0:
            report.bottleneck = "no_opportunities"
        elif report.approved_plans > 0 and report.executed_plans == 0:
            report.bottleneck = "operator_not_executing"
        else:
            # Lowest conversion rate is the bottleneck
            rates = {
                "scout_to_strategist": scout_to_strategist,
                "strategist_to_critic": strategist_to_critic,
            }
            if report.approved_plans > 0:
                rates["critic_to_operator"] = critic_to_operator

            if rates:
                report.bottleneck = min(rates, key=rates.get)
            else:
                report.bottleneck = "insufficient_data"

        logger.info(
            f"Pipeline analysis: {report.total_opportunities} opps → "
            f"{report.claimed_opportunities} claimed → {report.total_plans} plans → "
            f"{report.approved_plans} approved → {report.executed_plans} executed | "
            f"bottleneck={report.bottleneck}"
        )

        return report
