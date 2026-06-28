"""RSI, momentum, and volume-breakout — REBUILT from first principles.

These are deliberately NOT ported from jj-bot's simulator strategies. Investigation
found those versions silently no-op: they read indicator dict keys (``volume_sma``,
``close_prev`` …) that the pipeline never populates, so ``.get()`` returns ``None``
and the strategy returns nothing. Porting them would enshrine dead code.

Instead these compute directly from price/volume arrays using textbook definitions,
with tests validating the math from first principles (see tests/test_jj_signals.py).
Advisory only — they return ``TechnicalSignal`` and never execute.
"""

from __future__ import annotations

import logging

import numpy as np

from src.signals.jj.signal_types import Direction, TechnicalSignal

logger = logging.getLogger(__name__)


def _arr(x) -> np.ndarray:
    return np.asarray(x, dtype=float)


# --------------------------------------------------------------------------- RSI

def rsi(close, period: int = 14) -> float:
    """Wilder's RSI. Returns the latest value in [0, 100].

    Standard definition: average gains/losses over `period`, then Wilder
    smoothing thereafter; RSI = 100 - 100/(1 + avg_gain/avg_loss).
    """
    close = _arr(close)
    if len(close) < period + 1:
        raise ValueError(f"need at least period+1={period + 1} closes, got {len(close)}")

    deltas = np.diff(close)
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)

    avg_gain = float(gains[:period].mean())
    avg_loss = float(losses[:period].mean())
    for i in range(period, len(deltas)):  # Wilder smoothing
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - 100.0 / (1.0 + rs)


def rsi_signal(
    close, period: int = 14, oversold: float = 30.0, overbought: float = 70.0
) -> TechnicalSignal:
    """Mean-reversion RSI signal: oversold -> long, overbought -> short."""
    close = _arr(close)
    if len(close) < period + 1:
        return TechnicalSignal("rsi", Direction.FLAT, 0.0, "insufficient_data", {"period": period})

    value = rsi(close, period)
    if value <= oversold:
        conf = min(1.0, (oversold - value) / oversold) if oversold > 0 else 0.0
        return TechnicalSignal(
            "rsi", Direction.LONG, round(conf, 4), f"RSI {value:.1f} oversold", {"rsi": round(value, 4)}
        )
    if value >= overbought:
        conf = min(1.0, (value - overbought) / (100.0 - overbought)) if overbought < 100 else 0.0
        return TechnicalSignal(
            "rsi", Direction.SHORT, round(conf, 4), f"RSI {value:.1f} overbought", {"rsi": round(value, 4)}
        )
    return TechnicalSignal("rsi", Direction.FLAT, 0.0, f"RSI {value:.1f} neutral", {"rsi": round(value, 4)})


# ---------------------------------------------------------------------- Momentum

def momentum(close, lookback: int = 10) -> float:
    """Rate of change over `lookback` bars: (price_now - price_then) / price_then."""
    close = _arr(close)
    if len(close) < lookback + 1:
        raise ValueError(f"need at least lookback+1={lookback + 1} closes, got {len(close)}")
    past = close[-1 - lookback]
    if past == 0:
        return 0.0
    return float((close[-1] - past) / past)


def momentum_signal(close, lookback: int = 10, threshold: float = 0.003) -> TechnicalSignal:
    """Momentum signal: rise beyond +threshold -> long, fall beyond -threshold -> short.

    Confidence scales linearly, reaching 1.0 at 5x the threshold.
    """
    close = _arr(close)
    if len(close) < lookback + 1:
        return TechnicalSignal("momentum", Direction.FLAT, 0.0, "insufficient_data", {"lookback": lookback})

    m = momentum(close, lookback)
    conf = min(1.0, abs(m) / (threshold * 5)) if threshold > 0 else 0.0
    if m > threshold:
        return TechnicalSignal("momentum", Direction.LONG, round(conf, 4), f"momentum +{m:.4f}", {"momentum": round(m, 6)})
    if m < -threshold:
        return TechnicalSignal("momentum", Direction.SHORT, round(conf, 4), f"momentum {m:.4f}", {"momentum": round(m, 6)})
    return TechnicalSignal("momentum", Direction.FLAT, 0.0, f"momentum {m:.4f} below threshold", {"momentum": round(m, 6)})


# ---------------------------------------------------------------- Volume breakout

def volume_breakout_signal(
    close, volume, window: int = 20, multiplier: float = 2.0
) -> TechnicalSignal:
    """Volume-spike breakout: current volume > multiplier x the prior `window` average.

    On a spike, direction follows the last price move (up -> long, down -> short).
    Confidence scales with the spike ratio (1.0 at 2x the multiplier).
    """
    close, volume = _arr(close), _arr(volume)
    if len(close) < window + 1 or len(volume) < window + 1:
        return TechnicalSignal("volume_breakout", Direction.FLAT, 0.0, "insufficient_data", {"window": window})

    prior_avg = float(volume[-1 - window:-1].mean())
    current_vol = float(volume[-1])
    if prior_avg <= 0 or current_vol <= multiplier * prior_avg:
        return TechnicalSignal("volume_breakout", Direction.FLAT, 0.0, "no volume spike", {"vol_ratio": round(current_vol / prior_avg, 4) if prior_avg > 0 else None})

    ratio = current_vol / prior_avg
    conf = min(1.0, ratio / (multiplier * 2))
    went_up = close[-1] > close[-2]
    direction = Direction.LONG if went_up else Direction.SHORT
    return TechnicalSignal(
        "volume_breakout", direction, round(conf, 4),
        f"volume spike {ratio:.1f}x ({'up' if went_up else 'down'} move)",
        {"vol_ratio": round(ratio, 4)},
    )
