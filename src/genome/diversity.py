"""
Project Syndicate — Population Diversity Monitor

Measures genome similarity across the active population.
Triggers diversity pressure when convergence is too high.
"""

__version__ = "0.1.0"

import logging

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.common.config import config
from src.common.models import Agent, AgentGenome
from src.genome.genome_schema import flatten_genome

logger = logging.getLogger(__name__)


async def calculate_diversity_index(role: str | None, db_session: Session) -> float:
    """Calculate genome diversity index for active agents.

    Returns mean pairwise cosine distance (0.0 = identical, 1.0 = maximally different).
    Returns 1.0 if fewer than 2 agents.
    """
    try:
        query = (
            select(AgentGenome)
            .join(Agent, Agent.id == AgentGenome.agent_id)
            .where(Agent.status.in_(["active", "evaluating"]), Agent.id != 0)
        )
        if role:
            query = query.where(Agent.type == role)

        genomes = list(db_session.execute(query).scalars().all())
    except Exception:
        return 1.0

    if len(genomes) < 2:
        return 1.0

    # Flatten genomes to numerical vectors
    vectors = []
    for g in genomes:
        flat = flatten_genome(g.genome_data)
        vec = [v for _, v in flat if isinstance(v, (int, float))]
        vectors.append(vec)

    # Pad to same length
    max_len = max(len(v) for v in vectors)
    for v in vectors:
        while len(v) < max_len:
            v.append(0.0)

    arr = np.array(vectors, dtype=np.float64)

    # Pairwise cosine distances
    distances = []
    for i in range(len(arr)):
        for j in range(i + 1, len(arr)):
            dot = np.dot(arr[i], arr[j])
            norm_i = np.linalg.norm(arr[i])
            norm_j = np.linalg.norm(arr[j])
            if norm_i > 0 and norm_j > 0:
                cos_sim = dot / (norm_i * norm_j)
                distances.append(1.0 - cos_sim)
            else:
                distances.append(1.0)

    return float(np.mean(distances)) if distances else 1.0


def should_apply_diversity_pressure(diversity_index: float) -> bool:
    """Returns True if diversity is below threshold."""
    return diversity_index < config.genome_diversity_low_threshold
