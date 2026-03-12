"""
Project Syndicate — Output Validator

Phase 3 (VALIDATE) of the OODA loop.
Validates agent output: JSON parsing, schema check, action space check,
Warden pre-check, sanity checks. One retry for malformed JSON (double tax).
"""

__version__ = "0.8.0"

import enum
import json
import logging
from dataclasses import dataclass

import jsonschema

from src.agents.roles import (
    NORMAL_OUTPUT_SCHEMA,
    REFLECTION_OUTPUT_SCHEMA,
    get_action_names,
)

logger = logging.getLogger(__name__)


class ValidationFailure(enum.Enum):
    """Types of validation failure."""
    MALFORMED_JSON = "malformed_json"
    INVALID_SCHEMA = "invalid_schema"
    INVALID_ACTION = "invalid_action"
    WARDEN_REJECTED = "warden_rejected"
    SANITY_FAILURE = "sanity_failure"


@dataclass
class ValidationResult:
    """Result of output validation."""
    passed: bool
    parsed: dict | None = None
    failure_type: ValidationFailure | None = None
    failure_detail: str = ""
    retryable: bool = False


class OutputValidator:
    """Validates Claude API output from agent thinking cycles.

    Pipeline:
        1. JSON parse
        2. Schema validation
        3. Action space check
        4. Warden pre-check (for trades)
        5. Sanity check
    """

    def __init__(self, warden=None):
        """
        Args:
            warden: Optional Warden instance for trade pre-checks.
        """
        self.warden = warden

    def validate(
        self,
        agent_type: str,
        raw_output: str,
        cycle_type: str = "normal",
        agent_capital: float = 0.0,
    ) -> ValidationResult:
        """Validate the raw output from a Claude API call.

        Args:
            agent_type: The agent's role (scout, strategist, etc.)
            raw_output: Raw string output from the API.
            cycle_type: "normal" or "reflection"
            agent_capital: Agent's current capital for sanity checks.

        Returns:
            ValidationResult with parsed output or failure details.
        """
        # Step 1: JSON Parse
        try:
            # Strip markdown code fences if present
            clean = raw_output.strip()
            if clean.startswith("```"):
                lines = clean.split("\n")
                # Remove first and last lines (code fences)
                if lines[0].startswith("```"):
                    lines = lines[1:]
                if lines and lines[-1].strip() == "```":
                    lines = lines[:-1]
                clean = "\n".join(lines)
            parsed = json.loads(clean)
        except (json.JSONDecodeError, ValueError) as e:
            return ValidationResult(
                passed=False,
                failure_type=ValidationFailure.MALFORMED_JSON,
                failure_detail=f"JSON parse error: {e}",
                retryable=True,
            )

        # Step 2: Schema Validation
        schema = REFLECTION_OUTPUT_SCHEMA if cycle_type == "reflection" else NORMAL_OUTPUT_SCHEMA
        try:
            jsonschema.validate(parsed, schema)
        except jsonschema.ValidationError as e:
            return ValidationResult(
                passed=False,
                failure_type=ValidationFailure.INVALID_SCHEMA,
                failure_detail=f"Schema error: {e.message}",
                retryable=True,
            )

        # For reflection cycles, schema is all we need
        if cycle_type == "reflection":
            return ValidationResult(passed=True, parsed=parsed)

        # Step 3: Action Space Check
        action_type = parsed.get("action", {}).get("type", "")
        valid_actions = get_action_names(agent_type)
        if action_type not in valid_actions:
            return ValidationResult(
                passed=False,
                failure_type=ValidationFailure.INVALID_ACTION,
                failure_detail=f"Action '{action_type}' not in {agent_type} action space: {valid_actions}",
                retryable=False,  # hallucinated action = no retry
            )

        # Step 4: Warden Pre-Check (trade actions only)
        trade_actions = {"execute_trade", "adjust_position", "close_position", "hedge"}
        if action_type in trade_actions and self.warden:
            try:
                warden_result = self.warden.pre_check_action(parsed["action"])
                if warden_result.get("rejected"):
                    return ValidationResult(
                        passed=False,
                        failure_type=ValidationFailure.WARDEN_REJECTED,
                        failure_detail=f"Warden rejected: {warden_result.get('reason', 'unknown')}",
                        retryable=False,
                    )
            except Exception as e:
                logger.warning(f"Warden pre-check error: {e}")
                # Don't block on Warden errors — log and continue

        # Step 5: Sanity Check
        sanity_result = self._sanity_check(agent_type, parsed, agent_capital)
        if sanity_result:
            return ValidationResult(
                passed=False,
                failure_type=ValidationFailure.SANITY_FAILURE,
                failure_detail=sanity_result,
                retryable=False,
            )

        return ValidationResult(passed=True, parsed=parsed)

    def _sanity_check(self, agent_type: str, parsed: dict, agent_capital: float) -> str | None:
        """Run sanity checks on validated output. Returns error string or None."""
        action = parsed.get("action", {})
        action_type = action.get("type", "")
        params = action.get("params", {})

        # Check trade position size vs capital
        if action_type == "execute_trade" and agent_capital > 0:
            position_size = params.get("position_size_usd", 0)
            if isinstance(position_size, (int, float)) and position_size > agent_capital:
                return (
                    f"Position size ${position_size:.2f} exceeds agent capital "
                    f"${agent_capital:.2f}"
                )

        # Check confidence score range (already validated by schema, but extra safety)
        conf = parsed.get("confidence", {}).get("score")
        if conf is not None and not (1 <= conf <= 10):
            return f"Confidence score {conf} out of range [1, 10]"

        return None

    def build_repair_prompt(self, raw_output: str, failure_detail: str) -> str:
        """Build a repair prompt for retrying after malformed output.

        Args:
            raw_output: The original failed output.
            failure_detail: What went wrong.

        Returns:
            A repair prompt string.
        """
        # Truncate raw output to avoid huge prompts
        truncated = raw_output[:1000] if len(raw_output) > 1000 else raw_output
        return (
            f"Your output was not valid. Error: {failure_detail}\n\n"
            f"Here's what you sent:\n{truncated}\n\n"
            f"Respond with corrected JSON only. No other text."
        )
