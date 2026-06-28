"""VWAP-deviation signal engine.

Ported faithfully from jj-bot's ``modules/rl/vwap_calculator.py`` — the genuinely
sound part of jj-bot ("the jewel"). The math is preserved exactly: typical price,
rolling VWAP, deviation bands, mean-reversion and trend-following signal logic, and
regime bias. Refactored into pure, side-effect-free functions returning an advisory
``TechnicalSignal``.

Faithful-port notes (kept identical to the original on purpose):
- Typical price = (high + low + close) / 3.
- Rolling VWAP over a trailing window (default 20).
- Mean reversion: long when price is > threshold below VWAP, short when above.
- Trend following: long on an upward VWAP crossover, short on a downward one.
- Regime bias: in a bull regime shorts are suppressed (and longs boosted); in a
  bear regime longs are suppressed (and shorts boosted).
"""

from __future__ import annotations

import logging
from typing import Tuple

import numpy as np

from src.signals.jj.signal_types import Direction, TechnicalSignal

logger = logging.getLogger(__name__)

DEFAULT_WINDOW = 20
DEFAULT_STD_MULTIPLIER = 2.0
DEFAULT_DEVIATION_THRESHOLD = 0.02  # 2%

MEAN_REVERSION = "mean_reversion"
TREND_FOLLOWING = "trend_following"


def _arr(x) -> np.ndarray:
    return np.asarray(x, dtype=float)


def _rolling_sum(arr: np.ndarray, window: int) -> np.ndarray:
    """Trailing rolling sum (matches the original's semantics)."""
    arr = _arr(arr)
    out = np.zeros_like(arr)
    for i in range(len(arr)):
        start = max(0, i - window + 1)
        out[i] = np.sum(arr[start:i + 1])
    return out


def _rolling_std(arr: np.ndarray, window: int) -> np.ndarray:
    """Trailing rolling std; 0 until at least 2 points are available."""
    arr = _arr(arr)
    out = np.zeros_like(arr)
    for i in range(len(arr)):
        start = max(0, i - window + 1)
        out[i] = np.std(arr[start:i + 1]) if (i - start) >= 1 else 0.0
    return out


def rolling_vwap(
    high,
    low,
    close,
    volume,
    window: int = DEFAULT_WINDOW,
    std_multiplier: float = DEFAULT_STD_MULTIPLIER,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Rolling VWAP with deviation bands. Returns (vwap, upper_band, lower_band)."""
    high, low, close, volume = _arr(high), _arr(low), _arr(close), _arr(volume)
    typical_price = (high + low + close) / 3.0
    rolling_tp_vol = _rolling_sum(typical_price * volume, window)
    rolling_vol = _rolling_sum(volume, window)
    rolling_vol = np.where(rolling_vol == 0, 1, rolling_vol)  # avoid div-by-zero
    vwap = rolling_tp_vol / rolling_vol
    deviation = close - vwap
    rstd = _rolling_std(deviation, window)
    upper_band = vwap + std_multiplier * rstd
    lower_band = vwap - std_multiplier * rstd
    return vwap, upper_band, lower_band


def _current_deviation(high, low, close, volume, window: int) -> Tuple[float, float, float]:
    """Return (current_price, current_vwap, deviation_pct as decimal)."""
    vwap, _, _ = rolling_vwap(high, low, close, volume, window)
    current_price = float(_arr(close)[-1])
    current_vwap = float(vwap[-1])
    if current_vwap == 0:
        return current_price, current_vwap, 0.0
    return current_price, current_vwap, (current_price - current_vwap) / current_vwap


def strategy_for_regime(regime: str) -> str:
    """Bull regimes favour trend following; everything else mean reversion."""
    return TREND_FOLLOWING if regime.lower() == "bull" else MEAN_REVERSION


def _apply_regime_bias(
    direction: Direction, confidence: float, regime: str
) -> Tuple[Direction, float]:
    """Bias signals by regime (faithful to the original logic)."""
    regime = regime.lower()
    if regime == "bull":
        if direction == Direction.SHORT:
            return Direction.FLAT, confidence * 0.5
        if direction == Direction.LONG:
            return Direction.LONG, min(1.0, confidence * 1.2)
    elif regime == "bear":
        if direction == Direction.LONG:
            return Direction.FLAT, confidence * 0.5
        if direction == Direction.SHORT:
            return Direction.SHORT, min(1.0, confidence * 1.2)
    return direction, confidence


def vwap_deviation_signal(
    high,
    low,
    close,
    volume,
    strategy: str = MEAN_REVERSION,
    regime: str = "neutral",
    deviation_threshold: float = DEFAULT_DEVIATION_THRESHOLD,
    window: int = DEFAULT_WINDOW,
) -> TechnicalSignal:
    """Generate a VWAP-deviation advisory signal. Pure; no side effects."""
    close_arr = _arr(close)
    if len(close_arr) < 2:
        return TechnicalSignal(
            "vwap_deviation", Direction.FLAT, 0.0, "insufficient_data",
            {"strategy": strategy, "regime": regime},
        )

    price, vwap_val, dev = _current_deviation(high, low, close, volume, window)
    direction, confidence, reason = Direction.FLAT, 0.0, "within deviation band"

    if strategy == MEAN_REVERSION:
        if dev < -deviation_threshold:
            direction = Direction.LONG
            confidence = min(1.0, abs(dev) / (deviation_threshold * 2))
            reason = "price below VWAP (mean-reversion long)"
        elif dev > deviation_threshold:
            direction = Direction.SHORT
            confidence = min(1.0, abs(dev) / (deviation_threshold * 2))
            reason = "price above VWAP (mean-reversion short)"
        elif abs(dev) < deviation_threshold * 0.5:
            direction, confidence, reason = Direction.FLAT, 0.5, "price near VWAP"

    elif strategy == TREND_FOLLOWING:
        high_a, low_a, vol_a = _arr(high), _arr(low), _arr(volume)
        prev_close = close_arr[-2]
        prev_vwap_arr, _, _ = rolling_vwap(
            high_a[:-1], low_a[:-1], close_arr[:-1], vol_a[:-1], window
        )
        prev_vwap = float(prev_vwap_arr[-1])
        currently_above = close_arr[-1] > vwap_val
        previously_above = prev_close > prev_vwap
        if currently_above and not previously_above:
            direction, confidence, reason = Direction.LONG, 0.7, "breakout above VWAP"
        elif (not currently_above) and previously_above:
            direction, confidence, reason = Direction.SHORT, 0.7, "breakdown below VWAP"
        elif currently_above:
            direction, confidence, reason = Direction.FLAT, 0.5, "holding above VWAP (bullish)"
        else:
            direction, confidence, reason = Direction.FLAT, 0.3, "below VWAP (bearish)"
    else:
        raise ValueError(f"unknown strategy: {strategy!r}")

    direction, confidence = _apply_regime_bias(direction, confidence, regime)
    return TechnicalSignal(
        source="vwap_deviation",
        direction=direction,
        confidence=round(confidence, 4),
        reason=reason,
        details={
            "strategy": strategy,
            "regime": regime,
            "vwap": round(vwap_val, 8),
            "price": round(price, 8),
            "deviation_pct": round(dev * 100, 6),
        },
    )


def vwap_signal(high, low, close, volume, regime: str = "neutral", **kwargs) -> TechnicalSignal:
    """Regime-adaptive convenience wrapper: picks the strategy, then signals."""
    return vwap_deviation_signal(
        high, low, close, volume,
        strategy=strategy_for_regime(regime), regime=regime, **kwargs,
    )
