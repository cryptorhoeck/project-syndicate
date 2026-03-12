"""
Project Syndicate — Equity Snapshot Service

Takes periodic snapshots of agent equity for Sharpe ratio calculation
and performance tracking. Called by the SanityChecker every 5 minutes.
"""

__version__ = "0.9.0"

import logging
from datetime import datetime, timezone

from sqlalchemy import Date, distinct, func, select
from sqlalchemy.orm import sessionmaker

from src.common.models import Agent, AgentEquitySnapshot, Position

logger = logging.getLogger(__name__)


class EquitySnapshotService:
    """Takes and queries equity snapshots for all agents."""

    def __init__(self, db_session_factory: sessionmaker, price_cache=None):
        self.db_factory = db_session_factory
        self.price_cache = price_cache

    async def take_snapshots(self) -> int:
        """Snapshot total equity for every active agent with capital.

        Equity = cash_balance + sum(current_price * quantity) for open positions.

        Returns:
            Number of snapshots taken.
        """
        count = 0

        with self.db_factory() as session:
            agents = session.execute(
                select(Agent).where(
                    Agent.status.in_(["active", "hibernating"]),
                    Agent.cash_balance > 0,
                )
            ).scalars().all()

            for agent in agents:
                # Calculate position value
                positions = session.execute(
                    select(Position).where(
                        Position.agent_id == agent.id,
                        Position.status == "open",
                    )
                ).scalars().all()

                position_value = sum(
                    p.current_price * p.quantity for p in positions
                )

                equity = agent.cash_balance + position_value

                # Update agent total_equity
                agent.total_equity = equity
                agent.unrealized_pnl = sum(p.unrealized_pnl for p in positions)
                session.add(agent)

                # Write snapshot
                snapshot = AgentEquitySnapshot(
                    agent_id=agent.id,
                    equity=equity,
                    cash_balance=agent.cash_balance,
                    position_value=position_value,
                )
                session.add(snapshot)
                count += 1

            if count > 0:
                session.commit()
                logger.debug(f"Took {count} equity snapshots")

        return count

    async def get_daily_returns(self, agent_id: int, days: int = 30) -> list[float]:
        """Calculate daily returns from equity snapshots.

        Uses the last snapshot of each day to compute day-over-day returns.

        Args:
            agent_id: Agent ID.
            days: Number of days of history.

        Returns:
            List of daily return percentages.
        """
        with self.db_factory() as session:
            # Get last snapshot per day using window function approach
            # SQLite-compatible: group by date, max snapshot_at
            snapshots = session.execute(
                select(AgentEquitySnapshot)
                .where(AgentEquitySnapshot.agent_id == agent_id)
                .order_by(AgentEquitySnapshot.snapshot_at.asc())
            ).scalars().all()

        if len(snapshots) < 2:
            return []

        # Group by date, take last per day
        daily: dict[str, float] = {}
        for snap in snapshots:
            day_key = snap.snapshot_at.strftime("%Y-%m-%d")
            daily[day_key] = snap.equity

        # Convert to list and calculate returns
        equities = list(daily.values())[-days:]
        returns = []
        for i in range(1, len(equities)):
            if equities[i - 1] > 0:
                ret = (equities[i] - equities[i - 1]) / equities[i - 1]
                returns.append(ret)

        return returns
