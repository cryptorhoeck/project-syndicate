"""
VWAP Calculator and Signal Generator

Volume Weighted Average Price (VWAP) calculation with regime-adaptive signal generation.
Supports mean reversion and trend following strategies.

VWAP is used by Renaissance Technologies for:
- Dynamic support/resistance identification
- Fair value estimation
- Mean reversion signal generation
"""

import numpy as np
import pandas as pd
from typing import Dict, Optional, Tuple, List
from dataclasses import dataclass
from enum import Enum
import logging

logger = logging.getLogger(__name__)


class VWAPStrategy(str, Enum):
    """VWAP trading strategy types"""
    MEAN_REVERSION = "mean_reversion"
    TREND_FOLLOWING = "trend_following"


class Signal(int, Enum):
    """Trading signals"""
    BUY = 1
    HOLD = 0
    SELL = -1


@dataclass
class VWAPResult:
    """VWAP calculation result"""
    vwap: float
    current_price: float
    deviation: float  # Price deviation from VWAP
    deviation_pct: float
    upper_band: float
    lower_band: float
    position_relative: str  # "ABOVE" or "BELOW"


@dataclass
class VWAPSignal:
    """VWAP-based trading signal"""
    signal: Signal
    signal_type: str  # "BUY", "SELL", "HOLD"
    vwap_distance_pct: float
    current_price: float
    current_vwap: float
    strategy: VWAPStrategy
    regime: str
    confidence: float  # Signal confidence 0-1


