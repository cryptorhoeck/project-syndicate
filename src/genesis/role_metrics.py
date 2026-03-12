"""
Project Syndicate — Role-Specific Metric Calculators

Calculates composite performance scores for each agent role:
  - Operator: Sharpe, True P&L%, Thinking Efficiency, Consistency
  - Scout: Intel Conversion, Profitability, Signal Quality, Efficiency, Activity
  - Strategist: Approval Rate, Profitability, Efficiency, Revision Rate, Thinking Efficiency
  - Critic: Rejection Value, Approval Accuracy, Risk Flag Value, Throughput, Thinking Efficiency
"""

__version__ = "1.0.0"

import logging
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select, func
from sqlalchemy.orm import Session

from src.common.config import config
from src.common.models import (
    Agent, AgentCycle, AgentEquitySnapshot, Evaluation,
    Opportunity, Plan, Position, Transaction,
)

logger = logging.getLogger(__name__)


@dataclass
class MetricResult:
    """Result of a role-specific metric calculation."""
    composite_score: float
    metric_breakdown: dict = field(default_factory=dict)


def normalize(value: float, min_ref: float, max_ref: float) -> float:
    """Normalize a value to [0, 1] using fixed reference range."""
    if max_ref == min_ref:
        return 0.5
    return max(0.0, min(1.0, (value - min_ref) / (max_ref - min_ref)))


class OperatorMetrics:
    """Composite: (0.40 Sharpe) + (0.25 True P&L%) + (0.20 Efficiency) + (0.15 Consistency)."""

    async def calculate(
        self, session: Session, agent_id: int,
        period_start: datetime, period_end: datetime,
    ) -> MetricResult:
        agent = session.get(Agent, agent_id)
        if not agent:
            return MetricResult(composite_score=0.0)

        # Sharpe ratio from equity snapshots
        sharpe = await self._calculate_sharpe(session, agent_id, period_start, period_end)
        sharpe_norm = normalize(sharpe, *config.norm_operator_sharpe_range)

        # True P&L% = (realized + unrealized - api_cost) / capital_allocated
        api_cost = self._get_api_cost(session, agent_id, period_start, period_end)
        capital = agent.capital_allocated if agent.capital_allocated > 0 else 1.0
        true_pnl = agent.realized_pnl + agent.unrealized_pnl - api_cost
        true_pnl_pct = (true_pnl / capital) * 100
        pnl_norm = normalize(true_pnl_pct, *config.norm_operator_pnl_range)

        # Thinking efficiency = true_pnl / api_cost
        efficiency = true_pnl / api_cost if api_cost > 0 else 0.0
        efficiency_norm = normalize(efficiency, *config.norm_operator_efficiency_range)

        # Consistency = profitable_evaluations / total_evaluations
        consistency = (
            agent.profitable_evaluations / agent.evaluation_count
            if agent.evaluation_count > 0 else 0.0
        )
        consistency_norm = normalize(consistency, 0.0, 1.0)

        composite = (
            0.40 * sharpe_norm
            + 0.25 * pnl_norm
            + 0.20 * efficiency_norm
            + 0.15 * consistency_norm
        )

        return MetricResult(
            composite_score=composite,
            metric_breakdown={
                "sharpe": {"raw": sharpe, "normalized": sharpe_norm},
                "true_pnl_pct": {"raw": true_pnl_pct, "normalized": pnl_norm},
                "thinking_efficiency": {"raw": efficiency, "normalized": efficiency_norm},
                "consistency": {"raw": consistency, "normalized": consistency_norm},
            },
        )

    async def _calculate_sharpe(
        self, session: Session, agent_id: int,
        period_start: datetime, period_end: datetime,
    ) -> float:
        """Calculate annualized Sharpe ratio from equity snapshots."""
        snapshots = session.execute(
            select(AgentEquitySnapshot)
            .where(
                AgentEquitySnapshot.agent_id == agent_id,
                AgentEquitySnapshot.snapshot_at >= period_start,
                AgentEquitySnapshot.snapshot_at <= period_end,
            )
            .order_by(AgentEquitySnapshot.snapshot_at)
        ).scalars().all()

        if len(snapshots) < 2:
            return 0.0

        # Daily returns from equity changes
        daily_returns = []
        prev_equity = snapshots[0].equity
        current_day = None
        day_start_equity = prev_equity

        for snap in snapshots[1:]:
            snap_day = snap.snapshot_at.date() if hasattr(snap.snapshot_at, 'date') else snap.snapshot_at
            if current_day is None:
                current_day = snap_day
                day_start_equity = prev_equity

            if snap_day != current_day:
                if day_start_equity > 0:
                    daily_returns.append((prev_equity - day_start_equity) / day_start_equity)
                current_day = snap_day
                day_start_equity = prev_equity

            prev_equity = snap.equity

        # Final day
        if day_start_equity > 0 and current_day is not None:
            daily_returns.append((prev_equity - day_start_equity) / day_start_equity)

        if len(daily_returns) < 2:
            return 0.0

        import statistics
        avg_return = statistics.mean(daily_returns)
        std_return = statistics.stdev(daily_returns)

        if std_return == 0:
            return 0.0

        # Annualized Sharpe (365 trading days for crypto)
        return (avg_return / std_return) * (365 ** 0.5)

    def _get_api_cost(
        self, session: Session, agent_id: int,
        period_start: datetime, period_end: datetime,
    ) -> float:
        """Get total API cost for agent during period."""
        result = session.execute(
            select(func.sum(AgentCycle.api_cost_usd))
            .where(
                AgentCycle.agent_id == agent_id,
                AgentCycle.timestamp >= period_start,
                AgentCycle.timestamp <= period_end,
            )
        ).scalar()
        return result or 0.0


