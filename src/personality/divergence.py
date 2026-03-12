"""
Project Syndicate — Divergence Calculator (Phase 3E)

Measures how identical agents become different through experience.
Cosine distance between behavioral profile score vectors.
"""

__version__ = "1.1.0"

import logging
import math
from dataclasses import dataclass
from itertools import combinations

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.common.config import config
from src.common.models import Agent, BehavioralProfile, DivergenceScore

logger = logging.getLogger(__name__)


@dataclass
class DivergenceResult:
    """Result of divergence computation between two agents."""
    agent_a_id: int
    agent_b_id: int
    role: str
    score: float
    comparable_metrics: int


class DivergenceCalculator:
    """Computes pairwise divergence for same-role agents."""

    def __init__(self) -> None:
        self.log = logger

    async def compute_pairwise(
        self,
        session: Session,
        role: str | None = None,
    ) -> list[DivergenceResult]:
        """Compute divergence for all same-role active agent pairs."""
        query = select(Agent).where(Agent.status.in_(["active", "frozen"]))
        if role:
            query = query.where(Agent.type == role)
        agents = session.execute(query).scalars().all()

        # Group by role
        by_role: dict[str, list[Agent]] = {}
        for a in agents:
            by_role.setdefault(a.type, []).append(a)

        results = []
        min_metrics = config.divergence_min_comparable_metrics

        for role_name, role_agents in by_role.items():
            if len(role_agents) < 2:
                continue

            for a, b in combinations(role_agents, 2):
                profile_a = self._get_latest_profile(session, a.id)
                profile_b = self._get_latest_profile(session, b.id)

                if not profile_a or not profile_b:
                    continue

                vec_a = profile_a.raw_scores_vector()
                vec_b = profile_b.raw_scores_vector()

                # Only compare metrics where both have data
                valid_pairs = [
                    (va, vb)
                    for va, vb in zip(vec_a, vec_b)
                    if va is not None and vb is not None
                ]

                if len(valid_pairs) < min_metrics:
                    continue

                score = cosine_distance(
                    [p[0] for p in valid_pairs],
                    [p[1] for p in valid_pairs],
                )

                results.append(DivergenceResult(
                    agent_a_id=a.id,
                    agent_b_id=b.id,
                    role=role_name,
                    score=score,
                    comparable_metrics=len(valid_pairs),
                ))

        return results

    async def store_snapshot(
        self,
        session: Session,
        results: list[DivergenceResult],
        evaluation_id: int | None = None,
    ) -> None:
        """Store divergence scores linked to an evaluation."""
        for r in results:
            record = DivergenceScore(
                agent_a_id=r.agent_a_id,
                agent_b_id=r.agent_b_id,
                agent_a_role=r.role,
                divergence_score=r.score,
                comparable_metrics=r.comparable_metrics,
                evaluation_id=evaluation_id,
            )
            session.add(record)

    def _get_latest_profile(
        self,
        session: Session,
        agent_id: int,
    ) -> BehavioralProfile | None:
        """Get most recent behavioral profile for an agent."""
        return session.execute(
            select(BehavioralProfile).where(
                BehavioralProfile.agent_id == agent_id,
            ).order_by(BehavioralProfile.created_at.desc()).limit(1)
        ).scalar_one_or_none()


def cosine_distance(vec_a: list[float], vec_b: list[float]) -> float:
    """Compute cosine distance between two vectors. 0.0=identical, 1.0=opposite."""
    if not vec_a or not vec_b or len(vec_a) != len(vec_b):
        return 1.0

    dot = sum(a * b for a, b in zip(vec_a, vec_b))
    mag_a = math.sqrt(sum(a * a for a in vec_a))
    mag_b = math.sqrt(sum(b * b for b in vec_b))

    if mag_a == 0 or mag_b == 0:
        return 1.0

    similarity = dot / (mag_a * mag_b)
    # Clamp for floating point
    similarity = max(-1.0, min(1.0, similarity))
    return 1.0 - similarity
