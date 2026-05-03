"""
Project Syndicate — Trade Execution Service

Abstract interface for trade execution with paper trading implementation.
Switch between paper and live via TRADING_MODE config.
"""

__version__ = "0.9.0"

import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from src.common.config import config
from src.common.models import Agent, AgentCycle, Order, Position, Transaction

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------

@dataclass
class OrderResult:
    """Result of an order execution."""
    success: bool
    order_id: int | None = None
    position_id: int | None = None
    fill_price: float | None = None
    fill_quantity: float | None = None
    fill_value_usd: float | None = None
    fee_usd: float | None = None
    slippage_pct: float | None = None
    error: str | None = None


@dataclass
class CancelResult:
    """Result of an order cancellation."""
    success: bool
    order_id: int | None = None
    released_amount: float = 0.0
    error: str | None = None


@dataclass
class CloseResult:
    """Result of closing a position."""
    success: bool
    position_id: int | None = None
    realized_pnl: float | None = None
    close_price: float | None = None
    fee_usd: float | None = None
    error: str | None = None


@dataclass
class Balance:
    """Agent balance information."""
    cash_balance: float = 0.0
    reserved_cash: float = 0.0
    available_cash: float = 0.0
    total_equity: float = 0.0
    unrealized_pnl: float = 0.0
    position_count: int = 0


# ---------------------------------------------------------------------------
# Abstract Interface
# ---------------------------------------------------------------------------

class TradeExecutionService(ABC):
    """Abstract interface for trade execution (paper or live)."""

    @abstractmethod
    async def execute_market_order(
        self, agent_id: int, symbol: str, side: str, size_usd: float,
        source_plan_id: int | None = None, source_cycle_id: int | None = None,
        stop_loss: float | None = None, take_profit: float | None = None,
    ) -> OrderResult:
        """Execute a market order."""
        ...

    @abstractmethod
    async def execute_limit_order(
        self, agent_id: int, symbol: str, side: str, size_usd: float, price: float,
        source_plan_id: int | None = None, source_cycle_id: int | None = None,
        stop_loss: float | None = None, take_profit: float | None = None,
    ) -> OrderResult:
        """Execute a limit order."""
        ...

    @abstractmethod
    async def cancel_order(self, order_id: int) -> CancelResult:
        """Cancel a pending order."""
        ...

    @abstractmethod
    async def close_position(
        self, position_id: int, reason: str = "manual",
    ) -> CloseResult:
        """Close an open position."""
        ...

    @abstractmethod
    async def get_open_orders(self, agent_id: int) -> list[dict]:
        """Get all open orders for an agent."""
        ...

    @abstractmethod
    async def get_positions(self, agent_id: int) -> list[dict]:
        """Get all open positions for an agent."""
        ...

    @abstractmethod
    async def get_balance(self, agent_id: int) -> Balance:
        """Get agent balance information."""
        ...


# ---------------------------------------------------------------------------
# Paper Trading Implementation
# ---------------------------------------------------------------------------

