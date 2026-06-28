"""First-party tool rail — the reusable channel for the tools/knowledge catalogue.

A *first-party tool* is a pure, trusted, deterministic function that takes a
READ-ONLY snapshot of market data (`MarketView`) and returns a JSON-serializable
advisory result. Unlike sandboxed agent-written scripts, these are maintained
in-tree and free to run — but they are equally execution-incapable by construction:
a tool receives only data and returns only data. There is no path from here to the
trading service, the exchange, or the Warden.

This is the rail every future first-party tool plugs into (JJ is the first). The
agent-facing action layer (Step 2b) dispatches to `run_first_party_tool` and
persists the returned data into the agent's next prompt — agents *choose* to
consult; consulting is an evolvable behaviour, not an ambient feed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Tuple

ToolFn = Callable[["MarketView"], Dict[str, Any]]

_REGISTRY: Dict[str, ToolFn] = {}


@dataclass(frozen=True)
class MarketView:
    """Read-only snapshot handed to a first-party tool.

    A frozen value object containing ONLY data — no DB session, no service
    handles, no callables. Price series are tuples (immutable). This *is* the
    boundary: a tool can read these numbers and reach nothing else.
    """

    symbol: str
    regime: str
    high: Tuple[float, ...]
    low: Tuple[float, ...]
    close: Tuple[float, ...]
    volume: Tuple[float, ...]

    @classmethod
    def from_ohlcv(
        cls, candles: List[Dict[str, float]], symbol: str, regime: str = "neutral"
    ) -> "MarketView":
        """Build a view from a list of OHLCV dicts (e.g. from the read-only data API)."""
        return cls(
            symbol=symbol,
            regime=regime,
            high=tuple(float(c["high"]) for c in candles),
            low=tuple(float(c["low"]) for c in candles),
            close=tuple(float(c["close"]) for c in candles),
            volume=tuple(float(c.get("volume", 0.0)) for c in candles),
        )


def register(name: str) -> Callable[[ToolFn], ToolFn]:
    """Decorator: register a first-party tool under `name`."""

    def _decorate(fn: ToolFn) -> ToolFn:
        if name in _REGISTRY:
            raise ValueError(f"first-party tool already registered: {name!r}")
        _REGISTRY[name] = fn
        return fn

    return _decorate


def available_tools() -> List[str]:
    """Names of all registered first-party tools, sorted."""
    return sorted(_REGISTRY)


def run_first_party_tool(name: str, view: MarketView) -> Dict[str, Any]:
    """Dispatch to a registered tool. Returns its JSON-serializable result.

    Raises KeyError for an unknown tool. The tool receives only the read-only
    `view`; it has no handle to execution of any kind.
    """
    if name not in _REGISTRY:
        raise KeyError(f"unknown first-party tool: {name!r}")
    return _REGISTRY[name](view)


def load_builtin_tools() -> None:
    """Import built-in tool modules so their `@register` decorators run.

    Explicit (not import-time magic) to keep registration order predictable.
    """
    from src.signals.jj import tool as _jj_tool  # noqa: F401  (registers "jj_signals")
