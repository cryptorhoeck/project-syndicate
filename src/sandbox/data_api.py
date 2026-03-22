"""
Project Syndicate — Sandbox Data API

Pre-fetched data access functions injected into the sandbox namespace.
These are the ONLY way scripts can access data — no network, no filesystem.
"""

__version__ = "0.1.0"

import json
import logging
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)


class SandboxDataAPI:
    """Holds pre-fetched data and provides accessor functions for the sandbox."""

    def __init__(self, agent_id: int, watchlist: list[str] | None = None):
        self.agent_id = agent_id
        self.watchlist = watchlist or []
        self._price_cache: dict[str, dict] = {}
        self._ticker_cache: dict[str, dict] = {}
        self._trades_cache: list[dict] = []
        self._positions_cache: list[dict] = []
        self._agora_cache: dict[str, list[dict]] = {}
        self._regime_cache: dict = {}
        self._output = None

    async def prefetch_all(self, db_session, exchange_service=None, redis_client=None):
        """Pre-fetch all data the sandbox might need."""
        from src.common.models import Message, Position

        # Trades (closed positions)
        try:
            positions = list(
                db_session.execute(
                    __import__("sqlalchemy", fromlist=["select"]).select(Position)
                    .where(Position.agent_id == self.agent_id, Position.status != "open")
                    .order_by(Position.closed_at.desc())
                    .limit(50)
                ).scalars().all()
            )
            self._trades_cache = [
                {
                    "symbol": p.symbol, "side": p.side,
                    "entry_price": p.entry_price, "exit_price": p.exit_price,
                    "pnl": p.realized_pnl or 0.0, "size_usd": p.size_usd,
                    "status": p.status,
                }
                for p in positions
            ]
        except Exception:
            self._trades_cache = []

        # Open positions
        try:
            open_pos = list(
                db_session.execute(
                    __import__("sqlalchemy", fromlist=["select"]).select(Position)
                    .where(Position.agent_id == self.agent_id, Position.status == "open")
                ).scalars().all()
            )
            self._positions_cache = [
                {
                    "symbol": p.symbol, "side": p.side,
                    "entry_price": p.entry_price,
                    "unrealized_pnl": p.unrealized_pnl or 0.0,
                    "size_usd": p.size_usd,
                }
                for p in open_pos
            ]
        except Exception:
            self._positions_cache = []

        # Agora messages
        try:
            from sqlalchemy import select, desc
            for channel in ["market-intel", "strategy-proposals", "trade-results"]:
                msgs = list(
                    db_session.execute(
                        select(Message)
                        .where(Message.channel == channel)
                        .order_by(desc(Message.timestamp))
                        .limit(30)
                    ).scalars().all()
                )
                self._agora_cache[channel] = [
                    {
                        "agent_name": m.agent_name or "System",
                        "content": m.content[:300] if m.content else "",
                        "timestamp": str(m.timestamp) if m.timestamp else "",
                        "message_type": m.message_type or "system",
                    }
                    for m in msgs
                ]
        except Exception:
            pass

        # Market regime
        try:
            from src.common.models import SystemState
            state = db_session.execute(
                __import__("sqlalchemy", fromlist=["select"]).select(SystemState).limit(1)
            ).scalar_one_or_none()
            if state:
                self._regime_cache = {
                    "regime": state.current_regime or "unknown",
                    "alert_level": state.alert_status or "green",
                }
        except Exception:
            self._regime_cache = {"regime": "unknown"}

    def get_injected_functions(self) -> dict:
        """Return dict of functions to inject into sandbox globals."""
        return {
            "get_price_history": self._get_price_history,
            "get_current_price": self._get_current_price,
            "get_my_trades": self._get_my_trades,
            "get_my_positions": self._get_my_positions,
            "get_agora_messages": self._get_agora_messages,
            "get_market_regime": self._get_market_regime,
            "output": self._capture_output,
        }

    def _get_price_history(self, symbol: str = "BTC/USDT", timeframe: str = "1h", limit: int = 100) -> list:
        """Returns OHLCV data as list of dicts."""
        key = f"{symbol}:{timeframe}"
        data = self._price_cache.get(key, [])
        return data[:min(limit, 500)]

    def _get_current_price(self, symbol: str = "BTC/USDT") -> dict:
        """Returns ticker dict."""
        return self._ticker_cache.get(symbol, {})

    def _get_my_trades(self, limit: int = 50) -> list:
        """Returns closed trade history."""
        return self._trades_cache[:min(limit, 50)]

    def _get_my_positions(self) -> list:
        """Returns current open positions."""
        return self._positions_cache

    def _get_agora_messages(self, channel: str = "market-intel", limit: int = 50) -> list:
        """Returns recent messages from a channel."""
        return self._agora_cache.get(channel, [])[:min(limit, 50)]

    def _get_market_regime(self) -> dict:
        """Returns current market regime."""
        return self._regime_cache

    def _capture_output(self, data):
        """Capture script output. Must be JSON-serializable. Max 10KB."""
        serialized = json.dumps(data)
        if len(serialized) > 10240:
            raise ValueError(f"Output too large: {len(serialized)} bytes (max 10240)")
        self._output = data

    def get_captured_output(self):
        """Return whatever the script passed to output()."""
        return self._output

    def to_serializable(self) -> dict:
        """Serialize all cached data for subprocess injection."""
        return {
            "trades": self._trades_cache,
            "positions": self._positions_cache,
            "agora": self._agora_cache,
            "regime": self._regime_cache,
            "prices": self._price_cache,
            "tickers": self._ticker_cache,
        }
