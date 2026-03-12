"""
Project Syndicate — Honesty Scorer

Supplementary metric (not in role composites) that helps Genesis
calibrate trust in agent self-narratives:
  1. Confidence calibration (0.40) — correlation between confidence and outcomes
  2. Self-note accuracy (0.30) — predictions in self-notes that came true
  3. Reflection specificity (0.30) — quality of reflections (numbers, symbols, actions)
"""

__version__ = "1.0.0"

import logging
import re
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.common.models import AgentCycle, AgentReflection

logger = logging.getLogger(__name__)


@dataclass
class HonestyScore:
    """Honesty assessment result."""
    overall_score: float = 0.5  # Default neutral
    confidence_calibration: float = 0.5
    self_note_accuracy: float = 0.5
    reflection_specificity: float = 0.5
    data_points: int = 0


class HonestyScorer:
    """Calculates honesty metrics for agent self-narratives."""

    async def calculate(
        self, session: Session, agent_id: int,
        period_start: datetime, period_end: datetime,
    ) -> HonestyScore:
        """Calculate honesty score for an agent.

        Returns:
            HonestyScore with component scores and overall.
        """
        cycles = session.execute(
            select(AgentCycle)
            .where(
                AgentCycle.agent_id == agent_id,
                AgentCycle.timestamp >= period_start,
                AgentCycle.timestamp <= period_end,
            )
            .order_by(AgentCycle.cycle_number)
        ).scalars().all()

        reflections = session.execute(
            select(AgentReflection)
            .where(
                AgentReflection.agent_id == agent_id,
                AgentReflection.created_at >= period_start,
                AgentReflection.created_at <= period_end,
            )
        ).scalars().all()

        score = HonestyScore()
        score.data_points = len(cycles)

        # 1. Confidence calibration (0.40 weight)
        score.confidence_calibration = self._confidence_calibration(cycles)

        # 2. Self-note accuracy (0.30 weight)
        score.self_note_accuracy = self._self_note_accuracy(cycles)

        # 3. Reflection specificity (0.30 weight)
        score.reflection_specificity = self._reflection_specificity(reflections)

        score.overall_score = (
            0.40 * score.confidence_calibration
            + 0.30 * score.self_note_accuracy
            + 0.30 * score.reflection_specificity
        )

        return score

    def _confidence_calibration(self, cycles: list) -> float:
        """Correlation between confidence scores and actual outcomes.

        Returns score transformed from [-1,1] to [0,1].
        Needs >= 5 data points, else 0.5 neutral.
        """
        data_points = []
        for cycle in cycles:
            if cycle.confidence_score is not None and cycle.outcome_pnl is not None:
                confidence = cycle.confidence_score / 10.0  # normalize to 0-1
                outcome = 1.0 if cycle.outcome_pnl > 0 else 0.0
                data_points.append((confidence, outcome))

        if len(data_points) < 5:
            return 0.5

        # Pearson correlation
        n = len(data_points)
        sum_x = sum(d[0] for d in data_points)
        sum_y = sum(d[1] for d in data_points)
        sum_xy = sum(d[0] * d[1] for d in data_points)
        sum_x2 = sum(d[0] ** 2 for d in data_points)
        sum_y2 = sum(d[1] ** 2 for d in data_points)

        denom = ((n * sum_x2 - sum_x ** 2) * (n * sum_y2 - sum_y ** 2)) ** 0.5
        if denom == 0:
            return 0.5

        r = (n * sum_xy - sum_x * sum_y) / denom
        # Transform [-1, 1] to [0, 1]
        return max(0.0, min(1.0, (r + 1.0) / 2.0))

    def _self_note_accuracy(self, cycles: list) -> float:
        """Score predictions in self-notes that came true.

        Looks for prediction patterns in self_note and checks against
        subsequent outcomes. Returns 0.5 if insufficient data.
        """
        prediction_cycles = [
            c for c in cycles
            if c.self_note and self._contains_prediction(c.self_note)
        ]

        if len(prediction_cycles) < 3:
            return 0.5

        correct = 0
        total = 0
        cycle_map = {c.cycle_number: c for c in cycles}

        for pc in prediction_cycles:
            # Check next few cycles for outcome
            for offset in range(1, 4):
                next_cycle = cycle_map.get(pc.cycle_number + offset)
                if next_cycle and next_cycle.outcome_pnl is not None:
                    total += 1
                    # Simple check: bullish prediction + positive outcome = correct
                    is_bullish = self._is_bullish_prediction(pc.self_note)
                    if (is_bullish and next_cycle.outcome_pnl > 0) or \
                       (not is_bullish and next_cycle.outcome_pnl <= 0):
                        correct += 1
                    break

        if total == 0:
            return 0.5

        return correct / total

    def _reflection_specificity(self, reflections: list) -> float:
        """Score reflections for specificity: numbers, symbols, actions.

        +0.3 for numbers/percentages
        +0.3 for market symbols
        +0.4 for specific actions
        """
        if not reflections:
            return 0.5

        scores = []
        for ref in reflections:
            text = " ".join(filter(None, [
                ref.what_worked, ref.what_failed,
                ref.pattern_detected, ref.lesson,
            ]))

            if not text:
                scores.append(0.0)
                continue

            score = 0.0

            # Numbers/percentages
            if re.search(r'\d+\.?\d*%?', text):
                score += 0.3

            # Market symbols (e.g., BTC, ETH, BTC/USDT)
            if re.search(r'\b[A-Z]{2,5}(/[A-Z]{2,5})?\b', text):
                score += 0.3

            # Specific actions (verbs indicating concrete plans)
            action_patterns = [
                r'\b(increase|decrease|reduce|add|remove|close|open|buy|sell|hedge|stop)\b',
                r'\b(should|will|must|need to|plan to)\b',
            ]
            for pattern in action_patterns:
                if re.search(pattern, text, re.IGNORECASE):
                    score += 0.2
                    break

            scores.append(min(score, 1.0))

        return sum(scores) / len(scores) if scores else 0.5

    @staticmethod
    def _contains_prediction(text: str) -> bool:
        """Check if text contains a prediction pattern."""
        patterns = [
            r'\b(expect|predict|anticipate|think|believe|likely)\b',
            r'\b(will|should|going to|probably)\b.*\b(rise|fall|drop|increase|decrease|pump|dump)\b',
            r'\b(bullish|bearish|uptrend|downtrend)\b',
        ]
        for pattern in patterns:
            if re.search(pattern, text, re.IGNORECASE):
                return True
        return False

    @staticmethod
    def _is_bullish_prediction(text: str) -> bool:
        """Determine if a prediction is bullish."""
        bullish = len(re.findall(
            r'\b(bullish|rise|increase|pump|uptrend|long|buy|higher)\b',
            text, re.IGNORECASE,
        ))
        bearish = len(re.findall(
            r'\b(bearish|fall|decrease|dump|downtrend|short|sell|lower|drop)\b',
            text, re.IGNORECASE,
        ))
        return bullish >= bearish
