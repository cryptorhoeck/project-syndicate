"""
Project Syndicate — Evaluation Engine

Executes the 3-stage evaluation process:
  1. Quantitative pre-filter (role-specific thresholds)
  2. Genesis AI judgment (probation candidates only)
  3. Execute decisions (terminate/survive/probation)

Also handles role gap detection, capital reallocation,
and prestige milestone checks.
"""

__version__ = "1.2.0"

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

import anthropic
from sqlalchemy import select, func
from sqlalchemy.orm import Session

from src.common.async_bridge import run_async_safely
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
from src.dynasty.dynasty_manager import DynastyManager
from src.dynasty.lineage_manager import LineageManager
from src.dynasty.memorial_manager import MemorialManager

logger = logging.getLogger(__name__)

# Prestige milestones
PRESTIGE_MILESTONES = {
    3: "Apprentice",
    5: "Journeyman",
    10: "Expert",
    15: "Master",
    20: "Grandmaster",
}


# Subsystem P fix (WIRING_AUDIT_REPORT.md): consecutive-failure cap
# for async-bridge calls. After this many consecutive failures of a
# given call type, the engine emits CRITICAL + a system-alert post.
# Counter resets on the first success of that call type.
#
# Derivation (HONEST — Critic iteration 2 Finding 2 chose Option B):
# Threshold of 3 matches regime review's K=3
# (`REGIME_REVIEW_QUERY_FAILURE_ALERT_THRESHOLD` in `src/genesis/genesis.py`)
# for consistency across async-bridge users. The eval-engine cadence
# does NOT cleanly map to "K consecutive cycles = N minutes" the way
# regime review's per-cycle SELECT does — `EvaluationEngine` is
# instantiated FRESH at the top of each Genesis evaluation pass
# (see `genesis.py:858`), so the counter starts at 0 each cycle and
# 3 consecutive failures means "3 same-call-type failures in a row
# within ONE evaluation batch", not "3 cycles". The actual operational
# meaning therefore depends on batch size and per-agent call shape;
# fabricating a time-window derivation would be dishonest.
#
# Tunable if operational experience reveals a different appropriate
# value. The contract is consecutive-only failures; threshold value
# can move without changing the contract.
#
# CONTRACT: consecutive-only. An intermittent pattern (fail, success,
# fail, fail) does NOT escalate — see the negative test in
# `test_eval_engine_failure_escalation`. A cumulative-window detector
# is tracked in DEFERRED_ITEMS_TRACKER.md under "Regime review
# escalation: cumulative-window failure detection" — when that lands,
# the eval engine adopts it too.
ASYNC_FAILURE_ESCALATION_THRESHOLD = 3


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
        # Phase 3F: dynasty and memorial management
        self.dynasty_manager = DynastyManager()
        self.lineage_manager = LineageManager()
        self.memorial_manager = MemorialManager()

        # Subsystem P fix: per-call-type async-bridge failure counters.
        # Tracked separately because the two call types have different
        # root causes — track_api_call talks to the Accountant
        # (transactions table writes), update_fitness talks to the
        # GenomeManager (genome record + flush on the calling
        # thread's session). A failure in one shouldn't reset the
        # other's escalation counter. See `_record_async_outcome`.
        self._track_api_call_failure_count: int = 0
        self._update_fitness_failure_count: int = 0

    # ------------------------------------------------------------------
    # Async-bridge failure tracking (subsystem P fix)
    # ------------------------------------------------------------------

    def _record_async_outcome(
        self, call_type: str, success: bool, exc: Optional[Exception],
    ) -> None:
        """Update the per-call-type counter and escalate if the
        consecutive-failure threshold is reached.

        `call_type` must be one of "track_api_call" or
        "update_fitness". Unknown values are ignored (defensive).

        Resets the counter to 0 on success. On failure, increments
        the counter and (if at/over threshold) calls
        `_emit_async_failure_alert`.
        """
        if call_type == "track_api_call":
            counter_attr = "_track_api_call_failure_count"
        elif call_type == "update_fitness":
            counter_attr = "_update_fitness_failure_count"
        else:
            return

        if success:
            setattr(self, counter_attr, 0)
            return

        new_count = getattr(self, counter_attr) + 1
        setattr(self, counter_attr, new_count)

        if new_count >= ASYNC_FAILURE_ESCALATION_THRESHOLD:
            self._emit_async_failure_alert(call_type, exc, new_count)

    def _emit_async_failure_alert(
        self, call_type: str, exc: Optional[Exception], count: int,
    ) -> None:
        """Emit a consecutive-failure escalation alert.

        Contract (locked, Critic iteration 2 Finding 1):
          - The CRITICAL log is the alert-emission contract. It MUST
            fire FIRST, before the Agora post is even attempted. Once
            the CRITICAL line lands, the contract is satisfied — the
            failure is observable in stdout/log infrastructure that
            does not depend on Agora.
          - The Agora system-alerts post is a BEST-EFFORT secondary
            channel that may fail silently if Agora itself is
            unavailable. On Agora failure we log a single WARNING
            with the structured `agora_alert_emit_failed` field and
            return — we do NOT propagate the exception, do NOT
            increment any counter, and do NOT recursively re-escalate
            an alert-emit failure (alert-about-alert-about-alert is a
            maintenance trap; if Agora is down the colony will
            generate its own independent system-alerts elsewhere).

        Why not fire-and-forget the Agora post: that's exactly the
        anti-pattern subsystem P fixes. Outcomes from fire-and-forget
        coroutines are silently lost. Routing the post through
        `run_async_safely` blocks the calling sync frame for the
        Agora roundtrip (~5-50ms typical), but in exchange every
        Agora-emit failure is an observable WARNING with structured
        diagnostic fields rather than a black hole.
        """
        exc_type_name = type(exc).__name__ if exc is not None else "Unknown"
        exc_str = str(exc) if exc is not None else ""

        # 1. CRITICAL log — load-bearing signal. Fires FIRST so the
        # alert-emission contract is satisfied even if Agora is down.
        logger.critical(
            "eval_engine_async_failure_escalated",
            extra={
                "call_type": call_type,
                "consecutive_failures": count,
                "threshold": ASYNC_FAILURE_ESCALATION_THRESHOLD,
                "exception_type": exc_type_name,
                "exception_str": exc_str,
            },
        )

        # 2. Agora system-alerts post — best-effort secondary channel.
        if self.agora is None:
            return

        async def _post_alert() -> None:
            from src.agora.schemas import AgoraMessage, MessageType
            await self.agora.post_message(AgoraMessage(
                agent_id=0,
                agent_name="EvaluationEngine",
                channel="system-alerts",
                content=(
                    f"[EVAL ENGINE] {call_type} has failed {count} "
                    f"consecutive evaluations (threshold "
                    f"{ASYNC_FAILURE_ESCALATION_THRESHOLD}). "
                    f"Last error: {exc_type_name}: {exc_str}"
                ),
                message_type=MessageType.ALERT,
                importance=2,
                metadata={
                    "event_class": "eval_engine.async_failure_escalated",
                    "call_type": call_type,
                    "consecutive_failures": count,
                    "threshold": ASYNC_FAILURE_ESCALATION_THRESHOLD,
                    "exception_type": exc_type_name,
                    "exception_str": exc_str,
                },
            ))

        # Route through run_async_safely (NOT fire-and-forget) so the
        # outcome is observable. If the post raises, the helper
        # already logs an `async_bridge_failure` WARNING; we add a
        # second narrow WARNING tagged `agora_alert_emit_failed=True`
        # so dashboards can distinguish "Agora alert emit failed" from
        # generic async-bridge failures elsewhere in the engine. We
        # deliberately do NOT call `_record_async_outcome` here — that
        # would create the recursive alert-about-alert trap.
        post_success, post_exc = run_async_safely(
            _post_alert(), logger=logger,
        )
        if not post_success:
            logger.warning(
                "agora_alert_emit_failed",
                extra={
                    "agora_alert_emit_failed": True,
                    "call_type": call_type,
                    "underlying_failure_count": count,
                    "agora_exception_type": (
                        type(post_exc).__name__
                        if post_exc is not None else "Unknown"
                    ),
                    "agora_exception_str": (
                        str(post_exc) if post_exc is not None else ""
                    ),
                },
            )

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
                model=config.model_sonnet,  # Sonnet for high-stakes life/death judgment
                max_tokens=500,
                messages=[{"role": "user", "content": prompt}],
            )

            # Track cost through Accountant. track_api_call is async
            # because Accountant.track_api_call writes to the
            # transactions table inside its own session; failure here
            # means the cost is not recorded but the evaluation flow
            # proceeds (treasury lags by one Sonnet call until next
            # success). Subsystem P fix: was a fragile
            # `run_until_complete + bare except`; now wrapped in
            # `run_async_safely` so failures are observable via
            # WARNING + per-call-type counter and escalate to
            # CRITICAL + system-alert after 3 consecutive misses.
            from src.risk.accountant import Accountant
            acct = Accountant(db_session_factory=self.db_factory)
            success, exc = run_async_safely(
                acct.track_api_call(
                    agent_id=0, model=config.model_sonnet,
                    input_tokens=response.usage.input_tokens,
                    output_tokens=response.usage.output_tokens,
                ),
                logger=logger,
            )
            self._record_async_outcome("track_api_call", success, exc)

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
        """Terminate an agent: cancel orders, close positions, reclaim capital.

        Uses a savepoint for transaction safety — if the death protocol
        crashes partway through, the savepoint rolls back and the agent
        survives this evaluation rather than being left half-dead.
        """
        savepoint = session.begin_nested()
        try:
            await self._execute_death_protocol(session, agent, result, evaluation)
            savepoint.commit()
        except Exception as e:
            savepoint.rollback()
            logger.error(f"Death protocol failed for {agent.name}, agent survives: {e}")
            raise

    async def _execute_death_protocol(
        self, session: Session, agent: Agent,
        result: EvaluationResult, evaluation: Evaluation,
    ):
        """Inner death protocol — all steps inside a savepoint."""
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

        # Phase 8B: Generate last words before final death
        if config.death_last_words_enabled:
            try:
                client = anthropic.Anthropic(api_key=config.anthropic_api_key)
                last_words_response = client.messages.create(
                    model=config.death_last_words_model,
                    max_tokens=150,
                    system=(
                        f"You are {agent.name}. You have been terminated. This is your last cycle. "
                        f"Your evaluation: composite {evaluation.composite_score:.3f}. "
                        f"Genesis reasoning: {result.genesis_reasoning or result.pre_filter_result or 'underperformance'}. "
                        f"Leave ONE lesson under 100 words for surviving agents. Make it count."
                    ),
                    messages=[{"role": "user", "content": "Speak your last words."}],
                )
                words = last_words_response.content[0].text
                agent.last_words = str(words)[:500] if words else None
                # Track cost. Same async-bridge pattern as site 1.
                # Subsystem P fix: was `run_until_complete + bare
                # except`; now `run_async_safely` + counter.
                from src.risk.accountant import Accountant
                acct = Accountant(db_session_factory=self.db_factory)
                lw_success, lw_exc = run_async_safely(
                    acct.track_api_call(
                        agent_id=agent.id, model=config.death_last_words_model,
                        input_tokens=last_words_response.usage.input_tokens,
                        output_tokens=last_words_response.usage.output_tokens,
                    ),
                    logger=logger,
                )
                self._record_async_outcome("track_api_call", lw_success, lw_exc)
            except Exception as e:
                logger.debug(f"Last words generation failed for {agent.name}: {e}")

        # Phase 8B: Dissolve alliances on death
        try:
            from src.agents.alliance_manager import AllianceManager
            from src.common.models import AgentAlliance
            from sqlalchemy import or_ as sa_or
            # Sync dissolution — avoid async in this context
            alliances = list(session.execute(
                select(AgentAlliance).where(
                    AgentAlliance.status == "active",
                    sa_or(
                        AgentAlliance.proposer_agent_id == agent.id,
                        AgentAlliance.target_agent_id == agent.id,
                    ),
                )
            ).scalars().all())
            for alliance in alliances:
                alliance.status = "dissolved"
                alliance.dissolved_at = now
                alliance.dissolved_by = agent.id
                alliance.dissolution_reason = "Partner terminated"
            session.flush()
        except Exception as e:
            logger.debug(f"Alliance dissolution failed for {agent.name}: {e}")

        # Generate post-mortem
        await self._generate_post_mortem(session, agent, evaluation, result)

        # Phase 3E: Archive relationships for dead agent
        await self.relationship_manager.archive_dead_agent_relationships(session, agent.id)

        # Phase 3F: Knowledge preservation — memories kept but marked
        # (long-term memories NOT deleted, preserved for offspring inheritance)

        # Phase 3F: Lineage death record
        await self.lineage_manager.record_death(session, agent, evaluation)

        # Phase 3F: Dynasty death record
        await self.dynasty_manager.record_death(session, agent, agora_service=self.agora)

        # Phase 3F: Memorial record (The Fallen)
        await self.memorial_manager.create_memorial(session, agent, evaluation)

        # Phase 3F: Dynasty P&L update
        if agent.dynasty_id:
            await self.dynasty_manager.update_dynasty_pnl(session, agent.dynasty_id)

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

        # Phase 8C: Update genome fitness after evaluation. THE LOAD-
        # BEARING SELECTION-PRESSURE SITE — silent drops here corrupt
        # the colony's Darwinian mechanism.
        #
        # update_fitness is async (GenomeManager.update_fitness awaits
        # an internal get_genome_record). _apply_survival is SYNC but
        # called from the async _execute_decision -> running event
        # loop on this thread. The previous fragile pattern
        # (`run_until_complete + bare except`) raised on the running
        # loop and silently dropped the fitness write.
        #
        # Subsystem P fix: route through `run_async_safely`. When a
        # loop is running, the helper offloads to a worker thread on
        # a fresh event loop. SQLAlchemy sessions are NOT thread-safe,
        # so we MUST NOT pass the calling-thread `session` into the
        # worker. Wrap update_fitness in a closure that creates a
        # fresh session via `self.db_factory()` and commits inside
        # the worker thread. The fresh transaction writes the
        # genome_records row independently of the caller's session,
        # which is correct: genome_records are independent of the
        # agent record updates the caller will commit.
        from src.genome.genome_manager import GenomeManager
        genome_mgr = GenomeManager()
        agent_id_for_fitness = agent.id
        composite_score_for_fitness = agent.composite_score
        db_factory = self.db_factory

        async def _update_fitness_with_fresh_session() -> None:
            with db_factory() as fresh_session:
                await genome_mgr.update_fitness(
                    agent_id_for_fitness,
                    composite_score_for_fitness,
                    fresh_session,
                )
                fresh_session.commit()

        uf_success, uf_exc = run_async_safely(
            _update_fitness_with_fresh_session(),
            logger=logger,
        )
        self._record_async_outcome("update_fitness", uf_success, uf_exc)

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
                model=config.model_default,  # Haiku for cost efficiency
                max_tokens=1000,
                messages=[{"role": "user", "content": prompt}],
            )

            # Track cost. Same async-bridge pattern as sites 1 and 2.
            # Subsystem P fix: was `run_until_complete + bare except`;
            # now `run_async_safely` + counter.
            from src.risk.accountant import Accountant
            acct = Accountant(db_session_factory=self.db_factory)
            pm_success, pm_exc = run_async_safely(
                acct.track_api_call(
                    agent_id=0, model=config.model_default,
                    input_tokens=response.usage.input_tokens,
                    output_tokens=response.usage.output_tokens,
                ),
                logger=logger,
            )
            self._record_async_outcome("track_api_call", pm_success, pm_exc)

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
