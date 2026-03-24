"""
Project Syndicate — Exchange Data Service

Unified wrapper around ccxt for Kraken (primary) and Binance (secondary).
Also provides a PaperTradingService that simulates trades against real market
data without touching real money.
"""

__version__ = "0.2.0"

import asyncio
import time
import uuid
from datetime import datetime, timezone
from typing import Any

import ccxt.async_support as ccxt_async
import structlog

from src.common.config import config

logger = structlog.get_logger()


class ExchangeService:
    """Unified exchange interface wrapping ccxt for Kraken + Binance."""

    def __init__(self) -> None:
        self.log = logger.bind(component="exchange_service")

        # Primary: Kraken
        self.primary = ccxt_async.kraken({
            "apiKey": config.exchange_api_key,
            "secret": config.exchange_api_secret,
            "enableRateLimit": True,
        })

        # Secondary: Binance (optional, graceful if no keys)
        self.secondary = None
        if config.exchange_secondary_api_key:
            self.secondary = ccxt_async.binance({
                "apiKey": config.exchange_secondary_api_key,
                "secret": config.exchange_secondary_api_secret,
                "enableRateLimit": True,
            })
            self.log.info("exchanges_connected", primary="kraken", secondary="binance")
        else:
            self.log.info("exchanges_connected", primary="kraken", secondary="none")

    def _get_exchange(self, exchange: str) -> ccxt_async.Exchange:
        """Resolve exchange name to ccxt instance."""
        if exchange == "secondary" and self.secondary:
            return self.secondary
        return self.primary

    async def _call_with_retry(
        self,
        coro_factory,
        retries: int = 3,
        label: str = "exchange_call",
    ) -> Any:
        """Execute an async exchange call with exponential backoff retry."""
        for attempt in range(retries):
            start = time.monotonic()
            try:
                result = await coro_factory()
                elapsed = time.monotonic() - start
                self.log.debug(label, attempt=attempt + 1, elapsed_ms=round(elapsed * 1000))
                return result
            except (ccxt_async.NetworkError, ccxt_async.ExchangeNotAvailable) as exc:
                elapsed = time.monotonic() - start
                self.log.warning(
                    f"{label}_retry",
                    attempt=attempt + 1,
                    error=str(exc),
                    elapsed_ms=round(elapsed * 1000),
                )
                if attempt == retries - 1:
                    raise
                await asyncio.sleep(2 ** attempt)
            except ccxt_async.ExchangeError as exc:
                self.log.error(f"{label}_error", error=str(exc))
                raise

    async def get_ticker(self, symbol: str, exchange: str = "primary") -> dict:
        """Get current ticker data: price, bid, ask, volume, 24h change."""
        ex = self._get_exchange(exchange)
        try:
            ticker = await self._call_with_retry(
                lambda: ex.fetch_ticker(symbol),
                label="get_ticker",
            )
            return {
                "symbol": symbol,
                "last": ticker.get("last"),
                "bid": ticker.get("bid"),
                "ask": ticker.get("ask"),
                "volume": ticker.get("baseVolume"),
                "change_24h": ticker.get("percentage"),
                "timestamp": ticker.get("timestamp"),
            }
        except Exception:
            if exchange == "primary" and self.secondary:
                self.log.warning("ticker_fallback_to_secondary", symbol=symbol)
                return await self.get_ticker(symbol, exchange="secondary")
            raise

    async def get_ohlcv(
        self,
        symbol: str,
        timeframe: str = "1d",
        limit: int = 50,
        exchange: str = "primary",
    ) -> list:
        """Get OHLCV candlestick data for technical analysis."""
        ex = self._get_exchange(exchange)
        return await self._call_with_retry(
            lambda: ex.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit),
            label="get_ohlcv",
        )

    async def get_balance(self, exchange: str = "primary") -> dict:
        """Get account balance: total, free, used."""
        ex = self._get_exchange(exchange)
        balance = await self._call_with_retry(
            lambda: ex.fetch_balance(),
            label="get_balance",
        )
        return {
            "total": balance.get("total", {}),
            "free": balance.get("free", {}),
            "used": balance.get("used", {}),
        }

    async def place_order(
        self,
        symbol: str,
        side: str,
        amount: float,
        order_type: str = "limit",
        price: float | None = None,
        exchange: str = "primary",
    ) -> dict:
        """Place an order. THIS MUST GO THROUGH THE WARDEN FIRST."""
        ex = self._get_exchange(exchange)
        self.log.info(
            "placing_order",
            symbol=symbol, side=side, amount=amount,
            order_type=order_type, price=price, exchange=exchange,
        )
        order = await self._call_with_retry(
            lambda: ex.create_order(symbol, order_type, side, amount, price),
            label="place_order",
        )
        return {
            "order_id": order.get("id"),
            "status": order.get("status"),
            "filled": order.get("filled"),
            "remaining": order.get("remaining"),
            "price": order.get("price"),
            "average": order.get("average"),
            "fee": order.get("fee"),
            "symbol": symbol,
            "side": side,
            "amount": amount,
        }

    async def cancel_order(
        self,
        order_id: str,
        symbol: str,
        exchange: str = "primary",
    ) -> dict:
        """Cancel an open order."""
        ex = self._get_exchange(exchange)
        result = await self._call_with_retry(
            lambda: ex.cancel_order(order_id, symbol),
            label="cancel_order",
        )
        self.log.info("order_cancelled", order_id=order_id, symbol=symbol)
        return result

    async def get_open_orders(
        self,
        symbol: str | None = None,
        exchange: str = "primary",
    ) -> list:
        """Get all open orders, optionally filtered by symbol."""
        ex = self._get_exchange(exchange)
        return await self._call_with_retry(
            lambda: ex.fetch_open_orders(symbol),
            label="get_open_orders",
        )

    async def close_all_positions(self, exchange: str = "primary") -> list:
        """Emergency: close all open orders. Used by circuit breaker."""
        self.log.critical("EMERGENCY_CLOSE_ALL_POSITIONS", exchange=exchange)
        results = []
        try:
            orders = await self.get_open_orders(exchange=exchange)
            for order in orders:
                try:
                    r = await self.cancel_order(
                        order["id"], order["symbol"], exchange=exchange
                    )
                    results.append({"action": "cancelled", "order": order["id"], "result": r})
                except Exception as exc:
                    results.append({"action": "cancel_failed", "order": order["id"], "error": str(exc)})
        except Exception as exc:
            self.log.error("close_all_failed", error=str(exc))
            results.append({"action": "fetch_failed", "error": str(exc)})
        return results

    async def get_market_data_for_regime(self) -> dict:
        """Fetch all data needed for regime detection."""
        # BTC/USD ticker and OHLCV from primary
        ticker = await self.get_ticker("BTC/USD")
        ohlcv = await self.get_ohlcv("BTC/USD", timeframe="1d", limit=50)

        # Try to get BTC dominance and total market cap from Binance
        btc_dominance = 0.0
        total_market_cap = 0.0
        if self.secondary:
            try:
                btc_ticker = await self.get_ticker("BTC/USDT", exchange="secondary")
                # Binance doesn't directly provide dominance — would need CoinGecko/CMC API
                # Dominance is informational only (not used in regime classification)
                btc_dominance = 0.0
                total_market_cap = 0.0
            except Exception as exc:
                self.log.warning("secondary_market_data_failed", error=str(exc))

        return {
            "btc_price": ticker.get("last", 0.0),
            "btc_volume": ticker.get("volume", 0.0),
            "btc_change_24h": ticker.get("change_24h", 0.0),
            "ohlcv": ohlcv,
            "btc_dominance": btc_dominance,
            "total_market_cap": total_market_cap,
        }

    async def close(self) -> None:
        """Close exchange connections."""
        await self.primary.close()
        if self.secondary:
            await self.secondary.close()