class VWAPCalculator:
    """
    VWAP calculator with deviation bands and signal generation.

    Integrates with regime detection for adaptive strategy selection:
    - BULL regime: Trend following (buy breakouts above VWAP)
    - NEUTRAL/BEAR regime: Mean reversion (buy below VWAP, sell above)
    """

    def __init__(
        self,
        std_multiplier: float = 2.0,
        deviation_threshold: float = 0.02,  # 2% deviation for signals
    ):
        """
        Initialize VWAP calculator.

        Parameters
        ----------
        std_multiplier : float
            Standard deviation multiplier for bands (2.0 = 2 sigma)
        deviation_threshold : float
            Minimum deviation from VWAP for signal generation
        """
        self.std_multiplier = std_multiplier
        self.deviation_threshold = deviation_threshold

    def calculate_vwap(
        self,
        high: np.ndarray,
        low: np.ndarray,
        close: np.ndarray,
        volume: np.ndarray,
        anchor_period: Optional[str] = None,
    ) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        """
        Calculate VWAP with optional anchoring.

        Parameters
        ----------
        high : np.ndarray
            High prices
        low : np.ndarray
            Low prices
        close : np.ndarray
            Close prices
        volume : np.ndarray
            Volume data
        anchor_period : str, optional
            Anchor period for VWAP reset ('D'=daily, 'W'=weekly, 'M'=monthly)
            If None, cumulative VWAP is calculated

        Returns
        -------
        tuple
            (vwap, upper_band, lower_band) arrays
        """
        # Typical price
        typical_price = (high + low + close) / 3

        # Cumulative calculations
        cum_tp_vol = np.cumsum(typical_price * volume)
        cum_vol = np.cumsum(volume)

        # Avoid division by zero
        cum_vol = np.where(cum_vol == 0, 1, cum_vol)

        # VWAP
        vwap = cum_tp_vol / cum_vol

        # Calculate deviation for bands
        deviation = close - vwap
        rolling_std = self._rolling_std(deviation, window=20)

        upper_band = vwap + self.std_multiplier * rolling_std
        lower_band = vwap - self.std_multiplier * rolling_std

        return vwap, upper_band, lower_band

    def calculate_rolling_vwap(
        self,
        high: np.ndarray,
        low: np.ndarray,
        close: np.ndarray,
        volume: np.ndarray,
        window: int = 20,
    ) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        """
        Calculate rolling VWAP over specified window.

        Better for multi-day analysis as it doesn't anchor to a specific period.

        Parameters
        ----------
        high, low, close, volume : np.ndarray
            Price and volume data
        window : int
            Rolling window size

        Returns
        -------
        tuple
            (vwap, upper_band, lower_band) arrays
        """
        typical_price = (high + low + close) / 3

        # Rolling calculations
        rolling_tp_vol = self._rolling_sum(typical_price * volume, window)
        rolling_vol = self._rolling_sum(volume, window)

        # Avoid division by zero
        rolling_vol = np.where(rolling_vol == 0, 1, rolling_vol)

        vwap = rolling_tp_vol / rolling_vol

        # Bands
        deviation = close - vwap
        rolling_std = self._rolling_std(deviation, window)

        upper_band = vwap + self.std_multiplier * rolling_std
        lower_band = vwap - self.std_multiplier * rolling_std

        return vwap, upper_band, lower_band

    def get_current_vwap(
        self,
        high: np.ndarray,
        low: np.ndarray,
        close: np.ndarray,
        volume: np.ndarray,
        use_rolling: bool = True,
        window: int = 20,
    ) -> VWAPResult:
        """
        Get current VWAP calculation and deviation.

        Parameters
        ----------
        high, low, close, volume : np.ndarray
            Price and volume data
        use_rolling : bool
            Use rolling VWAP (True) or cumulative (False)
        window : int
            Rolling window if use_rolling=True

        Returns
        -------
        VWAPResult
            Current VWAP metrics
        """
        if use_rolling:
            vwap, upper, lower = self.calculate_rolling_vwap(
                high, low, close, volume, window
            )
        else:
            vwap, upper, lower = self.calculate_vwap(high, low, close, volume)

        current_price = close[-1]
        current_vwap = vwap[-1]

        deviation = current_price - current_vwap
        deviation_pct = deviation / current_vwap if current_vwap != 0 else 0

        return VWAPResult(
            vwap=current_vwap,
            current_price=current_price,
            deviation=deviation,
            deviation_pct=deviation_pct * 100,
            upper_band=upper[-1],
            lower_band=lower[-1],
            position_relative="ABOVE" if deviation > 0 else "BELOW",
        )

    def generate_signal(
        self,
        high: np.ndarray,
        low: np.ndarray,
        close: np.ndarray,
        volume: np.ndarray,
        strategy: VWAPStrategy = VWAPStrategy.MEAN_REVERSION,
        regime: str = "neutral",
    ) -> VWAPSignal:
        """
        Generate trading signal based on VWAP.

        Strategy Logic:
        - MEAN_REVERSION: Buy when price significantly below VWAP, sell above
        - TREND_FOLLOWING: Buy on VWAP breakout, sell on breakdown

        Parameters
        ----------
        high, low, close, volume : np.ndarray
            Price and volume data
        strategy : VWAPStrategy
            Strategy type
        regime : str
            Current market regime

        Returns
        -------
        VWAPSignal
            Trading signal with details
        """
        vwap_result = self.get_current_vwap(high, low, close, volume)
        deviation_pct = vwap_result.deviation_pct / 100  # Convert to decimal

        signal = Signal.HOLD
        confidence = 0.0

        if strategy == VWAPStrategy.MEAN_REVERSION:
            # Mean Reversion: Buy below VWAP, sell above VWAP
            if deviation_pct < -self.deviation_threshold:
                # Price significantly below VWAP - buy signal
                signal = Signal.BUY
                confidence = min(1.0, abs(deviation_pct) / (self.deviation_threshold * 2))
            elif deviation_pct > self.deviation_threshold:
                # Price significantly above VWAP - sell signal
                signal = Signal.SELL
                confidence = min(1.0, abs(deviation_pct) / (self.deviation_threshold * 2))
            elif abs(deviation_pct) < self.deviation_threshold * 0.5:
                # Price near VWAP - exit signal (hold/flat)
                signal = Signal.HOLD
                confidence = 0.5

        elif strategy == VWAPStrategy.TREND_FOLLOWING:
            # Trend Following: Buy breakouts, sell breakdowns
            prev_close = close[-2] if len(close) > 1 else close[-1]
            prev_vwap = self.get_current_vwap(
                high[:-1], low[:-1], close[:-1], volume[:-1]
            ).vwap if len(close) > 1 else vwap_result.vwap

            # Check for VWAP crossover
            currently_above = close[-1] > vwap_result.vwap
            previously_above = prev_close > prev_vwap

            if currently_above and not previously_above:
                # Breakout above VWAP - buy signal
                signal = Signal.BUY
                confidence = 0.7
            elif not currently_above and previously_above:
                # Breakdown below VWAP - sell signal
                signal = Signal.SELL
                confidence = 0.7
            elif currently_above:
                # Holding above VWAP - bullish
                signal = Signal.HOLD
                confidence = 0.5
            else:
                # Below VWAP - bearish
                signal = Signal.HOLD
                confidence = 0.3

        # Apply regime bias
        signal, confidence = self._apply_regime_bias(signal, confidence, regime)

        signal_type = {Signal.BUY: "BUY", Signal.SELL: "SELL", Signal.HOLD: "HOLD"}

        return VWAPSignal(
            signal=signal,
            signal_type=signal_type[signal],
            vwap_distance_pct=vwap_result.deviation_pct,
            current_price=vwap_result.current_price,
            current_vwap=vwap_result.vwap,
            strategy=strategy,
            regime=regime,
            confidence=confidence,
        )

    def _apply_regime_bias(
        self,
        signal: Signal,
        confidence: float,
        regime: str,
    ) -> Tuple[Signal, float]:
        """
        Apply regime-based bias to signals.

        - BULL: Bias towards long, avoid shorts
        - BEAR: Bias towards short, avoid longs
        - NEUTRAL: No bias
        """
        regime = regime.lower()

        if regime == "bull":
            # In bull market, don't short
            if signal == Signal.SELL:
                return Signal.HOLD, confidence * 0.5
            elif signal == Signal.BUY:
                return signal, min(1.0, confidence * 1.2)

        elif regime == "bear":
            # In bear market, don't go long
            if signal == Signal.BUY:
                return Signal.HOLD, confidence * 0.5
            elif signal == Signal.SELL:
                return signal, min(1.0, confidence * 1.2)

        return signal, confidence

    def get_strategy_for_regime(self, regime: str) -> VWAPStrategy:
        """
        Get recommended VWAP strategy based on market regime.

        Parameters
        ----------
        regime : str
            Market regime

        Returns
        -------
        VWAPStrategy
            Recommended strategy
        """
        regime = regime.lower()

        if regime == "bull":
            return VWAPStrategy.TREND_FOLLOWING
        else:
            # BEAR, NEUTRAL, SIDEWAYS, HIGH_VOLATILITY
            return VWAPStrategy.MEAN_REVERSION

    def _rolling_sum(self, arr: np.ndarray, window: int) -> np.ndarray:
        """Calculate rolling sum"""
        result = np.zeros_like(arr)
        for i in range(len(arr)):
            start = max(0, i - window + 1)
            result[i] = np.sum(arr[start:i + 1])
        return result

    def _rolling_std(self, arr: np.ndarray, window: int) -> np.ndarray:
        """Calculate rolling standard deviation"""
        result = np.zeros_like(arr)
        for i in range(len(arr)):
            start = max(0, i - window + 1)
            if i - start >= 1:  # Need at least 2 points for std
                result[i] = np.std(arr[start:i + 1])
            else:
                result[i] = 0
        return result


