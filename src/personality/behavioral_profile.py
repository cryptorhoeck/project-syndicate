"""
Project Syndicate — Behavioral Profile Calculator (Phase 3E)

Auto-generated personality fingerprint from actual behavior.
Agents never see their own profile — it's for Genesis and the owner.
"""

__version__ = "1.1.0"

import math
import logging
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.common.config import config
from src.common.models import (
    Agent, AgentCycle, BehavioralProfile, Evaluation,
    MarketRegime, Position,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Classification helpers
# ---------------------------------------------------------------------------

def classify(score: float, thresholds: list[float], labels: list[str]) -> str:
    """Map a 0-1 score to a label using threshold boundaries."""
    for i, threshold in enumerate(thresholds):
        if score < threshold:
            return labels[i]
    return labels[-1]


RISK_LABELS = ["ultra_conservative", "conservative", "moderate", "aggressive", "reckless"]
RISK_THRESHOLDS = [0.2, 0.4, 0.6, 0.8]

DECISION_LABELS = ["impulsive", "reactive", "deliberate", "cautious", "paralyzed"]
DECISION_THRESHOLDS = [0.2, 0.4, 0.6, 0.8]

COLLABORATION_LABELS = ["independent", "cooperative", "dependent"]
COLLABORATION_THRESHOLDS = [0.35, 0.65]

LEARNING_LABELS = ["stagnant", "slow_learner", "steady", "fast_learner", "adaptive"]
LEARNING_THRESHOLDS = [0.2, 0.4, 0.6, 0.8]

RESILIENCE_LABELS = ["fragile", "shaky", "steady", "resilient", "antifragile"]
RESILIENCE_THRESHOLDS = [0.2, 0.4, 0.6, 0.8]

# Tier distance maps for drift detection
TIER_DISTANCES: dict[str, dict[str, int]] = {
    "risk_appetite": {l: i for i, l in enumerate(RISK_LABELS)},
    "decision_style": {l: i for i, l in enumerate(DECISION_LABELS)},
    "collaboration": {l: i for i, l in enumerate(COLLABORATION_LABELS)},
    "learning_velocity": {l: i for i, l in enumerate(LEARNING_LABELS)},
    "resilience": {l: i for i, l in enumerate(RESILIENCE_LABELS)},
}


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class TraitResult:
    """Result for a single behavioral trait."""
    score: float | None = None
    label: str | None = None
    has_data: bool = False


@dataclass
class ProfileResult:
    """Full behavioral profile result."""
    risk_appetite: TraitResult = field(default_factory=TraitResult)
    market_focus: TraitResult = field(default_factory=TraitResult)
    market_focus_data: dict | None = None
    timing_heatmap: dict | None = None
    decision_style: TraitResult = field(default_factory=TraitResult)
    collaboration: TraitResult = field(default_factory=TraitResult)
    learning_velocity: TraitResult = field(default_factory=TraitResult)
    resilience: TraitResult = field(default_factory=TraitResult)
    is_complete: bool = False
    dominant_regime: str | None = None
    regime_distribution: dict | None = None

    def raw_scores(self) -> dict[str, float | None]:
        """Return dict of trait name → numeric score."""
        return {
            "risk_appetite": self.risk_appetite.score,
            "market_focus_entropy": self.market_focus.score,
            "decision_style": self.decision_style.score,
            "collaboration": self.collaboration.score,
            "learning_velocity": self.learning_velocity.score,
            "resilience": self.resilience.score,
        }


@dataclass
class DriftFlag:
    """Personality drift detection result."""
    trait: str
    old_label: str
    new_label: str
    tier_distance: int


# ---------------------------------------------------------------------------
# Main calculator
# ---------------------------------------------------------------------------

class BehavioralProfileCalculator:
    """Computes behavioral profiles entirely from actual agent behavior."""

    def __init__(self) -> None:
        self.log = logger

    async def compute(
        self,
        session: Session,
        agent_id: int,
        evaluation_id: int | None = None,
    ) -> BehavioralProfile:
        """Compute full 7-trait profile and store in DB."""
        agent = session.get(Agent, agent_id)
        if agent is None:
            raise ValueError(f"Agent {agent_id} not found")

        result = ProfileResult()

        # Compute each trait
        result.risk_appetite = self._compute_risk_appetite(session, agent)
        mf_result, mf_data = self._compute_market_focus(session, agent)
        result.market_focus = mf_result
        result.market_focus_data = mf_data
        result.timing_heatmap = self._compute_timing_pattern(session, agent)
        result.decision_style = self._compute_decision_style(session, agent)
        result.collaboration = self._compute_collaboration(session, agent)
        result.learning_velocity = self._compute_learning_velocity(session, agent)
        result.resilience = self._compute_resilience(session, agent)

        # Regime context
        regime_info = self._compute_regime_context(session, agent)
        result.dominant_regime = regime_info.get("dominant")
        result.regime_distribution = regime_info.get("distribution")

        # Check completeness
        traits = [
            result.risk_appetite, result.market_focus,
            result.decision_style, result.collaboration,
            result.learning_velocity, result.resilience,
        ]
        result.is_complete = all(t.has_data for t in traits)

        # Store in DB
        profile = BehavioralProfile(
            agent_id=agent_id,
            evaluation_id=evaluation_id,
            risk_appetite_score=result.risk_appetite.score,
            risk_appetite_label=result.risk_appetite.label,
            market_focus_data=result.market_focus_data,
            market_focus_entropy=result.market_focus.score,
            timing_heatmap=result.timing_heatmap,
            decision_style_score=result.decision_style.score,
            decision_style_label=result.decision_style.label,
            collaboration_score=result.collaboration.score,
            collaboration_label=result.collaboration.label,
            learning_velocity_score=result.learning_velocity.score,
            learning_velocity_label=result.learning_velocity.label,
            resilience_score=result.resilience.score,
            resilience_label=result.resilience.label,
            raw_scores=result.raw_scores(),
            is_complete=result.is_complete,
            dominant_regime=result.dominant_regime,
            regime_distribution=result.regime_distribution,
        )
        session.add(profile)
        session.flush()

        # Update agent's latest profile pointer
        agent.behavioral_profile_id = profile.id
        session.add(agent)

        self.log.info(
            "behavioral_profile_computed",
            extra={"agent_id": agent_id, "complete": result.is_complete},
        )
        return profile

    # ------------------------------------------------------------------
    # Trait Computations
    # ------------------------------------------------------------------

    def _compute_risk_appetite(self, session: Session, agent: Agent) -> TraitResult:
        """From position sizes, stop tightness, post-loss idle rate."""
        if agent.type != "operator":
            return TraitResult()  # N/A for non-operators

        positions = session.execute(
            select(Position).where(
                Position.agent_id == agent.id,
                Position.status != "open",
            )
        ).scalars().all()

        if len(positions) < config.profile_min_positions:
            return TraitResult()

        capital = agent.capital_allocated or 100.0
        size_ratios = [p.size_usd / capital for p in positions if capital > 0]
        avg_size_ratio = sum(size_ratios) / len(size_ratios) if size_ratios else 0

        # Stop-loss tightness: how tight are stops relative to entry?
        stop_tightness_values = []
        for p in positions:
            if p.stop_loss and p.entry_price and p.entry_price > 0:
                tightness = abs(p.stop_loss - p.entry_price) / p.entry_price
                stop_tightness_values.append(tightness)

        avg_stop_tightness = (
            sum(stop_tightness_values) / len(stop_tightness_values)
            if stop_tightness_values else 0.05
        )

        # Post-loss idle rate
        losing_positions = [p for p in positions if (p.realized_pnl or 0) < 0]
        post_loss_idle = 0.0
        if losing_positions:
            # Count idle cycles immediately after losses
            loss_close_times = [p.closed_at for p in losing_positions if p.closed_at]
            idle_after_loss = 0
            total_after_loss = 0
            for close_time in loss_close_times:
                next_cycles = session.execute(
                    select(AgentCycle).where(
                        AgentCycle.agent_id == agent.id,
                        AgentCycle.timestamp > close_time,
                    ).order_by(AgentCycle.timestamp).limit(3)
                ).scalars().all()
                for c in next_cycles:
                    total_after_loss += 1
                    if c.action_type == "go_idle":
                        idle_after_loss += 1
            post_loss_idle = idle_after_loss / total_after_loss if total_after_loss > 0 else 0.5

        # Combine: high size ratio + loose stops + low post-loss idle = aggressive
        # Normalize each to 0-1, then average
        size_score = min(avg_size_ratio / 0.25, 1.0)  # 25% of capital = max
        stop_score = min(avg_stop_tightness / 0.10, 1.0)  # 10% stop = very tight (inverted)
        stop_score = 1.0 - stop_score  # Tight stops = conservative
        idle_score = 1.0 - post_loss_idle  # High idle after loss = conservative

        risk_score = (size_score * 0.4 + stop_score * 0.3 + idle_score * 0.3)
        risk_score = max(0.0, min(1.0, risk_score))
        label = classify(risk_score, RISK_THRESHOLDS, RISK_LABELS)

        return TraitResult(score=risk_score, label=label, has_data=True)

    def _compute_market_focus(
        self, session: Session, agent: Agent,
    ) -> tuple[TraitResult, dict | None]:
        """From action market distribution."""
        cycles = session.execute(
            select(AgentCycle).where(
                AgentCycle.agent_id == agent.id,
                AgentCycle.action_type != "go_idle",
                AgentCycle.action_type.isnot(None),
            )
        ).scalars().all()

        if len(cycles) < config.profile_min_cycles:
            return TraitResult(), None

        # Extract markets from action params
        market_counts: Counter[str] = Counter()
        for cycle in cycles:
            params = cycle.action_params or {}
            market = params.get("symbol") or params.get("market") or params.get("asset")
            if market:
                market_counts[market] += 1

        if not market_counts:
            return TraitResult(), None

        total = sum(market_counts.values())
        market_data = {
            m: {"count": c, "pct": round(c / total, 3)}
            for m, c in market_counts.most_common(10)
        }

        # Shannon entropy for focus score
        probabilities = [c / total for c in market_counts.values()]
        entropy = -sum(p * math.log2(p) for p in probabilities if p > 0)
        max_entropy = math.log2(len(market_counts)) if len(market_counts) > 1 else 1.0
        normalized_entropy = entropy / max_entropy if max_entropy > 0 else 0.0

        return (
            TraitResult(score=normalized_entropy, label=None, has_data=True),
            market_data,
        )

    def _compute_timing_pattern(self, session: Session, agent: Agent) -> dict | None:
        """Activity heatmap by hour-of-day, day-of-week."""
        cycles = session.execute(
            select(AgentCycle).where(AgentCycle.agent_id == agent.id)
        ).scalars().all()

        if len(cycles) < 50:
            return None

        # Check 3+ different days
        days = set()
        for c in cycles:
            if c.timestamp:
                days.add(c.timestamp.date())
        if len(days) < config.profile_min_cycle_days:
            return None

        # Build heatmap
        heatmap: dict[str, dict[str, int]] = {}
        for c in cycles:
            if c.timestamp:
                hour = str(c.timestamp.hour)
                day = str(c.timestamp.weekday())  # 0=Monday
                heatmap.setdefault(day, {})
                heatmap[day][hour] = heatmap[day].get(hour, 0) + 1

        return heatmap

    def _compute_decision_style(self, session: Session, agent: Agent) -> TraitResult:
        """From reasoning length, confidence distribution, idle-to-action ratio."""
        action_cycles = session.execute(
            select(AgentCycle).where(
                AgentCycle.agent_id == agent.id,
                AgentCycle.action_type.isnot(None),
            )
        ).scalars().all()

        if len(action_cycles) < config.profile_min_actions:
            return TraitResult()

        # Average reasoning length (proxy for deliberation)
        reasoning_lengths = [
            len(c.reasoning or "") for c in action_cycles
        ]
        avg_reasoning = sum(reasoning_lengths) / len(reasoning_lengths) if reasoning_lengths else 0

        # Confidence distribution — std dev
        confidences = [c.confidence_score for c in action_cycles if c.confidence_score is not None]
        if len(confidences) < 3:
            return TraitResult()
        avg_conf = sum(confidences) / len(confidences)
        conf_std = math.sqrt(sum((c - avg_conf) ** 2 for c in confidences) / len(confidences))

        # Idle-to-action ratio
        total_cycles = session.execute(
            select(func.count(AgentCycle.id)).where(AgentCycle.agent_id == agent.id)
        ).scalar() or 1
        idle_count = session.execute(
            select(func.count(AgentCycle.id)).where(
                AgentCycle.agent_id == agent.id,
                AgentCycle.action_type == "go_idle",
            )
        ).scalar() or 0
        idle_ratio = idle_count / total_cycles

        # Combine: high reasoning + high idle ratio + low confidence variance = cautious
        reasoning_score = min(avg_reasoning / 500.0, 1.0)  # 500 chars = max deliberation
        idle_score = idle_ratio  # High idle = cautious
        conf_stability = 1.0 - min(conf_std / 3.0, 1.0)  # Low variance = cautious

        # Higher score = more cautious/paralyzed
        decision_score = (reasoning_score * 0.3 + idle_score * 0.4 + conf_stability * 0.3)
        decision_score = max(0.0, min(1.0, decision_score))
        label = classify(decision_score, DECISION_THRESHOLDS, DECISION_LABELS)

        return TraitResult(score=decision_score, label=label, has_data=True)

    def _compute_collaboration(self, session: Session, agent: Agent) -> TraitResult:
        """From pipeline connection rates."""
        # Count positions with source_plan_id (came from pipeline)
        pipeline_positions = session.execute(
            select(func.count(Position.id)).where(
                Position.agent_id == agent.id,
                Position.source_plan_id.isnot(None),
            )
        ).scalar() or 0

        total_positions = session.execute(
            select(func.count(Position.id)).where(
                Position.agent_id == agent.id,
            )
        ).scalar() or 0

        # For non-operators, check pipeline participation differently
        if agent.type != "operator":
            from src.common.models import Plan, Opportunity
            if agent.type == "scout":
                contrib = session.execute(
                    select(func.count(Opportunity.id)).where(
                        Opportunity.scout_agent_id == agent.id,
                        Opportunity.status == "converted",
                    )
                ).scalar() or 0
                total = session.execute(
                    select(func.count(Opportunity.id)).where(
                        Opportunity.scout_agent_id == agent.id,
                    )
                ).scalar() or 0
            elif agent.type == "strategist":
                contrib = session.execute(
                    select(func.count(Plan.id)).where(
                        Plan.strategist_agent_id == agent.id,
                        Plan.status.in_(["approved", "executing", "completed"]),
                    )
                ).scalar() or 0
                total = session.execute(
                    select(func.count(Plan.id)).where(
                        Plan.strategist_agent_id == agent.id,
                    )
                ).scalar() or 0
            elif agent.type == "critic":
                contrib = session.execute(
                    select(func.count(Plan.id)).where(
                        Plan.critic_agent_id == agent.id,
                        Plan.critic_verdict.isnot(None),
                    )
                ).scalar() or 0
                total = contrib  # all reviews are contributions
            else:
                return TraitResult()

            if total < config.profile_min_pipeline_outcomes:
                return TraitResult()
            score = contrib / total if total > 0 else 0.5
        else:
            if total_positions < config.profile_min_pipeline_outcomes:
                return TraitResult()
            score = pipeline_positions / total_positions if total_positions > 0 else 0.0

        score = max(0.0, min(1.0, score))
        label = classify(score, COLLABORATION_THRESHOLDS, COLLABORATION_LABELS)
        return TraitResult(score=score, label=label, has_data=True)

    def _compute_learning_velocity(self, session: Session, agent: Agent) -> TraitResult:
        """From composite score trend across evaluations."""
        evals = session.execute(
            select(Evaluation).where(
                Evaluation.agent_id == agent.id,
            ).order_by(Evaluation.evaluated_at)
        ).scalars().all()

        if len(evals) < config.profile_min_evaluations:
            return TraitResult()

        scores = [e.composite_score for e in evals if e.composite_score is not None]
        if len(scores) < 2:
            return TraitResult()

        # Simple linear trend: positive slope = learning
        n = len(scores)
        x_mean = (n - 1) / 2.0
        y_mean = sum(scores) / n
        numerator = sum((i - x_mean) * (s - y_mean) for i, s in enumerate(scores))
        denominator = sum((i - x_mean) ** 2 for i in range(n))
        slope = numerator / denominator if denominator > 0 else 0.0

        # Normalize slope to 0-1 range: -0.1 → 0.0, +0.1 → 1.0
        normalized = (slope + 0.1) / 0.2
        normalized = max(0.0, min(1.0, normalized))

        label = classify(normalized, LEARNING_THRESHOLDS, LEARNING_LABELS)
        return TraitResult(score=normalized, label=label, has_data=True)

    def _compute_resilience(self, session: Session, agent: Agent) -> TraitResult:
        """From loss-to-recovery cycle counts."""
        # Find positions with losses
        losing_positions = session.execute(
            select(Position).where(
                Position.agent_id == agent.id,
                Position.status != "open",
                Position.realized_pnl < 0,
            ).order_by(Position.closed_at)
        ).scalars().all()

        if len(losing_positions) < config.profile_min_losses:
            return TraitResult()

        recovery_scores = []
        for loss_pos in losing_positions:
            if not loss_pos.closed_at:
                continue
            # Find next profitable action after this loss
            next_profitable = session.execute(
                select(Position).where(
                    Position.agent_id == agent.id,
                    Position.closed_at > loss_pos.closed_at,
                    Position.realized_pnl > 0,
                ).order_by(Position.closed_at).limit(1)
            ).scalar_one_or_none()

            if next_profitable and next_profitable.closed_at:
                # Count cycles between loss and recovery
                cycles_between = session.execute(
                    select(func.count(AgentCycle.id)).where(
                        AgentCycle.agent_id == agent.id,
                        AgentCycle.timestamp > loss_pos.closed_at,
                        AgentCycle.timestamp < next_profitable.closed_at,
                    )
                ).scalar() or 0

                # Score: quick recovery = resilient (0 cycles = 1.0, 20+ cycles = 0.0)
                recovery = max(0.0, 1.0 - cycles_between / 20.0)
                recovery_scores.append(recovery)
            else:
                # No recovery found — fragile signal
                recovery_scores.append(0.1)

        if not recovery_scores:
            return TraitResult()

        avg_recovery = sum(recovery_scores) / len(recovery_scores)
        avg_recovery = max(0.0, min(1.0, avg_recovery))
        label = classify(avg_recovery, RESILIENCE_THRESHOLDS, RESILIENCE_LABELS)
        return TraitResult(score=avg_recovery, label=label, has_data=True)

    def _compute_regime_context(self, session: Session, agent: Agent) -> dict:
        """Get dominant market regime during agent's lifetime."""
        regimes = session.execute(
            select(MarketRegime).where(
                MarketRegime.detected_at >= agent.created_at,
            ).order_by(MarketRegime.detected_at)
        ).scalars().all()

        if not regimes:
            return {"dominant": None, "distribution": None}

        regime_counts: Counter[str] = Counter()
        for r in regimes:
            regime_counts[r.regime] += 1

        total = sum(regime_counts.values())
        distribution = {r: round(c / total, 3) for r, c in regime_counts.items()}
        dominant = regime_counts.most_common(1)[0][0]

        return {"dominant": dominant, "distribution": distribution}

    # ------------------------------------------------------------------
    # Drift detection
    # ------------------------------------------------------------------

    def detect_drift(
        self,
        previous: BehavioralProfile | None,
        current: BehavioralProfile,
    ) -> list[DriftFlag]:
        """Detect personality drift (2+ tier shift between consecutive profiles)."""
        if previous is None:
            return []

        flags = []
        trait_pairs = [
            ("risk_appetite", previous.risk_appetite_label, current.risk_appetite_label),
            ("decision_style", previous.decision_style_label, current.decision_style_label),
            ("collaboration", previous.collaboration_label, current.collaboration_label),
            ("learning_velocity", previous.learning_velocity_label, current.learning_velocity_label),
            ("resilience", previous.resilience_label, current.resilience_label),
        ]

        threshold = config.personality_drift_tier_threshold

        for trait, old_label, new_label in trait_pairs:
            if not old_label or not new_label:
                continue
            tier_map = TIER_DISTANCES.get(trait, {})
            old_tier = tier_map.get(old_label)
            new_tier = tier_map.get(new_label)
            if old_tier is not None and new_tier is not None:
                distance = abs(new_tier - old_tier)
                if distance >= threshold:
                    flags.append(DriftFlag(
                        trait=trait,
                        old_label=old_label,
                        new_label=new_label,
                        tier_distance=distance,
                    ))

        return flags
