"""
Project Syndicate — Genome Manager

Creates, stores, retrieves, and modifies agent genomes.
"""

__version__ = "0.1.0"

import logging
from datetime import datetime, timezone

from sqlalchemy import select, desc
from sqlalchemy.orm import Session

from src.common.config import config
from src.common.models import AgentGenome
from src.genome.genome_schema import (
    GENOME_BOUNDS, create_random_genome, clamp_genome, validate_genome,
)
from src.genome.mutation import mutate_genome, create_warmstart_genome

logger = logging.getLogger(__name__)


class GenomeManager:
    """Manages agent genome lifecycle."""

    async def create_genome(
        self,
        agent_id: int,
        role: str,
        parent_genome_id: int | None = None,
        parent_genome_data: dict | None = None,
        db_session: Session | None = None,
    ) -> dict:
        """Create and store a genome for an agent."""
        mutations = []

        if parent_genome_data:
            # Reproduction: mutate parent
            genome_data, mutations = mutate_genome(parent_genome_data)
            genome_data = clamp_genome(genome_data)
        else:
            # Check for best existing genome for warm-start
            best = await self.get_highest_fitness_genome(role, db_session)
            if best:
                genome_data = create_warmstart_genome(role, best)
            else:
                genome_data = create_random_genome(role)

        record = AgentGenome(
            agent_id=agent_id,
            genome_version=1,
            genome_data=genome_data,
            parent_genome_id=parent_genome_id,
            mutations_applied={"initial": mutations} if mutations else None,
            fitness_score=None,
            evaluations_with_genome=0,
        )

        if db_session:
            db_session.add(record)
            db_session.flush()

        return genome_data

    async def get_genome(self, agent_id: int, db_session: Session) -> dict | None:
        """Load agent's genome data from database."""
        record = db_session.execute(
            select(AgentGenome).where(AgentGenome.agent_id == agent_id)
        ).scalar_one_or_none()

        if not record:
            return None
        return record.genome_data

    async def get_genome_record(self, agent_id: int, db_session: Session) -> AgentGenome | None:
        """Load full genome record."""
        return db_session.execute(
            select(AgentGenome).where(AgentGenome.agent_id == agent_id)
        ).scalar_one_or_none()

    async def modify_genome(
        self,
        agent_id: int,
        parameter_path: str,
        new_value,
        evidence: str,
        confidence: int,
        db_session: Session,
    ) -> dict | None:
        """Agent-initiated genome modification."""
        record = await self.get_genome_record(agent_id, db_session)
        if not record:
            return None

        # Validate parameter exists in bounds
        if parameter_path not in GENOME_BOUNDS:
            logger.warning(f"Unknown genome parameter: {parameter_path}")
            return None

        # Validate value in bounds
        low, high = GENOME_BOUNDS[parameter_path]
        if isinstance(new_value, (int, float)):
            if new_value < low or new_value > high:
                logger.warning(f"Genome value {new_value} out of bounds [{low}, {high}]")
                return None

        # Apply modification
        parts = parameter_path.split(".")
        d = record.genome_data
        for part in parts[:-1]:
            if part not in d:
                d[part] = {}
            d = d[part]
        old_value = d.get(parts[-1])
        d[parts[-1]] = new_value

        # Record mutation
        if not record.mutations_applied:
            record.mutations_applied = {}
        mod_key = f"agent_mod_v{record.genome_version}"
        record.mutations_applied[mod_key] = {
            "path": parameter_path,
            "old": old_value,
            "new": new_value,
            "evidence": evidence[:500],
            "confidence": confidence,
        }

        record.genome_version += 1
        record.updated_at = datetime.now(timezone.utc)
        db_session.flush()

        return record.genome_data

    async def get_highest_fitness_genome(self, role: str, db_session: Session) -> dict | None:
        """Get the genome with highest fitness_score for a given role."""
        if not db_session:
            return None

        from src.common.models import Agent
        try:
            result = db_session.execute(
                select(AgentGenome)
                .join(Agent, Agent.id == AgentGenome.agent_id)
                .where(Agent.type == role, AgentGenome.fitness_score.isnot(None))
                .order_by(desc(AgentGenome.fitness_score))
                .limit(1)
            ).scalar_one_or_none()

            return result.genome_data if result else None
        except Exception:
            return None

    async def update_fitness(self, agent_id: int, composite_score: float, db_session: Session) -> None:
        """Update genome fitness after evaluation."""
        record = await self.get_genome_record(agent_id, db_session)
        if not record:
            return

        record.evaluations_with_genome = (record.evaluations_with_genome or 0) + 1
        age_bonus = config.genome_fitness_age_bonus
        record.fitness_score = composite_score * (1 + age_bonus * record.evaluations_with_genome)
        record.updated_at = datetime.now(timezone.utc)
        db_session.flush()