class PaperTradingService(TradeExecutionService):
    """Paper trading engine with realistic simulation.

    Uses real market data, slippage modeling, and exchange fee schedules
    but doesn't touch real money.
    """

    def __init__(
        self,
        db_session_factory: sessionmaker,
        price_cache=None,
        slippage_model=None,
        fee_schedule=None,
        warden=None,
        redis_client=None,
        agora_service=None,
    ):
        self.db_factory = db_session_factory
        self.price_cache = price_cache
        self.slippage = slippage_model
        self.fees = fee_schedule
        self.warden = warden
        self.redis = redis_client
        self.agora = agora_service
        self.exchange = config.default_exchange

    async def execute_market_order(
        self, agent_id: int, symbol: str, side: str, size_usd: float,
        source_plan_id: int | None = None, source_cycle_id: int | None = None,
        stop_loss: float | None = None, take_profit: float | None = None,
    ) -> OrderResult:
        """Execute a market order with slippage and fees."""
        start_ms = time.monotonic()

        with self.db_factory() as session:
            agent = session.get(Agent, agent_id)
            if not agent:
                return OrderResult(success=False, error=f"Agent {agent_id} not found")

            # 1. Check Warden — production wiring guarantees self.warden is
            # not None (build_trading_service refuses to construct without
            # one). The branch below is defense-in-depth: if we ever land
            # here with self.warden is None the colony's mechanical safety
            # gate is missing and the trade MUST be rejected, not soft-passed.
            if self.warden is None:
                await self._raise_warden_missing_alert(
                    agent_id=agent_id, symbol=symbol, side=side,
                    size_usd=size_usd, order_type="market",
                )
                return self._create_rejected_order(
                    session, agent, symbol, side, size_usd, "market",
                    "Warden missing — trade rejected (defense in depth)",
                    source_plan_id, source_cycle_id,
                )
            warden_result = await self.warden.evaluate_trade({
                "agent_id": agent_id,
                "amount": size_usd,
                "price": 1.0,
                "symbol": symbol,
                "side": side,
            })
            if warden_result.get("status") != "approved":
                return self._create_rejected_order(
                    session, agent, symbol, side, size_usd, "market",
                    warden_result.get("reason", "Warden rejected"),
                    source_plan_id, source_cycle_id,
                    warden_result.get("request_id"),
                )

            # 2. Check buying power
            available = agent.cash_balance - agent.reserved_cash
            if size_usd > available:
                return self._create_rejected_order(
                    session, agent, symbol, side, size_usd, "market",
                    f"Insufficient buying power: need ${size_usd:.2f}, have ${available:.2f}",
                    source_plan_id, source_cycle_id,
                )

            # 3. Fetch price
            ticker = None
            if self.price_cache:
                ticker, is_fresh = await self.price_cache.get_ticker(symbol)
            if not ticker:
                return OrderResult(success=False, error=f"No price data for {symbol}")

            market_bid = ticker.get("bid", 0)
            market_ask = ticker.get("ask", 0)
            market_price = market_ask if side == "buy" else market_bid

            if market_price <= 0:
                return OrderResult(success=False, error=f"Invalid market price for {symbol}")

            # 4. Calculate slippage
            slippage_pct = 0.0
            if self.slippage:
                slippage_pct = await self.slippage.calculate_slippage(
                    size_usd, symbol, side, self.price_cache
                )

            # 5. Calculate fill price
            if side == "buy":
                fill_price = market_price * (1 + slippage_pct)
            else:
                fill_price = market_price * (1 - slippage_pct)

            # 6. Calculate fee (taker for market orders)
            fee_usd, fee_rate = 0.0, 0.0
            if self.fees:
                fee_usd, fee_rate = self.fees.calculate_fee(size_usd, "market", self.exchange)

            # 7. Calculate quantity
            quantity = size_usd / fill_price

            # 8. Calculate total cost
            total_cost = size_usd + fee_usd

            # 9. Final buying power check with fee
            if total_cost > available:
                return self._create_rejected_order(
                    session, agent, symbol, side, size_usd, "market",
                    f"Insufficient after fees: need ${total_cost:.2f}, have ${available:.2f}",
                    source_plan_id, source_cycle_id,
                )

            # 10. Determine position side
            position_side = "long" if side == "buy" else "short"

            # 11. Create position
            now = datetime.now(timezone.utc)
            position = Position(
                agent_id=agent_id,
                agent_name=agent.name,
                symbol=symbol,
                side=position_side,
                entry_price=fill_price,
                current_price=fill_price,
                quantity=quantity,
                size_usd=size_usd,
                stop_loss=stop_loss,
                take_profit=take_profit,
                fees_entry=fee_usd,
                source_plan_id=source_plan_id,
                source_cycle_id=source_cycle_id,
                opened_at=now,
                status="open",
                execution_venue="paper",
            )
            session.add(position)
            session.flush()

            # 12. Create order record
            processing_ms = int((time.monotonic() - start_ms) * 1000)
            spread_pct = ((market_ask - market_bid) / market_bid * 100) if market_bid > 0 else 0

            order = Order(
                agent_id=agent_id,
                agent_name=agent.name,
                order_type="market",
                symbol=symbol,
                side=side,
                requested_size_usd=size_usd,
                fill_price=fill_price,
                fill_quantity=quantity,
                fill_value_usd=size_usd,
                slippage_pct=slippage_pct,
                fee_usd=fee_usd,
                fee_rate=fee_rate,
                market_bid=market_bid,
                market_ask=market_ask,
                market_spread_pct=spread_pct,
                market_volume_24h=ticker.get("baseVolume"),
                requested_at=now,
                filled_at=now,
                processing_time_ms=processing_ms,
                status="filled",
                source_plan_id=source_plan_id,
                source_cycle_id=source_cycle_id,
                position_id=position.id,
                execution_venue="paper",
            )
            session.add(order)

            # 13. Update agent balances
            agent.cash_balance -= total_cost
            agent.total_fees_paid += fee_usd
            agent.position_count += 1
            agent.total_equity = agent.cash_balance + size_usd  # approximate
            session.add(agent)

            # 14. Write transaction for Accountant bridge
            txn = Transaction(
                agent_id=agent_id,
                type="spot",
                exchange="paper",
                symbol=symbol,
                side=side,
                amount=quantity,
                price=fill_price,
                fee=fee_usd,
                pnl=0.0,
            )
            session.add(txn)

            session.commit()

            logger.info(
                f"Market {side} filled: {symbol} qty={quantity:.6f} @ ${fill_price:.4f} "
                f"(slippage={slippage_pct:.4%}, fee=${fee_usd:.4f})",
                extra={"agent_id": agent_id, "order_id": order.id},
            )

            return OrderResult(
                success=True,
                order_id=order.id,
                position_id=position.id,
                fill_price=fill_price,
                fill_quantity=quantity,
                fill_value_usd=size_usd,
                fee_usd=fee_usd,
                slippage_pct=slippage_pct,
            )

    async def execute_limit_order(
        self, agent_id: int, symbol: str, side: str, size_usd: float, price: float,
        source_plan_id: int | None = None, source_cycle_id: int | None = None,
        stop_loss: float | None = None, take_profit: float | None = None,
    ) -> OrderResult:
        """Place a limit order with cash reservation."""
        with self.db_factory() as session:
            agent = session.get(Agent, agent_id)
            if not agent:
                return OrderResult(success=False, error=f"Agent {agent_id} not found")

            # Check Warden — see execute_market_order for the defense-in-depth
            # rationale. Limit-order initiation is also a trade-initiation
            # point per WIRING_AUDIT_REPORT.md subsystem N.
            if self.warden is None:
                await self._raise_warden_missing_alert(
                    agent_id=agent_id, symbol=symbol, side=side,
                    size_usd=size_usd, order_type="limit",
                )
                return self._create_rejected_order(
                    session, agent, symbol, side, size_usd, "limit",
                    "Warden missing — trade rejected (defense in depth)",
                    source_plan_id, source_cycle_id,
                )
            warden_result = await self.warden.evaluate_trade({
                "agent_id": agent_id,
                "amount": size_usd,
                "price": price,
                "symbol": symbol,
                "side": side,
            })
            if warden_result.get("status") != "approved":
                return self._create_rejected_order(
                    session, agent, symbol, side, size_usd, "limit",
                    warden_result.get("reason", "Warden rejected"),
                    source_plan_id, source_cycle_id,
                    warden_result.get("request_id"),
                )

            # Calculate reservation: size + estimated fee
            est_fee, _ = (0.0, 0.0)
            if self.fees:
                est_fee, _ = self.fees.calculate_fee(size_usd, "limit", self.exchange)
            reservation = size_usd + est_fee

            # Check buying power
            available = agent.cash_balance - agent.reserved_cash
            if reservation > available:
                return self._create_rejected_order(
                    session, agent, symbol, side, size_usd, "limit",
                    f"Insufficient buying power: need ${reservation:.2f}, have ${available:.2f}",
                    source_plan_id, source_cycle_id,
                )

            # Reserve cash
            agent.reserved_cash += reservation
            session.add(agent)

            # Create order
            now = datetime.now(timezone.utc)
            order = Order(
                agent_id=agent_id,
                agent_name=agent.name,
                order_type="limit",
                symbol=symbol,
                side=side,
                requested_size_usd=size_usd,
                requested_price=price,
                reserved_amount=reservation,
                requested_at=now,
                status="pending",
                source_plan_id=source_plan_id,
                source_cycle_id=source_cycle_id,
                execution_venue="paper",
            )
            session.add(order)
            session.commit()

            logger.info(
                f"Limit {side} placed: {symbol} ${size_usd:.2f} @ ${price:.4f} "
                f"(reserved ${reservation:.4f})",
                extra={"agent_id": agent_id, "order_id": order.id},
            )

            return OrderResult(
                success=True,
                order_id=order.id,
            )

    async def cancel_order(self, order_id: int) -> CancelResult:
        """Cancel a pending order and release reservation."""
        with self.db_factory() as session:
            order = session.get(Order, order_id)
            if not order:
                return CancelResult(success=False, error=f"Order {order_id} not found")
            if order.status != "pending":
                return CancelResult(success=False, error=f"Order {order_id} is {order.status}, not pending")

            order.status = "cancelled"
            released = order.reserved_amount or 0.0

            if released > 0 and not order.reservation_released:
                agent = session.get(Agent, order.agent_id)
                if agent:
                    agent.reserved_cash = max(0, agent.reserved_cash - released)
                    session.add(agent)
                order.reservation_released = True

            session.add(order)
            session.commit()

            return CancelResult(success=True, order_id=order_id, released_amount=released)

    async def close_position(
        self, position_id: int, reason: str = "manual",
    ) -> CloseResult:
        """Close an open position with Redis lock to prevent double-close."""
        # Acquire lock
        lock_key = f"position:{position_id}:closing"
        if self.redis:
            acquired = self.redis.set(lock_key, "1", nx=True, ex=30)
            if not acquired:
                return CloseResult(success=False, error="Position is already being closed")

        try:
            return await self._do_close_position(position_id, reason)
        finally:
            if self.redis:
                try:
                    self.redis.delete(lock_key)
                except Exception:
                    pass

    async def _do_close_position(
        self, position_id: int, reason: str,
    ) -> CloseResult:
        """Internal position close logic."""
        with self.db_factory() as session:
            position = session.get(Position, position_id)
            if not position:
                return CloseResult(success=False, error=f"Position {position_id} not found")
            if position.status != "open":
                return CloseResult(success=False, error=f"Position {position_id} is {position.status}")

            agent = session.get(Agent, position.agent_id)
            if not agent:
                return CloseResult(success=False, error=f"Agent {position.agent_id} not found")

            # Get current price
            close_price = position.current_price
            if self.price_cache:
                ticker, _ = await self.price_cache.get_ticker(position.symbol)
                if ticker:
                    if position.side == "long":
                        close_price = ticker.get("bid", position.current_price)
                    else:
                        close_price = ticker.get("ask", position.current_price)

            # Calculate slippage on exit
            exit_slippage = 0.0
            exit_side = "sell" if position.side == "long" else "buy"
            if self.slippage:
                exit_slippage = await self.slippage.calculate_slippage(
                    position.size_usd, position.symbol, exit_side, self.price_cache
                )

            if position.side == "long":
                fill_price = close_price * (1 - exit_slippage)
            else:
                fill_price = close_price * (1 + exit_slippage)

            # Calculate exit fee
            exit_value = fill_price * position.quantity
            fee_usd, fee_rate = 0.0, 0.0
            if self.fees:
                fee_usd, fee_rate = self.fees.calculate_fee(exit_value, "market", self.exchange)

            # Calculate realized P&L
            if position.side == "long":
                raw_pnl = (fill_price - position.entry_price) * position.quantity
            else:
                raw_pnl = (position.entry_price - fill_price) * position.quantity

            realized_pnl = raw_pnl - position.fees_entry - fee_usd

            # Update position
            now = datetime.now(timezone.utc)
            position.status = reason if reason in ("stopped_out", "take_profit_hit") else "closed"
            position.close_price = fill_price
            position.closed_at = now
            position.realized_pnl = realized_pnl
            position.fees_exit = fee_usd
            position.close_reason = reason
            session.add(position)

            # Update agent
            agent.cash_balance += exit_value - fee_usd
            agent.realized_pnl += realized_pnl
            agent.total_fees_paid += fee_usd
            agent.position_count = max(0, agent.position_count - 1)
            agent.unrealized_pnl -= position.unrealized_pnl
            session.add(agent)

            # Write transaction for Accountant
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

            session.commit()

            logger.info(
                f"Position #{position_id} closed: {position.symbol} {position.side} "
                f"P&L=${realized_pnl:.4f} (reason={reason})",
                extra={"agent_id": position.agent_id},
            )

            return CloseResult(
                success=True,
                position_id=position_id,
                realized_pnl=realized_pnl,
                close_price=fill_price,
                fee_usd=fee_usd,
            )

    async def get_open_orders(self, agent_id: int) -> list[dict]:
        """Get all pending orders for an agent."""
        with self.db_factory() as session:
            orders = session.execute(
                select(Order).where(Order.agent_id == agent_id, Order.status == "pending")
            ).scalars().all()
            return [
                {
                    "id": o.id, "symbol": o.symbol, "side": o.side,
                    "type": o.order_type, "size_usd": o.requested_size_usd,
                    "price": o.requested_price, "status": o.status,
                }
                for o in orders
            ]

    async def get_positions(self, agent_id: int) -> list[dict]:
        """Get all open positions for an agent."""
        with self.db_factory() as session:
            positions = session.execute(
                select(Position).where(Position.agent_id == agent_id, Position.status == "open")
            ).scalars().all()
            return [
                {
                    "id": p.id, "symbol": p.symbol, "side": p.side,
                    "entry_price": p.entry_price, "current_price": p.current_price,
                    "quantity": p.quantity, "size_usd": p.size_usd,
                    "unrealized_pnl": p.unrealized_pnl, "stop_loss": p.stop_loss,
                    "take_profit": p.take_profit, "status": p.status,
                }
                for p in positions
            ]

    async def get_balance(self, agent_id: int) -> Balance:
        """Get agent balance information."""
        with self.db_factory() as session:
            agent = session.get(Agent, agent_id)
            if not agent:
                return Balance()
            return Balance(
                cash_balance=agent.cash_balance,
                reserved_cash=agent.reserved_cash,
                available_cash=agent.cash_balance - agent.reserved_cash,
                total_equity=agent.total_equity,
                unrealized_pnl=agent.unrealized_pnl,
                position_count=agent.position_count,
            )

    async def _raise_warden_missing_alert(
        self, *, agent_id: int, symbol: str, side: str,
        size_usd: float, order_type: str,
    ) -> None:
        """Loud alert path for the defense-in-depth `self.warden is None`
        branch in `execute_market_order` / `execute_limit_order`. Production
        wiring guarantees a Warden via `build_trading_service` and
        `run_agents.py:build_warden`. If we still hit this branch, the
        colony's mechanical safety gate is missing and the operator MUST
        be alerted, not silently soft-passed.
        """
        import logging
        log = logging.getLogger(__name__)
        log.critical(
            "trade_warden_missing",
            extra={
                "agent_id": agent_id, "symbol": symbol, "side": side,
                "size_usd": size_usd, "order_type": order_type,
            },
        )
        if self.agora is None:
            return
        try:
            from src.agora.schemas import AgoraMessage, MessageType
            await self.agora.post_message(AgoraMessage(
                agent_id=int(agent_id),
                agent_name="PaperTradingService",
                channel="system-alerts",
                content=(
                    f"[WARDEN MISSING] Trade rejected — Warden was None at "
                    f"trade-initiation point. agent_id={agent_id} symbol={symbol} "
                    f"{side} {order_type} ${size_usd:.2f}. This indicates a "
                    f"runtime wiring break; safety gate is absent."
                ),
                message_type=MessageType.ALERT,
                importance=2,
                metadata={
                    "event_class": "warden.missing_at_trade_gate",
                    "agent_id": agent_id, "symbol": symbol, "side": side,
                    "size_usd": size_usd, "order_type": order_type,
                },
            ))
        except Exception:
            log.exception("warden_missing_agora_post_failed")

    def _create_rejected_order(
        self, session: Session, agent: Agent, symbol: str, side: str,
        size_usd: float, order_type: str, reason: str,
        source_plan_id: int | None = None, source_cycle_id: int | None = None,
        warden_request_id: str | None = None,
    ) -> OrderResult:
        """Create a rejected order record."""
        order = Order(
            agent_id=agent.id,
            agent_name=agent.name,
            order_type=order_type,
            symbol=symbol,
            side=side,
            requested_size_usd=size_usd,
            status="rejected",
            rejection_reason=reason,
            warden_request_id=warden_request_id,
            source_plan_id=source_plan_id,
            source_cycle_id=source_cycle_id,
            execution_venue="paper",
        )
        session.add(order)
        session.commit()

        logger.warning(f"Order rejected: {symbol} {side} ${size_usd:.2f} — {reason}",
                       extra={"agent_id": agent.id})

        return OrderResult(success=False, order_id=order.id, error=reason)


# ---------------------------------------------------------------------------
# Factory Function
# ---------------------------------------------------------------------------

def get_trading_service(
    db_session_factory: sessionmaker,
    price_cache=None,
    slippage_model=None,
    fee_schedule=None,
    warden=None,
    redis_client=None,
    agora_service=None,
) -> TradeExecutionService:
    """Factory function returning the appropriate trading service.

    Returns PaperTradingService when TRADING_MODE=paper (default),
    or raises NotImplementedError for live mode (Phase 4+).
    """
    if config.trading_mode == "paper":
        return PaperTradingService(
            db_session_factory=db_session_factory,
            price_cache=price_cache,
            slippage_model=slippage_model,
            fee_schedule=fee_schedule,
            warden=warden,
            redis_client=redis_client,
            agora_service=agora_service,
        )
    else:
        raise NotImplementedError(
            f"Trading mode '{config.trading_mode}' not implemented. "
            "Only 'paper' is available in Phase 3C."
        )
