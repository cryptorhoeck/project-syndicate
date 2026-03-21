"""Tests for adaptive cycle frequency — Phase 3.5."""

__version__ = "0.1.0"

import pytest
from unittest.mock import patch

from src.agents.cycle_scheduler import (
    get_adjusted_interval,
    get_regime_multiplier,
    REGIME_FREQUENCY_MULTIPLIERS,
)


class TestRegimeMultipliers:
    def test_trending_bull(self):
        assert get_regime_multiplier("trending_bull") == 0.75

    def test_trending_bear(self):
        assert get_regime_multiplier("trending_bear") == 0.75

    def test_volatile(self):
        assert get_regime_multiplier("volatile") == 0.50

    def test_ranging(self):
        assert get_regime_multiplier("ranging") == 1.50

    def test_low_volatility(self):
        assert get_regime_multiplier("low_volatility") == 2.00

    def test_crab(self):
        assert get_regime_multiplier("crab") == 1.50

    def test_unknown(self):
        assert get_regime_multiplier("unknown") == 1.00

    def test_unrecognized_regime(self):
        assert get_regime_multiplier("never_heard_of_this") == 1.0


class TestAdjustedInterval:
    def test_trending_bull_faster(self):
        adjusted = get_adjusted_interval(300, "trending_bull")
        assert adjusted == 225  # 300 * 0.75

    def test_low_volatility_slower(self):
        adjusted = get_adjusted_interval(300, "low_volatility")
        assert adjusted == 600  # 300 * 2.0

    def test_volatile_fastest(self):
        adjusted = get_adjusted_interval(300, "volatile")
        assert adjusted == 150  # 300 * 0.5

    def test_unknown_no_change(self):
        adjusted = get_adjusted_interval(300, "unknown")
        assert adjusted == 300  # 300 * 1.0

    def test_minimum_floor(self):
        # Even at 0.5x on a 30s interval = 15s, floor is 30s
        adjusted = get_adjusted_interval(30, "volatile")
        assert adjusted == 30  # min_cycle_interval_seconds

    def test_floor_never_below_config(self):
        adjusted = get_adjusted_interval(10, "volatile")
        assert adjusted >= 30  # config default

    def test_disabled_returns_base(self):
        with patch("src.agents.cycle_scheduler.config") as mock_config:
            mock_config.adaptive_frequency_enabled = False
            adjusted = get_adjusted_interval(300, "trending_bull")
            assert adjusted == 300  # No adjustment

    def test_ranging_operator_slower(self):
        adjusted = get_adjusted_interval(60, "ranging")
        assert adjusted == 90  # 60 * 1.5
