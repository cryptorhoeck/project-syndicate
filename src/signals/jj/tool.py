"""JJ's first-party tool: bundles the four JJ analyses into one advisory result.

Registered on the shared rail (`src/signals/registry.py`) as "jj_signals". This is
Syndicate's first first-party deterministic tool and the template the catalogue
inherits: a pure function of a read-only `MarketView` returning JSON data only.
"""

from __future__ import annotations

from typing import Any, Dict

from src.signals.jj.indicators import momentum_signal, rsi_signal, volume_breakout_signal
from src.signals.jj.signal_types import TechnicalSignal
from src.signals.jj.vwap import vwap_signal
from src.signals.registry import MarketView, register


def _signal_to_dict(sig: TechnicalSignal) -> Dict[str, Any]:
    return {
        "source": sig.source,
        "direction": sig.direction.value,  # plain str, JSON-clean
        "confidence": sig.confidence,
        "reason": sig.reason,
        "details": sig.details,
    }


@register("jj_signals")
def jj_signals(view: MarketView) -> Dict[str, Any]:
    """Run JJ's VWAP / RSI / momentum / volume analyses on a read-only market view.

    Advisory only: returns observations for the agent to weigh. Places no trades.
    """
    high, low, close, volume = view.high, view.low, view.close, view.volume
    signals = [
        vwap_signal(high, low, close, volume, regime=view.regime),
        rsi_signal(close),
        momentum_signal(close),
        volume_breakout_signal(close, volume),
    ]
    return {
        "tool": "jj_signals",
        "market": view.symbol,
        "regime": view.regime,
        "signals": [_signal_to_dict(s) for s in signals],
    }
