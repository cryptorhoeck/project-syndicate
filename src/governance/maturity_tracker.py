"""
Colony Maturity Tracker.

Computes the colony's maturity stage from four metrics:
colony age, generational depth, SIP history, and population.

The maturity stage drives SIP governance parameters:
debate periods, voting periods, rate limits, Genesis posture,
and passing thresholds.

This is pure deterministic code -- no AI involved.
"""

__version__ = "0.1.0"

import logging
from enum import Enum
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, func

from src.common.models import (
    Agent, ColonyMaturity, SystemImprovementProposal,
)

logger = logging.getLogger(__name__)


class MaturityStage(str, Enum):
    NASCENT = "nascent"
    DEVELOPING = "developing"
    ESTABLISHED = "established"
    MATURE = "mature"


@dataclass
class MaturityConfig:
    """Dynamic governance parameters driven by maturity stage."""
    stage: MaturityStage
    debate_period_hours: int
    voting_period_hours: int
    sip_rate_limit_per_eval: int
    sip_thinking_tax_multiplier: float
    genesis_posture: str  # permissive, balanced, conservative, skeptical
    pass_threshold: float  # for Tier 1 SIPs
    structural_threshold: float  # for Tier 2 SIPs
    require_evidence: bool  # agents must cite data in proposals
    require_cosponsor: bool  # Tier 2 SIPs need a second agent to co-sign


MATURITY_CONFIGS = {
    MaturityStage.NASCENT: MaturityConfig(
        stage=MaturityStage.NASCENT,
        debate_period_hours=4,
        voting_period_hours=4,
        sip_rate_limit_per_eval=2,
        sip_thinking_tax_multiplier=1.5,
        genesis_posture="permissive",
        pass_threshold=0.60,
        structural_threshold=0.75,
        require_evidence=False,
        require_cosponsor=False,
    ),
    MaturityStage.DEVELOPING: MaturityConfig(
        stage=MaturityStage.DEVELOPING,
        debate_period_hours=8,
        voting_period_hours=8,
        sip_rate_limit_per_eval=1,
        sip_thinking_tax_multiplier=2.0,
        genesis_posture="balanced",
        pass_threshold=0.60,
        structural_threshold=0.75,
        require_evidence=False,
        require_cosponsor=False,
    ),
    MaturityStage.ESTABLISHED: MaturityConfig(
        stage=MaturityStage.ESTABLISHED,
        debate_period_hours=12,
        voting_period_hours=12,
        sip_rate_limit_per_eval=1,
        sip_thinking_tax_multiplier=2.0,
        genesis_posture="conservative",
        pass_threshold=0.60,
        structural_threshold=0.75,
        require_evidence=True,
        require_cosponsor=False,
    ),
    MaturityStage.MATURE: MaturityConfig(
        stage=MaturityStage.MATURE,
        debate_period_hours=24,
        voting_period_hours=24,
        sip_rate_limit_per_eval=1,
        sip_thinking_tax_multiplier=2.5,
        genesis_posture="skeptical",
        pass_threshold=0.65,
        structural_threshold=0.80,
        require_evidence=True,
        require_cosponsor=True,
    ),
}

# Ordered list for stage comparison
_STAGE_ORDER = [
    MaturityStage.NASCENT,
    MaturityStage.DEVELOPING,
    MaturityStage.ESTABLISHED,
    MaturityStage.MATURE,
]


