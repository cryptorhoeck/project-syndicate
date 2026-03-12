"""Tests for Dynamic Identity Builder — Phase 3E."""

import pytest

from src.personality.identity_builder import (
    DynamicIdentityBuilder, _BLOCKED_LABEL_WORDS, extract_evaluation_facts,
)


# --- Tier detection ---

def test_new_agent_identity():
    """New agents (low cycle count) get minimal identity."""
    builder = DynamicIdentityBuilder()
    result = builder.build_identity_section(
        name="Alpha-1", role="operator", generation=1,
        cycle_count=5, reputation_score=50.0,
        prestige_title=None, evaluation_count=0,
        probation=False,
    )

    assert "Alpha-1" in result
    assert "operator" in result
    assert "new" in result.lower() or "learn" in result.lower()


def test_established_agent_identity():
    """Established agents (30-99 cycles) get metrics in identity."""
    builder = DynamicIdentityBuilder()
    result = builder.build_identity_section(
        name="Beta-1", role="scout", generation=2,
        cycle_count=50, reputation_score=75.0,
        prestige_title="Promising", evaluation_count=3,
        probation=False,
        strongest_metric_name="signal_quality",
        strongest_metric_value=0.8,
        weakest_metric_name="intel_conversion",
        weakest_metric_value=0.2,
        long_term_memory_count=5,
    )

    assert "Beta-1" in result
    assert "scout" in result
    assert "75" in result  # reputation score
    assert "signal quality" in result.lower()
    assert "intel conversion" in result.lower()
    assert "5 lessons" in result.lower()


def test_veteran_agent_identity():
    """Veteran agents (100+ cycles) get rich factual identity."""
    builder = DynamicIdentityBuilder()
    result = builder.build_identity_section(
        name="Gamma-1", role="operator", generation=3,
        cycle_count=150, reputation_score=90.0,
        prestige_title="Elite", evaluation_count=10,
        probation=False,
        strongest_metric_name="sharpe",
        strongest_metric_value=1.5,
        strongest_metric_prev=1.2,
        weakest_metric_name="thinking_efficiency",
        weakest_metric_value=0.3,
        recent_trade_facts=["3 of 5 recent BTC trades hit stop-loss"],
    )

    assert "Gamma-1" in result
    assert "Elite" in result
    assert "10 evaluations" in result.lower()
    assert "stop-loss" in result.lower()
    assert "sharpe" in result.lower()


# --- Probation appendage ---

def test_probation_warning_appended():
    """Probation agents get WARNING in identity."""
    builder = DynamicIdentityBuilder()
    result = builder.build_identity_section(
        name="Delta-1", role="operator", generation=1,
        cycle_count=60, reputation_score=30.0,
        prestige_title=None, evaluation_count=2,
        probation=True,
        probation_warning="You failed your last survival check.",
        probation_days_left=5,
        weakest_metric_name="true_pnl",
        weakest_metric_value=0.1,
    )

    assert "PROBATION" in result
    assert "5 days" in result
    assert "true pnl" in result.lower()


# --- Metric trend direction ---

def test_metric_trend_shows_direction():
    """Metric facts show improvement or decline direction."""
    builder = DynamicIdentityBuilder()
    result = builder.build_identity_section(
        name="Echo-1", role="scout", generation=1,
        cycle_count=80, reputation_score=60.0,
        prestige_title=None, evaluation_count=4,
        probation=False,
        strongest_metric_name="signal_quality",
        strongest_metric_value=0.8,
        strongest_metric_prev=0.6,
    )

    assert "improved" in result.lower()
    assert "0.60" in result
    assert "0.80" in result


# --- Facts not labels validation ---

def test_no_blocked_label_words_in_output():
    """Identity output should never contain personality label words."""
    builder = DynamicIdentityBuilder()
    # None of the standard inputs should produce label words
    result = builder.build_identity_section(
        name="Foxtrot-1", role="operator", generation=2,
        cycle_count=120, reputation_score=85.0,
        prestige_title=None, evaluation_count=8,
        probation=False,
        strongest_metric_name="sharpe",
        strongest_metric_value=1.2,
        weakest_metric_name="true_pnl",
        weakest_metric_value=0.15,
    )

    result_lower = result.lower()
    for word in _BLOCKED_LABEL_WORDS:
        assert word not in result_lower, f"Blocked label word '{word}' found in identity"


def test_blocked_label_words_are_comprehensive():
    """Blocked words set should contain key personality labels."""
    required = {"conservative", "aggressive", "reckless", "resilient", "fragile"}
    assert required.issubset(_BLOCKED_LABEL_WORDS)


# --- extract_evaluation_facts ---

def test_extract_evaluation_facts_from_scorecard():
    """Extract strongest/weakest metrics from evaluation scorecard."""
    scorecard = {
        "metrics": {
            "sharpe": {"raw": 1.5, "normalized": 0.9},
            "true_pnl": {"raw": -0.05, "normalized": 0.1},
            "thinking_efficiency": {"raw": 0.6, "normalized": 0.5},
        }
    }

    facts = extract_evaluation_facts(scorecard)

    assert facts["strongest_metric_name"] == "sharpe"
    assert facts["strongest_metric_value"] == 0.9
    assert facts["weakest_metric_name"] == "true_pnl"
    assert facts["weakest_metric_value"] == 0.1


def test_extract_evaluation_facts_empty_scorecard():
    """Empty or None scorecard returns empty dict."""
    assert extract_evaluation_facts(None) == {}
    assert extract_evaluation_facts({}) == {}
    assert extract_evaluation_facts({"metrics": {}}) == {}
