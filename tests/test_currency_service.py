"""
Tests for CurrencyService and CAD accounting layer.
"""

__version__ = "1.0.0"

import pytest
from unittest.mock import patch, MagicMock

from src.common.currency_service import CurrencyService
from src.common.models import Agent, SystemState
from src.genesis.treasury import TreasuryManager
from src.risk.accountant import Accountant


# ── CurrencyService Unit Tests ─────────────────────────────


class TestCurrencyServiceFallback:
    """Tests for CurrencyService when no Redis or Kraken is available."""

    def test_fallback_rate_used_when_no_redis_or_kraken(self):
        """Without Redis or Kraken, should use config fallback rate."""
        cs = CurrencyService(redis_client=None)
        with patch.object(cs, "_fetch_usdt_cad_from_kraken", return_value=None):
            rate = cs.get_usdt_cad_rate()
            assert rate == 1.38  # config default fallback

    def test_usdt_to_cad_conversion(self):
        """100 USDT at 1.38 rate should be 138 CAD."""
        cs = CurrencyService(redis_client=None)
        with patch.object(cs, "get_usdt_cad_rate", return_value=1.38):
            result = cs.usdt_to_cad(100.0)
            assert result == 138.0

    def test_cad_to_usdt_conversion(self):
        """138 CAD at 1.38 rate should be 100 USDT."""
        cs = CurrencyService(redis_client=None)
        with patch.object(cs, "get_usdt_cad_rate", return_value=1.38):
            result = cs.cad_to_usdt(138.0)
            assert result == 100.0

    def test_usd_to_cad_conversion(self):
        """USD to CAD conversion for API costs."""
        cs = CurrencyService(redis_client=None)
        with patch.object(cs, "get_usd_cad_rate", return_value=1.38):
            result = cs.usd_to_cad(1.0)
            assert result == 1.38

    def test_zero_amount_returns_zero(self):
        """Zero input should return zero without hitting rate fetch."""
        cs = CurrencyService(redis_client=None)
        assert cs.usdt_to_cad(0.0) == 0.0
        assert cs.cad_to_usdt(0.0) == 0.0
        assert cs.usd_to_cad(0.0) == 0.0


class TestCurrencyServiceManualOverride:
    """Tests for manual rate override (testing mode)."""

    def test_manual_override_takes_precedence(self):
        """When manual override is set in config, use that rate."""
        cs = CurrencyService(redis_client=None)
        with patch("src.common.currency_service.config") as mock_config:
            mock_config.usdt_cad_manual_override = 1.50
            mock_config.currency_cache_ttl_seconds = 300
            rate = cs.get_usdt_cad_rate()
            assert rate == 1.50

    def test_manual_override_zero_means_disabled(self):
        """Override of 0.0 means disabled — use normal flow."""
        cs = CurrencyService(redis_client=None)
        with patch("src.common.currency_service.config") as mock_config:
            mock_config.usdt_cad_manual_override = 0.0
            mock_config.currency_cache_ttl_seconds = 300
            mock_config.usdt_cad_fallback_rate = 1.38
            with patch.object(cs, "_fetch_usdt_cad_from_kraken", return_value=None):
                rate = cs.get_usdt_cad_rate()
                assert rate == 1.38


class TestCurrencyServiceKrakenFetch:
    """Tests for Kraken rate fetching (mocked)."""

    def test_kraken_fetch_returns_rate(self):
        """Mocked Kraken fetch should return the ticker last price."""
        cs = CurrencyService(redis_client=None)
        mock_kraken = MagicMock()
        mock_kraken.fetch_ticker.return_value = {"last": 1.3733}

        with patch.dict("sys.modules", {"ccxt": MagicMock()}) as _:
            import sys
            sys.modules["ccxt"].kraken.return_value = mock_kraken
            rate = cs._fetch_usdt_cad_from_kraken()
            assert rate == 1.3733

    def test_kraken_fetch_failure_returns_none(self):
        """If Kraken fails, should return None (fallback will be used)."""
        cs = CurrencyService(redis_client=None)
        with patch.dict("sys.modules", {"ccxt": MagicMock()}) as _:
            import sys
            sys.modules["ccxt"].kraken.side_effect = Exception("network error")
            rate = cs._fetch_usdt_cad_from_kraken()
            assert rate is None


