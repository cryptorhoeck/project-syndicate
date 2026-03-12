"""
Project Syndicate — Evaluation Engine

Executes the 3-stage evaluation process:
  1. Quantitative pre-filter (role-specific thresholds)
  2. Genesis AI judgment (probation candidates only)
  3. Execute decisions (terminate/survive/probation)

Also handles role gap detection, capital reallocation,
and prestige milestone checks.
"""

__version__ = "1.1.0"

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

import anthropic
from sqlalchemy import select, func
from sqlalchemy.orm import Session

from src.common.config import config
from src.common.models import (
    Agent, Evaluation, Order, Position, PostMortem, SystemState, Transaction,
)
from src.genesis.evaluation_assembler import EvaluationAssembler, EvaluationPackage
from src.genesis.pipeline_analyzer import PipelineAnalyzer
from src.personality.behavioral_profile import BehavioralProfileCalculator
from src.personality.temperature_evolution import TemperatureEvolution
from src.personality.divergence import DivergenceCalculator
from src.personality.relationship_manager import RelationshipManager

logger = logging.getLogger(__name__)

# Prestige milestones
PRESTIGE_MILESTONES = {
    3: "Apprentice",
    5: "Journeyman",
    10: "Expert",
    15: "Master",
    20: "Grandmaster",
}


@dataclass
class EvaluationResult:
    """Result of evaluating a single agent."""
    agent_id: int
    agent_name: str
    agent_role: str
    pre_filter_result: str  # survive, probation, terminate
    genesis_decision: str | None = None  # survive_probation, terminate (for probation only)
    genesis_reasoning: str | None = None
    evaluation_id: int | None = None
    package: EvaluationPackage | None = None
    warning: str | None = None
    capital_adjustment: str | None = None
    thinking_budget_adjustment: str | None = None


