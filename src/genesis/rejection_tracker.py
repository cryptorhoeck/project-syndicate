"""
Project Syndicate — Rejection Tracker

Tracks rejected plans for counterfactual simulation:
  - Records plan parameters at rejection time
  - Monitors market to see if the trade would have been profitable
  - Determines whether the Critic was correct to reject
"""

__version__ = "1.0.0"

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.common.models import Plan, RejectionTracking

logger = logging.getLogger(__name__)

# Timeframe string → hours mapping
TIMEFRAME_HOURS = {
    "1h": 1, "2h": 2, "4h": 4, "8h": 8, "12h": 12,
    "1d": 24, "2d": 48, "3d": 72, "1w": 168,
    "scalp": 1, "intraday": 8, "swing": 72, "position": 168,
}


class RejectionTracker:
    """Tracks rejected plans for counterfactual analysis."""

    def __init__(self, price_cache=None):
        self.price_cache = price_cache

    async def track_rejection(
        self, session: Session, plan: Plan, current_price: float,
    ) -> RejectionTracking:
        """Start tracking a rejected plan for counterfactual simulation.

        Args:
            session: DB session.
            plan: The rejected Plan object.
            current_price: Market price at rejection time.

        Returns:
            The created RejectionTracking record.
        """
        now = datetime.now(timezone.utc)

        # Parse timeframe to determine check_until
        timeframe_str = plan.timeframe or "1d"
        hours = TIMEFRAME_HOURS.get(timeframe_str, 24)
        check_until = now + timedelta(hours=hours)

        # Extract stop/take-profit from plan exit_conditions if possible
        stop_loss = None
        take_profit = None
        try:
            import json
            exit_data = json.loads(plan.exit_conditions) if plan.exit_conditions else {}
            stop_loss = exit_data.get("stop_loss")
            take_profit = exit_data.get("take_profit")
        except (json.JSONDecodeError, TypeError):
            pass

        tracking = RejectionTracking(
            plan_id=plan.id,
            critic_id=plan.critic_agent_id,
            market=plan.market,
            direction=plan.direction,
            entry_price=current_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            timeframe=timeframe_str,
            rejected_at=now,
            check_until=check_until,
            status="tracking",
        )
        session.add(tracking)
        session.flush()

        logger.info(
            f"Tracking rejected plan {plan.id} ({plan.market} {plan.direction}) "
            f"until {check_until.isoformat()}"
        )

        return tracking

    async def monitor_tracked_rejections(self, session: Session) -> dict:
        """Check all active rejection trackings against current prices.

        Returns:
            Dict with counts: completed, still_tracking.
        """
        result = {"completed": 0, "still_tracking": 0}

        now = datetime.now(timezone.utc)
        trackings = session.execute(
            select(RejectionTracking).where(
                RejectionTracking.status == "tracking"
            )
        ).scalars().all()

        for tracking in trackings:
            current_price = await self._get_current_price(tracking.market)
            if current_price is None:
                result["still_tracking"] += 1
                continue

            completed = False

            # Check stop-loss
            if tracking.stop_loss is not None:
                if tracking.direction == "long" and current_price <= tracking.stop_loss:
                    tracking.outcome = "stop_loss_hit"
                    tracking.outcome_price = current_price
                    tracking.outcome_pnl_pct = (
                        (tracking.stop_loss - tracking.entry_price) / tracking.entry_price * 100
                    )
                    tracking.critic_correct = True
                    completed = True
                elif tracking.direction == "short" and current_price >= tracking.stop_loss:
                    tracking.outcome = "stop_loss_hit"
                    tracking.outcome_price = current_price
                    tracking.outcome_pnl_pct = (
                        (tracking.entry_price - tracking.stop_loss) / tracking.entry_price * 100
                    )
                    tracking.critic_correct = True
                    completed = True

            # Check take-profit
            if not completed and tracking.take_profit is not None:
                if tracking.direction == "long" and current_price >= tracking.take_profit:
                    tracking.outcome = "take_profit_hit"
                    tracking.outcome_price = current_price
                    tracking.outcome_pnl_pct = (
                        (tracking.take_profit - tracking.entry_price) / tracking.entry_price * 100
                    )
                    tracking.critic_correct = False
                    completed = True
                elif tracking.direction == "short" and current_price <= tracking.take_profit:
                    tracking.outcome = "take_profit_hit"
                    tracking.outcome_price = current_price
                    tracking.outcome_pnl_pct = (
                        (tracking.entry_price - tracking.take_profit) / tracking.entry_price * 100
                    )
                    tracking.critic_correct = False
                    completed = True

            # Check timeframe expiry
            check_until = tracking.check_until
            if check_until.tzinfo is None:
                check_until = check_until.replace(tzinfo=timezone.utc)
            if not completed and now >= check_until:
                tracking.outcome = "timeframe_expired"
                tracking.outcome_price = current_price
                if tracking.direction == "long":
                    pnl_pct = (current_price - tracking.entry_price) / tracking.entry_price * 100
                else:
                    pnl_pct = (tracking.entry_price - current_price) / tracking.entry_price * 100
                tracking.outcome_pnl_pct = pnl_pct
                tracking.critic_correct = pnl_pct < 0  # Critic right if trade would have lost
                completed = True

            if completed:
                tracking.status = "completed"
                tracking.completed_at = now
                session.add(tracking)
                result["completed"] += 1
                logger.info(
                    f"Rejection tracking {tracking.id} completed: "
                    f"{tracking.outcome}, critic_correct={tracking.critic_correct}"
                )
            else:
                result["still_tracking"] += 1

        return result

    async def get_critic_rejection_score(
        self, session: Session, critic_id: int,
        period_start: datetime, period_end: datetime,
    ) -> float:
        """Get rejection accuracy score for a critic.

        Returns:
            Fraction of correct rejections (0.0-1.0), or 0.5 if no data.
        """
        trackings = session.execute(
            select(RejectionTracking).where(
                RejectionTracking.critic_id == critic_id,
                RejectionTracking.status == "completed",
                RejectionTracking.completed_at >= period_start,
                RejectionTracking.completed_at <= period_end,
            )
        ).scalars().all()

        if not trackings:
            return 0.5  # Neutral on no data

        correct = sum(1 for t in trackings if t.critic_correct is True)
        return correct / len(trackings)

    async def _get_current_price(self, symbol: str) -> float | None:
        """Get current price for a symbol."""
        if not self.price_cache:
            return None
        try:
            ticker, fresh = await self.price_cache.get_ticker(symbol)
            if ticker:
                return ticker.get("last") or ticker.get("bid")
        except Exception:
            pass
        return None