# ---------------------------------------------------------------------------
# Paper Trading Service — same interface, simulated execution
# ---------------------------------------------------------------------------

class PaperTradingService:
    """Simulates trades against real market data without touching real money.

    Maintains an in-memory order book and balance tracker.
    Falls through to a real ExchangeService for market data only.
    """

    def __init__(self, initial_balance: dict[str, float] | None = None) -> None:
        self.log = logger.bind(component="paper_trading")
        self._balance: dict[str, float] = initial_balance or {"USD": 500.0}
        self._orders: dict[str, dict] = {}  # order_id -> order
        self._fills: list[dict] = []
        self._real_exchange: ExchangeService | None = None

    async def _get_real_exchange(self) -> ExchangeService:
        """Lazy-init a real exchange for market data only."""
        if self._real_exchange is None:
            self._real_exchange = ExchangeService()
        return self._real_exchange

    async def get_ticker(self, symbol: str, exchange: str = "primary") -> dict:
        """Get real market ticker data."""
        ex = await self._get_real_exchange()
        return await ex.get_ticker(symbol, exchange)

    async def get_ohlcv(
        self,
        symbol: str,
        timeframe: str = "1d",
        limit: int = 50,
        exchange: str = "primary",
    ) -> list:
        """Get real OHLCV data."""
        ex = await self._get_real_exchange()
        return await ex.get_ohlcv(symbol, timeframe, limit, exchange)

    async def get_balance(self, exchange: str = "primary") -> dict:
        """Return simulated balance."""
        return {
            "total": dict(self._balance),
            "free": dict(self._balance),
            "used": {},
        }

    async def place_order(
        self,
        symbol: str,
        side: str,
        amount: float,
        order_type: str = "limit",
        price: float | None = None,
        exchange: str = "primary",
    ) -> dict:
        """Simulate placing an order against real market price."""
        # Get current market price if not specified
        if price is None or order_type == "market":
            ticker = await self.get_ticker(symbol)
            price = ticker["last"]

        order_id = str(uuid.uuid4())[:12]
        base, quote = symbol.split("/")

        # Simulate fill
        fee_rate = 0.001  # 0.1% fee
        fee_amount = amount * price * fee_rate

        if side == "buy":
            cost = amount * price + fee_amount
            if self._balance.get(quote, 0) < cost:
                raise Exception(f"Insufficient {quote} balance: need {cost}, have {self._balance.get(quote, 0)}")
            self._balance[quote] = self._balance.get(quote, 0) - cost
            self._balance[base] = self._balance.get(base, 0) + amount
        else:  # sell
            if self._balance.get(base, 0) < amount:
                raise Exception(f"Insufficient {base} balance: need {amount}, have {self._balance.get(base, 0)}")
            self._balance[base] = self._balance.get(base, 0) - amount
            self._balance[quote] = self._balance.get(quote, 0) + (amount * price - fee_amount)

        order = {
            "order_id": order_id,
            "status": "closed",
            "filled": amount,
            "remaining": 0.0,
            "price": price,
            "average": price,
            "fee": {"cost": fee_amount, "currency": quote},
            "symbol": symbol,
            "side": side,
            "amount": amount,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        self._orders[order_id] = order
        self._fills.append(order)

        self.log.info(
            "paper_order_filled",
            order_id=order_id, symbol=symbol, side=side,
            amount=amount, price=price, fee=fee_amount,
        )
        return order

    async def cancel_order(
        self,
        order_id: str,
        symbol: str,
        exchange: str = "primary",
    ) -> dict:
        """Cancel a paper order (only works for unfilled orders)."""
        if order_id in self._orders:
            self._orders[order_id]["status"] = "canceled"
            return self._orders[order_id]
        return {"order_id": order_id, "status": "not_found"}

    async def get_open_orders(
        self,
        symbol: str | None = None,
        exchange: str = "primary",
    ) -> list:
        """Return open paper orders."""
        orders = [
            o for o in self._orders.values()
            if o["status"] == "open"
        ]
        if symbol:
            orders = [o for o in orders if o["symbol"] == symbol]
        return orders

    async def close_all_positions(self, exchange: str = "primary") -> list:
        """Close all paper positions."""
        results = []
        for oid, order in self._orders.items():
            if order["status"] == "open":
                order["status"] = "canceled"
                results.append({"action": "cancelled", "order": oid})
        return results

    async def get_market_data_for_regime(self) -> dict:
        """Delegate to real exchange for market data."""
        ex = await self._get_real_exchange()
        return await ex.get_market_data_for_regime()

    async def close(self) -> None:
        """Close underlying real exchange connection."""
        if self._real_exchange:
            await self._real_exchange.close()
