"""
Project Syndicate — Budget Gate

Pre-cycle check: Can the agent afford to think?
Determines NORMAL / SURVIVAL_MODE / SKIP_CYCLE before any API call.
"""

__version__ = "0.8.0"

import enum
import logging
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.orm import Session

from src.common.models import Agent, AgentCycle

logger = logging.getLogger(__name__)


class BudgetStatus(enum.Enum):
    """Result of a budget check."""
    NORMAL = "normal"
    SURVIVAL_MODE = "survival"
    SKIP_CYCLE = "skip"


@dataclass
class BudgetCheckResult:
    """Full result from a budget gate check."""
    status: BudgetStatus
    remaining_budget: float
    estimated_cost: float
    reason: str


class BudgetGate:
    """Pre-cycle gate that checks whether an agent can afford to think.

    Uses a rolling average of the last N cycles to estimate cost.
    If budget is exhausted → SKIP_CYCLE.
    If budget is low (< 3x estimated cost) → SURVIVAL_MODE.
    Otherwise → NORMAL.
    """

    # Default cost estimate when no history exists (Claude Sonnet ~$0.003 per call)
    DEFAULT_ESTIMATED_COST: float = 0.005
    ROLLING_WINDOW: int = 20

    def __init__(self, db_session: Session, agora_service=None):
        self.db = db_session
        self.agora = agora_service

    def _get_rolling_avg_cost(self, agent_id: int) -> float:
        """Calculate rolling average API cost from recent cycles."""
        recent_cycles = (
            self.db.query(AgentCycle.api_cost_usd)
            .filter(AgentCycle.agent_id == agent_id, AgentCycle.api_cost_usd > 0)
            .order_by(AgentCycle.id.desc())
            .limit(self.ROLLING_WINDOW)
            .all()
        )
        if not recent_cycles:
            return self.DEFAULT_ESTIMATED_COST
        costs = [c[0] for c in recent_cycles]
        return sum(costs) / len(costs)

    def check(self, agent: Agent) -> BudgetCheckResult:
        """Check whether the agent can afford to run a thinking cycle.

        Args:
            agent: The agent to check.

        Returns:
            BudgetCheckResult with status, remaining budget, and estimated cost.
        """
        estimated_cost = self._get_rolling_avg_cost(agent.id)
        remaining = agent.thinking_budget_daily - agent.thinking_budget_used_today

        # Budget exhausted
        if remaining < estimated_cost:
            logger.warning(
                "budget_exhausted",
                extra={"agent_id": agent.id, "agent_name": agent.name,
                       "remaining": remaining, "estimated_cost": estimated_cost},
            )
            # Broadcast resource_critical to Agora
            if self.agora:
                try:
                    self.agora.post_system_message(
                        channel="system-alerts",
                        content=(
                            f"{agent.name} budget exhausted — "
                            f"${remaining:.4f} remaining, need ${estimated_cost:.4f}. "
                            f"Consider hibernation."
                        ),
                        metadata={"event": "resource_critical", "agent_id": agent.id},
                    )
                except Exception:
                    logger.debug("Failed to broadcast resource_critical to Agora")

            return BudgetCheckResult(
                status=BudgetStatus.SKIP_CYCLE,
                remaining_budget=remaining,
                estimated_cost=estimated_cost,
                reason="budget_exhausted",
            )

        # Budget low — survival mode
        if remaining < (estimated_cost * 3):
            logger.info(
                "budget_low_survival_mode",
                extra={"agent_id": agent.id, "agent_name": agent.name,
                       "remaining": remaining, "threshold": estimated_cost * 3},
            )
            return BudgetCheckResult(
                status=BudgetStatus.SURVIVAL_MODE,
                remaining_budget=remaining,
                estimated_cost=estimated_cost,
                reason="budget_low",
            )

        # Normal operation
        return BudgetCheckResult(
            status=BudgetStatus.NORMAL,
            remaining_budget=remaining,
            estimated_cost=estimated_cost,
            reason="ok",
        )
