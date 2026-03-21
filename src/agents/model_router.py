"""
Project Syndicate — Model Router

Deterministic routing engine that selects which Claude model handles each
thinking cycle. Haiku for routine work, Sonnet for high-stakes decisions.
No AI involved — pure code logic.
"""

__version__ = "0.1.0"

import logging
from dataclasses import dataclass

from src.common.config import config

logger = logging.getLogger(__name__)


@dataclass
class ModelSelection:
    """Result of model routing decision."""
    model_id: str
    reason: str

    @property
    def is_sonnet(self) -> bool:
        return "sonnet" in self.model_id

    @property
    def cost_multiplier(self) -> float:
        """Cost relative to Sonnet. Haiku ~ 0.33x."""
        return 1.0 if self.is_sonnet else 0.33


class ModelRouter:
    """Deterministic model selection for agent thinking cycles.

    Routes routine cycles to Haiku 4.5 ($1/$5 per M tokens) and
    reserves Sonnet ($3/$15) for high-stakes decisions where
    maximum reasoning quality matters.
    """

    def select_model(
        self,
        agent_role: str,
        cycle_type: str,
        context: dict | None = None,
    ) -> ModelSelection:
        """Determine which model should handle this thinking cycle.

        Args:
            agent_role: Agent role (scout, strategist, critic, operator, genesis).
            cycle_type: Cycle type (normal, reflection, retry, evaluation, survival).
            context: Optional dict with has_pending_trade, alert_level, etc.

        Returns:
            ModelSelection with model_id, reason, and cost properties.
        """
        # Kill switch: if routing disabled, always use Sonnet
        if not config.model_routing_enabled:
            return ModelSelection(config.model_sonnet, "routing_disabled")

        ctx = context or {}

        # --- ALWAYS SONNET (high-stakes decisions) ---

        # 1. Genesis evaluation cycles — deciding agent life/death/reproduction
        if agent_role == "genesis" and cycle_type == "evaluation":
            return ModelSelection(config.model_sonnet, "genesis_evaluation")

        # 2. Operator cycles with pending trade execution
        if agent_role == "operator" and ctx.get("has_pending_trade"):
            return ModelSelection(config.model_sonnet, "capital_commitment")

        # 3. Critic stress-test cycles — reviewing plans before capital risk
        if agent_role == "critic" and cycle_type == "normal":
            return ModelSelection(config.model_sonnet, "plan_review")

        # 4. Strategist plan creation cycles
        if agent_role == "strategist" and cycle_type == "normal":
            return ModelSelection(config.model_sonnet, "plan_creation")

        # 5. Crisis: Red alert or Circuit Breaker — max intelligence
        if ctx.get("alert_level") in ("red", "circuit_breaker"):
            return ModelSelection(config.model_sonnet, "crisis_mode")

        # 6. Retry cycles — first attempt failed, escalate for repair
        if cycle_type == "retry":
            return ModelSelection(config.model_sonnet, "retry_escalation")

        # --- HAIKU FOR EVERYTHING ELSE ---
        # Scout routine scans, Operator position monitoring (no pending trade),
        # Reflection cycles, normal/yellow alert conditions,
        # Genesis routine cycles (non-evaluation)

        return ModelSelection(config.model_default, "routine")

    def estimate_cycle_cost(
        self,
        model: str,
        estimated_input_tokens: int,
        estimated_output_tokens: int,
    ) -> float:
        """Calculate estimated cost for budget gate checks.

        Args:
            model: Model ID string.
            estimated_input_tokens: Expected input token count.
            estimated_output_tokens: Expected output token count.

        Returns:
            Estimated cost in USD.
        """
        if "haiku" in model:
            input_rate = config.haiku_input_price
            output_rate = config.haiku_output_price
        else:
            input_rate = config.sonnet_input_price
            output_rate = config.sonnet_output_price

        return (
            (estimated_input_tokens / 1_000_000) * input_rate
            + (estimated_output_tokens / 1_000_000) * output_rate
        )
