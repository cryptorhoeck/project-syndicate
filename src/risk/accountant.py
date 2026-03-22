"""
Project Syndicate — The Accountant

Pure calculation engine. No LLM.
Handles P&L tracking, Sharpe ratio, composite scoring, leaderboard,
thinking tax collection, and system-wide financial summaries.
Phase 3.5: Multi-model cost tracking, cache savings, optimization stats.
"""

__version__ = "1.1.0"

import math
from datetime import datetime, timedelta, timezone

import numpy as np
import structlog
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from src.common.config import config
from src.common.models import Agent, AgentCycle, SystemState, Transaction

logger = structlog.get_logger()

# Model pricing per million tokens
MODEL_PRICING = {
    "claude-sonnet-4-20250514": {"input": 3.0, "output": 15.0},
    "claude-sonnet-4-6": {"input": 3.0, "output": 15.0},
    "claude-sonnet-4-5-20250514": {"input": 3.0, "output": 15.0},
    "claude-haiku-4-5-20251001": {"input": 1.0, "output": 5.0},
    # Default fallback
    "default": {"input": 3.0, "output": 15.0},
}

# Sonnet baseline rates for savings calculation
SONNET_INPUT_RATE = 3.0
SONNET_OUTPUT_RATE = 15.0


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

    async def calculate_sharpe_ratio(self, agent_id: int, period_days: int = 14) -> float | None:
        """Calculate annualized Sharpe ratio from daily returns.

        Returns None for non-Operator roles (they don't trade directly).
        """
        with self.db_session_factory() as session:
            agent = session.get(Agent, agent_id)
            if agent and agent.type not in ("operator", "genesis"):
                return None

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

        # Phase 8B: Reputation component (10% of composite)
        rep_weight = config.reputation_evaluation_weight
        perf_scale = 1.0 - rep_weight  # Scale down performance weights to make room

        # Get reputation for normalization
        with self.db_session_factory() as session:
            agent_for_rep = session.get(Agent, agent_id)
            rep_score = agent_for_rep.reputation_score if agent_for_rep else 0.0
            # Normalize reputation: 0-200 range → 0-1
            norm_reputation = max(0.0, min(1.0, rep_score / 200.0))

        composite = (
            config.eval_weight_sharpe * perf_scale * norm_sharpe
            + config.eval_weight_true_pnl * perf_scale * norm_pnl
            + config.eval_weight_thinking_efficiency * perf_scale * norm_efficiency
            + config.eval_weight_consistency * perf_scale * norm_consistency
            + rep_weight * norm_reputation
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
        cache_creation_tokens: int = 0,
        cache_read_tokens: int = 0,
        model_reason: str = "",
    ) -> float:
        """Track an API call cost with full breakdown.

        Args:
            agent_id: The agent that made the call.
            model: Model ID used for the call.
            input_tokens: Standard input tokens.
            output_tokens: Output tokens.
            cache_creation_tokens: Tokens written to cache (1.25x rate).
            cache_read_tokens: Tokens read from cache (0.1x rate).
            model_reason: Why this model was selected (for logging).

        Returns:
            Actual cost in USD.
        """
        pricing = MODEL_PRICING.get(model, MODEL_PRICING["default"])

        # Calculate actual cost components
        standard_input_cost = (input_tokens / 1_000_000) * pricing["input"]
        output_cost = (output_tokens / 1_000_000) * pricing["output"]
        cache_write_cost = (cache_creation_tokens / 1_000_000) * pricing["input"] * 1.25
        cache_read_cost = (cache_read_tokens / 1_000_000) * pricing["input"] * 0.10

        cost = round(standard_input_cost + output_cost + cache_write_cost + cache_read_cost, 6)

        # Calculate what this WOULD have cost at Sonnet rates without caching
        all_input = input_tokens + cache_creation_tokens + cache_read_tokens
        unoptimized_cost = (
            (all_input / 1_000_000) * SONNET_INPUT_RATE
            + (output_tokens / 1_000_000) * SONNET_OUTPUT_RATE
        )
        savings = round(unoptimized_cost - cost, 6)

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
            model_reason=model_reason,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_creation_tokens=cache_creation_tokens,
            cache_read_tokens=cache_read_tokens,
            cost=cost,
            savings=savings,
        )
        return cost

    # ------------------------------------------------------------------
    # System Summary
    # ------------------------------------------------------------------

    async def get_system_summary(self) -> dict:
        """Get a full system financial summary including cost optimization stats."""
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

            # Total API spend (all time)
            total_api_spend = session.execute(
                select(func.coalesce(func.sum(Transaction.amount), 0.0)).where(
                    Transaction.type == "api_cost"
                )
            ).scalar() or 0.0

            # Today's API spend
            today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
            today_api_spend = session.execute(
                select(func.coalesce(func.sum(Transaction.amount), 0.0)).where(
                    Transaction.type == "api_cost",
                    Transaction.timestamp >= today_start,
                )
            ).scalar() or 0.0

            # Distance from circuit breaker
            distance_from_cb = 0.0
            if peak_treasury > 0:
                current_ratio = total_treasury / peak_treasury
                cb_ratio = 1.0 - config.circuit_breaker_threshold
                distance_from_cb = round((current_ratio - cb_ratio) / cb_ratio * 100, 1)

            # Phase 3.5: Cost optimization stats from agent_cycles
            # Model distribution today
            try:
                haiku_count = session.execute(
                    select(func.count()).select_from(AgentCycle).where(
                        AgentCycle.timestamp >= today_start,
                        AgentCycle.model_used.like("%haiku%"),
                    )
                ).scalar() or 0
            except Exception:
                haiku_count = 0

            try:
                sonnet_count = session.execute(
                    select(func.count()).select_from(AgentCycle).where(
                        AgentCycle.timestamp >= today_start,
                        AgentCycle.model_used.like("%sonnet%"),
                    )
                ).scalar() or 0
            except Exception:
                sonnet_count = 0

            total_cycles_today = haiku_count + sonnet_count

            # Average cost per cycle today
            try:
                avg_cost = session.execute(
                    select(func.avg(AgentCycle.api_cost_usd)).where(
                        AgentCycle.timestamp >= today_start,
                        AgentCycle.api_cost_usd > 0,
                    )
                ).scalar() or 0.0
            except Exception:
                avg_cost = 0.0

            # Estimate savings: what all-Sonnet would have cost
            try:
                total_input_today = session.execute(
                    select(func.coalesce(func.sum(AgentCycle.input_tokens), 0)).where(
                        AgentCycle.timestamp >= today_start,
                    )
                ).scalar() or 0
                total_output_today = session.execute(
                    select(func.coalesce(func.sum(AgentCycle.output_tokens), 0)).where(
                        AgentCycle.timestamp >= today_start,
                    )
                ).scalar() or 0
            except Exception:
                total_input_today = 0
                total_output_today = 0

            sonnet_baseline_today = (
                (total_input_today / 1_000_000) * SONNET_INPUT_RATE
                + (total_output_today / 1_000_000) * SONNET_OUTPUT_RATE
            )
            savings_today = round(sonnet_baseline_today - float(today_api_spend), 4)

            # All-time savings estimate (rough: total tokens * diff)
            try:
                total_input_all = session.execute(
                    select(func.coalesce(func.sum(AgentCycle.input_tokens), 0))
                ).scalar() or 0
                total_output_all = session.execute(
                    select(func.coalesce(func.sum(AgentCycle.output_tokens), 0))
                ).scalar() or 0
            except Exception:
                total_input_all = 0
                total_output_all = 0

            sonnet_baseline_all = (
                (total_input_all / 1_000_000) * SONNET_INPUT_RATE
                + (total_output_all / 1_000_000) * SONNET_OUTPUT_RATE
            )
            savings_alltime = round(sonnet_baseline_all - float(total_api_spend), 4)

            return {
                "total_treasury": round(total_treasury, 2),
                "peak_treasury": round(peak_treasury, 2),
                "total_api_spend": round(float(total_api_spend), 4),
                "total_api_spend_today": round(float(today_api_spend), 4),
                "active_agents": active_count,
                "hibernating_agents": hibernating_count,
                "alert_status": state.alert_status if state else "unknown",
                "current_regime": state.current_regime if state else "unknown",
                "distance_from_circuit_breaker_pct": distance_from_cb,
                # Phase 3.5: Cost optimization stats
                "estimated_savings_today": max(0, savings_today),
                "estimated_savings_alltime": max(0, savings_alltime),
                "model_distribution_today": {
                    "haiku": haiku_count,
                    "sonnet": sonnet_count,
                },
                "haiku_ratio_today": round(haiku_count / total_cycles_today * 100, 1) if total_cycles_today > 0 else 0.0,
                "avg_cost_per_cycle_today": round(float(avg_cost), 6),
                "total_cycles_today": total_cycles_today,
            }
