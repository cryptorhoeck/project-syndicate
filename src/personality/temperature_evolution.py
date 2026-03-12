"""
Project Syndicate — Temperature Evolution Engine (Phase 3E)

Agent thinking style drifts based on performance-creativity correlation.
Maximum ±0.05 per evaluation with 2-eval momentum requirement.
"""

__version__ = "1.1.0"

import logging
import math
from collections import Counter
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.common.config import config
from src.common.models import Agent, AgentCycle

logger = logging.getLogger(__name__)


@dataclass
class TemperatureResult:
    """Result of temperature evolution computation."""
    old_temp: float
    new_temp: float
    signal: int  # -1, 0, +1
    changed: bool
    reasoning: str


class TemperatureEvolution:
    """Evolves agent API temperature based on diversity-profitability correlation."""

    TEMPERATURE_BOUNDS: dict[str, tuple[float, float]] = {
        "scout": (0.3, 0.9),
        "strategist": (0.2, 0.7),
        "critic": (0.1, 0.4),
        "operator": (0.1, 0.4),
    }

    def __init__(self) -> None:
        self.log = logger

    async def evolve(
        self,
        session: Session,
        agent: Agent,
        period_start: datetime,
        period_end: datetime,
    ) -> TemperatureResult:
        """Evolve agent temperature based on evaluation period data."""
        role = agent.type
        bounds = self._get_bounds(role)
        old_temp = agent.api_temperature
        if old_temp is None:
            # Use role default
            defaults = {
                "scout": config.scout_temperature,
                "strategist": config.strategist_temperature,
                "critic": config.critic_temperature,
                "operator": config.operator_temperature,
            }
            old_temp = defaults.get(role, 0.5)

        # 1. Compute action diversity for the period
        diversity = self._compute_action_diversity(session, agent.id, period_start, period_end)

        # 2. Correlate diversity with profitability
        correlation = self._correlate_diversity_profit(
            session, agent.id, period_start, period_end,
        )

        # 3. Determine signal
        threshold = config.temperature_signal_threshold
        if correlation > threshold:
            signal = 1  # exploration helps → drift warmer
        elif correlation < -threshold:
            signal = -1  # focus helps → drift cooler
        else:
            signal = 0

        # 4. Check momentum — same signal for 2 consecutive evals
        last_signal = agent.last_temperature_signal or 0
        drift = config.temperature_drift_amount
        new_temp = old_temp
        changed = False
        reasoning = ""

        if signal == last_signal and signal != 0:
            new_temp = old_temp + signal * drift
            changed = True
            direction = "warmer" if signal > 0 else "cooler"
            reasoning = (
                f"Diversity-profit correlation={correlation:.3f} "
                f"(signal={signal}) confirmed for 2 consecutive evals → "
                f"drift {direction} by {drift}"
            )
        elif signal != 0:
            reasoning = (
                f"Diversity-profit correlation={correlation:.3f} "
                f"(signal={signal}), waiting for momentum confirmation"
            )
        else:
            reasoning = (
                f"Diversity-profit correlation={correlation:.3f} "
                f"— no clear signal, temperature unchanged"
            )

        # 5. Clamp to role bounds
        new_temp = max(bounds[0], min(bounds[1], new_temp))

        # 6. Update agent
        agent.api_temperature = new_temp
        agent.last_temperature_signal = signal

        # 7. Record in temperature history
        history = agent.temperature_history or []
        history.append({
            "old_temp": round(old_temp, 3),
            "new_temp": round(new_temp, 3),
            "signal": signal,
            "correlation": round(correlation, 4),
            "diversity": round(diversity, 4),
            "changed": changed,
            "timestamp": period_end.isoformat(),
        })
        agent.temperature_history = history
        session.add(agent)

        self.log.info(
            "temperature_evolved",
            extra={
                "agent_id": agent.id,
                "old_temp": round(old_temp, 3),
                "new_temp": round(new_temp, 3),
                "signal": signal,
                "changed": changed,
            },
        )

        return TemperatureResult(
            old_temp=round(old_temp, 3),
            new_temp=round(new_temp, 3),
            signal=signal,
            changed=changed,
            reasoning=reasoning,
        )

    def _get_bounds(self, role: str) -> tuple[float, float]:
        """Get temperature bounds from config or defaults."""
        bounds_map = {
            "scout": config.temperature_bounds_scout,
            "strategist": config.temperature_bounds_strategist,
            "critic": config.temperature_bounds_critic,
            "operator": config.temperature_bounds_operator,
        }
        bounds = bounds_map.get(role, [0.1, 0.9])
        return (bounds[0], bounds[1])

    def _compute_action_diversity(
        self,
        session: Session,
        agent_id: int,
        period_start: datetime,
        period_end: datetime,
    ) -> float:
        """Compute Shannon entropy of action types in the period."""
        cycles = session.execute(
            select(AgentCycle).where(
                AgentCycle.agent_id == agent_id,
                AgentCycle.timestamp >= period_start,
                AgentCycle.timestamp <= period_end,
                AgentCycle.action_type.isnot(None),
            )
        ).scalars().all()

        if len(cycles) < 3:
            return 0.0

        # Count distinct action+market combinations
        action_keys: Counter[str] = Counter()
        for c in cycles:
            params = c.action_params or {}
            market = params.get("symbol", params.get("market", ""))
            key = f"{c.action_type}:{market}" if market else c.action_type
            action_keys[key] += 1

        total = sum(action_keys.values())
        if total == 0:
            return 0.0

        probabilities = [c / total for c in action_keys.values()]
        entropy = -sum(p * math.log2(p) for p in probabilities if p > 0)
        max_entropy = math.log2(len(action_keys)) if len(action_keys) > 1 else 1.0

        return entropy / max_entropy if max_entropy > 0 else 0.0

    def _correlate_diversity_profit(
        self,
        session: Session,
        agent_id: int,
        period_start: datetime,
        period_end: datetime,
        window_size: int = 10,
    ) -> float:
        """Correlate action diversity with profitability in sliding windows."""
        cycles = session.execute(
            select(AgentCycle).where(
                AgentCycle.agent_id == agent_id,
                AgentCycle.timestamp >= period_start,
                AgentCycle.timestamp <= period_end,
                AgentCycle.action_type.isnot(None),
            ).order_by(AgentCycle.timestamp)
        ).scalars().all()

        if len(cycles) < window_size * 2:
            return 0.0  # Not enough data

        # Create windows
        diversities = []
        profitabilities = []

        for i in range(0, len(cycles) - window_size + 1, window_size // 2):
            window = cycles[i:i + window_size]

            # Diversity: unique action types in window
            action_types = set(c.action_type for c in window if c.action_type)
            diversity = len(action_types) / max(len(window), 1)

            # Profitability: sum of outcome_pnl in window
            pnl = sum(c.outcome_pnl or 0 for c in window)

            diversities.append(diversity)
            profitabilities.append(pnl)

        if len(diversities) < 3:
            return 0.0

        # Pearson correlation
        return self._pearson(diversities, profitabilities)

    @staticmethod
    def _pearson(x: list[float], y: list[float]) -> float:
        """Compute Pearson correlation coefficient."""
        n = len(x)
        if n < 3:
            return 0.0

        x_mean = sum(x) / n
        y_mean = sum(y) / n

        numerator = sum((xi - x_mean) * (yi - y_mean) for xi, yi in zip(x, y))
        denom_x = math.sqrt(sum((xi - x_mean) ** 2 for xi in x))
        denom_y = math.sqrt(sum((yi - y_mean) ** 2 for yi in y))

        if denom_x == 0 or denom_y == 0:
            return 0.0

        return numerator / (denom_x * denom_y)
