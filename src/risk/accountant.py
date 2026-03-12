"""
Project Syndicate — The Accountant

Pure calculation engine. No LLM.
Handles P&L tracking, Sharpe ratio, composite scoring, leaderboard,
thinking tax collection, and system-wide financial summaries.
"""

__version__ = "0.2.0"

import math
from datetime import datetime, timedelta, timezone

import numpy as np
import structlog
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from src.common.config import config
from src.common.models import Agent, SystemState, Transaction

logger = structlog.get_logger()

# Claude Sonnet pricing (per million tokens)
MODEL_PRICING = {
    "claude-sonnet-4-6": {"input": 3.0, "output": 15.0},
    "claude-sonnet-4-5-20250514": {"input": 3.0, "output": 15.0},
    "claude-haiku-4-5-20251001": {"input": 0.80, "output": 4.0},
    # Default fallback
    "default": {"input": 3.0, "output": 15.0},
}


class Accountant:
    """Pure calculation engine for all financial metrics."""

    def __init__(self, db_session_factory: sessionmaker | None = None) -> None:
        self.log = logger.bind(component="accountant")
        if db_session_factory:
            self.db_session_factory = db_session_factory
        else:
            engine = create_engine(config.database_url)
            self.db_session_factory = sessionmaker(bind=engine)

    # ------------------------------------------------------------------
    # P&L Calculation
    # ------------------------------------------------------------------

    async def calculate_agent_pnl(self, agent_id: int) -> dict:
        """Calculate full P&L breakdown for an agent."""
        with self.db_session_factory() as session:
            # Trade transactions
            trades = session.execute(
                select(Transaction).where(
                    Transaction.agent_id == agent_id,
                    Transaction.type != "api_cost",
                )
            ).scalars().all()

            # API cost transactions
            api_costs = session.execute(
                select(func.coalesce(func.sum(Transaction.amount), 0.0)).where(
                    Transaction.agent_id == agent_id,
                    Transaction.type == "api_cost",
                )
            ).scalar()

            gross_pnl = sum(t.pnl for t in trades)
            total_fees = sum(t.fee for t in trades)
            trade_count = len(trades)
            wins = [t for t in trades if t.pnl > 0]
            losses = [t for t in trades if t.pnl < 0]

            api_cost = float(api_costs or 0.0)
            true_pnl = gross_pnl - api_cost

            # Get agent capital for percentage
            agent = session.get(Agent, agent_id)
            allocated = agent.capital_allocated if agent else 0.0
            true_pnl_pct = (true_pnl / allocated * 100) if allocated > 0 else 0.0

            return {
                "agent_id": agent_id,
                "gross_pnl": round(gross_pnl, 4),
                "api_cost": round(api_cost, 4),
                "true_pnl": round(true_pnl, 4),
                "true_pnl_pct": round(true_pnl_pct, 2),
                "total_fees": round(total_fees, 4),
                "trade_count": trade_count,
                "win_rate": round(len(wins) / trade_count * 100, 1) if trade_count > 0 else 0.0,
                "avg_win": round(sum(t.pnl for t in wins) / len(wins), 4) if wins else 0.0,
                "avg_loss": round(sum(t.pnl for t in losses) / len(losses), 4) if losses else 0.0,
            }

    # ------------------------------------------------------------------
    # Sharpe Ratio
    # ------------------------------------------------------------------

    async def calculate_sharpe_ratio(self, agent_id: int, period_days: int = 14) -> float:
        """Calculate annualized Sharpe ratio from daily returns."""
        with self.db_session_factory() as session:
            cutoff = datetime.now(timezone.utc) - timedelta(days=period_days)
            trades = session.execute(
                select(Transaction).where(
                    Transaction.agent_id == agent_id,
                    Transaction.type != "api_cost",
                    Transaction.timestamp >= cutoff,
                ).order_by(Transaction.timestamp)
            ).scalars().all()

            if len(trades) < 2:
                return 0.0

            # Group PnL by day
            daily_pnl: dict[str, float] = {}
            for t in trades:
                day = t.timestamp.strftime("%Y-%m-%d")
                daily_pnl[day] = daily_pnl.get(day, 0.0) + t.pnl

            returns = list(daily_pnl.values())
            if len(returns) < 2:
                return 0.0

            arr = np.array(returns, dtype=np.float64)
            mean_return = float(np.mean(arr))
            std_return = float(np.std(arr, ddof=1))

            if std_return == 0.0:
                return 0.0

            sharpe = (mean_return / std_return) * math.sqrt(252)
            return round(sharpe, 4)

    # ------------------------------------------------------------------
    # Thinking Efficiency
    # ------------------------------------------------------------------

    async def calculate_thinking_efficiency(self, agent_id: int) -> float:
        """Thinking Efficiency = True P&L / API Cost. > 1.0 is profitable thinking."""
        pnl_data = await self.calculate_agent_pnl(agent_id)
        api_cost = pnl_data["api_cost"]
        if api_cost == 0.0:
            return 0.0
        return round(pnl_data["true_pnl"] / api_cost, 4)

    # ------------------------------------------------------------------
    # Consistency
    # ------------------------------------------------------------------

    async def calculate_consistency(self, agent_id: int) -> float:
        """Consistency = profitable_evaluations / total_evaluations."""
        with self.db_session_factory() as session:
            agent = session.get(Agent, agent_id)
            if agent is None or agent.evaluation_count == 0:
                return 0.0
            return round(agent.profitable_evaluations / agent.evaluation_count, 4)

    # ------------------------------------------------------------------
    # Composite Score
    # ------------------------------------------------------------------

    async def calculate_composite_score(self, agent_id: int) -> float:
        """Calculate the weighted composite score and persist to database.

        Composite = (0.40 * sharpe) + (0.25 * true_pnl_pct) +
                    (0.20 * thinking_efficiency) + (0.15 * consistency)

        Each component is normalized to 0-1 scale.
        """
        sharpe = await self.calculate_sharpe_ratio(agent_id)
        pnl_data = await self.calculate_agent_pnl(agent_id)
        efficiency = await self.calculate_thinking_efficiency(agent_id)
        consistency = await self.calculate_consistency(agent_id)

        # Normalize to 0-1 scale
        # Sharpe: clip to [-3, 3], then scale to [0, 1]
        norm_sharpe = max(0.0, min(1.0, (sharpe + 3.0) / 6.0))
        # True P&L %: clip to [-100, 100], scale to [0, 1]
        norm_pnl = max(0.0, min(1.0, (pnl_data["true_pnl_pct"] + 100.0) / 200.0))
        # Thinking efficiency: clip to [0, 10], scale to [0, 1]
        norm_efficiency = max(0.0, min(1.0, efficiency / 10.0))
        # Consistency: already 0-1
        norm_consistency = consistency

        composite = (
            config.eval_weight_sharpe * norm_sharpe
            + config.eval_weight_true_pnl * norm_pnl
            + config.eval_weight_thinking_efficiency * norm_efficiency
            + config.eval_weight_consistency * norm_consistency
        )
        composite = round(composite, 4)

        # Persist
        with self.db_session_factory() as session:
            agent = session.get(Agent, agent_id)
            if agent:
                agent.composite_score = composite
                agent.total_gross_pnl = pnl_data["gross_pnl"]
                agent.total_api_cost = pnl_data["api_cost"]
                agent.total_true_pnl = pnl_data["true_pnl"]
                session.commit()

        self.log.info(
            "composite_score_calculated",
            agent_id=agent_id,
            composite=composite,
            sharpe=sharpe,
            true_pnl_pct=pnl_data["true_pnl_pct"],
            efficiency=efficiency,
            consistency=consistency,
        )
        return composite

    # ------------------------------------------------------------------
    # Leaderboard
    # ------------------------------------------------------------------

    async def generate_leaderboard(self) -> list[dict]:
        """Calculate composite scores for all active agents, return ranked list."""
        with self.db_session_factory() as session:
            agents = session.execute(
                select(Agent).where(Agent.status.in_(["active", "evaluating"]))
            ).scalars().all()
            agent_ids = [a.id for a in agents]

        leaderboard = []
        for aid in agent_ids:
            score = await self.calculate_composite_score(aid)
            pnl_data = await self.calculate_agent_pnl(aid)
            sharpe = await self.calculate_sharpe_ratio(aid)

            with self.db_session_factory() as session:
                agent = session.get(Agent, aid)
                leaderboard.append({
                    "agent_id": aid,
                    "name": agent.name if agent else "unknown",
                    "composite_score": score,
                    "true_pnl": pnl_data["true_pnl"],
                    "sharpe": sharpe,
                    "prestige_title": agent.prestige_title if agent else None,
                })

        leaderboard.sort(key=lambda x: x["composite_score"], reverse=True)
        for i, entry in enumerate(leaderboard):
            entry["rank"] = i + 1

        return leaderboard

    # ------------------------------------------------------------------
    # API Cost Tracking
    # ------------------------------------------------------------------

    async def track_api_call(
        self,
        agent_id: int,
        model: str,
        input_tokens: int,
        output_tokens: int,
    ) -> float:
        """Track an API call cost. Returns the cost in USD."""
        pricing = MODEL_PRICING.get(model, MODEL_PRICING["default"])
        cost = (
            (input_tokens / 1_000_000) * pricing["input"]
            + (output_tokens / 1_000_000) * pricing["output"]
        )
        cost = round(cost, 6)

        with self.db_session_factory() as session:
            # Log as transaction
            tx = Transaction(
                agent_id=agent_id,
                type="api_cost",
                amount=cost,
                pnl=-cost,  # API costs are negative P&L
            )
            session.add(tx)

            # Update agent counters
            agent = session.get(Agent, agent_id)
            if agent:
                agent.thinking_budget_used_today = (agent.thinking_budget_used_today or 0.0) + cost
                agent.total_api_cost = (agent.total_api_cost or 0.0) + cost
                agent.api_cost_total = (agent.api_cost_total or 0.0) + cost

            session.commit()

        self.log.debug(
            "api_cost_tracked",
            agent_id=agent_id,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost=cost,
        )
        return cost

    # ------------------------------------------------------------------
    # System Summary
    # ------------------------------------------------------------------

    async def get_system_summary(self) -> dict:
        """Get a full system financial summary."""
        with self.db_session_factory() as session:
            state = session.execute(select(SystemState).limit(1)).scalar_one_or_none()

            total_treasury = state.total_treasury if state else 0.0
            peak_treasury = state.peak_treasury if state else 0.0

            # Count agents
            active_count = session.execute(
                select(func.count()).where(Agent.status == "active")
            ).scalar() or 0
            hibernating_count = session.execute(
                select(func.count()).where(Agent.status == "hibernating")
            ).scalar() or 0

            # Total API spend
            total_api_spend = session.execute(
                select(func.coalesce(func.sum(Transaction.amount), 0.0)).where(
                    Transaction.type == "api_cost"
                )
            ).scalar() or 0.0

            # Distance from circuit breaker
            distance_from_cb = 0.0
            if peak_treasury > 0:
                current_ratio = total_treasury / peak_treasury
                cb_ratio = 1.0 - config.circuit_breaker_threshold
                distance_from_cb = round((current_ratio - cb_ratio) / cb_ratio * 100, 1)

            return {
                "total_treasury": round(total_treasury, 2),
                "peak_treasury": round(peak_treasury, 2),
                "total_api_spend": round(float(total_api_spend), 4),
                "active_agents": active_count,
                "hibernating_agents": hibernating_count,
                "alert_status": state.alert_status if state else "unknown",
                "current_regime": state.current_regime if state else "unknown",
                "distance_from_circuit_breaker_pct": distance_from_cb,
            }
