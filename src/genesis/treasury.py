"""
Project Syndicate — Treasury Manager

Manages capital allocation, reclamation, position inheritance,
and the anti-monopoly random allocation mechanism.

Treasury is denominated in CAD (owner's home currency).
Agent capital is denominated in USDT (trading currency).
Conversion happens at the treasury↔agent boundary.
"""

__version__ = "0.3.0"

import random
from datetime import datetime, timedelta, timezone

import structlog
from sqlalchemy import create_engine, select, update
from sqlalchemy.orm import sessionmaker

from src.common.config import config
from src.common.models import Agent, InheritedPosition, SystemState

logger = structlog.get_logger()


class TreasuryManager:
    """Manages the Syndicate's treasury and capital allocation.

    Treasury values are in CAD.  Agent capital values are in USDT.
    The CurrencyService converts at the boundary.
    """

    RESERVE_RATIO = config.treasury_reserve_ratio  # 20%
    RANDOM_ALLOCATION_PCT = config.random_allocation_pct  # 10%

    def __init__(
        self,
        exchange_service=None,
        db_session_factory: sessionmaker | None = None,
        currency_service=None,
    ) -> None:
        self.log = logger.bind(component="treasury")
        self.exchange = exchange_service
        self._currency = currency_service

        if db_session_factory:
            self.db_session_factory = db_session_factory
        else:
            engine = create_engine(config.database_url)
            self.db_session_factory = sessionmaker(bind=engine)

    @property
    def currency(self):
        """Lazy-init CurrencyService if not injected."""
        if self._currency is None:
            from src.common.currency_service import CurrencyService
            self._currency = CurrencyService()
        return self._currency

    # ------------------------------------------------------------------
    # Balance
    # ------------------------------------------------------------------

    async def get_treasury_balance(self) -> dict:
        """Get full treasury breakdown.

        All returned values are in CAD.  Agent capital (stored in USDT)
        is converted to CAD at the current rate for the available-balance
        calculation.
        """
        with self.db_session_factory() as session:
            state = session.execute(select(SystemState).limit(1)).scalar_one_or_none()
            total = state.total_treasury if state else 0.0  # CAD
            peak = state.peak_treasury if state else 0.0  # CAD

            # Sum capital allocated to agents (USDT)
            allocated_usdt = session.execute(
                select(
                    __import__("sqlalchemy").func.coalesce(
                        __import__("sqlalchemy").func.sum(Agent.capital_allocated), 0.0
                    )
                ).where(Agent.status.in_(["active", "evaluating", "hibernating"]))
            ).scalar() or 0.0

        # Convert agent USDT allocations to CAD for the balance calc
        allocated_cad = self.currency.usdt_to_cad(float(allocated_usdt))
        reserved = total * self.RESERVE_RATIO
        available = max(0.0, total - reserved - allocated_cad)
        drawdown_pct = round((1.0 - total / peak) * 100, 2) if peak > 0 else 0.0

        return {
            "total": round(total, 2),  # CAD
            "available_for_allocation": round(available, 2),  # CAD
            "reserved": round(reserved, 2),  # CAD
            "allocated_to_agents": round(allocated_cad, 2),  # CAD equivalent
            "allocated_to_agents_usdt": round(float(allocated_usdt), 2),
            "peak": round(peak, 2),  # CAD
            "drawdown_pct": drawdown_pct,
            "currency": config.home_currency,
            "usdt_cad_rate": self.currency.get_usdt_cad_rate(),
        }

    # ------------------------------------------------------------------
    # Capital Allocation
    # ------------------------------------------------------------------

    async def allocate_capital(self, agent_id: int, amount_cad: float) -> bool:
        """Allocate capital to an agent, respecting reserve.

        Args:
            agent_id: The agent to receive capital.
            amount_cad: Amount in CAD to allocate from treasury.
                        Converted to USDT for the agent's capital fields.
        """
        balance = await self.get_treasury_balance()
        if amount_cad > balance["available_for_allocation"]:
            self.log.warning(
                "allocation_rejected_insufficient",
                agent_id=agent_id,
                requested_cad=amount_cad,
                available_cad=balance["available_for_allocation"],
            )
            return False

        # Convert CAD → USDT for the agent
        amount_usdt = self.currency.cad_to_usdt(amount_cad)

        with self.db_session_factory() as session:
            agent = session.get(Agent, agent_id)
            if agent is None:
                return False

            agent.capital_allocated = (agent.capital_allocated or 0.0) + amount_usdt
            agent.capital_current = (agent.capital_current or 0.0) + amount_usdt
            session.commit()

        self.log.info(
            "capital_allocated",
            agent_id=agent_id,
            amount_cad=round(amount_cad, 2),
            amount_usdt=round(amount_usdt, 2),
        )
        return True

    async def reclaim_capital(self, agent_id: int) -> float:
        """Reclaim all capital from an agent back to treasury.

        Agent's USDT capital is converted to CAD before adding to
        the treasury.  Returns the reclaimed amount in USDT.
        """
        with self.db_session_factory() as session:
            agent = session.get(Agent, agent_id)
            if agent is None:
                return 0.0

            reclaimed_usdt = agent.capital_current or 0.0
            agent.capital_allocated = 0.0
            agent.capital_current = 0.0

            # Convert USDT → CAD before adding to treasury
            reclaimed_cad = self.currency.usdt_to_cad(reclaimed_usdt)
            state = session.execute(select(SystemState).limit(1)).scalar_one_or_none()
            if state:
                state.total_treasury = (state.total_treasury or 0.0) + reclaimed_cad
            session.commit()

        self.log.info(
            "capital_reclaimed",
            agent_id=agent_id,
            amount_usdt=round(reclaimed_usdt, 2),
            amount_cad=round(reclaimed_cad, 2),
        )
        return reclaimed_usdt

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