class ScoutMetrics:
    """Composite: (0.30 Conversion) + (0.30 Profitability) + (0.15 Signal Quality)
    + (0.15 Efficiency) + (0.10 Activity)."""

    async def calculate(
        self, session: Session, agent_id: int,
        period_start: datetime, period_end: datetime,
    ) -> MetricResult:
        agent = session.get(Agent, agent_id)
        if not agent:
            return MetricResult(composite_score=0.0)

        # Intel conversion: plans_from_my_opps / total_opps
        opps = session.execute(
            select(Opportunity).where(
                Opportunity.scout_agent_id == agent_id,
                Opportunity.created_at >= period_start,
                Opportunity.created_at <= period_end,
            )
        ).scalars().all()

        total_opps = len(opps)
        claimed_opps = sum(1 for o in opps if o.converted_to_plan_id is not None)
        intel_conversion = claimed_opps / total_opps if total_opps > 0 else 0.0
        conversion_norm = normalize(intel_conversion, *config.norm_scout_conversion_range)

        # Intel profitability: avg P&L of trades linked to my opportunities
        attributed_pnl = self._get_attributed_pnl(session, opps)
        profitability_norm = normalize(attributed_pnl, *config.norm_scout_profitability_range)

        # Signal quality: correlation(confidence, outcomes)
        signal_quality = self._calculate_signal_quality(session, opps)
        signal_quality_norm = normalize(signal_quality, 0.0, 1.0)

        # Thinking efficiency: opps_claimed / api_cost
        api_cost = self._get_api_cost(session, agent_id, period_start, period_end)
        thinking_efficiency = claimed_opps / api_cost if api_cost > 0 else 0.0
        efficiency_norm = normalize(thinking_efficiency, 0.0, 10.0)

        # Activity rate: productive_cycles / total_cycles
        cycles = session.execute(
            select(AgentCycle).where(
                AgentCycle.agent_id == agent_id,
                AgentCycle.timestamp >= period_start,
                AgentCycle.timestamp <= period_end,
            )
        ).scalars().all()
        total_cycles = len(cycles)
        idle_cycles = sum(1 for c in cycles if c.action_type in (None, "wait", "observe"))
        failed_cycles = sum(1 for c in cycles if not c.validation_passed)
        productive = total_cycles - idle_cycles - failed_cycles
        activity_rate = productive / total_cycles if total_cycles > 0 else 0.0
        activity_norm = normalize(activity_rate, 0.0, 1.0)

        composite = (
            0.30 * conversion_norm
            + 0.30 * profitability_norm
            + 0.15 * signal_quality_norm
            + 0.15 * efficiency_norm
            + 0.10 * activity_norm
        )

        return MetricResult(
            composite_score=composite,
            metric_breakdown={
                "intel_conversion": {"raw": intel_conversion, "normalized": conversion_norm},
                "intel_profitability": {"raw": attributed_pnl, "normalized": profitability_norm},
                "signal_quality": {"raw": signal_quality, "normalized": signal_quality_norm},
                "thinking_efficiency": {"raw": thinking_efficiency, "normalized": efficiency_norm},
                "activity_rate": {"raw": activity_rate, "normalized": activity_norm},
            },
        )

    def _get_attributed_pnl(self, session: Session, opps: list) -> float:
        """Average P&L% of positions linked to scout's opportunities."""
        opp_ids = [o.id for o in opps if o.converted_to_plan_id is not None]
        if not opp_ids:
            return 0.0

        positions = session.execute(
            select(Position).where(
                Position.source_opp_id.in_(opp_ids),
                Position.status != "open",
            )
        ).scalars().all()

        if not positions:
            return 0.0

        pnl_pcts = []
        for p in positions:
            if p.size_usd > 0 and p.realized_pnl is not None:
                pnl_pcts.append((p.realized_pnl / p.size_usd) * 100)

        return sum(pnl_pcts) / len(pnl_pcts) if pnl_pcts else 0.0

    def _calculate_signal_quality(self, session: Session, opps: list) -> float:
        """Correlation between confidence scores and actual outcomes."""
        data_points = []
        for opp in opps:
            if opp.converted_to_plan_id is None:
                continue
            # Find positions from this opp
            positions = session.execute(
                select(Position).where(
                    Position.source_opp_id == opp.id,
                    Position.status != "open",
                )
            ).scalars().all()

            for pos in positions:
                if pos.realized_pnl is not None and pos.size_usd > 0:
                    outcome = 1.0 if pos.realized_pnl > 0 else 0.0
                    confidence = opp.confidence / 10.0  # normalize to 0-1
                    data_points.append((confidence, outcome))

        if len(data_points) < 5:
            return 0.5  # Neutral on insufficient data

        # Simple correlation
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
        # Transform from [-1, 1] to [0, 1]
        return (r + 1.0) / 2.0

    def _get_api_cost(self, session, agent_id, period_start, period_end):
        result = session.execute(
            select(func.sum(AgentCycle.api_cost_usd))
            .where(
                AgentCycle.agent_id == agent_id,
                AgentCycle.timestamp >= period_start,
                AgentCycle.timestamp <= period_end,
            )
        ).scalar()
        return result or 0.0


