"""
Project Syndicate — Dynasty Analytics (Phase 3F)

Cross-dynasty comparisons, generational improvement tracking,
and dynasty performance reports.
"""

__version__ = "1.2.0"

import logging
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.common.models import (
    Agent, BehavioralProfile, Dynasty, Evaluation, Lineage,
)

logger = logging.getLogger(__name__)


@dataclass
class DynastyReport:
    """Performance report for a single dynasty."""
    dynasty_id: int
    dynasty_name: str
    status: str
    total_pnl: float
    avg_lifespan_days: float | None
    total_members: int
    living_members: int
    total_generations: int
    generational_improvement: float
    dominant_traits: dict | None
    market_focus: dict | None


@dataclass
class DynastyComparison:
    """Cross-dynasty comparison entry."""
    dynasty_id: int
    dynasty_name: str
    status: str
    total_pnl: float
    living_members: int
    avg_composite: float
    generational_improvement: float
    strength: str  # "strong", "stable", "weak"


class DynastyAnalytics:
    """Computes dynasty performance metrics and cross-dynasty comparisons."""

    def __init__(self) -> None:
        self.log = logger

    async def dynasty_performance(
        self, session: Session, dynasty_id: int,
    ) -> DynastyReport | None:
        """Generate a performance report for a dynasty."""
        dynasty = session.get(Dynasty, dynasty_id)
        if not dynasty:
            return None

        improvement = await self.generational_improvement(session, dynasty_id)

        # Get dominant behavioral traits from profile snapshots
        dominant_traits = self._get_dominant_traits(session, dynasty_id)

        # Get market focus distribution
        market_focus = self._get_market_focus(session, dynasty_id)

        return DynastyReport(
            dynasty_id=dynasty.id,
            dynasty_name=dynasty.dynasty_name,
            status=dynasty.status,
            total_pnl=dynasty.total_pnl,
            avg_lifespan_days=dynasty.avg_lifespan_days,
            total_members=dynasty.total_members,
            living_members=dynasty.living_members,
            total_generations=dynasty.total_generations,
            generational_improvement=improvement,
            dominant_traits=dominant_traits,
            market_focus=market_focus,
        )

    async def cross_dynasty_comparison(
        self, session: Session,
    ) -> list[DynastyComparison]:
        """Compare all active dynasties ranked by total P&L."""
        dynasties = session.execute(
            select(Dynasty).order_by(Dynasty.total_pnl.desc())
        ).scalars().all()

        comparisons = []
        for dynasty in dynasties:
            # Get average composite of living members
            living = session.execute(
                select(Agent).where(
                    Agent.dynasty_id == dynasty.id,
                    Agent.status == "active",
                )
            ).scalars().all()
            avg_composite = 0.0
            if living:
                scores = [a.composite_score or 0 for a in living]
                avg_composite = sum(scores) / len(scores)

            improvement = await self.generational_improvement(session, dynasty.id)

            # Classify strength
            if improvement > 0.05:
                strength = "strong"
            elif improvement > -0.05:
                strength = "stable"
            else:
                strength = "weak"

            comparisons.append(DynastyComparison(
                dynasty_id=dynasty.id,
                dynasty_name=dynasty.dynasty_name,
                status=dynasty.status,
                total_pnl=dynasty.total_pnl,
                living_members=dynasty.living_members,
                avg_composite=avg_composite,
                generational_improvement=improvement,
                strength=strength,
            ))

        return comparisons

    async def generational_improvement(
        self, session: Session, dynasty_id: int,
    ) -> float:
        """Calculate whether later generations outperform earlier ones."""
        lineages = session.execute(
            select(Lineage).where(
                Lineage.dynasty_id == dynasty_id,
                Lineage.parent_id.isnot(None),
            )
        ).scalars().all()

        if not lineages:
            return 0.0

        improvements = []
        for lin in lineages:
            # Get offspring's peak composite
            offspring = session.get(Agent, lin.agent_id)
            if not offspring:
                continue

            offspring_peak = self._get_peak_composite(session, offspring.id)

            # Get parent's peak composite
            parent_peak = None
            if lin.parent_composite_at_reproduction:
                parent_peak = lin.parent_composite_at_reproduction
            elif lin.parent_id:
                parent_peak = self._get_peak_composite(session, lin.parent_id)

            if parent_peak and parent_peak > 0 and offspring_peak is not None:
                improvement = (offspring_peak - parent_peak) / parent_peak
                improvements.append(improvement)

        return sum(improvements) / len(improvements) if improvements else 0.0

    async def lineage_knowledge_depth(
        self, session: Session, agent_id: int,
    ) -> int:
        """How many generations of knowledge does this agent carry?"""
        depth = 1  # self
        current = session.get(Lineage, agent_id)

        while current and current.parent_id:
            parent_lineage = session.get(Lineage, current.parent_id)
            if parent_lineage:
                depth += 1
                current = parent_lineage
            else:
                break

        return depth

    def _get_peak_composite(self, session: Session, agent_id: int) -> float | None:
        """Get the peak composite score an agent ever achieved."""
        evals = session.execute(
            select(Evaluation.composite_score).where(
                Evaluation.agent_id == agent_id,
                Evaluation.composite_score.isnot(None),
            ).order_by(Evaluation.composite_score.desc()).limit(1)
        ).scalar_one_or_none()

        if evals is not None:
            return evals

        # Fallback: current composite
        agent = session.get(Agent, agent_id)
        return agent.composite_score if agent else None

    def _get_dominant_traits(
        self, session: Session, dynasty_id: int,
    ) -> dict | None:
        """Get averaged behavioral traits across dynasty members."""
        members = session.execute(
            select(Agent).where(Agent.dynasty_id == dynasty_id)
        ).scalars().all()

        if not members:
            return None

        trait_sums: dict[str, list[float]] = {
            "risk_appetite": [], "market_focus_entropy": [],
            "decision_style": [], "learning_velocity": [],
            "resilience": [],
        }

        for member in members:
            profile = session.execute(
                select(BehavioralProfile)
                .where(BehavioralProfile.agent_id == member.id)
                .order_by(BehavioralProfile.created_at.desc())
                .limit(1)
            ).scalar_one_or_none()

            if not profile:
                continue

            if profile.risk_appetite_score is not None:
                trait_sums["risk_appetite"].append(profile.risk_appetite_score)
            if profile.market_focus_entropy is not None:
                trait_sums["market_focus_entropy"].append(profile.market_focus_entropy)
            if profile.decision_style_score is not None:
                trait_sums["decision_style"].append(profile.decision_style_score)
            if profile.learning_velocity_score is not None:
                trait_sums["learning_velocity"].append(profile.learning_velocity_score)
            if profile.resilience_score is not None:
                trait_sums["resilience"].append(profile.resilience_score)

        result = {}
        for trait, values in trait_sums.items():
            if values:
                result[trait] = round(sum(values) / len(values), 3)

        return result if result else None

    def _get_market_focus(
        self, session: Session, dynasty_id: int,
    ) -> dict | None:
        """Get market focus distribution across dynasty members."""
        members = session.execute(
            select(Agent).where(
                Agent.dynasty_id == dynasty_id,
                Agent.watched_markets.isnot(None),
            )
        ).scalars().all()

        if not members:
            return None

        market_counts: dict[str, int] = {}
        for member in members:
            for market in (member.watched_markets or []):
                market_counts[market] = market_counts.get(market, 0) + 1

        return market_counts if market_counts else None
