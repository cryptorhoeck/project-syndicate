"""Tests for the JJ signal pack (``src/signals/jj``).

Validates the ported VWAP-deviation engine and the rebuilt-from-first-principles
RSI, momentum, and volume-breakout signals. RSI is checked against a HAND-COMPUTED
Wilder value. Where jj-bot's original strategies were broken (silent no-ops), we
test that the rebuilt versions intentionally diverge and actually fire.
"""

import numpy as np
import pytest

from src.signals.jj import (
    Direction,
    TechnicalSignal,
    momentum,
    momentum_signal,
    rolling_vwap,
    rsi,
    rsi_signal,
    volume_breakout_signal,
    vwap_deviation_signal,
    vwap_signal,
)


# ------------------------------------------------------------------ TechnicalSignal

def test_technical_signal_is_advisory_shape():
    sig = vwap_deviation_signal([1, 2], [1, 2], [1, 2], [1, 1])
    assert isinstance(sig, TechnicalSignal)
    assert sig.direction in (Direction.LONG, Direction.SHORT, Direction.FLAT)
    assert 0.0 <= sig.confidence <= 1.0


# ------------------------------------------------------------------------- RSI math

def test_rsi_hand_computed_wilder_period2():
    # closes [10,11,10,11,12], period=2 -> Wilder RSI = 87.5 (derived by hand).
    assert rsi([10, 11, 10, 11, 12], period=2) == pytest.approx(87.5, abs=1e-9)


def test_rsi_all_gains_is_100():
    assert rsi(list(range(1, 30)), period=14) == 100.0


def test_rsi_all_losses_is_zero():
    assert rsi(list(range(30, 1, -1)), period=14) == 0.0


def test_rsi_signal_oversold_is_long():
    sig = rsi_signal(list(range(30, 1, -1)), period=14)
    assert sig.source == "rsi"
    assert sig.direction == Direction.LONG


def test_rsi_signal_overbought_is_short():
    sig = rsi_signal(list(range(1, 30)), period=14)
    assert sig.direction == Direction.SHORT


def test_rsi_raises_on_too_short_input():
    with pytest.raises(ValueError):
        rsi([1, 2, 3], period=14)


# -------------------------------------------------------------------- Momentum math

def test_momentum_known_value():
    closes = [100.0] * 5 + [110.0]  # past = closes[-6] = 100, now = 110
    assert momentum(closes, lookback=5) == pytest.approx(0.10)


def test_momentum_signal_long_on_rise():
    closes = list(np.linspace(100, 120, 30))
    assert momentum_signal(closes, lookback=10, threshold=0.003).direction == Direction.LONG


def test_momentum_signal_short_on_fall():
    closes = list(np.linspace(120, 100, 30))
    assert momentum_signal(closes, lookback=10, threshold=0.003).direction == Direction.SHORT


def test_momentum_signal_flat_when_below_threshold():
    closes = [100.0] * 30
    assert momentum_signal(closes, lookback=10).direction == Direction.FLAT


# ------------------------------------------------------------- Volume-breakout math

def test_volume_breakout_long_on_spike_up():
    close = [100.0] * 20 + [101.0]      # last move is up
    volume = [1000.0] * 20 + [5000.0]   # 5x spike vs prior 20-bar average
    sig = volume_breakout_signal(close, volume, window=20, multiplier=2.0)
    assert sig.direction == Direction.LONG
    assert sig.confidence > 0


def test_volume_breakout_short_on_spike_down():
    close = [100.0] * 20 + [99.0]       # last move is down
    volume = [1000.0] * 20 + [5000.0]
    assert volume_breakout_signal(close, volume, window=20).direction == Direction.SHORT


def test_volume_breakout_flat_without_spike():
    close = [100.0] * 20 + [101.0]
    volume = [1000.0] * 21              # no spike
    assert volume_breakout_signal(close, volume, window=20).direction == Direction.FLAT