class TestCurrencyServiceRedisCache:
    """Tests for Redis caching of rates."""

    def test_redis_cache_hit(self):
        """Should return cached rate from Redis without fetching."""
        mock_redis = MagicMock()
        mock_redis.get.return_value = b"1.3700"

        cs = CurrencyService(redis_client=mock_redis)
        with patch("src.common.currency_service.config") as mock_config:
            mock_config.usdt_cad_manual_override = 0.0
            mock_config.currency_cache_ttl_seconds = 300
            rate = cs.get_usdt_cad_rate()
            assert rate == 1.37

    def test_cache_write_on_fetch(self):
        """After fetching from Kraken, rate should be cached in Redis."""
        mock_redis = MagicMock()
        mock_redis.get.return_value = None  # Cache miss

        cs = CurrencyService(redis_client=mock_redis)
        with patch.object(cs, "_fetch_usdt_cad_from_kraken", return_value=1.3733):
            with patch("src.common.currency_service.config") as mock_config:
                mock_config.usdt_cad_manual_override = 0.0
                mock_config.currency_cache_ttl_seconds = 300
                rate = cs.get_usdt_cad_rate()
                assert rate == 1.3733
                # Verify Redis setex was called
                mock_redis.setex.assert_called()


# ── Treasury CAD Integration Tests ─────────────────────────


@pytest.mark.asyncio
async def test_treasury_starts_at_500_cad(seeded_db, mock_currency):
    """Treasury should initialize to 500 CAD."""
    treasury = TreasuryManager(db_session_factory=seeded_db, currency_service=mock_currency)
    balance = await treasury.get_treasury_balance()
    assert balance["total"] == 500.0
    assert balance["currency"] == "CAD"


@pytest.mark.asyncio
async def test_treasury_balance_returns_usdt_cad_rate(seeded_db, mock_currency_realistic):
    """Balance should include the current USDT/CAD rate."""
    treasury = TreasuryManager(db_session_factory=seeded_db, currency_service=mock_currency_realistic)
    balance = await treasury.get_treasury_balance()
    assert balance["usdt_cad_rate"] == 1.38


@pytest.mark.asyncio
async def test_allocation_converts_cad_to_usdt(seeded_db, mock_currency_realistic):
    """Capital allocation should convert CAD to USDT for agent."""
    treasury = TreasuryManager(db_session_factory=seeded_db, currency_service=mock_currency_realistic)

    # Allocate 50 CAD → should become ~36.23 USDT on the agent
    success = await treasury.allocate_capital(1, 50.0)
    assert success is True

    with seeded_db() as session:
        agent = session.get(Agent, 1)
        # Original 100 USDT + 50 CAD / 1.38 ≈ 136.23 USDT
        expected_usdt = 100.0 + (50.0 / 1.38)
        assert abs(agent.capital_allocated - expected_usdt) < 0.01


@pytest.mark.asyncio
async def test_reclaim_converts_usdt_to_cad(seeded_db, mock_currency_realistic):
    """Capital reclamation should convert agent USDT back to CAD for treasury."""
    treasury = TreasuryManager(db_session_factory=seeded_db, currency_service=mock_currency_realistic)

    # Agent has 100 USDT. Reclaim should add 138 CAD to treasury.
    reclaimed = await treasury.reclaim_capital(1)
    assert reclaimed == 100.0  # Returns USDT amount

    with seeded_db() as session:
        from sqlalchemy import select
        state = session.execute(select(SystemState).limit(1)).scalar_one()
        # 500 CAD + 100 USDT * 1.38 = 638 CAD
        assert abs(state.total_treasury - 638.0) < 0.01


# ── Accountant CAD Integration Tests ───────────────────────


@pytest.mark.asyncio
async def test_pnl_includes_cad_values(seeded_db, mock_currency_realistic):
    """P&L calculation should include both USDT and CAD values."""
    accountant = Accountant(db_session_factory=seeded_db, currency_service=mock_currency_realistic)
    pnl = await accountant.calculate_agent_pnl(1)

    # Should have both USDT and CAD keys
    assert "true_pnl" in pnl  # USDT
    assert "true_pnl_cad" in pnl  # CAD
    assert "gross_pnl_cad" in pnl
    assert "api_cost_cad" in pnl


@pytest.mark.asyncio
async def test_system_summary_includes_cad_rate(seeded_db, mock_currency_realistic):
    """System summary should include USDT/CAD rate."""
    accountant = Accountant(db_session_factory=seeded_db, currency_service=mock_currency_realistic)
    summary = await accountant.get_system_summary()

    assert summary["usdt_cad_rate"] == 1.38
    assert summary["currency"] == "CAD"
    assert "total_api_spend_cad" in summary
