"""
Project Syndicate — Paper Trading Sanity Checker

Periodic health checks for the paper trading subsystem:
  - Negative cash balance detection (CRITICAL — indicates a bug)
  - Equity reconciliation (auto-corrects drift > $0.01)
  - Orphaned position detection
  - Stale reservation cleanup
  - Concentration monitoring
"""

__version__ = "0.9.0"

import logging
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from src.common.config import config
from src.common.models import Agent, Order, Position

logger = logging.getLogger(__name__)


class ConcentrationMonitor:
    """Monitors position concentration across the syndicate."""

    THRESHOLD = config.concentration_warning_threshold

    def __init__(self, db_session_factory: sessionmaker, agora_service=None):
        self.db_factory = db_session_factory
        self.agora = agora_service

    async def check(self) -> list[dict]:
        """Check if any single position exceeds concentration threshold.

        Returns:
            List of concentration warnings.
        """
        warnings = []

        with self.db_factory() as session:
            # Get total deployed capital across all agents
            total_deployed = session.execute(
                select(func.sum(Position.size_usd)).where(Position.status == "open")
            ).scalar() or 0

            if total_deployed <= 0:
                return warnings

            # Check each open position
            positions = session.execute(
                select(Position).where(Position.status == "open")
            ).scalars().all()

            for pos in positions:
                concentration = pos.size_usd / total_deployed
                if concentration > self.THRESHOLD:
                    warning = {
                        "position_id": pos.id,
                        "agent_id": pos.agent_id,
                        "symbol": pos.symbol,
                        "size_usd": pos.size_usd,
                        "concentration_pct": round(concentration * 100, 1),
                        "threshold_pct": round(self.THRESHOLD * 100, 1),
                    }
                    warnings.append(warning)
                    logger.warning(
                        f"Concentration warning: {pos.symbol} is {concentration:.1%} "
                        f"of deployed capital (threshold={self.THRESHOLD:.0%})",
                        extra={"position_id": pos.id, "agent_id": pos.agent_id},
                    )

        return warnings


class PaperTradingSanityChecker:
    """Runs periodic sanity checks on the paper trading subsystem."""

    def __init__(
        self,
        db_session_factory: sessionmaker,
        equity_snapshot_service=None,
        concentration_monitor=None,
        agora_service=None,
    ):
        self.db_factory = db_session_factory
        self.equity_service = equity_snapshot_service
        self.concentration = concentration_monitor
        self.agora = agora_service

    async def run_all(self) -> dict:
        """Run all sanity checks.

        Returns:
            Dict with results of each check.
        """
        results = {}
        results["negative_cash"] = await self.check_cash_balances()
        results["equity_corrections"] = await self.check_equity_reconciliation()
        results["orphaned_positions"] = await self.check_orphaned_positions()
        results["stale_reservations"] = await self.check_stale_reservations()

        if self.concentration:
            results["concentration_warnings"] = await self.concentration.check()

        if self.equity_service:
            results["snapshots_taken"] = await self.equity_service.take_snapshots()

        return results

    async def check_cash_balances(self) -> list[dict]:
        """Flag agents with negative cash balance.

        This is CRITICAL — indicates a bug in the trading engine.
        Does NOT auto-fix (the root cause must be found).
        """
        flagged = []

        with self.db_factory() as session:
            agents = session.execute(
                select(Agent).where(
                    Agent.status.in_(["active", "hibernating"]),
                    Agent.cash_balance < -0.01,
                )
            ).scalars().all()

            for agent in agents:
                issue = {
                    "agent_id": agent.id,
                    "name": agent.name,
                    "cash_balance": agent.cash_balance,
                    "severity": "CRITICAL",
                }
                flagged.append(issue)
                logger.critical(
                    f"NEGATIVE CASH: Agent {agent.name} has ${agent.cash_balance:.4f}",
                    extra={"agent_id": agent.id},
                )

        return flagged

    async def check_equity_reconciliation(self) -> int:
        """Recalculate equity and auto-correct drift > $0.01.

        Returns:
            Number of agents corrected.
        """
        corrections = 0

        with self.db_factory() as session:
            agents = session.execute(
                select(Agent).where(
                    Agent.status.in_(["active", "hibernating"]),
                )
            ).scalars().all()

            for agent in agents:
                # Calculate expected equity
                positions = session.execute(
                    select(Position).where(
                        Position.agent_id == agent.id,
                        Position.status == "open",
                    )
                ).scalars().all()

                long_value = sum(
                    p.current_price * p.quantity for p in positions if p.side == "long"
                )
                short_value = sum(
                    p.current_price * p.quantity for p in positions if p.side == "short"
                )
                expected = agent.cash_balance + long_value - short_value

                drift = abs(expected - agent.total_equity)
                if drift > 0.01:
                    logger.info(
                        f"Equity drift corrected for {agent.name}: "
                        f"${agent.total_equity:.4f} → ${expected:.4f} (drift=${drift:.4f})",
                        extra={"agent_id": agent.id},
                    )
                    agent.total_equity = expected
                    session.add(agent)
                    corrections += 1

            if corrections > 0:
                session.commit()

        return corrections

    async def check_orphaned_positions(self) -> list[dict]:
        """Find open positions for dead/terminated agents.

        Returns:
            List of orphaned positions.
        """
        orphans = []

        with self.db_factory() as session:
            positions = session.execute(
                select(Position).where(Position.status == "open")
            ).scalars().all()

            for pos in positions:
                agent = session.get(Agent, pos.agent_id)
                if agent and agent.status not in ("active", "hibernating"):
                    orphans.append({
                        "position_id": pos.id,
                        "agent_id": pos.agent_id,
                        "agent_status": agent.status if agent else "missing",
                        "symbol": pos.symbol,
                        "size_usd": pos.size_usd,
                    })
                    logger.warning(
                        f"Orphaned position: #{pos.id} ({pos.symbol}) "
                        f"belongs to {agent.status if agent else 'missing'} agent {pos.agent_id}",
                    )

        return orphans

    async def check_stale_reservations(self) -> int:
        """Find cancelled/expired orders with unreleased reservations.

        Auto-fixes by releasing the reservation.

        Returns:
            Number of stale reservations released.
        """
        released = 0

        with self.db_factory() as session:
            orders = session.execute(
                select(Order).where(
                    Order.status.in_(["cancelled", "expired"]),
                    Order.reservation_released == False,
                    Order.reserved_amount > 0,
                )
            ).scalars().all()

            for order in orders:
                agent = session.get(Agent, order.agent_id)
                if agent:
                    agent.reserved_cash = max(0, agent.reserved_cash - order.reserved_amount)
                    session.add(agent)
                order.reservation_released = True
                session.add(order)
                released += 1

                logger.info(
                    f"Released stale reservation: order #{order.id} "
                    f"${order.reserved_amount:.4f}",
                    extra={"agent_id": order.agent_id},
                )

            if released > 0:
                session.commit()

        return released
