"""Tests for the Output Validator module."""

__version__ = "0.7.0"

import json
import pytest

from src.agents.output_validator import OutputValidator, ValidationFailure


@pytest.fixture
def validator():
    return OutputValidator()


def _make_valid_output(**overrides):
    """Build a valid normal cycle output."""
    output = {
        "situation": "BTC consolidating near support with rising volume",
        "confidence": {"score": 7, "reasoning": "Strong volume pattern"},
        "recent_pattern": "Last 3 cycles were idle, market was choppy",
        "action": {
            "type": "broadcast_opportunity",
            "params": {
                "market": "BTC/USDT",
                "signal": "volume_breakout",
                "urgency": "medium",
                "details": "Volume 3x average at key support level",
            },
        },
        "reasoning": "Volume spike at support suggests accumulation phase ending",
        "self_note": "Watch BTC/USDT for confirmation in next cycle",
    }
    output.update(overrides)
    return json.dumps(output)


def _make_valid_reflection():
    """Build a valid reflection cycle output."""
    return json.dumps({
        "what_worked": "Signal timing improved after watching volume first",
        "what_failed": "Two false breakout calls this period",
        "pattern_detected": "I perform better with higher timeframe confirmation",
        "lesson": "Wait for 4h close above resistance, not just the wick",
        "confidence_trend": "improving",
        "confidence_reason": "Hit rate up from 40% to 60%",
        "strategy_note": "Focus on fewer, higher-confidence signals",
        "memory_promotion": ["SOL volatile during Asian session"],
        "memory_demotion": [],
    })


class TestValidJSON:
    def test_valid_output_passes(self, validator):
        result = validator.validate("scout", _make_valid_output())
        assert result.passed
        assert result.parsed is not None
        assert result.parsed["action"]["type"] == "broadcast_opportunity"

    def test_valid_reflection_passes(self, validator):
        result = validator.validate("scout", _make_valid_reflection(), cycle_type="reflection")
        assert result.passed
        assert result.parsed["confidence_trend"] == "improving"

    def test_valid_go_idle(self, validator):
        output = _make_valid_output(
            action={"type": "go_idle", "params": {"reason": "nothing to do"}}
        )
        result = validator.validate("scout", output)
        assert result.passed


class TestMalformedJSON:
    def test_invalid_json_fails_retryable(self, validator):
        result = validator.validate("scout", "not json at all")
        assert not result.passed
        assert result.failure_type == ValidationFailure.MALFORMED_JSON
        assert result.retryable

    def test_truncated_json_fails(self, validator):
        result = validator.validate("scout", '{"situation": "test"')
        assert not result.passed
        assert result.failure_type == ValidationFailure.MALFORMED_JSON

    def test_markdown_fenced_json_passes(self, validator):
        fenced = "```json\n" + _make_valid_output() + "\n```"
        result = validator.validate("scout", fenced)
        assert result.passed


class TestSchemaValidation:
    def test_missing_required_field(self, validator):
        output = json.dumps({"situation": "test"})  # missing most fields
        result = validator.validate("scout", output)
        assert not result.passed
        assert result.failure_type == ValidationFailure.INVALID_SCHEMA
        assert result.retryable

    def test_confidence_out_of_range(self, validator):
        output = _make_valid_output(confidence={"score": 15, "reasoning": "too high"})
        result = validator.validate("scout", output)
        assert not result.passed
        assert result.failure_type == ValidationFailure.INVALID_SCHEMA


class TestActionSpace:
    def test_invalid_action_type_not_retryable(self, validator):
        output = _make_valid_output(
            action={"type": "hack_the_planet", "params": {}}
        )
        result = validator.validate("scout", output)
        assert not result.passed
        assert result.failure_type == ValidationFailure.INVALID_ACTION
        assert not result.retryable  # hallucinated = no retry

    def test_strategist_cannot_execute_trade(self, validator):
        output = _make_valid_output(
            action={"type": "execute_trade", "params": {"market": "BTC/USDT", "direction": "long",
                     "order_type": "market", "position_size_usd": 100, "stop_loss": 50000,
                     "take_profit": 70000, "plan_id": 1}}
        )
        result = validator.validate("strategist", output)
        assert not result.passed
        assert result.failure_type == ValidationFailure.INVALID_ACTION

    def test_operator_can_execute_trade(self, validator):
        output = _make_valid_output(
            action={"type": "execute_trade", "params": {"market": "BTC/USDT", "direction": "long",
                     "order_type": "market", "position_size_usd": 100, "stop_loss": 50000,
                     "take_profit": 70000, "plan_id": 1}}
        )
        result = validator.validate("operator", output)
        assert result.passed


class TestSanityChecks:
    def test_position_exceeds_capital(self, validator):
        output = _make_valid_output(
            action={"type": "execute_trade", "params": {
                "market": "BTC/USDT", "direction": "long", "order_type": "market",
                "position_size_usd": 500.0, "stop_loss": 50000, "take_profit": 70000,
                "plan_id": 1,
            }}
        )
        result = validator.validate("operator", output, agent_capital=100.0)
        assert not result.passed
        assert result.failure_type == ValidationFailure.SANITY_FAILURE


class TestRepairPrompt:
    def test_repair_prompt_generated(self, validator):
        prompt = validator.build_repair_prompt(
            "bad json here", "JSON parse error"
        )
        assert "not valid" in prompt
        assert "bad json here" in prompt
