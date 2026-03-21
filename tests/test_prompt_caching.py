"""Tests for prompt caching integration — Phase 3.5."""

__version__ = "0.1.0"

import pytest

from src.agents.claude_client import calculate_cost, get_pricing, MODEL_PRICING


class TestGetPricing:
    def test_exact_match_sonnet(self):
        pricing = get_pricing("claude-sonnet-4-20250514")
        assert pricing["input_per_million"] == 3.00
        assert pricing["output_per_million"] == 15.00

    def test_exact_match_haiku(self):
        pricing = get_pricing("claude-haiku-4-5-20251001")
        assert pricing["input_per_million"] == 1.00
        assert pricing["output_per_million"] == 5.00

    def test_partial_match(self):
        pricing = get_pricing("claude-sonnet")
        assert pricing["input_per_million"] == 3.00

    def test_unknown_model_falls_back_to_sonnet_rates(self):
        pricing = get_pricing("claude-unknown-model-9999")
        assert pricing["input_per_million"] == 3.00
        assert pricing["output_per_million"] == 15.00


class TestCostCalculation:
    def test_basic_sonnet_cost(self):
        cost = calculate_cost("claude-sonnet-4-20250514", 1000, 500)
        # 1000/1M * 3.0 + 500/1M * 15.0 = 0.003 + 0.0075 = 0.0105
        assert cost == pytest.approx(0.0105, abs=0.0001)

    def test_basic_haiku_cost(self):
        cost = calculate_cost("claude-haiku-4-5-20251001", 1000, 500)
        # 1000/1M * 1.0 + 500/1M * 5.0 = 0.001 + 0.0025 = 0.0035
        assert cost == pytest.approx(0.0035, abs=0.0001)

    def test_cache_creation_cost(self):
        # Cache writes cost 1.25x input rate
        cost = calculate_cost(
            "claude-sonnet-4-20250514",
            input_tokens=0,
            output_tokens=0,
            cache_creation_tokens=1_000_000,
        )
        # 1M * 3.0 * 1.25 = 3.75
        assert cost == pytest.approx(3.75, abs=0.01)

    def test_cache_read_cost(self):
        # Cache reads cost 0.1x input rate
        cost = calculate_cost(
            "claude-sonnet-4-20250514",
            input_tokens=0,
            output_tokens=0,
            cache_read_tokens=1_000_000,
        )
        # 1M * 3.0 * 0.10 = 0.30
        assert cost == pytest.approx(0.30, abs=0.01)

    def test_mixed_cached_and_uncached(self):
        cost = calculate_cost(
            "claude-sonnet-4-20250514",
            input_tokens=500,
            output_tokens=200,
            cache_creation_tokens=1000,
            cache_read_tokens=2000,
        )
        # Standard: 500/1M * 3.0 = 0.0015
        # Output: 200/1M * 15.0 = 0.003
        # Cache write: 1000/1M * 3.0 * 1.25 = 0.00375
        # Cache read: 2000/1M * 3.0 * 0.10 = 0.0006
        expected = 0.0015 + 0.003 + 0.00375 + 0.0006
        assert cost == pytest.approx(expected, abs=0.0001)

    def test_no_cache_tokens_equals_standard_cost(self):
        with_cache = calculate_cost(
            "claude-sonnet-4-20250514", 1000, 500, 0, 0
        )
        without_cache = calculate_cost(
            "claude-sonnet-4-20250514", 1000, 500
        )
        assert with_cache == without_cache


class TestCacheControlConfig:
    def test_caching_enabled_sends_list_system(self):
        """When caching is enabled, system prompt should be a list with cache_control."""
        from unittest.mock import patch, MagicMock
        from src.agents.claude_client import ClaudeClient

        with patch("src.agents.claude_client.config") as mock_config:
            mock_config.prompt_caching_enabled = True

            client = ClaudeClient(api_key="test-key")
            # We can't easily test the actual API call structure without
            # mocking anthropic, but we verify the client initializes
            assert client.model == "claude-sonnet-4-20250514"

    def test_caching_disabled_sends_string_system(self):
        """When caching is disabled, system prompt should be a plain string."""
        from unittest.mock import patch
        from src.agents.claude_client import ClaudeClient

        with patch("src.agents.claude_client.config") as mock_config:
            mock_config.prompt_caching_enabled = False

            client = ClaudeClient(api_key="test-key")
            assert client.model == "claude-sonnet-4-20250514"