# Convenience functions

def calculate_vwap(
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    volume: np.ndarray,
) -> float:
    """
    Quick VWAP calculation returning current value.

    Parameters
    ----------
    high, low, close, volume : np.ndarray
        Price and volume data

    Returns
    -------
    float
        Current VWAP value
    """
    calc = VWAPCalculator()
    result = calc.get_current_vwap(high, low, close, volume)
    return result.vwap


def get_vwap_signal(
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    volume: np.ndarray,
    regime: str = "neutral",
) -> Dict:
    """
    Get VWAP-based trading signal with regime-adaptive strategy.

    Parameters
    ----------
    high, low, close, volume : np.ndarray
        Price and volume data
    regime : str
        Market regime

    Returns
    -------
    dict
        Signal information
    """
    calc = VWAPCalculator()
    strategy = calc.get_strategy_for_regime(regime)
    signal = calc.generate_signal(high, low, close, volume, strategy, regime)

    return {
        'signal': signal.signal.value,
        'signal_type': signal.signal_type,
        'vwap': signal.current_vwap,
        'price': signal.current_price,
        'deviation_pct': signal.vwap_distance_pct,
        'strategy': signal.strategy.value,
        'regime': signal.regime,
        'confidence': signal.confidence,
    }


# Global instance
default_vwap = VWAPCalculator()
