"""
Project Syndicate — Genesis Agent

The immortal God Node. Manages the entire ecosystem:
spawning, evaluating, killing agents, capital allocation,
market regime detection, and daily reporting.
"""

__version__ = "1.0.0"

import json
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional, TYPE_CHECKING

import anthropic
import redis
import structlog
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from src.agora.schemas import MessageType
from src.common.base_agent import BaseAgent
from src.common.config import config
from src.common.models import (
    Agent,
    DailyReport,
    Evaluation,
    Lineage,
    Message,
    SystemState,
)
from src.genesis.ecosystem_contribution import EcosystemContributionCalculator
from src.genesis.evaluation_engine import EvaluationEngine
from src.genesis.regime_detector import RegimeDetector
from src.genesis.rejection_tracker import RejectionTracker
from src.genesis.treasury import TreasuryManager
from src.risk.accountant import Accountant

if TYPE_CHECKING:
    from src.agora.agora_service import AgoraService
    from src.economy.economy_service import EconomyService
    from src.library.library_service import LibraryService

logger = structlog.get_logger()


class GenesisAgent(BaseAgent):
    """The immortal Genesis agent — manages the entire ecosystem."""

    def __init__(
        self,
        db_session_factory: sessionmaker,
        exchange_service=None,
        agora_service: Optional["AgoraService"] = None,
        library_service: Optional["LibraryService"] = None,
        economy_service: Optional["EconomyService"] = None,
    ) -> None:
        super().__init__(
            agent_id=0,
            name="Genesis",
            agent_type="genesis",
            db_session_factory=db_session_factory,
            agora_service=agora_service,
            library_service=library_service,
        )

        self.exchange = exchange_service
        self.economy: Optional["EconomyService"] = economy_service
        self.accountant = Accountant(db_session_factory=db_session_factory)
        self.treasury = TreasuryManager(
            exchange_service=exchange_service,
            db_session_factory=db_session_factory,
        )
        self.regime_detector = RegimeDetector(
            exchange_service=exchange_service,
            db_session_factory=db_session_factory,
        )
        self.redis_client = redis.Redis.from_url(config.redis_url, decode_responses=True)

        # Anthropic client for Claude API calls
        self.claude: anthropic.Anthropic | None = None
        if config.anthropic_api_key:
            self.claude = anthropic.Anthropic(api_key=config.anthropic_api_key)

        # Track last hourly maintenance
        self._last_hourly_maintenance: datetime | None = None

        self.log = logger.bind(component="genesis")

    # ------------------------------------------------------------------
    # BaseAgent interface
    # ------------------------------------------------------------------

    async def initialize(self) -> None:
        """Initialize Genesis. Register self in agents table, create system_state if missing."""
        with self.db_session_factory() as session:
            # Ensure Genesis has a row in agents (needed for FK on messages, etc.)
            genesis_row = session.get(Agent, 0)
            if genesis_row is None:
                genesis_row = Agent(
                    id=0,
                    name="Genesis",
                    type="genesis",
                    status="active",
                    generation=0,
                    capital_allocated=0.0,
                    capital_current=0.0,
                    strategy_summary="Immortal God Node — ecosystem manager",
                )
                session.add(genesis_row)
                session.commit()
                self.log.info("genesis_agent_registered", agent_id=0)

            # Ensure system_state record exists
            state = session.execute(select(SystemState).limit(1)).scalar_one_or_none()
            if state is None:
                state = SystemState(
                    total_treasury=0.0,
                    peak_treasury=0.0,
                    current_regime="unknown",
                    active_agent_count=0,
                    alert_status="green",
                )
                session.add(state)
                session.commit()
                self.log.info("system_state_initialized")

        self.log.info("genesis_initialized")

    async def run(self) -> None:
        """Main Genesis loop — one iteration."""
        await self.run_cycle()

    async def evaluate(self) -> dict[str, Any]:
        """Genesis doesn't evaluate itself — it's immortal."""
        return {"status": "immortal"}

    # ------------------------------------------------------------------
    # The Genesis Cycle
    # ------------------------------------------------------------------

    async def run_cycle(self) -> dict:
        """One full Genesis cycle."""
        cycle_report: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        try:
            # 1. HEALTH CHECK
            health = self._health_check()
            cycle_report["health"] = health
            if not health["db_ok"] or not health["redis_ok"]:
                self.log.critical("health_check_failed", **health)
                return cycle_report

            # 2. TREASURY UPDATE
            await self.treasury.update_peak_treasury()
            await self.treasury.close_inherited_positions()
            treasury = await self.treasury.get_treasury_balance()
            cycle_report["treasury"] = treasury

            # 3. MARKET REGIME CHECK
            try:
                regime = await self.regime_detector.detect_regime()
                cycle_report["regime"] = regime
                if regime.get("changed"):
                    await self.post_to_agora(
                        "market-intel",
                        f"Regime change: {regime.get('previous_regime')} -> {regime['regime']}",
                        message_type=MessageType.SIGNAL,
                        metadata=regime.get("indicators"),
                        importance=1,
                    )
                    # Update system state
                    with self.db_session_factory() as session:
                        state = session.execute(select(SystemState).limit(1)).scalar_one_or_none()
                        if state:
                            state.current_regime = regime["regime"]
                            session.commit()
            except Exception as exc:
                self.log.warning("regime_detection_failed", error=str(exc))
                cycle_report["regime_error"] = str(exc)

            # 4. AGENT HEALTH CHECK
            agent_health = self._check_agent_health()
            cycle_report["agent_health"] = agent_health

            # 4b. NEGATIVE REPUTATION CHECK
            if self.economy is not None:
                try:
                    neg_rep_agents = await self.economy.check_negative_reputation_agents()
                    if neg_rep_agents:
                        for aid in neg_rep_agents:
                            if aid not in agent_health.get("due_for_evaluation", []):
                                agent_health.setdefault("due_for_evaluation", []).append(aid)
                        self.log.warning("negative_reputation_agents", agent_ids=neg_rep_agents)
                except Exception as exc:
                    self.log.warning("neg_rep_check_failed", error=str(exc))

            # 5. EVALUATIONS
            evaluations = await self._run_evaluations()
            cycle_report["evaluations"] = evaluations

            # 6. CAPITAL ALLOCATION
            if evaluations:
                leaderboard = await self.accountant.generate_leaderboard()
                allocations = await self.treasury.perform_capital_allocation_round(leaderboard)
                cycle_report["allocations"] = allocations

            # 7. SPAWN DECISIONS
            spawn = await self._make_spawn_decisions()
            cycle_report["spawn"] = spawn

            # 8. REPRODUCTION (top performers)
            reproduction = await self._check_reproduction()
            cycle_report["reproduction"] = reproduction

            # 9. AGORA MONITORING
            agora_summary = await self._monitor_agora()
            cycle_report["agora"] = agora_summary

            # 9b. SETTLEMENT CYCLE
            if self.economy is not None:
                try:
                    settlement_results = await self.economy.run_settlement_cycle()
                    if settlement_results.get("settled", 0) > 0 or settlement_results.get("expired", 0) > 0:
                        cycle_report["settlement"] = settlement_results
                        self.log.info("settlement_cycle", **settlement_results)
                except Exception as exc:
                    self.log.warning("settlement_cycle_failed", error=str(exc))

            # 10. HOURLY MAINTENANCE (expired messages cleanup)
            await self._maybe_run_hourly_maintenance()

            # 11. LOG CYCLE
            await self.post_to_agora(
                "genesis-log",
                f"Cycle complete. Treasury: ${treasury['total']:.2f}, "
                f"Agents: {agent_health.get('active', 0)} active",
                message_type=MessageType.SYSTEM,
                metadata={"cycle_report_keys": list(cycle_report.keys())},
            )

        except Exception as exc:
            self.log.error("genesis_cycle_error", error=str(exc))
            cycle_report["error"] = str(exc)

        return cycle_report

    # ------------------------------------------------------------------
    # Health Check
    # ------------------------------------------------------------------

    def _health_check(self) -> dict:
        """Verify database, Redis, and Warden are alive."""
        result = {"db_ok": False, "redis_ok": False, "warden_ok": False}

        # Database
        try:
            with self.db_session_factory() as session:
                session.execute(select(SystemState).limit(1))
            result["db_ok"] = True
        except Exception as exc:
            self.log.error("db_health_check_failed", error=str(exc))

        # Redis
        try:
            self.redis_client.ping()
            result["redis_ok"] = True
        except Exception as exc:
            self.log.error("redis_health_check_failed", error=str(exc))

        # Warden (check heartbeat key)
        try:
            heartbeat = self.redis_client.get("warden:heartbeat")
            result["warden_ok"] = heartbeat is not None
            if not result["warden_ok"]:
                self.log.warning("warden_heartbeat_missing")
        except Exception:
            pass

        return result

    # ------------------------------------------------------------------
    # Agent Health
    # ------------------------------------------------------------------

    def _check_agent_health(self) -> dict:
        """Check all active agents for survival clock expiry and budget limits."""
        now = datetime.now(timezone.utc)
        due_for_eval = []
        over_budget = []

        with self.db_session_factory() as session:
            agents = session.execute(
                select(Agent).where(Agent.status.in_(["active", "hibernating"]))
            ).scalars().all()

            active_count = sum(1 for a in agents if a.status == "active")
            hibernating_count = sum(1 for a in agents if a.status == "hibernating")

            for agent in agents:
                # Check survival clock
                if agent.survival_clock_end and agent.survival_clock_end <= now:
                    if not agent.survival_clock_paused:
                        due_for_eval.append(agent.id)

                # Check thinking budget
                if (agent.thinking_budget_used_today or 0) >= (agent.thinking_budget_daily or 1.0):
                    over_budget.append(agent.id)

            # Update system state
            state = session.execute(select(SystemState).limit(1)).scalar_one_or_none()
            if state:
                state.active_agent_count = active_count
                session.commit()

        return {
            "active": active_count,
            "hibernating": hibernating_count,
            "due_for_evaluation": due_for_eval,
            "over_budget": over_budget,
        }

    # ------------------------------------------------------------------
    # Evaluations
    # ------------------------------------------------------------------

    async def _run_evaluations(self) -> list[dict]:
        """Evaluate agents whose survival clocks have expired.

        Uses the Phase 3D EvaluationEngine for role-specific metrics,
        pre-filter, Genesis AI judgment, and decision execution.
        """
        now = datetime.now(timezone.utc)
        results = []

        with self.db_session_factory() as session:
            # Find agents due for evaluation
            due_agents = session.execute(
                select(Agent).where(
                    Agent.status == "active",
                    Agent.survival_clock_end <= now,
                    Agent.survival_clock_paused == False,
                )
            ).scalars().all()

            # Also include agents flagged for pending evaluation
            pending_agents = session.execute(
                select(Agent).where(
                    Agent.status == "active",
                    Agent.pending_evaluation == True,
                )
            ).scalars().all()

            # Deduplicate
            all_due = {a.id: a for a in due_agents}
            for a in pending_agents:
                all_due[a.id] = a

            if not all_due:
                return results

            agents_to_eval = list(all_due.values())

            # Determine evaluation period
            period_end = now
            period_start = now - timedelta(days=config.default_survival_clock_days)

            # Run evaluation engine
            engine = EvaluationEngine(
                db_session_factory=self.db_session_factory,
                agora_service=self.agora,
            )

            try:
                eval_results = await engine.evaluate_batch(
                    session, agents_to_eval, period_start, period_end
                )
            except Exception as exc:
                self.log.error("evaluation_batch_error", error=str(exc))
                return results

            # Decrement probation grace cycles for all active probation agents
            probation_agents = session.execute(
                select(Agent).where(
                    Agent.status == "active",
                    Agent.probation == True,
                    Agent.probation_grace_cycles > 0,
                )
            ).scalars().all()
            for pa in probation_agents:
                pa.probation_grace_cycles -= 1
                session.add(pa)

            session.commit()

            # Convert to result dicts and post to Agora
            for er in eval_results:
                result_dict = {
                    "agent_id": er.agent_id,
                    "name": er.agent_name,
                    "role": er.agent_role,
                    "pre_filter": er.pre_filter_result,
                    "genesis_decision": er.genesis_decision,
                    "evaluation_id": er.evaluation_id,
                    "composite_score": er.package.metrics.composite_score if er.package and er.package.metrics else 0,
                }
                results.append(result_dict)

                # Post to Agora
                decision = er.genesis_decision or er.pre_filter_result
                await self.post_to_agora(
                    "genesis-log",
                    f"Evaluation: {er.agent_name} ({er.agent_role}) — {decision} "
                    f"(score: {result_dict['composite_score']:.4f})",
                    message_type=MessageType.EVALUATION,
                    metadata=result_dict,
                )

            # Role gap detection — log if any critical role has no agents
            role_gaps = engine._detect_role_gaps(session)
            if role_gaps:
                self.log.warning("role_gaps_detected", gaps=role_gaps)
                await self.post_to_agora(
                    "genesis-log",
                    f"ROLE GAP ALERT: No active agents for: {', '.join(role_gaps)}",
                    message_type=MessageType.ALERT,
                    importance=2,
                )

        return results

    async def _evaluate_agent(self, agent_id: int, agent_name: str) -> dict:
        """Evaluate a single agent. Rules-based pre-filter, Claude for edge cases."""
        # Calculate composite score
        score = await self.accountant.calculate_composite_score(agent_id)
        pnl_data = await self.accountant.calculate_agent_pnl(agent_id)
        true_pnl_pct = pnl_data["true_pnl_pct"]

        decision = None
        reason = ""

        # Rules-based pre-filter
        if true_pnl_pct > 0:
            decision = "survive"
            reason = f"Profitable: True P&L {true_pnl_pct:+.1f}%"
        elif true_pnl_pct >= -10:
            # Probation candidate — ask Claude
            decision = await self._claude_probation_decision(agent_id, agent_name, pnl_data, score)
            reason = f"Claude probation decision for P&L {true_pnl_pct:+.1f}%"
        else:
            decision = "terminate"
            reason = f"Unprofitable: True P&L {true_pnl_pct:+.1f}% (below -10%)"

        # Execute decision
        with self.db_session_factory() as session:
            agent = session.get(Agent, agent_id)
            if agent is None:
                return {"agent_id": agent_id, "decision": "error", "reason": "Agent not found"}

            if decision == "terminate":
                agent.status = "terminated"
                agent.termination_reason = reason
                agent.terminated_at = datetime.now(timezone.utc)
                session.commit()

                # Reclaim capital and inherit positions
                await self.treasury.reclaim_capital(agent_id)
                await self.treasury.inherit_positions(agent_id)
                await self.post_to_agora(
                    "genesis-log",
                    f"TERMINATED: {agent_name} — {reason}",
                    message_type=MessageType.SYSTEM,
                    metadata=pnl_data,
                    importance=2,
                )

                # Create post-mortem in The Library
                if self.library is not None:
                    try:
                        await self.library.create_post_mortem(agent_id)
                    except Exception as exc:
                        self.log.warning("post_mortem_failed", agent_id=agent_id, error=str(exc))
            else:
                # Survive
                agent.evaluation_count = (agent.evaluation_count or 0) + 1
                if true_pnl_pct > 0:
                    agent.profitable_evaluations = (agent.profitable_evaluations or 0) + 1

                # Check prestige milestones
                evals = agent.evaluation_count
                if evals >= config.prestige_veteran_threshold:
                    agent.prestige_title = "Veteran"
                elif evals >= config.prestige_proven_threshold:
                    agent.prestige_title = "Proven"

                # Reset survival clock
                agent.survival_clock_start = datetime.now(timezone.utc)
                agent.survival_clock_end = datetime.now(timezone.utc) + timedelta(
                    days=config.default_survival_clock_days
                )
                session.commit()

            # Record evaluation
            evaluation = Evaluation(
                agent_id=agent_id,
                evaluation_type="survival_check",
                pnl_gross=pnl_data["gross_pnl"],
                pnl_net=pnl_data["true_pnl"],
                api_cost=pnl_data["api_cost"],
                sharpe_ratio=await self.accountant.calculate_sharpe_ratio(agent_id),
                result="survived" if decision == "survive" else "terminated",
                notes=reason,
            )
            session.add(evaluation)
            session.commit()
            evaluation_id = evaluation.id

            # Create strategy record for profitable survivors
            if decision == "survive" and true_pnl_pct > 0 and self.library is not None:
                try:
                    await self.library.create_strategy_record(agent_id, evaluation_id)
                except Exception as exc:
                    self.log.warning("strategy_record_failed", agent_id=agent_id, error=str(exc))

        # Post evaluation result to Agora
        await self.post_to_agora(
            "genesis-log",
            f"Evaluation: {agent_name} — {decision} (score: {score:.4f})",
            message_type=MessageType.EVALUATION,
            metadata={"agent_id": agent_id, "decision": decision, "score": score},
        )

        self.log.info(
            "agent_evaluated",
            agent_id=agent_id,
            name=agent_name,
            decision=decision,
            reason=reason,
            composite_score=score,
        )
        return {
            "agent_id": agent_id,
            "name": agent_name,
            "decision": decision,
            "reason": reason,
            "composite_score": score,
        }

    async def _claude_probation_decision(
        self,
        agent_id: int,
        agent_name: str,
        pnl_data: dict,
        score: float,
    ) -> str:
        """Ask Claude whether a borderline agent should survive."""
        if self.claude is None:
            return "survive"

        try:
            prompt = (
                f"Agent '{agent_name}' (ID: {agent_id}) is up for evaluation.\n"
                f"True P&L: {pnl_data['true_pnl_pct']:+.1f}%\n"
                f"Composite Score: {score:.4f}\n"
                f"Win Rate: {pnl_data['win_rate']}%\n"
                f"Trade Count: {pnl_data['trade_count']}\n"
                f"API Cost: ${pnl_data['api_cost']:.4f}\n\n"
                f"This agent is in the probation zone (-10% to 0% P&L). "
                f"Should it get another chance? Answer SURVIVE or TERMINATE, then explain why."
            )
            response = self.claude.messages.create(
                model="claude-sonnet-4-5-20250514",
                max_tokens=200,
                messages=[{"role": "user", "content": prompt}],
            )
            answer = response.content[0].text.strip().upper()

            await self.accountant.track_api_call(
                agent_id=0,
                model="claude-sonnet-4-5-20250514",
                input_tokens=response.usage.input_tokens,
                output_tokens=response.usage.output_tokens,
            )

            return "terminate" if "TERMINATE" in answer else "survive"
        except Exception as exc:
            self.log.error("claude_probation_error", error=str(exc))
            return "survive"

    # ------------------------------------------------------------------
    # Spawn Decisions
    # ------------------------------------------------------------------

    async def _make_spawn_decisions(self) -> dict:
        """Decide whether to spawn new agents. Uses Claude API."""
        balance = await self.treasury.get_treasury_balance()
        available = balance["available_for_allocation"]

        if available < config.min_spawn_capital:
            return {"spawned": False, "reason": "Insufficient capital"}

        with self.db_session_factory() as session:
            active_count = session.execute(
                select(func.count()).where(Agent.status == "active")
            ).scalar() or 0

        if active_count >= config.max_agents:
            return {"spawned": False, "reason": "Max agents reached"}

        if self.claude is None:
            return {"spawned": False, "reason": "No API key configured"}

        try:
            summary = await self.accountant.get_system_summary()
            prompt = (
                f"You are Genesis, managing the Project Syndicate AI trading ecosystem.\n"
                f"Current state:\n"
                f"- Treasury: ${summary['total_treasury']:.2f}\n"
                f"- Available for allocation: ${available:.2f}\n"
                f"- Active agents: {active_count}\n"
                f"- Market regime: {summary['current_regime']}\n"
                f"- Alert status: {summary['alert_status']}\n\n"
                f"Should I spawn a new agent? If yes, specify type (scout/strategist/critic/operator) and why.\n"
                f"Answer YES or NO, then explain."
            )
            response = self.claude.messages.create(
                model="claude-sonnet-4-5-20250514",
                max_tokens=300,
                messages=[{"role": "user", "content": prompt}],
            )
            answer = response.content[0].text.strip()

            await self.accountant.track_api_call(
                agent_id=0,
                model="claude-sonnet-4-5-20250514",
                input_tokens=response.usage.input_tokens,
                output_tokens=response.usage.output_tokens,
            )

            if "YES" in answer.upper()[:20]:
                await self.post_to_agora(
                    "genesis-log",
                    f"Spawn decision: YES — {answer[:200]}",
                    message_type=MessageType.SYSTEM,
                    importance=1,
                )
                return {"spawned": True, "claude_reasoning": answer[:500]}
            return {"spawned": False, "claude_reasoning": answer[:500]}

        except Exception as exc:
            self.log.error("spawn_decision_error", error=str(exc))
            return {"spawned": False, "reason": f"API error: {str(exc)}"}

    # ------------------------------------------------------------------
    # Reproduction
    # ------------------------------------------------------------------

    async def _check_reproduction(self) -> dict:
        """Check if top performer qualifies for reproduction."""
        with self.db_session_factory() as session:
            top_agent = session.execute(
                select(Agent)
                .where(Agent.status == "active")
                .order_by(Agent.composite_score.desc())
                .limit(1)
            ).scalar_one_or_none()

            if top_agent is None:
                return {"reproduction": False, "reason": "No active agents"}

            if top_agent.prestige_title not in ("Veteran", "Elite", "Legendary"):
                return {
                    "reproduction": False,
                    "reason": f"Top agent {top_agent.name} has insufficient prestige: {top_agent.prestige_title}",
                }

        return {
            "reproduction": True,
            "candidate": top_agent.name,
            "prestige": top_agent.prestige_title,
            "note": "Reproduction would be triggered here with Claude API mutation",
        }

    # ------------------------------------------------------------------
    # Agora Monitoring
    # ------------------------------------------------------------------

    async def _monitor_agora(self) -> dict:
        """Read recent Agora messages for SIPs and anomalies."""
        # Check unread messages via AgoraService
        unread = await self.get_agora_unread()

        # Process SIP proposals if any
        if unread.get("sip-proposals", 0) > 0:
            sips = await self.read_agora("sip-proposals", only_unread=True)
            self.log.info("sip_proposals_found", count=len(sips))
            await self.mark_agora_read("sip-proposals")

        # Get overall message count for the report
        with self.db_session_factory() as session:
            recent = session.execute(
                select(func.count()).select_from(Message).where(
                    Message.timestamp >= datetime.now(timezone.utc) - timedelta(hours=1)
                )
            ).scalar() or 0

        return {
            "messages_last_hour": recent,
            "unread_channels": unread,
        }

    # ------------------------------------------------------------------
    # Hourly Maintenance
    # ------------------------------------------------------------------

    async def _maybe_run_hourly_maintenance(self) -> None:
        """Run maintenance tasks once per hour."""
        now = datetime.now(timezone.utc)
        if (
            self._last_hourly_maintenance is not None
            and (now - self._last_hourly_maintenance) < timedelta(hours=1)
        ):
            return

        self._last_hourly_maintenance = now

        # Clean up expired Agora messages
        if self.agora is not None:
            deleted = await self.agora.cleanup_expired_messages()
            if deleted > 0:
                self.log.info("agora_cleanup", expired_messages_deleted=deleted)

        # Library maintenance: publish delayed entries, handle review timeouts
        if self.library is not None:
            published = await self.library.publish_delayed_entries()
            if published:
                self.log.info("library_delayed_published", count=len(published))
            await self.library.handle_review_timeouts()

        # Economy maintenance: expire stale review requests, check overdue assignments
        if self.economy is not None:
            try:
                expired = await self.economy.review_market.expire_stale_requests()
                overdue = await self.economy.review_market.check_overdue_assignments()
                if expired > 0 or overdue > 0:
                    self.log.info("economy_maintenance", expired_reviews=expired, overdue_assignments=overdue)
            except Exception as exc:
                self.log.warning("economy_maintenance_failed", error=str(exc))

        # Phase 3D: Rejection tracker monitoring (counterfactual simulation)
        try:
            from src.common.models import PostMortem
            tracker = RejectionTracker()
            with self.db_session_factory() as session:
                tracking_result = await tracker.monitor_tracked_rejections(session)
                if tracking_result.get("completed", 0) > 0:
                    self.log.info("rejection_tracker", **tracking_result)
                session.commit()
        except Exception as exc:
            self.log.warning("rejection_tracker_maintenance_failed", error=str(exc))

        # Phase 3D: Post-mortem publication (publish after 6-hour delay)
        try:
            from src.common.models import PostMortem, LibraryEntry
            with self.db_session_factory() as session:
                unpublished = session.execute(
                    select(PostMortem).where(
                        PostMortem.published == False,
                        PostMortem.publish_at <= now,
                    )
                ).scalars().all()
                for pm in unpublished:
                    # Create library entry
                    if self.library is not None:
                        entry = LibraryEntry(
                            category="post_mortem",
                            title=pm.title,
                            content=f"{pm.summary}\n\n{pm.lesson}\n\n{pm.recommendation}",
                            summary=pm.summary,
                            tags=["post_mortem", pm.agent_role, f"gen{pm.generation}"],
                            source_agent_id=pm.agent_id,
                            source_agent_name=pm.agent_name,
                            related_evaluation_id=pm.evaluation_id,
                            is_published=True,
                            published_at=now,
                        )
                        session.add(entry)
                        session.flush()
                        pm.library_entry_id = entry.id
                    pm.published = True
                    session.add(pm)
                if unpublished:
                    session.commit()
                    self.log.info("post_mortems_published", count=len(unpublished))
        except Exception as exc:
            self.log.warning("post_mortem_publication_failed", error=str(exc))

    # ------------------------------------------------------------------
    # Daily Report
    # ------------------------------------------------------------------

    async def generate_daily_report(self) -> str | None:
        """Generate and send daily report using Claude API."""
        summary = await self.accountant.get_system_summary()
        leaderboard = await self.accountant.generate_leaderboard()

        report_data = {
            "summary": summary,
            "leaderboard": leaderboard[:10],
            "date": date.today().isoformat(),
        }

        # Economy stats + gaming detection (daily)
        if self.economy is not None:
            try:
                economy_stats = await self.economy.get_economy_stats()
                report_data["economy"] = economy_stats.model_dump()
            except Exception as exc:
                self.log.warning("economy_stats_failed", error=str(exc))

            try:
                gaming_flags = await self.economy.run_gaming_detection()
                if gaming_flags:
                    report_data["gaming_flags"] = len(gaming_flags)
                    self.log.warning("gaming_flags_detected", count=len(gaming_flags))
            except Exception as exc:
                self.log.warning("gaming_detection_failed", error=str(exc))

        if self.claude is None:
            report_content = (
                f"Daily Report — {report_data['date']}\n"
                f"Treasury: ${summary['total_treasury']:.2f}\n"
                f"Active Agents: {summary['active_agents']}\n"
                f"Regime: {summary['current_regime']}\n"
                f"Alert: {summary['alert_status']}\n"
            )
        else:
            try:
                prompt = (
                    "You are the narrator of Project Syndicate, an autonomous AI trading ecosystem. "
                    "Generate a daily report for the owner. Be concise, insightful, and honest. "
                    "Highlight what matters. End with a one-line system health assessment: "
                    "thriving/stable/struggling/critical.\n\n"
                    f"Data: {json.dumps(report_data, default=str)}"
                )
                response = self.claude.messages.create(
                    model="claude-sonnet-4-5-20250514",
                    max_tokens=1000,
                    messages=[{"role": "user", "content": prompt}],
                )
                report_content = response.content[0].text

                await self.accountant.track_api_call(
                    agent_id=0,
                    model="claude-sonnet-4-5-20250514",
                    input_tokens=response.usage.input_tokens,
                    output_tokens=response.usage.output_tokens,
                )
            except Exception as exc:
                self.log.error("daily_report_generation_error", error=str(exc))
                report_content = f"Report generation failed: {str(exc)}"

        # Save to database
        with self.db_session_factory() as session:
            report = DailyReport(
                report_date=date.today(),
                treasury_balance=summary["total_treasury"],
                treasury_change_24h=0.0,
                active_agents=summary["active_agents"],
                market_regime=summary["current_regime"],
                alert_status=summary["alert_status"],
                total_api_cost_24h=summary["total_api_spend"],
                report_content=report_content,
            )
            session.add(report)
            session.commit()

        await self.post_to_agora(
            "daily-report",
            report_content[:500],
            message_type=MessageType.SYSTEM,
            importance=1,
        )
        self.log.info("daily_report_generated", date=date.today().isoformat())
        return report_content

    # ------------------------------------------------------------------
    # Cold Start Boot Sequence
    # ------------------------------------------------------------------

    async def cold_start_boot_sequence(self) -> list[dict]:
        """Spawn the initial Gen 1 agents when system has zero agents."""
        with self.db_session_factory() as session:
            active_count = session.execute(
                select(func.count()).where(Agent.status.in_(["active", "hibernating"]))
            ).scalar() or 0

        if active_count > 0:
            return [{"status": "skip", "reason": f"{active_count} agents already exist"}]

        balance = await self.treasury.get_treasury_balance()
        total = balance["total"]
        if total < config.min_spawn_capital:
            return [{"status": "skip", "reason": f"Insufficient capital: ${total}"}]

        gen1_agents = [
            {"name": "Scout-Alpha", "type": "scout", "mandate": "Crypto market scanner"},
            {"name": "Scout-Beta", "type": "scout", "mandate": "Broader opportunity scanner"},
            {"name": "Strategist-Prime", "type": "strategist", "mandate": "Strategy builder"},
            {"name": "Critic-One", "type": "critic", "mandate": "Plan stress-tester"},
            {"name": "Operator-Genesis", "type": "operator", "mandate": "First execution agent"},
        ]

        available = balance["available_for_allocation"]
        per_agent = available / len(gen1_agents)

        spawned = []
        now = datetime.now(timezone.utc)

        with self.db_session_factory() as session:
            for spec in gen1_agents:
                agent = Agent(
                    name=spec["name"],
                    type=spec["type"],
                    status="active",
                    generation=1,
                    capital_allocated=per_agent,
                    capital_current=per_agent,
                    thinking_budget_daily=config.new_agent_daily_thinking_budget,
                    strategy_summary=spec["mandate"],
                    survival_clock_start=now,
                    survival_clock_end=now + timedelta(days=config.default_survival_clock_days),
                )
                session.add(agent)
                session.flush()

                lineage = Lineage(
                    agent_id=agent.id,
                    parent_id=None,
                    generation=1,
                    lineage_path=str(agent.id),
                )
                session.add(lineage)

                spawned.append({
                    "agent_id": agent.id,
                    "name": spec["name"],
                    "type": spec["type"],
                    "capital": round(per_agent, 2),
                })

            session.commit()

        # Initialize reputation for new agents
        if self.economy is not None:
            for s in spawned:
                try:
                    await self.economy.initialize_agent_reputation(s["agent_id"])
                except Exception as exc:
                    self.log.warning("rep_init_failed", agent_id=s["agent_id"], error=str(exc))

        await self.post_to_agora(
            "genesis-log",
            f"GENESIS RECORD ZERO: {len(spawned)} Gen 1 agents spawned. "
            f"Total capital deployed: ${available:.2f}",
            message_type=MessageType.SYSTEM,
            metadata={"gen1_agents": spawned},
            importance=1,
        )
        self.log.info("cold_start_complete", agents_spawned=len(spawned))
        return spawned
