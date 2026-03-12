"""
Project Syndicate — Limit Order Monitor

Monitors pending limit orders on a 10-second loop:
  - Fills when price crosses the limit (with price improvement)
  - Expires orders after 24 hours
  - Releases cash reservations on fill/expiry
  - Does NOT fill on stale prices
"""

__version__ = "0.9.0"

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from src.common.config import config
from src.common.models import Agent, Order, Position, Transaction

logger = logging.getLogger(__name__)

MONITOR_INTERVAL = config.limit_order_monitor_interval
HEARTBEAT_KEY = "heartbeat:limit_order_monitor"
DEFAULT_EXPIRY_HOURS = config.default_limit_order_expiry_hours


class LimitOrderMonitor:
    """Monitors pending limit orders for fill conditions and expiry."""

    def __init__(
        self,
        db_session_factory: sessionmaker,
        price_cache=None,
        fee_schedule=None,
        redis_client=None,
        agora_service=None,
    ):
        self.db_factory = db_session_factory
        self.price_cache = price_cache
        self.fees = fee_schedule
        self.redis = redis_client
        self.agora = agora_service
        self._running = False

    async def run(self):
        """Main monitoring loop with crash resilience."""
        self._running = True
        logger.info("Limit order monitor started", extra={"interval": MONITOR_INTERVAL})

        while self._running:
            try:
                await self.check_pending_orders()
            except Exception as e:
                logger.error(f"Limit order monitor cycle error: {e}")

            # Update heartbeat
            if self.redis:
                try:
                    self.redis.set(HEARTBEAT_KEY, datetime.now(timezone.utc).isoformat(), ex=30)
                except Exception:
                    pass

            await asyncio.sleep(MONITOR_INTERVAL)

    def stop(self):
        """Signal the monitor to stop."""
        self._running = False

    async def check_pending_orders(self) -> dict:
        """Check all pending limit orders for fill or expiry.

        Returns:
            Dict with counts of fills and expirations.
        """
        result = {"filled": 0, "expired": 0, "skipped_stale": 0}

        with self.db_factory() as session:
            orders = session.execute(
                select(Order).where(
                    Order.status == "pending",
                    Order.order_type == "limit",
                )
            ).scalars().all()

            now = datetime.now(timezone.utc)

            for order in orders:
                # Check expiry first
                requested_at = order.requested_at
                if requested_at and requested_at.tzinfo is None:
                    requested_at = requested_at.replace(tzinfo=timezone.utc)
                expiry_time = requested_at + timedelta(hours=DEFAULT_EXPIRY_HOURS)
                if now >= expiry_time:
                    self._expire_order(session, order)
                    result["expired"] += 1
                    continue

                # Get ticker — do NOT fill on stale prices
                if not self.price_cache:
                    continue

                ticker, is_fresh = await self.price_cache.get_ticker(order.symbol)
                if ticker is None or not is_fresh:
                    result["skipped_stale"] += 1
                    continue

                # Check fill condition
                if self._should_fill(order, ticker):
                    await self._fill_limit_order(session, order, ticker)
                    result["filled"] += 1

            session.commit()

        return result

    def _should_fill(self, order: Order, ticker: dict) -> bool:
        """Check if a limit order should fill at the current price."""
        if order.side == "buy":
            # Buy limit fills when ask <= requested_price
            current_ask = ticker.get("ask", 0)
            return current_ask > 0 and current_ask <= order.requested_price
        else:
            # Sell limit fills when bid >= requested_price
            current_bid = ticker.get("bid", 0)
            return current_bid > 0 and current_bid >= order.requested_price

    async def _fill_limit_order(self, session, order: Order, ticker: dict):
        """Fill a limit order with price improvement."""
        # Price improvement: buy fills at min(limit, ask), sell at max(limit, bid)
        if order.side == "buy":
            fill_price = min(order.requested_price, ticker.get("ask", order.requested_price))
        else:
            fill_price = max(order.requested_price, ticker.get("bid", order.requested_price))

        # Calculate maker fee
        fill_value = order.requested_size_usd
        fee_usd, fee_rate = 0.0, 0.0
        if self.fees:
            fee_usd, fee_rate = self.fees.calculate_fee(fill_value, "limit", config.default_exchange)

        # Calculate quantity
        quantity = fill_value / fill_price if fill_price > 0 else 0

        now = datetime.now(timezone.utc)
        bid = ticker.get("bid", 0)
        ask = ticker.get("ask", 0)
        spread_pct = ((ask - bid) / bid * 100) if bid > 0 else 0

        # Update order
        order.fill_price = fill_price
        order.fill_quantity = quantity
        order.fill_value_usd = fill_value
        order.fee_usd = fee_usd
        order.fee_rate = fee_rate
        order.market_bid = bid
        order.market_ask = ask
        order.market_spread_pct = spread_pct
        order.filled_at = now
        order.status = "filled"
        session.add(order)

        # Release reservation
        agent = session.get(Agent, order.agent_id)
        if not agent:
            return

        if order.reserved_amount and not order.reservation_released:
            agent.reserved_cash = max(0, agent.reserved_cash - order.reserved_amount)
            order.reservation_released = True

        # Determine position side
        position_side = "long" if order.side == "buy" else "short"

        # Create position
        position = Position(
            agent_id=order.agent_id,
            agent_name=order.agent_name,
            symbol=order.symbol,
            side=position_side,
            entry_price=fill_price,
            current_price=fill_price,
            quantity=quantity,
            size_usd=fill_value,
            fees_entry=fee_usd,
            source_plan_id=order.source_plan_id,
            source_cycle_id=order.source_cycle_id,
            opened_at=now,
            status="open",
            execution_venue="paper",
        )
        session.add(position)
        session.flush()

        # Link order to position
        order.position_id = position.id

        # Update agent cash (deduct actual cost, not reservation)
        total_cost = fill_value + fee_usd
        agent.cash_balance -= total_cost
        agent.total_fees_paid += fee_usd
        agent.position_count += 1
        session.add(agent)

        # Transaction for Accountant
        txn = Transaction(
            agent_id=order.agent_id,
            type="spot",
            exchange="paper",
            symbol=order.symbol,
            side=order.side,
            amount=quantity,
            price=fill_price,
            fee=fee_usd,
            pnl=0.0,
        )
        session.add(txn)

        logger.info(
            f"Limit {order.side} filled: {order.symbol} qty={quantity:.6f} "
            f"@ ${fill_price:.4f} (requested ${order.requested_price:.4f})",
            extra={"agent_id": order.agent_id, "order_id": order.id},
        )

    def _expire_order(self, session, order: Order):
        """Expire a pending order and release reservation."""
        order.status = "expired"
        session.add(order)

        # Release reservation
        if order.reserved_amount and not order.reservation_released:
            agent = session.get(Agent, order.agent_id)
            if agent:
                agent.reserved_cash = max(0, agent.reserved_cash - order.reserved_amount)
                order.reservation_released = True
                session.add(agent)

        logger.info(
            f"Limit order expired: {order.symbol} {order.side} "
            f"${order.requested_size_usd:.2f} @ ${order.requested_price:.4f}",
            extra={"agent_id": order.agent_id, "order_id": order.id},
        )
