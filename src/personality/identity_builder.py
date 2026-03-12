"""
Project Syndicate — Dynamic Identity Builder (Phase 3E)

Builds evolving system prompt identity sections from FACTS, not labels.
Agents see what they've DONE, not what they ARE.

ARCHITECTURAL CONSTRAINT:
This module NEVER imports BehavioralProfile or accepts profile label fields.
It only accepts raw metrics, trend data, and factual observations.
"""

__version__ = "1.1.0"

import logging
from datetime import datetime, timezone

from src.common.config import config

logger = logging.getLogger(__name__)

# Blocked words that indicate personality labels, not facts
_BLOCKED_LABEL_WORDS = frozenset({
    "conservative", "aggressive", "reckless", "cautious", "impulsive",
    "reactive", "deliberate", "paralyzed", "fragile", "resilient",
    "antifragile", "stagnant", "adaptive", "independent", "cooperative",
    "dependent", "ultra_conservative", "shaky",
})


class DynamicIdentityBuilder:
    """Builds evolving identity sections from raw factual data.

    NEVER pass BehavioralProfile objects or label strings to this class.
    """

    def __init__(self) -> None:
        self.log = logger

    def build_identity_section(
        self,
        *,
        # Agent core data
        name: str,
        role: str,
        generation: int,
        cycle_count: int,
        reputation_score: float,
        prestige_title: str | None,
        evaluation_count: int,
        probation: bool,
        probation_warning: str | None = None,
        probation_days_left: int | None = None,
        # Evaluation metrics (raw values only)
        strongest_metric_name: str | None = None,
        strongest_metric_value: float | None = None,
        strongest_metric_prev: float | None = None,
        weakest_metric_name: str | None = None,
        weakest_metric_value: float | None = None,
        weakest_metric_prev: float | None = None,
        # Recent factual observations
        long_term_memory_count: int = 0,
        recent_trade_facts: list[str] | None = None,
    ) -> str:
        """Build identity section. Returns 2-4 lines of factual description."""
        tier = self._determine_tier(cycle_count)
        lines = []

        if tier == "new":
            lines.append(f"You are {name}, a {role} agent. Generation {generation}.")
            lines.append("You are new. Learn quickly. Your survival depends on it.")

        elif tier == "established":
            prestige = f" ({prestige_title})" if prestige_title else ""
            lines.append(
                f"You are {name}, a {role} agent. Generation {generation}. "
                f"Reputation: {reputation_score:.0f}{prestige}."
            )
            if long_term_memory_count > 0:
                lines.append(f"You have {long_term_memory_count} lessons from experience.")
            if strongest_metric_name and strongest_metric_value is not None:
                lines.append(
                    f"Strongest area: {self._format_metric_fact(strongest_metric_name, strongest_metric_value, strongest_metric_prev)}."
                )
            if weakest_metric_name and weakest_metric_value is not None:
                lines.append(
                    f"Area to improve: {self._format_metric_fact(weakest_metric_name, weakest_metric_value, weakest_metric_prev)}."
                )

        else:  # veteran
            prestige = f" ({prestige_title})" if prestige_title else ""
            lines.append(
                f"You are {name}, a {role} agent. Generation {generation}. "
                f"Reputation: {reputation_score:.0f}{prestige}. "
                f"Survived {evaluation_count} evaluations."
            )
            # Recent factual pattern
            if recent_trade_facts:
                facts_str = "; ".join(recent_trade_facts[:2])
                lines.append(f"Recent pattern: {facts_str}.")
            if strongest_metric_name and strongest_metric_value is not None:
                lines.append(
                    f"Your edge: {self._format_metric_fact(strongest_metric_name, strongest_metric_value, strongest_metric_prev)}."
                )
            if weakest_metric_name and weakest_metric_value is not None:
                lines.append(
                    f"Watch out for: {self._format_metric_fact(weakest_metric_name, weakest_metric_value, weakest_metric_prev)}."
                )

        # Probation appendage
        if probation:
            warning = probation_warning or "Improve your performance."
            days_text = f" You have {probation_days_left} days to improve or face termination." if probation_days_left else ""
            lines.append(
                f"WARNING: You are on PROBATION. {warning}{days_text}"
            )
            if weakest_metric_name:
                lines.append(f"Focus on: {weakest_metric_name.replace('_', ' ')}.")

        # Cap at 4 lines
        result = "\n".join(lines[:4])

        # Safety check: no personality labels leaked
        self._validate_no_labels(result)

        return result

    def _determine_tier(self, cycle_count: int) -> str:
        """Determine identity tier from cycle count."""
        if cycle_count < config.identity_new_threshold:
            return "new"
        elif cycle_count < config.identity_established_threshold:
            return "established"
        return "veteran"

    def _format_metric_fact(
        self,
        metric_name: str,
        value: float,
        previous_value: float | None = None,
    ) -> str:
        """Convert a metric into a factual statement, not a label."""
        display_name = metric_name.replace("_", " ")

        if previous_value is not None:
            direction = "improved" if value > previous_value else "declined"
            return (
                f"{display_name} {direction} from "
                f"{previous_value:.2f} to {value:.2f}"
            )
        return f"{display_name} at {value:.2f}"

    def _validate_no_labels(self, text: str) -> None:
        """Warn if blocked personality label words appear in output."""
        text_lower = text.lower()
        for word in _BLOCKED_LABEL_WORDS:
            if word in text_lower:
                self.log.warning(
                    "identity_contains_label_word",
                    extra={"word": word, "identity_text": text[:200]},
                )


def extract_evaluation_facts(scorecard: dict | None) -> dict:
    """Extract strongest/weakest metric facts from an evaluation scorecard.

    Returns dict with keys for DynamicIdentityBuilder kwargs.
    Only uses raw metric values, never labels.
    """
    if not scorecard:
        return {}

    metrics = scorecard.get("metrics", {})
    if not metrics:
        return {}

    strongest_name = None
    strongest_val = -float("inf")
    weakest_name = None
    weakest_val = float("inf")

    for name, data in metrics.items():
        if isinstance(data, dict):
            val = data.get("normalized", data.get("raw"))
        else:
            val = data
        if val is None:
            continue
        if val > strongest_val:
            strongest_val = val
            strongest_name = name
        if val < weakest_val:
            weakest_val = val
            weakest_name = name

    result: dict = {}
    if strongest_name is not None:
        result["strongest_metric_name"] = strongest_name
        result["strongest_metric_value"] = strongest_val
    if weakest_name is not None:
        result["weakest_metric_name"] = weakest_name
        result["weakest_metric_value"] = weakest_val

    return result