class StrategistMetrics:
    """Composite: (0.25 Approval) + (0.30 Profitability) + (0.15 Efficiency)
    + (0.15 Revision) + (0.15 Thinking Efficiency)."""

    async def calculate(
        self, session: Session, agent_id: int,
        period_start: datetime, period_end: datetime,
    ) -> MetricResult:
        agent = session.get(Agent, agent_id)
        if not agent:
            return MetricResult(composite_score=0.0)

        # Plans during period
        plans = session.execute(
            select(Plan).where(
                Plan.strategist_agent_id == agent_id,
                Plan.created_at >= period_start,
                Plan.created_at <= period_end,
            )
        ).scalars().all()

        total_plans = len(plans)
        approved_plans = sum(1 for p in plans if p.critic_verdict == "approved")
        revised_plans = sum(1 for p in plans if p.revision_count > 0)

        # Plan approval rate
        approval_rate = approved_plans / total_plans if total_plans > 0 else 0.0
        approval_norm = normalize(approval_rate, *config.norm_strategist_approval_range)

        # Plan profitability: avg P&L of trades linked to my plans
        attributed_pnl = self._get_plan_pnl(session, plans)
        profitability_norm = normalize(attributed_pnl, *config.norm_scout_profitability_range)

        # Plan efficiency: approved_plans / api_cost
        api_cost = self._get_api_cost(session, agent_id, period_start, period_end)
        plan_efficiency = approved_plans / api_cost if api_cost > 0 else 0.0
        efficiency_norm = normalize(plan_efficiency, 0.0, 5.0)

        # Revision rate (inverted): 1 - (revised / total)
        revision_rate = 1.0 - (revised_plans / total_plans if total_plans > 0 else 0.0)
        revision_norm = normalize(revision_rate, 0.0, 1.0)

        # Thinking efficiency: approved / api_cost
        thinking_efficiency = approved_plans / api_cost if api_cost > 0 else 0.0
        thinking_norm = normalize(thinking_efficiency, 0.0, 10.0)

        composite = (
            0.25 * approval_norm
            + 0.30 * profitability_norm
            + 0.15 * efficiency_norm
            + 0.15 * revision_norm
            + 0.15 * thinking_norm
        )

        return MetricResult(
            composite_score=composite,
            metric_breakdown={
                "plan_approval_rate": {"raw": approval_rate, "normalized": approval_norm},
                "plan_profitability": {"raw": attributed_pnl, "normalized": profitability_norm},
                "plan_efficiency": {"raw": plan_efficiency, "normalized": efficiency_norm},
                "revision_rate": {"raw": revision_rate, "normalized": revision_norm},
                "thinking_efficiency": {"raw": thinking_efficiency, "normalized": thinking_norm},
            },
        )

    def _get_plan_pnl(self, session: Session, plans: list) -> float:
        """Average P&L% of positions linked to strategist's plans."""
        plan_ids = [p.id for p in plans]
        if not plan_ids:
            return 0.0

        positions = session.execute(
            select(Position).where(
                Position.source_plan_id.in_(plan_ids),
                Position.status != "open",
            )
        ).scalars().all()

        if not positions:
            return 0.0

        pnl_pcts = []
        for p in positions:
            if p.size_usd > 0 and p.realized_pnl is not None:
                pnl_pcts.append((p.realized_pnl / p.size_usd) * 100)

        return sum(pnl_pcts) / len(pnl_pcts) if pnl_pcts else 0.0

    def _get_api_cost(self, session, agent_id, period_start, period_end):
        result = session.execute(
            select(func.sum(AgentCycle.api_cost_usd))
            .where(
                AgentCycle.agent_id == agent_id,
                AgentCycle.timestamp >= period_start,
                AgentCycle.timestamp <= period_end,
            )
        ).scalar()
        return result or 0.0


