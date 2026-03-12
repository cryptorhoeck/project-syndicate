"""
Project Syndicate — Position Monitor

Monitors all open positions on a 10-second loop:
  - Updates unrealized P&L
  - Triggers stop-loss (fills at BID + slippage for longs)
  - Triggers take-profit (fills at take-profit price, maker fee)
  - Pauses stop/TP checks on stale data (>60s)
  - Redis heartbeat for Dead Man's Switch
"""

__version__ = "0.9.0"

import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from src.common.config import config
from src.common.models import Agent, AgentCycle, Position, Transaction

logger = logging.getLogger(__name__)

MONITOR_INTERVAL = config.position_monitor_interval
HEARTBEAT_KEY = "heartbeat:position_monitor"


class PositionMonitor:
    """Monitors open positions for stop-loss and take-profit triggers."""

    def __init__(
        self,
        db_session_factory: sessionmaker,
        price_cache=None,
        slippage_model=None,
        fee_schedule=None,
        redis_client=None,
        agora_service=None,
    ):
        self.db_factory = db_session_factory
        self.price_cache = price_cache
        self.slippage = slippage_model
        self.fees = fee_schedule
        self.redis = redis_client
        self.agora = agora_service
        self._running = False

    async def run(self):
        """Main monitoring loop with crash resilience."""
        self._running = True
        logger.info("Position monitor started", extra={"interval": MONITOR_INTERVAL})

        while self._running:
            try:
                await self.check_all_positions()
            except Exception as e:
                logger.error(f"Position monitor cycle error: {e}")

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

    async def check_all_positions(self) -> dict:
        """Check all open positions, update P&L, trigger stops/TPs.

        Returns:
            Dict with counts of actions taken.
        """
        result = {"updated": 0, "stopped": 0, "tp_hit": 0, "stale_skipped": 0}

        with self.db_factory() as session:
            positions = session.execute(
                select(Position).where(Position.status == "open")
            ).scalars().all()

            if not positions:
                return result

            # Batch fetch unique symbols
            symbols = list({p.symbol for p in positions})
            tickers = {}
            if self.price_cache:
                try:
                    tickers = await self.price_cache.batch_fetch_tickers(symbols)
                except Exception as e:
                    logger.warning(f"Batch ticker fetch failed: {e}")
                    return result

            for position in positions:
                ticker = tickers.get(position.symbol)
                if not ticker:
                    result["stale_skipped"] += 1
                    continue

                # Check staleness
                is_stale = self.price_cache.is_stale(position.symbol) if self.price_cache else True

                # Always update unrealized P&L
                self._update_unrealized_pnl(session, position, ticker)
                result["updated"] += 1

                # Only trigger stops/TPs on fresh data
                if is_stale:
                    result["stale_skipped"] += 1
                    self._alert_stale_price(position.symbol)
                    continue

                # Check stop-loss
                if position.stop_loss:
                    triggered = self._check_stop_loss(position, ticker)
                    if triggered:
                        await self._execute_stop_loss(session, position, ticker)
                        result["stopped"] += 1
                        continue

                # Check take-profit
                if position.take_profit:
                    triggered = self._check_take_profit(position, ticker)
                    if triggered:
                        await self._execute_take_profit(session, position, ticker)
                        result["tp_hit"] += 1

            session.commit()

        return result

    def _update_unrealized_pnl(self, session, position: Position, ticker: dict):
        """Update unrealized P&L for a position using mid-price."""
        bid = ticker.get("bid", 0)
        ask = ticker.get("ask", 0)
        mid = (bid + ask) / 2 if bid and ask else ticker.get("last", position.current_price)

        position.current_price = mid

        if position.side == "long":
            pnl = (mid - position.entry_price) * position.quantity
        else:
            pnl = (position.entry_price - mid) * position.quantity

        position.unrealized_pnl = pnl
        position.unrealized_pnl_pct = (pnl / position.size_usd * 100) if position.size_usd > 0 else 0
        session.add(position)

    def _check_stop_loss(self, position: Position, ticker: dict) -> bool:
        """Check if stop-loss has been triggered."""
        if position.side == "long":
            # Long stop: triggers when bid <= stop_loss
            current_bid = ticker.get("bid", 0)
            return current_bid > 0 and current_bid <= position.stop_loss
        else:
            # Short stop: triggers when ask >= stop_loss
            current_ask = ticker.get("ask", 0)
            return current_ask > 0 and current_ask >= position.stop_loss

    def _check_take_profit(self, position: Position, ticker: dict) -> bool:
        """Check if take-profit has been triggered."""
        if position.side == "long":
            # Long TP: triggers when bid >= take_profit
            current_bid = ticker.get("bid", 0)
            return current_bid > 0 and current_bid >= position.take_profit
        else:
            # Short TP: triggers when ask <= take_profit
            current_ask = ticker.get("ask", 0)
            return current_ask > 0 and current_ask <= position.take_profit

    async def _execute_stop_loss(self, session, position: Position, ticker: dict):
        """Execute a stop-loss: fills at BID (longs) or ASK (shorts) WITH slippage."""
        if position.side == "long":
            base_price = ticker.get("bid", position.current_price)
        else:
            base_price = ticker.get("ask", position.current_price)

        # Add slippage (stop-losses fill at worse prices)
        slippage_pct = 0.0
        exit_side = "sell" if position.side == "long" else "buy"
        if self.slippage:
            slippage_pct = await self.slippage.calculate_slippage(
                position.size_usd, position.symbol, exit_side, self.price_cache
            )

        if position.side == "long":
            fill_price = base_price * (1 - slippage_pct)
        else:
            fill_price = base_price * (1 + slippage_pct)

        # Taker fee for stop-loss (market order)
        exit_value = fill_price * position.quantity
        fee_usd = 0.0
        if self.fees:
            fee_usd, _ = self.fees.calculate_fee(exit_value, "market", config.default_exchange)

        await self._close_position(
            session, position, fill_price, fee_usd, "stop_loss", "stopped_out"
        )

        logger.info(
            f"Stop-loss triggered: {position.symbol} {position.side} @ ${fill_price:.4f} "
            f"(stop={position.stop_loss}, slippage={slippage_pct:.4%})",
            extra={"agent_id": position.agent_id, "position_id": position.id},
        )

    async def _execute_take_profit(self, session, position: Position, ticker: dict):
        """Execute take-profit: fills at TP price (limit-style, maker fee)."""
        fill_price = position.take_profit  # Limit-style fill at exact TP price

        # Maker fee for take-profit (limit-style execution)
        exit_value = fill_price * position.quantity
        fee_usd = 0.0
        if self.fees:
            fee_usd, _ = self.fees.calculate_fee(exit_value, "limit", config.default_exchange)

        await self._close_position(
            session, position, fill_price, fee_usd, "take_profit", "take_profit_hit"
        )

        logger.info(
            f"Take-profit hit: {position.symbol} {position.side} @ ${fill_price:.4f}",
            extra={"agent_id": position.agent_id, "position_id": position.id},
        )

    async def _close_position(
        self, session, position: Position, fill_price: float,
        fee_usd: float, reason: str, status: str,
    ):
        """Close a position and update all records."""
        # Acquire Redis lock
        lock_key = f"position:{position.id}:closing"
        if self.redis:
            acquired = self.redis.set(lock_key, "1", nx=True, ex=30)
            if not acquired:
                return  # Another process is closing this position

        try:
            now = datetime.now(timezone.utc)

            # Calculate realized P&L
            if position.side == "long":
                raw_pnl = (fill_price - position.entry_price) * position.quantity
            else:
                raw_pnl = (position.entry_price - fill_price) * position.quantity

            realized_pnl = raw_pnl - position.fees_entry - fee_usd

            # Update position
            position.status = status
            position.close_price = fill_price
            position.closed_at = now
            position.realized_pnl = realized_pnl
            position.fees_exit = fee_usd
            position.close_reason = reason
            session.add(position)

            # Update agent
            agent = session.get(Agent, position.agent_id)
            if agent:
                exit_value = fill_price * position.quantity
                agent.cash_balance += exit_value - fee_usd
                agent.realized_pnl += realized_pnl
                agent.total_fees_paid += fee_usd
                agent.position_count = max(0, agent.position_count - 1)
                agent.unrealized_pnl -= position.unrealized_pnl
                session.add(agent)

            # Accountant transaction
            exit_side = "sell" if position.side == "long" else "buy"
            txn = Transaction(
                agent_id=position.agent_id,
                type="spot",
                exchange="paper",
                symbol=position.symbol,
                side=exit_side,
                amount=position.quantity,
                price=fill_price,
                fee=fee_usd,
                pnl=realized_pnl,
            )
            session.add(txn)

            # Backfill cycle outcome if we have a source cycle
            if position.source_cycle_id:
                cycle = session.get(AgentCycle, position.source_cycle_id)
                if cycle:
                    cycle.outcome = f"{reason}: P&L=${realized_pnl:.4f}"
                    cycle.outcome_pnl = realized_pnl
                    session.add(cycle)

        finally:
            if self.redis:
                try:
                    self.redis.delete(lock_key)
                except Exception:
                    pass

    def _alert_stale_price(self, symbol: str):
        """Broadcast a stale price alert (deduplicated)."""
        if not self.redis:
            return
        alert_key = f"stale_alert:{symbol}"
        # Only alert once per 60s per symbol
        if not self.redis.set(alert_key, "1", nx=True, ex=60):
            return
        logger.warning(f"Stale price data for {symbol} — stop/TP monitoring paused")