class EvaluationEngine:
    """Runs the full evaluation cycle for a batch of agents."""

    def __init__(self, db_session_factory=None, agora_service=None):
        self.db_factory = db_session_factory
        self.agora = agora_service
        self.assembler = EvaluationAssembler()
        self.pipeline_analyzer = PipelineAnalyzer()
        self.profile_calculator = BehavioralProfileCalculator()
        self.temp_evolution = TemperatureEvolution()
        self.divergence_calculator = DivergenceCalculator()
        self.relationship_manager = RelationshipManager()

    async def evaluate_batch(
        self, session: Session, agents: list[Agent],
        period_start: datetime, period_end: datetime,
    ) -> list[EvaluationResult]:
        """Evaluate a batch of agents.

        Args:
            session: DB session.
            agents: List of agents to evaluate.
            period_start: Start of evaluation period.
            period_end: End of evaluation period.

        Returns:
            List of EvaluationResult objects.
        """
        results = []

        # Pipeline analysis (shared across all agents)
        pipeline_report = await self.pipeline_analyzer.analyze(
            session, period_start, period_end
        )

        # Get system state for regime info
        state = session.execute(select(SystemState)).scalars().first()
        regime = state.current_regime if state else "unknown"
        alert_status = state.alert_status if state else "green"

        # Calculate alert hours during period
        alert_hours = self._calculate_alert_hours(session, period_start, period_end)

        # Phase 1: Gather data + pre-filter for all agents
        packages = []
        for agent in agents:
            pkg = await self.assembler.build(
                session, agent, period_start, period_end, pipeline_report
            )
            packages.append(pkg)

        # Phase 2: Pre-filter each agent
        for pkg in packages:
            pre_result = self._pre_filter(pkg, alert_hours, period_start, period_end)
            results.append(pre_result)

        # Phase 3: Genesis AI judgment for probation candidates
        probation_candidates = [
            (r, p) for r, p in zip(results, packages)
            if r.pre_filter_result == "probation"
        ]
        if probation_candidates:
            await self._genesis_judgment(session, probation_candidates)

        # Phase 4: Execute decisions
        for result, pkg in zip(results, packages):
            await self._execute_decision(session, result, pkg, regime, alert_hours)

        # Phase 5: Role gap detection
        role_gaps = self._detect_role_gaps(session)
        if role_gaps:
            logger.warning(f"Role gaps detected: {role_gaps}")

        # Phase 6: Capital/budget reallocation
        await self._reallocate_capital_and_budget(session)

        # Phase 7 (3E): Behavioral profiles + temperature evolution for survivors
        surviving_eval_ids = []
        for result, pkg in zip(results, packages):
            if result.pre_filter_result != "terminate" and result.evaluation_id:
                agent = session.get(Agent, result.agent_id)
                if agent and agent.status in ("active", "frozen"):
                    try:
                        # Compute behavioral profile
                        profile = await self.profile_calculator.compute(
                            session, agent.id, result.evaluation_id,
                        )

                        # Personality drift detection
                        from src.common.models import BehavioralProfile
                        previous = session.execute(
                            select(BehavioralProfile).where(
                                BehavioralProfile.agent_id == agent.id,
                                BehavioralProfile.id != profile.id,
                            ).order_by(BehavioralProfile.created_at.desc()).limit(1)
                        ).scalar_one_or_none()

                        drift_flags = self.profile_calculator.detect_drift(previous, profile)
                        if drift_flags:
                            for df in drift_flags:
                                logger.warning(
                                    f"PERSONALITY DRIFT: {agent.name} {df.trait} "
                                    f"shifted from {df.old_label} to {df.new_label} "
                                    f"({df.tier_distance} tiers)"
                                )

                        # Temperature evolution
                        temp_result = await self.temp_evolution.evolve(
                            session, agent, period_start, period_end,
                        )

                        surviving_eval_ids.append(result.evaluation_id)
                    except Exception as e:
                        logger.error(f"Phase 3E processing failed for {agent.name}: {e}")

        # Phase 8 (3E): Divergence computation
        try:
            divergence_results = await self.divergence_calculator.compute_pairwise(session)
            if divergence_results:
                eval_id = surviving_eval_ids[0] if surviving_eval_ids else None
                await self.divergence_calculator.store_snapshot(
                    session, divergence_results, eval_id,
                )

                # Flag low divergence
                from src.common.config import config as cfg
                for dr in divergence_results:
                    if dr.score < cfg.divergence_low_threshold:
                        logger.info(
                            f"Low divergence ({dr.score:.3f}) between agents "
                            f"{dr.agent_a_id} and {dr.agent_b_id} (role: {dr.role})"
                        )
        except Exception as e:
            logger.error(f"Divergence computation failed: {e}")

        session.commit()

        return results

    def _pre_filter(
        self, pkg: EvaluationPackage, alert_hours: float,
        period_start: datetime, period_end: datetime,
    ) -> EvaluationResult:
        """Apply role-specific quantitative pre-filter.

        Returns EvaluationResult with pre_filter_result set.
        """
        result = EvaluationResult(
            agent_id=pkg.agent_id,
            agent_name=pkg.agent_name,
            agent_role=pkg.agent_role,
            pre_filter_result="probation",  # default
            package=pkg,
        )

        metrics = pkg.metrics
        if not metrics:
            result.pre_filter_result = "probation"
            return result

        # First-evaluation leniency
        is_first = pkg.evaluation_number <= 1
        if is_first and config.first_eval_leniency:
            result.pre_filter_result = "survive"
            return result

        # Regime adjustment: if alert hours > 50% of period, be lenient
        period_hours = max(1, (period_end - period_start).total_seconds() / 3600)
        regime_stressed = alert_hours > (period_hours * 0.5)

        role = pkg.agent_role
        breakdown = metrics.metric_breakdown

        if role == "operator":
            result.pre_filter_result = self._pre_filter_operator(breakdown, regime_stressed)
        elif role == "scout":
            result.pre_filter_result = self._pre_filter_scout(breakdown, pkg, regime_stressed)
        elif role == "strategist":
            result.pre_filter_result = self._pre_filter_strategist(breakdown, pkg, regime_stressed)
        elif role == "critic":
            result.pre_filter_result = self._pre_filter_critic(breakdown, pkg, regime_stressed)
        else:
            result.pre_filter_result = "survive"  # Genesis and unknown roles survive

        return result

    def _pre_filter_operator(self, breakdown: dict, regime_stressed: bool) -> str:
        """Operator: P&L > 0 = SURVIVE, -10% to 0 = PROBATION, < -10% = TERMINATE."""
        pnl_raw = breakdown.get("true_pnl_pct", {}).get("raw", 0)

        if pnl_raw > 0:
            return "survive"
        elif pnl_raw > -10.0 or regime_stressed:
            return "probation"
        else:
            return "terminate"

    def _pre_filter_scout(self, breakdown: dict, pkg: EvaluationPackage, regime_stressed: bool) -> str:
        """Scout: conversion > 0.10 AND profitability > 0 = SURVIVE."""
        conversion = breakdown.get("intel_conversion", {}).get("raw", 0)
        profitability = breakdown.get("intel_profitability", {}).get("raw", 0)
        total_opps = pkg.financial.get("positions_opened", 0)  # approximate

        if conversion > 0.10 and profitability > 0:
            return "survive"
        elif conversion > 0.05 or regime_stressed:
            return "probation"
        elif conversion < 0.05 and total_opps < 5:
            return "terminate"
        else:
            return "probation"

    def _pre_filter_strategist(self, breakdown: dict, pkg: EvaluationPackage, regime_stressed: bool) -> str:
        """Strategist: approval > 0.30 AND profitability > 0 = SURVIVE."""
        approval = breakdown.get("plan_approval_rate", {}).get("raw", 0)
        profitability = breakdown.get("plan_profitability", {}).get("raw", 0)

        if approval > 0.30 and profitability > 0:
            return "survive"
        elif approval > 0.15 or regime_stressed:
            return "probation"
        elif approval < 0.15:
            return "terminate"
        else:
            return "probation"

    def _pre_filter_critic(self, breakdown: dict, pkg: EvaluationPackage, regime_stressed: bool) -> str:
        """Critic: accuracy > 0.50 AND throughput > 0.5/day = SURVIVE."""
        accuracy = breakdown.get("approval_accuracy", {}).get("raw", 0)
        throughput = breakdown.get("throughput", {}).get("raw", 0)
        risk_flag_value = breakdown.get("risk_flag_value", {}).get("raw", 0)

        if accuracy > 0.50 and throughput > 0.5:
            return "survive"
        elif accuracy > 0.30 or risk_flag_value > 0 or regime_stressed:
            return "probation"
        elif accuracy < 0.30 and throughput < 0.3:
            return "terminate"
        else:
            return "probation"

    async def _genesis_judgment(
        self, session: Session,
        candidates: list[tuple[EvaluationResult, EvaluationPackage]],
    ):
        """Use Genesis AI to decide fate of probation candidates."""
        for result, pkg in candidates:
            try:
                decision, reasoning = await self._call_genesis_ai(pkg)
                result.genesis_decision = decision
                result.genesis_reasoning = reasoning
            except Exception as e:
                logger.error(f"Genesis AI judgment failed for {pkg.agent_name}: {e}")
                result.genesis_decision = "survive_probation"
                result.genesis_reasoning = f"AI judgment failed: {e}. Defaulting to probation."

    async def _call_genesis_ai(self, pkg: EvaluationPackage) -> tuple[str, str]:
        """Call Claude API for Genesis judgment on a probation candidate.

        Returns:
            Tuple of (decision, reasoning).
        """
        prompt = f"""You are Genesis, the immortal God Node of Project Syndicate.
An agent is on probation and you must decide their fate.

{pkg.compressed}

Based on this data, decide:
- "survive_probation" — give them a chance with reduced resources
- "terminate" — they are a net negative to the ecosystem

Respond with JSON: {{"decision": "survive_probation"|"terminate", "reasoning": "...", "warning": "..."}}
The warning will be shown to the agent if they survive."""

        try:
            client = anthropic.Anthropic(api_key=config.anthropic_api_key)
            response = client.messages.create(
                model="claude-sonnet-4-5-20250514",
                max_tokens=500,
                messages=[{"role": "user", "content": prompt}],
            )

            text = response.content[0].text
            # Extract JSON from response
            import re
            json_match = re.search(r'\{.*\}', text, re.DOTALL)
            if json_match:
                data = json.loads(json_match.group())
                return (
                    data.get("decision", "survive_probation"),
                    data.get("reasoning", text),
                )
            return ("survive_probation", text)

        except Exception as e:
            logger.error(f"Claude API call failed: {e}")
            return ("survive_probation", f"API error: {e}")

    async def _execute_decision(
        self, session: Session, result: EvaluationResult,
        pkg: EvaluationPackage, regime: str, alert_hours: float,
    ):
        """Execute the evaluation decision for an agent."""
        agent = session.get(Agent, result.agent_id)
        if not agent:
            return

        now = datetime.now(timezone.utc)
        is_first = pkg.evaluation_number <= 1

        # Determine final decision
        if result.pre_filter_result == "survive":
            final = "survived"
        elif result.pre_filter_result == "terminate":
            if is_first and config.first_eval_leniency:
                final = "survived"  # First-eval leniency
            else:
                final = "terminated"
        else:  # probation
            if result.genesis_decision == "terminate":
                if is_first and config.first_eval_leniency:
                    final = "probation"
                else:
                    final = "terminated"
            else:
                final = "probation"

        # Create evaluation record
        evaluation = Evaluation(
            agent_id=agent.id,
            evaluation_type="survival_check",
            agent_name=agent.name,
            agent_role=agent.type,
            generation=agent.generation,
            evaluation_number=pkg.evaluation_number,
            evaluation_period_start=pkg.period_start,
            evaluation_period_end=pkg.period_end,
            evaluated_at=now,
            composite_score=pkg.metrics.composite_score if pkg.metrics else 0.0,
            metric_breakdown=pkg.metrics.metric_breakdown if pkg.metrics else {},
            role_rank=pkg.ecosystem_context.get("role_rank"),
            role_rank_total=pkg.ecosystem_context.get("role_total"),
            ecosystem_contribution_score=agent.ecosystem_contribution,
            pre_filter_result=result.pre_filter_result,
            genesis_decision=result.genesis_decision,
            genesis_reasoning=result.genesis_reasoning,
            warning_to_agent=result.warning,
            market_regime=regime,
            alert_hours_during_period=alert_hours,
            regime_adjustment_applied=alert_hours > 0,
            first_evaluation=is_first,
            prestige_before=agent.prestige_title,
            capital_before=agent.capital_allocated,
            thinking_budget_before=agent.thinking_budget_daily,
            pnl_gross=agent.total_gross_pnl,
            pnl_net=agent.total_true_pnl,
            api_cost=pkg.financial.get("api_cost_period", 0),
            result=final,
        )

        # Flush evaluation first so it has an ID for post-mortems
        session.add(evaluation)
        session.flush()

        if final == "terminated":
            await self._terminate_agent(session, agent, result, evaluation)
        elif final == "probation":
            self._apply_probation(session, agent, result, evaluation)
        else:
            self._apply_survival(session, agent, result, evaluation, pkg)

        # Store evaluation ID
        result.evaluation_id = evaluation.id
        agent.last_evaluation_id = evaluation.id
        agent.evaluation_count += 1
        agent.pending_evaluation = False

        # Check prestige milestones
        self._check_prestige(agent, evaluation)

        evaluation.prestige_after = agent.prestige_title
        evaluation.capital_after = agent.capital_allocated
        evaluation.thinking_budget_after = agent.thinking_budget_daily

        session.add(agent)

    async def _terminate_agent(
        self, session: Session, agent: Agent,
        result: EvaluationResult, evaluation: Evaluation,
    ):
        """Terminate an agent: cancel orders, close positions, reclaim capital."""
        now = datetime.now(timezone.utc)

        # Cancel all pending orders
        pending_orders = session.execute(
            select(Order).where(
                Order.agent_id == agent.id,
                Order.status == "pending",
            )
        ).scalars().all()
        for order in pending_orders:
            order.status = "cancelled"
            order.rejection_reason = "agent_terminated"
            if order.reserved_amount:
                agent.reserved_cash -= order.reserved_amount
                order.reservation_released = True
            session.add(order)

        # Mark open positions for inheritance (Genesis inherits)
        open_positions = session.execute(
            select(Position).where(
                Position.agent_id == agent.id,
                Position.status == "open",
            )
        ).scalars().all()
        for position in open_positions:
            position.status = "closed"
            position.close_reason = "agent_death"
            position.closed_at = now
            session.add(position)

        # Update agent
        agent.status = "terminated"
        agent.terminated_at = now
        agent.termination_reason = (
            result.genesis_reasoning or
            f"Pre-filter: {result.pre_filter_result}"
        )

        # Generate post-mortem
        await self._generate_post_mortem(session, agent, evaluation, result)

        # Phase 3E: Archive relationships for dead agent
        await self.relationship_manager.archive_dead_agent_relationships(session, agent.id)

        logger.info(f"Agent {agent.name} terminated: {agent.termination_reason}")

    def _apply_probation(
        self, session: Session, agent: Agent,
        result: EvaluationResult, evaluation: Evaluation,
    ):
        """Apply probation: shortened clock, budget cut, grace period."""
        agent.probation = True
        agent.probation_grace_cycles = config.probation_grace_cycles

        # Shortened survival clock (half of original)
        if agent.survival_clock_end:
            remaining = agent.survival_clock_end - datetime.now(timezone.utc)
            new_remaining = remaining / 2
            agent.survival_clock_end = datetime.now(timezone.utc) + new_remaining
            evaluation.survival_clock_new_days = max(1, int(new_remaining.days))

        # Budget cut
        original_budget = agent.thinking_budget_daily
        agent.thinking_budget_daily *= (1 - config.probation_budget_decrease)
        evaluation.thinking_budget_adjustment = (
            f"reduced {config.probation_budget_decrease:.0%}"
        )

        # Warning
        warning = (
            result.genesis_reasoning or
            "You are on probation. Improve performance or face termination."
        )
        agent.evaluation_scorecard = {
            "result": "probation",
            "composite_score": evaluation.composite_score,
            "warning": warning,
            "metrics": evaluation.metric_breakdown,
        }
        evaluation.warning_to_agent = warning
        result.warning = warning

        logger.info(
            f"Agent {agent.name} placed on probation "
            f"(budget: ${original_budget:.2f} → ${agent.thinking_budget_daily:.2f})"
        )

    def _apply_survival(
        self, session: Session, agent: Agent,
        result: EvaluationResult, evaluation: Evaluation,
        pkg: EvaluationPackage,
    ):
        """Apply survival: update counters, reset clock."""
        # Update profitable evaluation counter
        true_pnl = pkg.financial.get("true_pnl", 0)
        if true_pnl > 0:
            agent.profitable_evaluations += 1

        # Reset survival clock
        if agent.survival_clock_end:
            clock_days = config.survival_clock_days if hasattr(config, 'survival_clock_days') else 14
            agent.survival_clock_end = datetime.now(timezone.utc) + timedelta(days=clock_days)
            evaluation.survival_clock_new_days = clock_days

        # Clear probation if was on probation
        if agent.probation:
            agent.probation = False
            agent.probation_grace_cycles = 0

        # Scorecard for agent context
        agent.evaluation_scorecard = {
            "result": "survived",
            "composite_score": evaluation.composite_score,
            "rank": pkg.ecosystem_context.get("role_rank"),
            "metrics": evaluation.metric_breakdown,
        }

        agent.composite_score = evaluation.composite_score or 0.0

    def _check_prestige(self, agent: Agent, evaluation: Evaluation):
        """Check if agent has reached a prestige milestone."""
        evals = agent.evaluation_count
        for threshold, title in sorted(PRESTIGE_MILESTONES.items()):
            if evals >= threshold:
                if agent.prestige_title != title:
                    agent.prestige_title = title
                    logger.info(f"Agent {agent.name} promoted to {title}!")

    async def _generate_post_mortem(
        self, session: Session, agent: Agent,
        evaluation: Evaluation, result: EvaluationResult,
    ):
        """Generate a post-mortem for a terminated agent."""
        now = datetime.now(timezone.utc)
        publish_at = now + timedelta(hours=config.post_mortem_publish_delay_hours)

        try:
            client = anthropic.Anthropic(api_key=config.anthropic_api_key)
            prompt = f"""Generate a post-mortem analysis for a terminated trading agent.

Agent: {agent.name} (Role: {agent.type}, Generation: {agent.generation})
Termination reason: {agent.termination_reason}
Composite score: {evaluation.composite_score}
Metrics: {json.dumps(evaluation.metric_breakdown or {}, indent=2)}

Respond with JSON:
{{
    "title": "Post-Mortem: [agent name]",
    "summary": "One paragraph summary",
    "what_went_wrong": "Key failures",
    "what_went_right": "Any positives",
    "lesson": "What future agents can learn",
    "market_context": "Market conditions during agent's life",
    "recommendation": "What to do differently"
}}"""

            response = client.messages.create(
                model="claude-sonnet-4-5-20250514",
                max_tokens=1000,
                messages=[{"role": "user", "content": prompt}],
            )

            text = response.content[0].text
            import re
            json_match = re.search(r'\{.*\}', text, re.DOTALL)
            if json_match:
                data = json.loads(json_match.group())
            else:
                data = {
                    "title": f"Post-Mortem: {agent.name}",
                    "summary": text[:500],
                    "what_went_wrong": agent.termination_reason or "Unknown",
                    "what_went_right": "Insufficient data",
                    "lesson": "Further analysis needed",
                    "market_context": "Unknown",
                    "recommendation": "Monitor similar patterns",
                }
        except Exception as e:
            logger.error(f"Post-mortem generation failed: {e}")
            data = {
                "title": f"Post-Mortem: {agent.name}",
                "summary": f"Agent terminated. Reason: {agent.termination_reason}",
                "what_went_wrong": agent.termination_reason or "Performance below threshold",
                "what_went_right": "Agent operated within risk limits",
                "lesson": "Monitor key performance metrics more closely",
                "market_context": "See market regime data for period",
                "recommendation": "Adjust spawn parameters for similar roles",
            }

        post_mortem = PostMortem(
            agent_id=agent.id,
            agent_name=agent.name,
            agent_role=agent.type,
            generation=agent.generation,
            evaluation_id=evaluation.id,
            title=data.get("title", f"Post-Mortem: {agent.name}"),
            summary=data.get("summary", ""),
            what_went_wrong=data.get("what_went_wrong", ""),
            what_went_right=data.get("what_went_right", ""),
            lesson=data.get("lesson", ""),
            market_context=data.get("market_context", ""),
            recommendation=data.get("recommendation", ""),
            genesis_visible=True,
            published=False,
            publish_at=publish_at,
        )
        session.add(post_mortem)
        session.flush()

        logger.info(
            f"Post-mortem created for {agent.name}, "
            f"publish scheduled at {publish_at.isoformat()}"
        )

    def _detect_role_gaps(self, session: Session) -> list[str]:
        """Detect if any critical role has no active agents."""
        gaps = []
        for role in ["scout", "strategist", "critic", "operator"]:
            count = session.execute(
                select(func.count()).select_from(Agent).where(
                    Agent.type == role,
                    Agent.status == "active",
                )
            ).scalar()
            if count == 0:
                gaps.append(role)
        return gaps

    async def _reallocate_capital_and_budget(self, session: Session):
        """Reallocate capital and thinking budget based on performance."""
        active_agents = session.execute(
            select(Agent).where(Agent.status == "active")
        ).scalars().all()

        if not active_agents:
            return

        # Sort by ecosystem contribution
        ranked = sorted(
            active_agents,
            key=lambda a: a.ecosystem_contribution,
            reverse=True,
        )

        for i, agent in enumerate(ranked):
            agent.role_rank = i + 1

            # Budget adjustments for top performers
            if i == 0:  # Top performer
                agent.thinking_budget_daily *= (1 + config.top_performer_budget_increase)
            elif i == 1:  # Second
                agent.thinking_budget_daily *= (1 + config.second_performer_budget_increase)

            session.add(agent)

    def _calculate_alert_hours(
        self, session: Session,
        period_start: datetime, period_end: datetime,
    ) -> float:
        """Calculate hours the system was in alert state during period."""
        # Simplified: check current alert status and estimate
        state = session.execute(select(SystemState)).scalars().first()
        if not state:
            return 0.0

        if state.alert_status in ("yellow", "red", "circuit_breaker"):
            # Rough estimate: assume alert for some portion of period
            period_hours = (period_end - period_start).total_seconds() / 3600
            multiplier = {"yellow": 0.25, "red": 0.5, "circuit_breaker": 1.0}
            return period_hours * multiplier.get(state.alert_status, 0)

        return 0.0