class CriticMetrics:
    """Composite: (0.30 Rejection Value) + (0.25 Approval Accuracy) + (0.15 Risk Flag)
    + (0.15 Throughput) + (0.15 Thinking Efficiency)."""

    async def calculate(
        self, session: Session, agent_id: int,
        period_start: datetime, period_end: datetime,
    ) -> MetricResult:
        from src.common.models import RejectionTracking

        agent = session.get(Agent, agent_id)
        if not agent:
            return MetricResult(composite_score=0.0)

        # Plans reviewed by this critic during period
        reviewed_plans = session.execute(
            select(Plan).where(
                Plan.critic_agent_id == agent_id,
                Plan.reviewed_at >= period_start,
                Plan.reviewed_at <= period_end,
            )
        ).scalars().all()

        total_reviewed = len(reviewed_plans)
        approved = [p for p in reviewed_plans if p.critic_verdict == "approved"]
        rejected = [p for p in reviewed_plans if p.critic_verdict == "rejected"]

        # Rejection value: from counterfactual tracking
        rejection_trackings = session.execute(
            select(RejectionTracking).where(
                RejectionTracking.critic_id == agent_id,
                RejectionTracking.status == "completed",
                RejectionTracking.completed_at >= period_start,
                RejectionTracking.completed_at <= period_end,
            )
        ).scalars().all()

        correct_rejections = sum(1 for t in rejection_trackings if t.critic_correct is True)
        total_tracked = len(rejection_trackings)
        rejection_value = (
            (correct_rejections / total_tracked * 2 - 1)  # [-1, 1] scale
            if total_tracked > 0 else 0.0
        )
        rejection_norm = normalize(rejection_value, *config.norm_critic_rejection_range)

        # Approval accuracy: profitable_approved / total_approved
        # with rubber-stamp penalty
        approved_profitable = 0
        for plan in approved:
            positions = session.execute(
                select(Position).where(
                    Position.source_plan_id == plan.id,
                    Position.status != "open",
                )
            ).scalars().all()
            if any(p.realized_pnl and p.realized_pnl > 0 for p in positions):
                approved_profitable += 1

        approval_accuracy = (
            approved_profitable / len(approved) if approved else 0.0
        )

        # Rubber-stamp penalty: if approval rate > 90%, halve accuracy score
        approval_rate = len(approved) / total_reviewed if total_reviewed > 0 else 0.0
        if approval_rate > config.critic_rubber_stamp_threshold:
            approval_accuracy *= config.critic_rubber_stamp_penalty
        approval_accuracy_norm = normalize(approval_accuracy, 0.0, 1.0)

        # Risk flag value: flags that materialized / total flags
        # Use critic_risk_notes as a proxy for risk flags
        plans_with_flags = [p for p in reviewed_plans if p.critic_risk_notes]
        flags_confirmed = 0
        for plan in plans_with_flags:
            positions = session.execute(
                select(Position).where(
                    Position.source_plan_id == plan.id,
                    Position.status.in_(["stopped_out"]),
                )
            ).scalars().all()
            if positions:
                flags_confirmed += 1

        risk_flag_value = (
            flags_confirmed / len(plans_with_flags)
            if plans_with_flags else 0.0
        )
        risk_flag_norm = normalize(risk_flag_value, 0.0, 1.0)

        # Throughput: plans_reviewed / period_days
        period_days = max(1, (period_end - period_start).days)
        throughput = total_reviewed / period_days
        throughput_norm = normalize(throughput, *config.norm_critic_throughput_range)

        # Thinking efficiency: plans_reviewed / api_cost
        api_cost = self._get_api_cost(session, agent_id, period_start, period_end)
        thinking_efficiency = total_reviewed / api_cost if api_cost > 0 else 0.0
        thinking_norm = normalize(thinking_efficiency, 0.0, 10.0)

        composite = (
            0.30 * rejection_norm
            + 0.25 * approval_accuracy_norm
            + 0.15 * risk_flag_norm
            + 0.15 * throughput_norm
            + 0.15 * thinking_norm
        )

        return MetricResult(
            composite_score=composite,
            metric_breakdown={
                "rejection_value": {"raw": rejection_value, "normalized": rejection_norm},
                "approval_accuracy": {"raw": approval_accuracy, "normalized": approval_accuracy_norm},
                "risk_flag_value": {"raw": risk_flag_value, "normalized": risk_flag_norm},
                "throughput": {"raw": throughput, "normalized": throughput_norm},
                "thinking_efficiency": {"raw": thinking_efficiency, "normalized": thinking_norm},
                "rubber_stamp_penalty_applied": approval_rate > config.critic_rubber_stamp_threshold,
            },
        )

    def _get_api_cost(self, session, agent_id, period_start, period_end):
        result = session.execute(
            select(func.sum(AgentCycle.api_cost_usd))
            .where(
                AgentCycle.agent_id == agent_id,
                AgentCycle.timestamp >= period_start,
                AgentCycle.timestamp <= period_end,
            )
        ).scalar()
        return result or 0.0


def get_metrics_calculator(role: str):
    """Factory: return the appropriate metrics calculator for a role."""
    calculators = {
        "operator": OperatorMetrics(),
        "scout": ScoutMetrics(),
        "strategist": StrategistMetrics(),
        "critic": CriticMetrics(),
    }
    return calculators.get(role)