class ColonyMaturityTracker:
    """Tracks and advances colony maturity based on observable metrics."""

    def get_config(self, db_session) -> MaturityConfig:
        """Read current maturity stage from database and return the config."""
        row = db_session.execute(
            select(ColonyMaturity).limit(1)
        ).scalar_one_or_none()

        if row is None:
            # Insert default NASCENT row
            row = ColonyMaturity(stage="nascent")
            db_session.add(row)
            db_session.flush()

        stage = MaturityStage(row.stage)
        return MATURITY_CONFIGS[stage]

    async def compute_stage(self, db_session) -> MaturityStage:
        """Compute current maturity stage from colony metrics."""
        now = datetime.now(timezone.utc)

        # Colony age: days since the first agent was spawned
        first_agent = db_session.execute(
            select(func.min(Agent.created_at))
        ).scalar()
        if first_agent is None:
            return MaturityStage.NASCENT
        colony_age_days = (now - first_agent.replace(tzinfo=timezone.utc)).days

        # Max generation
        max_gen = db_session.execute(
            select(func.max(Agent.generation))
        ).scalar() or 1

        # Total SIPs implemented
        total_sips_passed = db_session.execute(
            select(func.count()).select_from(SystemImprovementProposal).where(
                SystemImprovementProposal.lifecycle_status == "implemented"
            )
        ).scalar() or 0

        # Determine stage (can only advance, never regress)
        computed = MaturityStage.NASCENT
        if (colony_age_days >= 8 and max_gen >= 2 and total_sips_passed >= 1):
            computed = MaturityStage.DEVELOPING
        if (colony_age_days >= 22 and max_gen >= 3 and total_sips_passed >= 4):
            computed = MaturityStage.ESTABLISHED
        if (colony_age_days >= 60 and max_gen >= 5 and total_sips_passed >= 10):
            computed = MaturityStage.MATURE

        return computed

    async def update(self, db_session, agora_service=None) -> tuple[MaturityStage, bool]:
        """Recompute maturity and update the database.

        Returns (current_stage, did_transition).
        """
        now = datetime.now(timezone.utc)
        row = db_session.execute(
            select(ColonyMaturity).limit(1)
        ).scalar_one_or_none()

        if row is None:
            row = ColonyMaturity(stage="nascent")
            db_session.add(row)
            db_session.flush()

        current = MaturityStage(row.stage)
        computed = await self.compute_stage(db_session)

        # Never regress
        current_idx = _STAGE_ORDER.index(current)
        computed_idx = _STAGE_ORDER.index(computed)
        new_stage = computed if computed_idx > current_idx else current
        did_transition = new_stage != current

        # Update metrics
        first_agent = db_session.execute(
            select(func.min(Agent.created_at))
        ).scalar()
        age_days = 0
        if first_agent:
            age_days = (now - first_agent.replace(tzinfo=timezone.utc)).days

        row.stage = new_stage.value
        row.colony_age_days = age_days
        row.max_generation = db_session.execute(
            select(func.max(Agent.generation))
        ).scalar() or 1
        row.total_sips_passed = db_session.execute(
            select(func.count()).select_from(SystemImprovementProposal).where(
                SystemImprovementProposal.lifecycle_status == "implemented"
            )
        ).scalar() or 0
        row.active_agent_count = db_session.execute(
            select(func.count()).select_from(Agent).where(Agent.status == "active")
        ).scalar() or 0
        row.last_computed_at = now

        if did_transition:
            row.last_stage_transition_at = now
            config = MATURITY_CONFIGS[new_stage]
            logger.info(
                "colony_maturity_transition",
                extra={"from": current.value, "to": new_stage.value},
            )
            # Post to Agora if service available
            if agora_service:
                try:
                    await agora_service.post_message(
                        channel="system-alerts",
                        agent_id=0,
                        agent_name="SYSTEM",
                        content=(
                            f"[COLONY MATURITY] The colony has advanced to "
                            f"{new_stage.value.upper()} stage. Governance parameters "
                            f"updated: debate period now {config.debate_period_hours}hr, "
                            f"voting period now {config.voting_period_hours}hr."
                        ),
                        message_type="SYSTEM",
                    )
                except Exception as e:
                    logger.warning(f"Failed to post maturity transition: {e}")

        db_session.flush()
        return new_stage, did_transition

    def get_debate_end_time(self, db_session, proposed_at: datetime) -> datetime:
        """Calculate when debate ends based on current maturity config."""
        config = self.get_config(db_session)
        return proposed_at + timedelta(hours=config.debate_period_hours)

    def get_voting_end_time(self, db_session, debate_ended_at: datetime) -> datetime:
        """Calculate when voting ends based on current maturity config."""
        config = self.get_config(db_session)
        return debate_ended_at + timedelta(hours=config.voting_period_hours)
