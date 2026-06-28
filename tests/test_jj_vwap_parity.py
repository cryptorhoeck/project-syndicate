"""Parity tests: ported ``src/signals/jj/vwap.py`` vs the FROZEN original.

A faithful port must reproduce the original's outputs *exactly*, not merely behave
"reasonably" (that's what the first-principles tests in test_jj_signals.py are for).
These feed identical fixed arrays into jj-bot's original ``vwap_calculator`` (frozen
verbatim in tests/_reference, blob 6ccba93) and into our port, then assert:
  - identical rolling-VWAP / band arrays, and
  - identical signal decisions (direction + confidence) across every
    strategy x regime combination.

If the port ever silently drifts (rolling-vs-cumulative, off-by-one window, edge
handling, a shifted threshold), these fail.
"""

import numpy as np
import pytest

from src.signals.jj import vwap as new_vwap
from src.signals.jj.signal_types import Direction
from tests._reference.jj_vwap_calculator_original import (
    VWAPCalculator,
    VWAPStrategy,
    get_vwap_signal as orig_get_vwap_signal,
)

SIGNAL_TYPE_TO_DIRECTION = {
    "BUY": Direction.LONG,
    "SELL": Direction.SHORT,
    "HOLD": Direction.FLAT,
}


def _make_scenarios():
    """Deterministic OHLCV scenarios exercising both signal branches."""
    rng = np.random.RandomState(7)
    specs = [
        ("flat", lambda n: np.full(n, 100.0)),
        ("trend_up", lambda n: np.linspace(90, 130, n)),
        ("trend_down", lambda n: np.linspace(130, 90, n)),
        ("random_walk", lambda n: 100 + np.cumsum(rng.randn(n))),
        ("volatile", lambda n: 100 + np.cumsum(rng.randn(n) * 3)),
    ]
    scenarios = []
    for name, fn in specs:
        for n in (25, 40, 60):
            close = np.asarray(fn(n), dtype=float)
            noise = np.abs(rng.randn(n)) * 0.5 + 0.1
            high = close + noise
            low = close - noise
            volume = np.abs(rng.randn(n)) * 500 + 1000
            scenarios.append((f"{name}-{n}", high, low, close, volume))
    return scenarios


SCENARIOS = _make_scenarios()
SCEN_IDS = [s[0] for s in SCENARIOS]
STRATEGIES = [VWAPStrategy.MEAN_REVERSION, VWAPStrategy.TREND_FOLLOWING]
REGIMES = ["neutral", "bull", "bear"]


@pytest.mark.parametrize("scen", SCENARIOS, ids=SCEN_IDS)
def test_rolling_vwap_arrays_match_original(scen):
    _, high, low, close, volume = scen
    o_vwap, o_up, o_lo = VWAPCalculator().calculate_rolling_vwap(high, low, close, volume, window=20)
    n_vwap, n_up, n_lo = new_vwap.rolling_vwap(high, low, close, volume, window=20)
    assert np.allclose(o_vwap, n_vwap, atol=1e-9, rtol=0)
    assert np.allclose(o_up, n_up, atol=1e-9, rtol=0)
    assert np.allclose(o_lo, n_lo, atol=1e-9, rtol=0)


@pytest.mark.parametrize("scen", SCENARIOS, ids=SCEN_IDS)
@pytest.mark.parametrize("strategy", STRATEGIES, ids=lambda s: s.value)
@pytest.mark.parametrize("regime", REGIMES)
def test_signal_decisions_match_original(scen, strategy, regime):
    _, high, low, close, volume = scen
    orig = VWAPCalculator().generate_signal(high, low, close, volume, strategy=strategy, regime=regime)
    mine = new_vwap.vwap_deviation_signal(high, low, close, volume, strategy=strategy.value, regime=regime)
    assert mine.direction == SIGNAL_TYPE_TO_DIRECTION[orig.signal_type]
    assert mine.confidence == pytest.approx(orig.confidence, abs=1e-4)


@pytest.mark.parametrize("scen", SCENARIOS, ids=SCEN_IDS)
@pytest.mark.parametrize("regime", REGIMES)
def test_regime_adaptive_convenience_matches_original(scen, regime):
    _, high, low, close, volume = scen
    orig = orig_get_vwap_signal(high, low, close, volume, regime=regime)  # dict
    mine = new_vwap.vwap_signal(high, low, close, volume, regime=regime)
    assert mine.direction == SIGNAL_TYPE_TO_DIRECTION[orig["signal_type"]]
    assert mine.confidence == pytest.approx(orig["confidence"], abs=1e-4)
    assert mine.details["vwap"] == pytest.approx(orig["vwap"], abs=1e-6)