def test_volume_breakout_diverges_from_jjbot_broken_noop():
    # jj-bot's simulator volume_breakout silently no-ops (reads a 'volume_sma' key
    # that's never populated). Ours computes from the array, so a real spike fires.
    # This intentional divergence is the whole point of rebuilding, not porting.
    close = [100.0] * 20 + [101.0]
    volume = [1000.0] * 20 + [5000.0]
    assert volume_breakout_signal(close, volume, window=20).direction == Direction.LONG


# --------------------------------------------------------------------- VWAP engine

def test_rolling_vwap_known_value():
    # Constant price -> VWAP equals that price; bands collapse to it.
    n = 25
    high = np.full(n, 100.0)
    low = np.full(n, 100.0)
    close = np.full(n, 100.0)
    volume = np.full(n, 1000.0)
    vwap, upper, lower = rolling_vwap(high, low, close, volume, window=20)
    assert vwap[-1] == pytest.approx(100.0)
    assert upper[-1] == pytest.approx(100.0)
    assert lower[-1] == pytest.approx(100.0)


def test_vwap_mean_reversion_long_when_price_below():
    n = 30
    high = np.full(n, 100.5)
    low = np.full(n, 99.5)
    close = np.full(n, 100.0)
    volume = np.full(n, 1000.0)
    close[-1], high[-1], low[-1] = 90.0, 90.5, 89.5  # sharp drop well below VWAP
    sig = vwap_deviation_signal(high, low, close, volume, strategy="mean_reversion", regime="neutral")
    assert sig.direction == Direction.LONG
    assert sig.confidence > 0


def test_vwap_mean_reversion_short_when_price_above():
    n = 30
    high = np.full(n, 100.5)
    low = np.full(n, 99.5)
    close = np.full(n, 100.0)
    volume = np.full(n, 1000.0)
    close[-1], high[-1], low[-1] = 110.0, 110.5, 109.5  # spike well above VWAP
    sig = vwap_deviation_signal(high, low, close, volume, strategy="mean_reversion", regime="neutral")
    assert sig.direction == Direction.SHORT


def test_vwap_trend_following_breakout_long():
    # Flat at 100, then prev bar dips below VWAP and last bar crosses above it.
    n = 30
    close = np.full(n, 100.0)
    close[-2], close[-1] = 99.0, 101.0
    high = close.copy()
    low = close.copy()
    volume = np.full(n, 1000.0)
    sig = vwap_deviation_signal(high, low, close, volume, strategy="trend_following", regime="neutral")
    assert sig.direction == Direction.LONG
    assert sig.confidence == pytest.approx(0.7)


def test_vwap_regime_bias_suppresses_short_in_bull():
    n = 30
    high = np.full(n, 100.5)
    low = np.full(n, 99.5)
    close = np.full(n, 100.0)
    volume = np.full(n, 1000.0)
    close[-1], high[-1], low[-1] = 110.0, 110.5, 109.5
    neutral = vwap_deviation_signal(high, low, close, volume, strategy="mean_reversion", regime="neutral")
    bull = vwap_deviation_signal(high, low, close, volume, strategy="mean_reversion", regime="bull")
    assert neutral.direction == Direction.SHORT      # would short...
    assert bull.direction == Direction.FLAT          # ...but bull regime suppresses it


def test_vwap_signal_picks_trend_following_in_bull():
    n = 30
    close = np.full(n, 100.0)
    high = close.copy()
    low = close.copy()
    volume = np.full(n, 1000.0)
    sig = vwap_signal(high, low, close, volume, regime="bull")
    assert sig.details["strategy"] == "trend_following"


def test_vwap_unknown_strategy_raises():
    n = 5
    a = np.full(n, 100.0)
    with pytest.raises(ValueError):
        vwap_deviation_signal(a, a, a, np.full(n, 1.0), strategy="nonsense")


# ------------------------------------------------------------- Graceful short input

def test_signals_handle_short_input_gracefully():
    assert vwap_deviation_signal([1], [1], [1], [1]).direction == Direction.FLAT
    assert rsi_signal([1, 2, 3]).direction == Direction.FLAT
    assert momentum_signal([1, 2, 3]).direction == Direction.FLAT
    assert volume_breakout_signal([1, 2, 3], [1, 2, 3]).direction == Direction.FLAT
