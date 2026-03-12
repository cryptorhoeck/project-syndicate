"""
Project Syndicate — Treasury Manager

Manages capital allocation, reclamation, position inheritance,
and the anti-monopoly random allocation mechanism.
"""

__version__ = "0.2.0"

import random
from datetime import datetime, timedelta, timezone

import structlog
from sqlalchemy import create_engine, select, update
from sqlalchemy.orm import sessionmaker

from src.common.config import config
from src.common.models import Agent, InheritedPosition, SystemState

logger = structlog.get_logger()


class TreasuryManager:
    """Manages the Syndicate's treasury and capital allocation."""

    RESERVE_RATIO = config.treasury_reserve_ratio  # 20%
    RANDOM_ALLOCATION_PCT = config.random_allocation_pct  # 10%

    def __init__(
        self,
        exchange_service=None,
        db_session_factory: sessionmaker | None = None,
    ) -> None:
        self.log = logger.bind(component="treasury")
        self.exchange = exchange_service

        if db_session_factory:
            self.db_session_factory = db_session_factory
        else:
            engine = create_engine(config.database_url)
            self.db_session_factory = sessionmaker(bind=engine)

    # ------------------------------------------------------------------
    # Balance
    # ------------------------------------------------------------------

    async def get_treasury_balance(self) -> dict:
        """Get full treasury breakdown."""
        with self.db_session_factory() as session:
            state = session.execute(select(SystemState).limit(1)).scalar_one_or_none()
            total = state.total_treasury if state else 0.0
            peak = state.peak_treasury if state else 0.0

            # Sum capital allocated to agents
            allocated = session.execute(
                select(
                    __import__("sqlalchemy").func.coalesce(
                        __import__("sqlalchemy").func.sum(Agent.capital_allocated), 0.0
                    )
                ).where(Agent.status.in_(["active", "evaluating", "hibernating"]))
            ).scalar() or 0.0

        reserved = total * self.RESERVE_RATIO
        available = max(0.0, total - reserved - float(allocated))
        drawdown_pct = round((1.0 - total / peak) * 100, 2) if peak > 0 else 0.0

        return {
            "total": round(total, 2),
            "available_for_allocation": round(available, 2),
            "reserved": round(reserved, 2),
            "allocated_to_agents": round(float(allocated), 2),
            "peak": round(peak, 2),
            "drawdown_pct": drawdown_pct,
        }

    # ------------------------------------------------------------------
    # Capital Allocation
    # ------------------------------------------------------------------

    async def allocate_capital(self, agent_id: int, amount: float) -> bool:
        """Allocate capital to an agent, respecting reserve."""
        balance = await self.get_treasury_balance()
        if amount > balance["available_for_allocation"]:
            self.log.warning(
                "allocation_rejected_insufficient",
                agent_id=agent_id,
                requested=amount,
                available=balance["available_for_allocation"],
            )
            return False

        with self.db_session_factory() as session:
            agent = session.get(Agent, agent_id)
            if agent is None:
                return False

            agent.capital_allocated = (agent.capital_allocated or 0.0) + amount
            agent.capital_current = (agent.capital_current or 0.0) + amount
            session.commit()

        self.log.info("capital_allocated", agent_id=agent_id, amount=amount)
        return True

    async def reclaim_capital(self, agent_id: int) -> float:
        """Reclaim all capital from an agent back to treasury."""
        with self.db_session_factory() as session:
            agent = session.get(Agent, agent_id)
            if agent is None:
                return 0.0

            reclaimed = agent.capital_current or 0.0
            agent.capital_allocated = 0.0
            agent.capital_current = 0.0

            # Add back to treasury
            state = session.execute(select(SystemState).limit(1)).scalar_one_or_none()
            if state:
                state.total_treasury = (state.total_treasury or 0.0) + reclaimed
            session.commit()

        self.log.info("capital_reclaimed", agent_id=agent_id, amount=reclaimed)
        return reclaimed

    # ------------------------------------------------------------------
    # Capital Allocation Round
    # ------------------------------------------------------------------

    async def perform_capital_allocation_round(self, leaderboard: list) -> list:
        """Perform a full capital allocation round after evaluations.

        90% by rank, 10% random (anti-monopoly).
        Prestige multipliers apply.
        """
        balance = await self.get_treasury_balance()
        available = balance["available_for_allocation"]
        if available <= 0:
            return []

        rank_pool = available * (1.0 - self.RANDOM_ALLOCATION_PCT)
        random_pool = available * self.RANDOM_ALLOCATION_PCT

        decisions = []

        # Rank-based allocation (top agents get proportionally more)
        eligible = [e for e in leaderboard if e["composite_score"] > 0]
        if eligible:
            total_score = sum(e["composite_score"] for e in eligible)
            for entry in eligible:
                share = (entry["composite_score"] / total_score) * rank_pool if total_score > 0 else 0
                multiplier = self._get_prestige_multiplier(entry.get("prestige_title"))
                amount = share * multiplier
                amount = min(amount, rank_pool)  # Don't exceed pool

                if amount > 0:
                    success = await self.allocate_capital(entry["agent_id"], amount)
                    decisions.append({
                        "agent_id": entry["agent_id"],
                        "type": "rank_based",
                        "amount": round(amount, 2),
                        "multiplier": multiplier,
                        "success": success,
                    })

        # Random allocation (anti-rich-get-richer)
        if eligible and random_pool > 0:
            random_recipients = random.sample(
                eligible,
                min(len(eligible), max(1, len(eligible) // 3)),
            )
            per_agent = random_pool / len(random_recipients)
            for entry in random_recipients:
                success = await self.allocate_capital(entry["agent_id"], per_agent)
                decisions.append({
                    "agent_id": entry["agent_id"],
                    "type": "random",
                    "amount": round(per_agent, 2),
                    "success": success,
                })

        self.log.info(
            "allocation_round_complete",
            decisions=len(decisions),
            rank_pool=round(rank_pool, 2),
            random_pool=round(random_pool, 2),
        )
        return decisions

    # ------------------------------------------------------------------
    # Position Inheritance
    # ------------------------------------------------------------------

    async def inherit_positions(self, dead_agent_id: int) -> list:
        """Inherit open positions from a dead agent. Genesis takes ownership."""
        inherited = []

        with self.db_session_factory() as session:
            # Get agent's transactions that represent open positions
            # For now, we create inheritance records based on the agent's last trades
            agent = session.get(Agent, dead_agent_id)
            if agent is None:
                return inherited

            # Create inherited position record (placeholder — real positions
            # would come from exchange API in production)
            self.log.info(
                "positions_inherited",
                dead_agent_id=dead_agent_id,
                inherited_by="genesis",
            )

        return inherited

    async def close_inherited_positions(self) -> list:
        """Close any inherited positions past the 24-hour deadline."""
        closed = []
        deadline = datetime.now(timezone.utc) - timedelta(hours=24)

        with self.db_session_factory() as session:
            open_positions = session.execute(
                select(InheritedPosition).where(
                    InheritedPosition.status == "open",
                    InheritedPosition.inherited_at <= deadline,
                )
            ).scalars().all()

            for pos in open_positions:
                # In production, would close via exchange_service
                pos.status = "closed"
                pos.closed_at = datetime.now(timezone.utc)
                closed.append({
                    "position_id": pos.id,
                    "symbol": pos.symbol,
                    "amount": pos.amount,
                })
                self.log.info("inherited_position_closed", position_id=pos.id, symbol=pos.symbol)

            if closed:
                session.commit()

        return closed

    # ------------------------------------------------------------------
    # Peak Treasury
    # ------------------------------------------------------------------

    async def update_peak_treasury(self) -> None:
        """Update peak treasury if current exceeds previous peak."""
        with self.db_session_factory() as session:
            state = session.execute(select(SystemState).limit(1)).scalar_one_or_none()
            if state is None:
                return

            if state.total_treasury > state.peak_treasury:
                state.peak_treasury = state.total_treasury
                session.commit()
                self.log.info(
                    "peak_treasury_updated",
                    new_peak=state.peak_treasury,
                )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _get_prestige_multiplier(self, title: str | None) -> float:
        """Return the capital allocation multiplier for a prestige title."""
        if title is None:
            return 1.0
        title_lower = title.lower()
        if "legendary" in title_lower:
            return config.prestige_legendary_multiplier
        if "elite" in title_lower:
            return config.prestige_elite_multiplier
        if "veteran" in title_lower:
            return config.prestige_veteran_multiplier
        if "proven" in title_lower:
            return config.prestige_proven_multiplier
        return 1.0
