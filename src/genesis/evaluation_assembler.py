"""
Project Syndicate — Evaluation Data Assembler

Builds the full evaluation data package for each agent:
  - Role-specific metrics
  - Pipeline analysis
  - Idle analysis
  - Honesty score
  - Financial data
  - Behavioral data
  - Ecosystem context

Produces both full (DB storage) and compressed (<1000 tokens) versions.
"""

__version__ = "1.0.0"

import logging
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select, func
from sqlalchemy.orm import Session

from src.common.config import config
from src.common.models import Agent, AgentCycle, Position, SystemState
from src.genesis.honesty_scorer import HonestyScorer
from src.genesis.idle_analyzer import IdleAnalyzer
from src.genesis.pipeline_analyzer import PipelineAnalyzer, PipelineReport
from src.genesis.role_metrics import get_metrics_calculator, MetricResult

logger = logging.getLogger(__name__)


@dataclass
class EvaluationPackage:
    """Full evaluation data for one agent."""
    agent_id: int
    agent_name: str
    agent_role: str
    generation: int
    evaluation_number: int
    period_start: datetime
    period_end: datetime
    metrics: MetricResult | None = None
    pipeline: PipelineReport | None = None
    idle_breakdown: dict | None = None
    honesty_score: dict | None = None
    financial: dict = field(default_factory=dict)
    behavioral: dict = field(default_factory=dict)
    ecosystem_context: dict = field(default_factory=dict)
    compressed: str = ""


