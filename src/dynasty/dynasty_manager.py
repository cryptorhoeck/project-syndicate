"""
Project Syndicate — Dynasty Manager (Phase 3F)

Creates, updates, and manages dynasties — the family trees of agent lineages.
"""

__version__ = "1.2.0"

import logging
from datetime import datetime, timezone

from sqlalchemy import select, func
from sqlalchemy.orm import Session

from src.common.models import Agent, Dynasty, Lineage

logger = logging.getLogger(__name__)


class DynastyManager:
    """Manages agent dynasties and their lifecycle."""

    def __init__(self) -> None:
        self.log = logger

    async def create_dynasty(self, session: Session, founder: Agent) -> Dynasty:
        """Create a new dynasty with this agent as founder."""
        dynasty = Dynasty(
            founder_id=founder.id,
            founder_name=founder.name,
            founder_role=founder.type,
            dynasty_name=f"Dynasty {founder.name}",
            founded_at=datetime.now(timezone.utc),
            status="active",
            total_generations=1,
            total_members=1,
            living_members=1,
            peak_members=1,
        )
        session.add(dynasty)
        session.flush()

        founder.dynasty_id = dynasty.id
        session.add(founder)

        self.log.info(f"Dynasty created: {dynasty.dynasty_name} (founder: {founder.name})")
        return dynasty

    async def record_birth(
        self, session: Session, parent: Agent, offspring: Agent,
    ) -> None:
        """Update dynasty stats when an offspring is born."""
        if not parent.dynasty_id:
            return

        dynasty = session.get(Dynasty, parent.dynasty_id)
        if not dynasty:
            return

        dynasty.total_members += 1
        dynasty.living_members += 1

        if offspring.generation > dynasty.total_generations:
            dynasty.total_generations = offspring.generation

        if dynasty.living_members > dynasty.peak_members:
            dynasty.peak_members = dynasty.living_members

        session.add(dynasty)
        self.log.info(
            f"Birth in {dynasty.dynasty_name}: {offspring.name} "
            f"(Gen {offspring.generation}, members: {dynasty.living_members})"
        )

    async def record_death(
        self, session: Session, agent: Agent, agora_service=None,
    ) -> None:
        """Update dynasty stats when an agent dies."""
        if not agent.dynasty_id:
            return

        dynasty = session.get(Dynasty, agent.dynasty_id)
        if not dynasty:
            return

        dynasty.living_members = max(0, dynasty.living_members - 1)

        # Update avg lifespan from all dead members
        dead_lineages = session.execute(
            select(Lineage).where(
                Lineage.dynasty_id == dynasty.id,
                Lineage.died_at.isnot(None),
            )
        ).scalars().all()
        if dead_lineages:
            lifespans = [l.lifespan_days for l in dead_lineages if l.lifespan_days]
            if lifespans:
                dynasty.avg_lifespan_days = sum(lifespans) / len(lifespans)

        # Check extinction
        if dynasty.living_members <= 0:
            dynasty.status = "extinct"
            dynasty.extinct_at = datetime.now(timezone.utc)

            days_alive = (dynasty.extinct_at - dynasty.founded_at).days
            extinction_msg = (
                f"Dynasty {dynasty.dynasty_name} has gone extinct after "
                f"{dynasty.total_generations} generation(s) and "
                f"{days_alive} days. Founded by {dynasty.founder_name}. "
                f"Peak members: {dynasty.peak_members}. "
                f"Total P&L: ${dynasty.total_pnl:.2f}."
            )

            self.log.warning(extinction_msg)

            if agora_service:
                try:
                    await agora_service.post_message(
                        agent_id=0, agent_name="Genesis",
                        channel="genesis-log",
                        content=extinction_msg,
                        message_type="system",
                        importance=5,
                    )
                except Exception as e:
                    self.log.debug(f"Agora extinction post failed: {e}")

        session.add(dynasty)

    async def update_dynasty_pnl(self, session: Session, dynasty_id: int) -> None:
        """Recalculate total dynasty P&L from all members."""
        dynasty = session.get(Dynasty, dynasty_id)
        if not dynasty:
            return

        members = session.execute(
            select(Agent).where(Agent.dynasty_id == dynasty_id)
        ).scalars().all()

        if not members:
            return

        total_pnl = sum(
            (a.realized_pnl or 0) + (a.unrealized_pnl or 0) for a in members
        )
        dynasty.total_pnl = total_pnl

        # Update best performer
        best = max(
            members,
            key=lambda m: (m.realized_pnl or 0) + (m.unrealized_pnl or 0),
        )
        best_pnl = (best.realized_pnl or 0) + (best.unrealized_pnl or 0)
        dynasty.best_performer_id = best.id
        dynasty.best_performer_pnl = best_pnl

        session.add(dynasty)

    async def get_dynasty_concentration(
        self, session: Session, dynasty_id: int,
    ) -> float:
        """Get dynasty's share of total active agents."""
        dynasty = session.get(Dynasty, dynasty_id)
        if not dynasty:
            return 0.0

        total_active = session.execute(
            select(func.count()).select_from(Agent).where(
                Agent.status == "active",
            )
        ).scalar() or 0

        if total_active == 0:
            return 0.0

        return dynasty.living_members / total_active
