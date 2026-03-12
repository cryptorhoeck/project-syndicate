"""Tests for FeeSchedule — Phase 3C."""

from src.trading.fee_schedule import FeeSchedule


def test_market_order_uses_taker_rate():
    """Market orders should use taker rate."""
    fee, rate = FeeSchedule.calculate_fee(1000.0, "market", "kraken")
    assert rate == 0.0026
    assert abs(fee - 2.6) < 0.001


def test_limit_order_uses_maker_rate():
    """Limit orders should use maker rate."""
    fee, rate = FeeSchedule.calculate_fee(1000.0, "limit", "kraken")
    assert rate == 0.0016
    assert abs(fee - 1.6) < 0.001


def test_kraken_rates():
    """Kraken rates: 0.16% maker, 0.26% taker."""
    assert FeeSchedule.get_rate("market", "kraken") == 0.0026
    assert FeeSchedule.get_rate("limit", "kraken") == 0.0016


def test_binance_rates():
    """Binance rates: 0.10% maker, 0.10% taker."""
    assert FeeSchedule.get_rate("market", "binance") == 0.0010
    assert FeeSchedule.get_rate("limit", "binance") == 0.0010


def test_binance_fee_calculation():
    """Binance fee should be lower than Kraken for same order."""
    kraken_fee, _ = FeeSchedule.calculate_fee(1000.0, "market", "kraken")
    binance_fee, _ = FeeSchedule.calculate_fee(1000.0, "market", "binance")
    assert binance_fee < kraken_fee


def test_unknown_exchange_defaults_to_kraken():
    """Unknown exchange should fall back to Kraken rates."""
    fee, rate = FeeSchedule.calculate_fee(1000.0, "market", "unknown_exchange")
    assert rate == 0.0026


def test_zero_size_order():
    """Zero-size order should return zero fee."""
    fee, rate = FeeSchedule.calculate_fee(0.0, "market", "kraken")
    assert fee == 0.0
