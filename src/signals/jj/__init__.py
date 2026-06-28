"""JJ signal pack — the ported "JJ Gorilla" technical analysis.

- VWAP-deviation engine: ported faithfully from jj-bot's ``vwap_calculator.py``
  (its genuinely sound "jewel").
- RSI / momentum / volume-breakout: REBUILT from first principles (textbook
  definitions), deliberately NOT copied from jj-bot's simulator versions, which
  silently no-op on unpopulated indicator fields.

Advisory only — these inform an agent's reasoning; they never execute. See
``src/signals/__init__.py``.
"""

from src.signals.jj.indicators import (
    momentum,
    momentum_signal,
    rsi,
    rsi_signal,
    volume_breakout_signal,
)
from src.signals.jj.signal_types import Direction, TechnicalSignal
from src.signals.jj.vwap import (
    rolling_vwap,
    strategy_for_regime,
    vwap_deviation_signal,
    vwap_signal,
)

__version__ = "0.1.0"

__all__ = [
    "Direction",
    "TechnicalSignal",
    "rolling_vwap",
    "strategy_for_regime",
    "vwap_deviation_signal",
    "vwap_signal",
    "rsi",
    "rsi_signal",
    "momentum",
    "momentum_signal",
    "volume_breakout_signal",
]