class EvaluationAssembler:
    """Assembles evaluation data packages for agents."""

    def __init__(self):
        self.idle_analyzer = IdleAnalyzer()
        self.honesty_scorer = HonestyScorer()
        self.pipeline_analyzer = PipelineAnalyzer()

    async def build(
        self, session: Session, agent: Agent,
        period_start: datetime, period_end: datetime,
        pipeline_report: PipelineReport | None = None,
    ) -> EvaluationPackage:
        """Build a full evaluation package for an agent.

        Args:
            session: DB session.
            agent: Agent to evaluate.
            period_start: Start of evaluation period.
            period_end: End of evaluation period.
            pipeline_report: Optional pre-computed pipeline report.

        Returns:
            EvaluationPackage with all data.
        """
        pkg = EvaluationPackage(
            agent_id=agent.id,
            agent_name=agent.name,
            agent_role=agent.type,
            generation=agent.generation,
            evaluation_number=agent.evaluation_count + 1,
            period_start=period_start,
            period_end=period_end,
        )

        # 1. Role-specific metrics
        calculator = get_metrics_calculator(agent.type)
        if calculator:
            pkg.metrics = await calculator.calculate(
                session, agent.id, period_start, period_end
            )

        # 2. Pipeline analysis (reuse if provided)
        pkg.pipeline = pipeline_report or await self.pipeline_analyzer.analyze(
            session, period_start, period_end
        )

        # 3. Idle analysis
        idle = await self.idle_analyzer.analyze_idle_periods(
            session, agent.id, period_start, period_end
        )
        pkg.idle_breakdown = {
            "total_idle": idle.total_idle,
            "total_cycles": idle.total_cycles,
            "idle_rate": idle.idle_rate,
            "breakdown": idle.breakdown,
            "breakdown_pct": idle.breakdown_pct,
        }

        # 4. Honesty score
        honesty = await self.honesty_scorer.calculate(
            session, agent.id, period_start, period_end
        )
        pkg.honesty_score = {
            "overall": honesty.overall_score,
            "confidence_calibration": honesty.confidence_calibration,
            "self_note_accuracy": honesty.self_note_accuracy,
            "reflection_specificity": honesty.reflection_specificity,
            "data_points": honesty.data_points,
        }

        # 5. Financial data
        pkg.financial = self._gather_financial(session, agent, period_start, period_end)

        # 6. Behavioral data
        pkg.behavioral = self._gather_behavioral(session, agent, period_start, period_end)

        # 7. Ecosystem context
        pkg.ecosystem_context = self._gather_ecosystem(session, agent)

        # 8. Build compressed summary
        pkg.compressed = self._compress(pkg)

        return pkg

    def _gather_financial(
        self, session: Session, agent: Agent,
        period_start: datetime, period_end: datetime,
    ) -> dict:
        """Gather financial data for the agent."""
        # Count positions during period
        positions = session.execute(
            select(Position).where(
                Position.agent_id == agent.id,
                Position.opened_at >= period_start,
                Position.opened_at <= period_end,
            )
        ).scalars().all()

        open_positions = [p for p in positions if p.status == "open"]
        closed_positions = [p for p in positions if p.status != "open"]
        profitable = [p for p in closed_positions if p.realized_pnl and p.realized_pnl > 0]

        # API cost during period
        api_cost = session.execute(
            select(func.sum(AgentCycle.api_cost_usd)).where(
                AgentCycle.agent_id == agent.id,
                AgentCycle.timestamp >= period_start,
                AgentCycle.timestamp <= period_end,
            )
        ).scalar() or 0.0

        return {
            "capital_allocated": agent.capital_allocated,
            "cash_balance": agent.cash_balance,
            "realized_pnl": agent.realized_pnl,
            "unrealized_pnl": agent.unrealized_pnl,
            "total_equity": agent.total_equity,
            "total_fees_paid": agent.total_fees_paid,
            "api_cost_period": api_cost,
            "true_pnl": agent.realized_pnl + agent.unrealized_pnl - api_cost,
            "positions_opened": len(positions),
            "positions_closed": len(closed_positions),
            "positions_open": len(open_positions),
            "win_rate": len(profitable) / len(closed_positions) if closed_positions else 0.0,
        }

    def _gather_behavioral(
        self, session: Session, agent: Agent,
        period_start: datetime, period_end: datetime,
    ) -> dict:
        """Gather behavioral data from cycle records."""
        cycles = session.execute(
            select(AgentCycle).where(
                AgentCycle.agent_id == agent.id,
                AgentCycle.timestamp >= period_start,
                AgentCycle.timestamp <= period_end,
            )
        ).scalars().all()

        total = len(cycles)
        if total == 0:
            return {"total_cycles": 0}

        return {
            "total_cycles": total,
            "avg_confidence": (
                sum(c.confidence_score or 0 for c in cycles) / total
            ),
            "warden_violations": sum(c.warden_flags for c in cycles),
            "validation_failures": sum(1 for c in cycles if not c.validation_passed),
            "avg_api_cost": sum(c.api_cost_usd for c in cycles) / total,
            "total_api_cost": sum(c.api_cost_usd for c in cycles),
        }

    def _gather_ecosystem(self, session: Session, agent: Agent) -> dict:
        """Gather ecosystem context: other agents, regime, etc."""
        # Active agents by role
        role_counts = {}
        active_agents = session.execute(
            select(Agent).where(Agent.status == "active")
        ).scalars().all()

        for a in active_agents:
            role_counts[a.type] = role_counts.get(a.type, 0) + 1

        # Current regime
        state = session.execute(select(SystemState)).scalars().first()
        regime = state.current_regime if state else "unknown"

        # Agent's rank among same-role peers
        same_role = [a for a in active_agents if a.type == agent.type]
        same_role_sorted = sorted(same_role, key=lambda a: a.composite_score, reverse=True)
        rank = next(
            (i + 1 for i, a in enumerate(same_role_sorted) if a.id == agent.id),
            len(same_role_sorted),
        )

        return {
            "total_active_agents": len(active_agents),
            "role_counts": role_counts,
            "market_regime": regime,
            "role_rank": rank,
            "role_total": len(same_role),
        }

    def _compress(self, pkg: EvaluationPackage) -> str:
        """Compress evaluation data to <1000 tokens for Claude prompt."""
        lines = []
        lines.append(f"=== EVAL #{pkg.evaluation_number}: {pkg.agent_name} ({pkg.agent_role}) Gen{pkg.generation} ===")

        # Metrics summary
        if pkg.metrics:
            lines.append(f"Composite: {pkg.metrics.composite_score:.3f}")
            for name, data in pkg.metrics.metric_breakdown.items():
                if isinstance(data, dict) and "raw" in data:
                    lines.append(f"  {name}: {data['raw']:.4f} (norm={data['normalized']:.3f})")

        # Financial summary
        fin = pkg.financial
        lines.append(f"P&L: realized=${fin.get('realized_pnl', 0):.2f} unrealized=${fin.get('unrealized_pnl', 0):.2f}")
        lines.append(f"True P&L: ${fin.get('true_pnl', 0):.2f} (API cost: ${fin.get('api_cost_period', 0):.4f})")
        lines.append(f"Trades: {fin.get('positions_opened', 0)} opened, win_rate={fin.get('win_rate', 0):.1%}")

        # Idle summary
        idle = pkg.idle_breakdown or {}
        if idle.get("total_idle", 0) > 0:
            bd = idle.get("breakdown", {})
            lines.append(f"Idle: {idle.get('idle_rate', 0):.1%} (caution={bd.get('post_loss_caution', 0)}, no_input={bd.get('no_input', 0)}, patience={bd.get('strategic_patience', 0)}, paralysis={bd.get('paralysis', 0)})")

        # Honesty
        if pkg.honesty_score:
            lines.append(f"Honesty: {pkg.honesty_score.get('overall', 0.5):.2f}")

        # Ecosystem
        eco = pkg.ecosystem_context
        lines.append(f"Rank: #{eco.get('role_rank', '?')}/{eco.get('role_total', '?')} | Regime: {eco.get('market_regime', '?')}")

        return "\n".join(lines)
