"""Tests for the Model Router — Phase 3.5."""

__version__ = "0.1.0"

import pytest
from unittest.mock import patch

from src.agents.model_router import ModelRouter, ModelSelection


@pytest.fixture
def router():
    return ModelRouter()


class TestModelSelection:
    def test_is_sonnet_true(self):
        sel = ModelSelection(model_id="claude-sonnet-4-20250514", reason="test")
        assert sel.is_sonnet

    def test_is_sonnet_false(self):
        sel = ModelSelection(model_id="claude-haiku-4-5-20251001", reason="test")
        assert not sel.is_sonnet

    def test_cost_multiplier_sonnet(self):
        sel = ModelSelection(model_id="claude-sonnet-4-20250514", reason="test")
        assert sel.cost_multiplier == 1.0

    def test_cost_multiplier_haiku(self):
        sel = ModelSelection(model_id="claude-haiku-4-5-20251001", reason="test")
        assert sel.cost_multiplier == pytest.approx(0.33, abs=0.01)


class TestSonnetRouting:
    """Test cases where Sonnet should be selected."""

    def test_genesis_evaluation(self, router):
        result = router.select_model("genesis", "evaluation")
        assert result.is_sonnet
        assert result.reason == "genesis_evaluation"

    def test_operator_pending_trade(self, router):
        result = router.select_model(
            "operator", "normal", {"has_pending_trade": True}
        )
        assert result.is_sonnet
        assert result.reason == "capital_commitment"

    def test_critic_plan_review(self, router):
        result = router.select_model("critic", "normal")
        assert result.is_sonnet
        assert result.reason == "plan_review"

    def test_strategist_plan_creation(self, router):
        result = router.select_model("strategist", "normal")
        assert result.is_sonnet
        assert result.reason == "plan_creation"

    def test_red_alert(self, router):
        result = router.select_model(
            "scout", "normal", {"alert_level": "red"}
        )
        assert result.is_sonnet
        assert result.reason == "crisis_mode"

    def test_circuit_breaker(self, router):
        result = router.select_model(
            "operator", "normal", {"alert_level": "circuit_breaker"}
        )
        assert result.is_sonnet
        assert result.reason == "crisis_mode"

    def test_retry_escalation(self, router):
        result = router.select_model("scout", "retry")
        assert result.is_sonnet
        assert result.reason == "retry_escalation"


class TestHaikuRouting:
    """Test cases where Haiku should be selected."""

    def test_scout_normal(self, router):
        result = router.select_model("scout", "normal")
        assert not result.is_sonnet
        assert result.reason == "routine"

    def test_operator_no_pending_trade(self, router):
        result = router.select_model(
            "operator", "normal", {"has_pending_trade": False}
        )
        assert not result.is_sonnet
        assert result.reason == "routine"

    def test_reflection_cycle(self, router):
        result = router.select_model("scout", "reflection")
        assert not result.is_sonnet
        assert result.reason == "routine"

    def test_genesis_non_evaluation(self, router):
        result = router.select_model("genesis", "normal")
        assert not result.is_sonnet
        assert result.reason == "routine"

    def test_yellow_alert(self, router):
        result = router.select_model(
            "scout", "normal", {"alert_level": "yellow"}
        )
        assert not result.is_sonnet
        assert result.reason == "routine"

    def test_green_alert(self, router):
        result = router.select_model(
            "operator", "normal", {"alert_level": "green", "has_pending_trade": False}
        )
        assert not result.is_sonnet

    def test_no_context(self, router):
        result = router.select_model("scout", "normal", None)
        assert not result.is_sonnet


class TestEstimateCycleCost:
    def test_haiku_cost(self, router):
        cost = router.estimate_cycle_cost(
            "claude-haiku-4-5-20251001", 3000, 500
        )
        # 3000/1M * 1.0 + 500/1M * 5.0 = 0.003 + 0.0025 = 0.0055
        assert cost == pytest.approx(0.0055, abs=0.0001)

    def test_sonnet_cost(self, router):
        cost = router.estimate_cycle_cost(
            "claude-sonnet-4-20250514", 3000, 500
        )
        # 3000/1M * 3.0 + 500/1M * 15.0 = 0.009 + 0.0075 = 0.0165
        assert cost == pytest.approx(0.0165, abs=0.0001)

    def test_haiku_much_cheaper(self, router):
        haiku = router.estimate_cycle_cost("claude-haiku-4-5-20251001", 3000, 500)
        sonnet = router.estimate_cycle_cost("claude-sonnet-4-20250514", 3000, 500)
        assert haiku < sonnet * 0.5  # Haiku should be significantly cheaper


class TestKillSwitch:
    def test_routing_disabled_returns_sonnet(self, router):
        with patch("src.agents.model_router.config") as mock_config:
            mock_config.model_routing_enabled = False
            mock_config.model_sonnet = "claude-sonnet-4-20250514"
            result = router.select_model("scout", "normal")
            assert result.is_sonnet
            assert result.reason == "routing_disabled"

    def test_routing_enabled_returns_haiku_for_scout(self, router):
        with patch("src.agents.model_router.config") as mock_config:
            mock_config.model_routing_enabled = True
            mock_config.model_default = "claude-haiku-4-5-20251001"
            mock_config.model_sonnet = "claude-sonnet-4-20250514"
            result = router.select_model("scout", "normal")
            assert not result.is_sonnet
