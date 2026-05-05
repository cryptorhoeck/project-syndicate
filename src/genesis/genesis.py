"""
Project Syndicate — Genesis Agent

The immortal God Node. Manages the entire ecosystem:
spawning, evaluating, killing agents, capital allocation,
market regime detection, and daily reporting.
"""

__version__ = "1.3.0"

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
from src.risk.dms_meta_monitor import DmsMetaMonitor, AGORA_CHANNEL as DMS_AGORA_CHANNEL
from src.wire.models import WireEvent

# Bound the regime-review queue work per cycle. Sev-5 events are rare;
# a backlog this large means the colony was offline for hours. Cap so
# one cycle never monopolises Genesis on queue catch-up. Excess rows
# stay 'pending' for the next cycle.
#
# Derivation: 50 rows × 5-min Genesis cycle = 600 events/hour catch-up
# throughput, which is ~10x the realistic sev-5 event rate (typically
# <1/hour during normal operation). Sufficient for catch-up after a
# multi-day colony outage without flooding any single cycle.
REGIME_REVIEW_BATCH_LIMIT = 50

# Per-row retry cap (Critic iteration 2 Finding 1, poison-pill guard).
# A row that has been consumed this many times without a successful
# end-of-cycle mark-reviewed gets flipped to 'failed' instead of being
# re-consumed. Same anti-DMS-self-defeat semantics as the warden
# safety_state_unknown latch.
#
# Derivation: a persistently-failing row is marked 'failed' after 3
# cycles = 15 minutes of retry. Long enough to absorb transient DB
# issues; short enough that a true poison-pill row doesn't block
# diagnostic attention indefinitely. Tunable via config if operational
# experience requires adjustment.
REGIME_REVIEW_MAX_ATTEMPTS = 3

# Cycle-level escalation cap for consumption-query failures (Critic
# iteration 2 Finding 5). Distinct from per-row attempt_count: this
# counts consecutive failures of the SELECT itself (DB unreachable,
# schema mismatch, etc.). After this many consecutive cycles where the
# query path is broken, escalate to CRITICAL + system-alert.
#
# CONTRACT (Critic iteration 3 Finding 4): consecutive-only.
# `_regime_review_query_failure_count` resets to 0 on the first
# successful consumption query. An intermittent pattern (fail,
# success, fail, fail, fail) does NOT escalate even though there
# were 4 failures in 5 cycles — the success in cycle 2 reset the
# counter, and only 3 consecutive failures are required.
#
# War Room confirmed this trade-off: persistent issues will hit the
# consecutive-failure threshold; truly intermittent issues are by
# definition self-healing. A cumulative-window detector
# (M-failures-within-N-cycles) is tracked in DEFERRED_ITEMS_TRACKER.md
# under "Regime review escalation: cumulative-window failure
# detection" as a future observability improvement. The negative test
# `test_escalation_does_not_fire_on_intermittent_pattern` locks in
# this contract.
REGIME_REVIEW_QUERY_FAILURE_ALERT_THRESHOLD = 3

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
        email_service=None,
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
        self.email_service = email_service
        self.accountant = Accountant(db_session_factory=db_session_factory)
        self.treasury = TreasuryManager(
            exchange_service=exchange_service,
            db_session_factory=db_session_factory,
        )
        self.regime_detector = RegimeDetector(
            exchange_service=exchange_service,
            db_session_factory=db_session_factory,
        )
        self.redis_client = redis.Redis.from_url(
            config.redis_url, decode_responses=True,
            socket_timeout=10, socket_connect_timeout=5, retry_on_timeout=True,
        )

        # Anthropic client for Claude API calls
        self.claude: anthropic.Anthropic | None = None
        if config.anthropic_api_key:
            self.claude = anthropic.Anthropic(api_key=config.anthropic_api_key)

        # Track last hourly maintenance
        self._last_hourly_maintenance: datetime | None = None
        # Track last daily budget reset
        self._last_budget_reset_date: date | None = None

        # Consumption-query failure latch (Critic iteration 2 Finding 5).
        # Counts consecutive cycles where the regime-review SELECT
        # itself raised. Reset to 0 on first successful query. At
        # REGIME_REVIEW_QUERY_FAILURE_ALERT_THRESHOLD, escalates to
        # CRITICAL + system-alert. Distinct from per-row attempt_count
        # which counts how many cycles a specific row has been consumed
        # without successful mark-reviewed.
        self._regime_review_query_failure_count: int = 0

        # Phase tag set by the consume helper before raising, read by
        # step 2c's escalation log so on-call can tell whether the
        # failure was on the read path (SELECT) or the write path
        # (commit). Set to "commit" by the commit's except branch;
        # left at None for SELECT failures (step 2c interprets None
        # as "select"). See `_consume_pending_regime_reviews`.
        self._last_query_failure_phase: Optional[str] = None

        # Dead Man's Switch meta-monitor. Genesis is the canonical "always
        # running" process and so is the natural host for the failsafe-of-
        # the-failsafe. The publish callable trips post_to_agora, which the
        # meta-monitor invokes via a sync wrapper on each cycle.
        def _dms_publish(channel, content, metadata):
            return self.post_to_agora(
                channel=channel,
                content=content,
                message_type=MessageType.ALERT,
                metadata=metadata,
                importance=2,  # critical
            )
        self.dms_meta_monitor = DmsMetaMonitor(publish=_dms_publish)

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

        # Consumed regime-review event IDs for this cycle. Populated at
        # top-of-cycle in step 2c, marked 'reviewed' at end-of-cycle in
        # step 12. If run_cycle raises mid-execution, the mark step does
        # NOT run and these rows stay 'pending' for the next cycle —
        # at-least-once semantics per WIRING_AUDIT subsystem H directive.
        consumed_regime_review_ids: list[int] = []

        try:
            # 0. BOOT SEQUENCE CHECK (auto-trigger on zero agents)
            boot_result = await self._maybe_run_boot_sequence()
            if boot_result:
                cycle_report["boot_sequence"] = boot_result

            # 1. HEALTH CHECK
            health = self._health_check()
            cycle_report["health"] = health
            if not health["db_ok"] or not health["redis_ok"]:
                self.log.critical("health_check_failed", **health)
                return cycle_report

            # 1b. DEAD MAN'S SWITCH META-MONITOR
            # Watches the heartbeat from outside the DMS process so a dead
            # DMS surfaces as `dead_mans_switch.silent_failure` in the Agora.
            try:
                with self.db_session_factory() as dms_session:
                    dms_status = self.dms_meta_monitor.check(dms_session)
                cycle_report["dms_heartbeat"] = {
                    "is_silent": dms_status.is_silent,
                    "age_seconds": dms_status.age_seconds,
                    "last_heartbeat_at": (
                        dms_status.last_heartbeat_at.isoformat()
                        if dms_status.last_heartbeat_at
                        else None
                    ),
                }
            except Exception as exc:
                # Monitoring code must never take down the host process.
                self.log.warning("dms_meta_monitor_failed", error=str(exc))

            # 2. TREASURY UPDATE
            await self.treasury.update_peak_treasury()
            await self.treasury.close_inherited_positions()
            treasury = await self.treasury.get_treasury_balance()
            cycle_report["treasury"] = treasury

            # 2c. CONSUME PENDING REGIME REVIEWS (subsystem H, Option C).
            # Sev-5 wire events queued by the digester are acknowledged
            # here. The ENRICHment is the structured log per consumed
            # row + the regime detection step below running with all
            # current data; the existing detect_regime() inputs are NOT
            # modified by this fix. Rows are flipped 'reviewed' at
            # end-of-cycle (step 12) so an exception in any later step
            # leaves them 'pending' for next cycle — at-least-once.
            #
            # Two distinct failure modes here:
            #   - per-row failures: handled inside the helper via the
            #     attempt_count cap (Finding 1).
            #   - consumption-query (SELECT) failures: counted via
            #     `_regime_review_query_failure_count`; after
            #     REGIME_REVIEW_QUERY_FAILURE_ALERT_THRESHOLD consecutive
            #     cycles, escalate to CRITICAL + system-alert
            #     (Finding 5). Reset on first success.
            try:
                self._last_query_failure_phase = None
                consumed_regime_review_ids = self._consume_pending_regime_reviews()
                if consumed_regime_review_ids:
                    cycle_report["regime_reviews_consumed"] = len(
                        consumed_regime_review_ids
                    )
                # Reset escalation counter on success.
                self._regime_review_query_failure_count = 0
            except Exception as exc:
                consumed_regime_review_ids = []
                self._regime_review_query_failure_count += 1
                threshold = REGIME_REVIEW_QUERY_FAILURE_ALERT_THRESHOLD
                # If the helper reached the commit before raising, the
                # commit's except branch will have tagged the phase
                # "commit". Otherwise the failure was upstream (SELECT
                # or row processing that escaped the per-row except),
                # tagged "select" for short-hand.
                failure_phase = self._last_query_failure_phase or "select"
                if self._regime_review_query_failure_count >= threshold:
                    self.log.critical(
                        "regime_review_query_failure_escalated",
                        consecutive_failures=self._regime_review_query_failure_count,
                        threshold=threshold,
                        failure_phase=failure_phase,
                        error=str(exc),
                        error_type=type(exc).__name__,
                    )
                    try:
                        await self.post_to_agora(
                            "system-alerts",
                            (
                                f"[REGIME REVIEW] Consumption query has failed "
                                f"{self._regime_review_query_failure_count} consecutive cycles "
                                f"(threshold {threshold}, phase {failure_phase}). "
                                f"Last error: {type(exc).__name__}: {exc}"
                            ),
                            message_type=MessageType.ALERT,
                            metadata={
                                "event_class": "regime_review.query_failure_escalated",
                                "consecutive_failures": self._regime_review_query_failure_count,
                                "threshold": threshold,
                                "failure_phase": failure_phase,
                                "error": str(exc),
                                "error_type": type(exc).__name__,
                            },
                            importance=2,
                        )
                    except Exception:
                        # Alert path is best-effort — log already fired.
                        self.log.exception(
                            "regime_review_query_alert_post_failed"
                        )
                else:
                    self.log.warning(
                        "regime_review_consumption_failed",
                        error=str(exc),
                        consecutive_failures=self._regime_review_query_failure_count,
                    )

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

            # 9c. INTEL ACCURACY SETTLEMENT (Phase 8B)
            try:
                from src.economy.intel_tracker import IntelAccuracyTracker
                tracker = IntelAccuracyTracker()
                with self.db_session_factory() as intel_session:
                    settled = await tracker.settle_pending_intel(intel_session)
                    challenged = await tracker.settle_challenges(intel_session)
                    intel_session.commit()
                    if settled or challenged:
                        self.log.info("intel_settlement", settled=settled, challenges=challenged)
            except Exception as exc:
                self.log.debug("intel_settlement_skipped", error=str(exc))

            # 9d. GOVERNANCE — Colony Maturity + SIP Lifecycle (Phase 9A)
            try:
                from src.governance.maturity_tracker import ColonyMaturityTracker
                from src.governance.sip_lifecycle import SIPLifecycleManager
                from src.governance.parameter_registry import ParameterRegistry

                with self.db_session_factory() as gov_session:
                    maturity_tracker = ColonyMaturityTracker()
                    stage, did_transition = await maturity_tracker.update(
                        gov_session, agora_service=self.agora
                    )
                    cycle_report["colony_maturity"] = stage.value if hasattr(stage, 'value') else str(stage)

                    sip_lifecycle = SIPLifecycleManager(
                        maturity_tracker, ParameterRegistry(), self.agora
                    )
                    await sip_lifecycle.advance_all_sips(gov_session)

                    # Genesis reviews tallied SIPs (passed the popular vote)
                    await self._review_tallied_sips(gov_session, maturity_tracker)

                    gov_session.commit()
            except Exception as exc:
                self.log.debug("governance_cycle_skipped", error=str(exc))

            # 9e. HIBERNATION WAKE CHECK — prevent total ecosystem death
            await self._check_hibernation_wake()

            # 10. HOURLY MAINTENANCE (expired messages cleanup)
            await self._maybe_run_hourly_maintenance()

            # 10b. EQUITY SNAPSHOTS + SANITY CHECKS (periodic)
            try:
                from src.trading.equity_snapshots import EquitySnapshotService
                snap_svc = EquitySnapshotService(db_session_factory=self.db_session_factory)
                snap_count = await snap_svc.take_snapshots()
                if snap_count:
                    cycle_report["equity_snapshots"] = snap_count
            except Exception as exc:
                self.log.debug("equity_snapshot_skipped", error=str(exc))

            try:
                from src.trading.sanity_checker import PaperTradingSanityChecker
                checker = PaperTradingSanityChecker(
                    db_session_factory=self.db_session_factory,
                    agora_service=self.agora,
                )
                sanity = await checker.run_all()
                issues = {k: v for k, v in sanity.items() if v}
                if issues:
                    self.log.warning("sanity_check_issues", issues=issues)
                    cycle_report["sanity_issues"] = issues
            except Exception as exc:
                self.log.debug("sanity_check_skipped", error=str(exc))

            # 11. LOG CYCLE
            await self.post_to_agora(
                "genesis-log",
                f"Cycle complete. Treasury: C${treasury['total']:.2f}, "
                f"Agents: {agent_health.get('active', 0)} active",
                message_type=MessageType.SYSTEM,
                metadata={"cycle_report_keys": list(cycle_report.keys())},
            )

            # 12. MARK CONSUMED REGIME REVIEWS (subsystem H, Option C).
            # Single UPDATE, runs only if every prior step in this try
            # block succeeded. Any exception in steps 3-11 leaves the
            # rows 'pending' for next cycle — by design.
            if consumed_regime_review_ids:
                self._mark_regime_reviews_reviewed(consumed_regime_review_ids)

        except Exception as exc:
            self.log.error("genesis_cycle_error", error=str(exc))
            cycle_report["error"] = str(exc)
            # NOTE (Critic iteration 3 Finding 2): cycle-level failures
            # do NOT stamp last_error on consumed rows. A poison row in
            # a batch of 50 would have corrupted the diagnostic data
            # for the other 49 — useless for identifying the actual
            # offender. Per-row failures are now stamped inside the
            # consumption loop, where the offending row is known.
            # Cycle-level failures surface via cycle_report["error"]
            # and the structured `genesis_cycle_error` log only.

        return cycle_report

    # ------------------------------------------------------------------
    # Regime-review queue (subsystem H, Option C)
    # ------------------------------------------------------------------

    def _consume_pending_regime_reviews(self) -> list[int]:
        """Read up to REGIME_REVIEW_BATCH_LIMIT consumable sev-5 wire
        events. "Consumable" excludes rows whose attempt_count has hit
        the retry cap (those are flipped to 'failed' in a separate
        pre-flip pass — Critic iteration 3 Finding 1).

        Two passes inside one session:
          1. PRE-FLIP: SELECT pending rows where
             attempt_count >= MAX_ATTEMPTS. Flip each to 'failed' with
             last_error populated. Defensive: even if the consume
             SELECT below were to mistakenly include them, this pass
             flips first.
          2. CONSUME: SELECT pending rows where
             attempt_count < MAX_ATTEMPTS. For each row, increment
             attempt_count BEFORE the operation (so a mid-cycle crash
             still records the attempt) and log the structured event.
             The increment is wrapped in a per-row try/except (Critic
             iteration 3 Finding 2) so an exception during one row's
             processing stamps last_error on THAT row only — never
             corrupts the diagnostic data of the other rows in the
             batch. Failed rows are NOT added to the consumed list,
             so end-of-cycle won't mark them 'reviewed'.

        Returns the list of consumed IDs ONLY for rows whose
        attempt_count increment was successfully persisted. The commit
        is wrapped in try/except (Critic iteration 4 Finding 2): on
        commit failure the helper raises, the in-memory mutations
        roll back via the `with` block's __exit__, and `run_cycle`
        step 2c's existing try/except sets `consumed_regime_review_ids
        = []` and increments the consumption-query failure counter.
        Net effect: zero event_id leakage to the mark-reviewed path,
        commit failures escalate alongside SELECT failures.

        End-of-cycle (step 12) marks the returned IDs 'reviewed' if
        every prior step in run_cycle succeeded; otherwise they stay
        'pending' for the next cycle (at-least-once).
        """
        with self.db_session_factory() as session:
            # PRE-FLIP PASS (Critic iteration 3 Finding 1).
            cap_rows = (
                session.execute(
                    select(WireEvent)
                    .where(WireEvent.regime_review_status == "pending")
                    .where(
                        WireEvent.attempt_count >= REGIME_REVIEW_MAX_ATTEMPTS
                    )
                )
                .scalars()
                .all()
            )
            failed_count = 0
            for row in cap_rows:
                row.regime_review_status = "failed"
                if not row.last_error:
                    row.last_error = (
                        f"exceeded max regime-review attempts "
                        f"({REGIME_REVIEW_MAX_ATTEMPTS}) without successful "
                        f"mark-reviewed"
                    )
                failed_count += 1
                self.log.critical(
                    "genesis_regime_review_poison_pill",
                    event_id=row.id,
                    severity=row.severity,
                    coin=row.coin,
                    event_type=row.event_type,
                    attempt_count=row.attempt_count,
                    last_error=row.last_error,
                )
            if failed_count > 0:
                self.log.critical(
                    "genesis_regime_review_failed_batch",
                    failed_count=failed_count,
                    max_attempts=REGIME_REVIEW_MAX_ATTEMPTS,
                )

            # CONSUME PASS. SELECT explicitly excludes rows at the cap
            # — defense-in-depth against a future refactor that drops
            # the pre-flip pass.
            rows = (
                session.execute(
                    select(WireEvent)
                    .where(WireEvent.regime_review_status == "pending")
                    .where(
                        WireEvent.attempt_count < REGIME_REVIEW_MAX_ATTEMPTS
                    )
                    .order_by(
                        WireEvent.severity.desc(), WireEvent.occurred_at
                    )
                    .limit(REGIME_REVIEW_BATCH_LIMIT)
                )
                .scalars()
                .all()
            )

            event_ids: list[int] = []
            for row in rows:
                # Increment attempt_count before processing, so retry cap fires even if helper raises during attribute access
                row.attempt_count = row.attempt_count + 1
                try:
                    # Per-row try/except (Critic iteration 3 Finding 2).
                    # last_error attribution is per-row, never batched.
                    self._process_pending_regime_review_row(row)
                    event_ids.append(row.id)
                except Exception as exc:
                    # Stamp last_error on THIS row only. The other rows
                    # in the batch keep their last_error untouched. The
                    # increment above already landed (Critic iteration 4
                    # follow-up 1), so the cap fires deterministically
                    # even on attribute-access failures inside the
                    # helper. Row stays 'pending' (NOT in event_ids,
                    # so end-of-cycle won't mark reviewed) and will be
                    # re-attempted next cycle until the cap fires.
                    row.last_error = f"{type(exc).__name__}: {exc}"
                    self.log.exception(
                        "regime_review_consume_row_failed",
                        event_id=row.id,
                        severity=row.severity,
                        coin=row.coin,
                        event_type=row.event_type,
                    )

            # COMMIT (Critic iteration 4 Finding 2): wrap explicitly so a
            # commit-time failure (deadlock retry exhausted, FK
            # violation, schema drift, network blip) is observable and
            # does NOT leak event_ids to the caller. The `with` block
            # would roll back the session on its way out via __exit__,
            # so the in-memory increments and last_error stamps are
            # discarded with the connection. We re-raise the exception
            # so `run_cycle` step 2c's existing try/except handles it
            # the same way as a SELECT-level failure: increments the
            # consumption-query failure counter, escalates after
            # threshold, sets `consumed_regime_review_ids = []`. End
            # result observable at the run_cycle layer: empty list,
            # nothing leaks, no row gets marked 'reviewed' on the back
            # of an increment that didn't persist.
            try:
                session.commit()
            except Exception as exc:
                # Tag the failure phase so step 2c's escalation log
                # can distinguish read-path (SELECT) from write-path
                # (commit) failures (Critic iteration 4 follow-up 3).
                self._last_query_failure_phase = "commit"
                self.log.critical(
                    "regime_review_consumption_commit_failed",
                    error=str(exc),
                    error_type=type(exc).__name__,
                    in_flight_event_ids=event_ids,
                )
                raise

        return event_ids

    def _process_pending_regime_review_row(self, row) -> None:
        """Per-row consumption side-effects (logging only).

        Critic iteration 4 follow-up 1: `attempt_count` is now
        incremented by the caller (`_consume_pending_regime_reviews`)
        BEFORE this helper is invoked. That guarantees the retry cap
        fires deterministically even if attribute access on a corrupt
        ORM object raises inside this method.

        This method now only emits the
        `genesis_consuming_regime_review` structured log. It still
        serves as the test seam for injecting per-row failures.
        """
        self.log.info(
            "genesis_consuming_regime_review",
            event_id=row.id,
            severity=row.severity,
            coin=row.coin,
            event_type=row.event_type,
            attempt_count=row.attempt_count,
        )

    def _mark_regime_reviews_reviewed(self, event_ids: list[int]) -> None:
        """Flip the given IDs to 'reviewed'.

        Critic iteration 2 Finding 2: filter by `id IN (event_ids)`
        ONLY — not also by `regime_review_status='pending'`. The
        consumption query at top-of-cycle returned exactly these IDs;
        we promised end-of-cycle to mark them. Keeping the status
        filter would silently drop the marker if a concurrent process
        (manual SQL, a separate Genesis instance) had flipped the
        status — and that's a less correct outcome than honoring the
        cycle's contract.

        The race the Critic flagged ("new pending rows arrive
        mid-cycle and get silently marked reviewed") is already
        impossible because the WHERE includes the explicit id list —
        new rows have new IDs the cycle never saw. The fix is to drop
        the redundant (and behaviorally-wrong) status filter.
        """
        if not event_ids:
            return
        with self.db_session_factory() as session:
            session.execute(
                WireEvent.__table__.update()
                .where(WireEvent.id.in_(event_ids))
                .values(regime_review_status="reviewed")
            )
            session.commit()


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
                # Check survival clock (handle naive/aware datetime mismatch)
                clock_end = agent.survival_clock_end
                if clock_end:
                    if clock_end.tzinfo is None:
                        clock_end = clock_end.replace(tzinfo=timezone.utc)
                if clock_end and clock_end <= now:
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

            # Genome diversity check (post-evaluation)
            try:
                from src.genome.diversity import calculate_diversity_index, should_apply_diversity_pressure
                diversity_idx = await calculate_diversity_index(None, session)
                if should_apply_diversity_pressure(diversity_idx):
                    self.log.warning(
                        "genome_convergence_alert",
                        diversity_index=round(diversity_idx, 3),
                        threshold=config.genome_diversity_low_threshold,
                    )
                    await self.post_to_agora(
                        "system-alerts",
                        f"Genome convergence alert: diversity index {diversity_idx:.3f} "
                        f"below threshold {config.genome_diversity_low_threshold}. "
                        f"Diversity pressure mutations will be applied at next reproduction.",
                        message_type=MessageType.ALERT,
                    )
            except Exception as exc:
                self.log.debug("diversity_check_skipped", error=str(exc))

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
                model=config.model_default,  # Haiku for cost efficiency
                max_tokens=200,
                messages=[{"role": "user", "content": prompt}],
            )
            answer = response.content[0].text.strip().upper()

            await self.accountant.track_api_call(
                agent_id=0,
                model=config.model_default,  # Haiku for cost efficiency
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
                model=config.model_default,  # Haiku for cost efficiency
                max_tokens=300,
                messages=[{"role": "user", "content": prompt}],
            )
            answer = response.content[0].text.strip()

            await self.accountant.track_api_call(
                agent_id=0,
                model=config.model_default,  # Haiku for cost efficiency
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
        """Check reproduction eligibility and execute if approved by Genesis AI."""
        from src.dynasty.reproduction import ReproductionEngine

        engine = ReproductionEngine()

        with self.db_session_factory() as session:
            result = await engine.check_and_reproduce(
                session, agora_service=self.agora,
            )
            session.commit()

        if result.reproduced:
            return {
                "reproduction": True,
                "parent": result.parent.name if result.parent else "unknown",
                "offspring": result.offspring.name if result.offspring else "unknown",
                "generation": result.offspring.generation if result.offspring else 0,
            }
        else:
            return {
                "reproduction": False,
                "reason": result.reason,
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

            # Phase 8B/9A: Genesis SIP review
            # If voting is enabled (Phase 9A), tallied SIPs are handled
            # in the governance cycle (step 9d). The legacy flow below
            # only runs when voting is disabled.
            if not config.sip_voting_enabled:
                try:
                    from src.common.models import SystemImprovementProposal
                    with self.db_session_factory() as sip_session:
                        pending_sips = list(sip_session.execute(
                            select(SystemImprovementProposal)
                            .where(
                                SystemImprovementProposal.status == "proposed",
                                SystemImprovementProposal.genesis_verdict.is_(None),
                            )
                            .limit(2)
                        ).scalars().all())

                        for sip in pending_sips:
                            try:
                                response = self.claude.messages.create(
                                    model=config.death_last_words_model,
                                    max_tokens=200,
                                    system="You are Genesis, evaluating a System Improvement Proposal for an AI trading ecosystem.",
                                    messages=[{"role": "user", "content": (
                                        f"Proposer: {sip.proposer_agent_name}\n"
                                        f"Title: {sip.title}\nCategory: {sip.category}\n"
                                        f"Proposal: {sip.proposal[:500]}\nRationale: {sip.rationale[:300]}\n\n"
                                        f"Respond in JSON: {{\"verdict\": \"approve|reject|defer\", \"reasoning\": \"under 100 words\"}}"
                                    )}],
                                )
                                try:
                                    verdict_data = json.loads(response.content[0].text)
                                    sip.genesis_verdict = verdict_data.get("verdict", "defer")
                                    sip.genesis_reasoning = verdict_data.get("reasoning", "")[:500]
                                except json.JSONDecodeError:
                                    sip.genesis_verdict = "defer"
                                    sip.genesis_reasoning = response.content[0].text[:500]
                                sip.status = "reviewed"
                                await self.accountant.track_api_call(
                                    agent_id=0, model=config.death_last_words_model,
                                    input_tokens=response.usage.input_tokens,
                                    output_tokens=response.usage.output_tokens,
                                )
                            except Exception as e:
                                self.log.debug(f"SIP review failed: {e}")
                        sip_session.commit()
                except Exception as e:
                    self.log.debug(f"SIP review cycle failed: {e}")

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
    # Phase 9A: Genesis Ratification of Tallied SIPs
    # ------------------------------------------------------------------

    async def _review_tallied_sips(self, db_session, maturity_tracker) -> None:
        """Review SIPs that passed the popular vote. Ratify or veto."""
        from src.common.models import SystemImprovementProposal, SIPDebate
        from src.governance.maturity_tracker import MATURITY_CONFIGS, MaturityStage

        tallied = list(db_session.execute(
            select(SystemImprovementProposal).where(
                SystemImprovementProposal.lifecycle_status == "tallied"
            ).limit(2)
        ).scalars().all())

        if not tallied:
            return

        config_obj = maturity_tracker.get_config(db_session)
        posture = config_obj.genesis_posture

        posture_text = {
            "permissive": "In this early stage, you are inclined to let agents experiment. Only veto if clearly harmful.",
            "balanced": "Evaluate whether the proposal benefits the ecosystem broadly or primarily serves the proposer.",
            "conservative": "The colony is established. Changes should be well-justified. Consider whether the current system works well enough.",
            "skeptical": "The colony is mature. The bar for change is high. Veto unless compelling evidence exists.",
        }

        for sip in tallied:
            now = datetime.now(timezone.utc)
            try:
                # Get debate arguments
                debates = db_session.execute(
                    select(SIPDebate).where(SIPDebate.sip_id == sip.id)
                ).scalars().all()
                for_args = [d.argument[:200] for d in debates if d.position == "for"][:3]
                against_args = [d.argument[:200] for d in debates if d.position == "against"][:3]

                pct = f"{sip.vote_pass_percentage * 100:.0f}%" if sip.vote_pass_percentage else "N/A"

                prompt = (
                    f"Proposer: {sip.proposer_agent_name}\n"
                    f"Title: {sip.title}\nCategory: {sip.category}\n"
                    f"Proposal: {sip.proposal[:500]}\nRationale: {sip.rationale[:300]}\n"
                    f"Target parameter: {sip.target_parameter_key or 'general proposal'}\n"
                    f"Proposed value: {sip.proposed_value}\n\n"
                    f"Vote result: {pct} support ({sip.weighted_support:.1f} for, {sip.weighted_oppose:.1f} against)\n"
                    f"Debate: {len(debates)} arguments\n"
                    f"Key arguments FOR: {'; '.join(for_args) or 'None'}\n"
                    f"Key arguments AGAINST: {'; '.join(against_args) or 'None'}\n\n"
                    f"Colony maturity: {config_obj.stage.value}\n"
                    f"{posture_text.get(posture, '')}\n\n"
                    f'Respond in JSON: {{"decision": "ratify"|"veto", "reasoning": "under 100 words"}}'
                )

                response = self.claude.messages.create(
                    model=config.death_last_words_model,
                    max_tokens=200,
                    system="You are Genesis, the immortal God Node of an AI trading ecosystem. A SIP has passed the agent vote. You must ratify or veto.",
                    messages=[{"role": "user", "content": prompt}],
                )

                try:
                    verdict = json.loads(response.content[0].text)
                except json.JSONDecodeError:
                    verdict = {"decision": "ratify", "reasoning": response.content[0].text[:200]}

                decision = verdict.get("decision", "ratify").lower()
                reasoning = verdict.get("reasoning", "")[:500]

                sip.genesis_verdict = "approved" if decision == "ratify" else "rejected"
                sip.genesis_reasoning = reasoning
                sip.genesis_reviewed_at = now

                if decision == "ratify":
                    sip.lifecycle_status = "owner_review"
                    await self.post_to_agora(
                        "sip-proposals",
                        f"[GENESIS RATIFICATION] SIP #{sip.id}: RATIFIED. {reasoning}",
                        message_type=MessageType.SYSTEM,
                    )
                else:
                    sip.lifecycle_status = "vetoed_by_genesis"
                    sip.genesis_veto_used = True
                    sip.resolved_at = now
                    await self.post_to_agora(
                        "system-alerts",
                        f"[GENESIS VETO] SIP #{sip.id} '{sip.title}' VETOED despite "
                        f"{pct} popular support. Reasoning: {reasoning}",
                        message_type=MessageType.SYSTEM,
                    )

                # Track API cost
                await self.accountant.track_api_call(
                    agent_id=0, model=config.death_last_words_model,
                    input_tokens=response.usage.input_tokens,
                    output_tokens=response.usage.output_tokens,
                )

            except Exception as e:
                self.log.debug(f"SIP ratification failed for #{sip.id}: {e}")

    # ------------------------------------------------------------------
    # Hibernation Wake Check
    # ------------------------------------------------------------------

    async def _check_hibernation_wake(self) -> None:
        """Detect total ecosystem hibernation and take corrective action.

        Checks three wake conditions:
        1. Regime change: if market regime changed since agent hibernated, wake them
        2. Duration expired: if agent set a duration-based wake, check expiry
        3. Total hibernation: if ALL agents are hibernating, force-wake the most
           promising ones to prevent a silent 21-day Arena
        """
        with self.db_session_factory() as session:
            hibernating = list(session.execute(
                select(Agent).where(
                    Agent.status == "hibernating",
                    Agent.id != 0,
                )
            ).scalars().all())

            active = session.execute(
                select(func.count()).where(
                    Agent.status == "active",
                    Agent.id != 0,
                )
            ).scalar() or 0

            if not hibernating:
                return

            state = session.execute(select(SystemState).limit(1)).scalar_one_or_none()
            current_regime = state.current_regime if state else "unknown"

            woken = []

            for agent in hibernating:
                should_wake = False
                reason = ""

                # Check regime change wake condition
                # (agents who hibernated when regime was different should wake)
                if current_regime != "unknown":
                    # Agent's last known regime is in their cycle context,
                    # but simplest: if regime is NOT "unknown", it may have changed
                    # We wake if they've been hibernating > 30 minutes
                    if agent.last_cycle_at:
                        last = agent.last_cycle_at
                        if last.tzinfo is None:
                            last = last.replace(tzinfo=timezone.utc)
                        mins_asleep = (datetime.now(timezone.utc) - last).total_seconds() / 60
                        if mins_asleep > 30:
                            should_wake = True
                            reason = f"regime_active ({current_regime}), asleep {mins_asleep:.0f}m"

                if should_wake:
                    agent.status = "active"
                    session.add(agent)
                    woken.append(agent.name)

            # CRITICAL: If ALL non-genesis agents are hibernating and none woke
            # from regime check, force-wake the top-ranked ones
            if active == 0 and not woken and hibernating:
                # Sort by composite score — wake the best performers
                ranked = sorted(hibernating, key=lambda a: a.composite_score or 0, reverse=True)
                # Wake at least 3 agents (or all if fewer than 3)
                to_wake = ranked[:min(3, len(ranked))]
                for agent in to_wake:
                    agent.status = "active"
                    session.add(agent)
                    woken.append(agent.name)

                self.log.warning(
                    "total_hibernation_override",
                    woken=woken,
                    reason="All agents hibernating — force-waking top performers",
                )

                # Post to Agora
                try:
                    await self.post_to_agora(
                        "genesis-log",
                        f"GENESIS OVERRIDE: All agents were hibernating. "
                        f"Force-waking {', '.join(woken)} to prevent ecosystem death.",
                        message_type=MessageType.SYSTEM,
                        importance=2,
                    )
                except Exception:
                    pass

            if woken:
                session.commit()
                self.log.info("hibernation_wake", woken=woken, active_after=active + len(woken))

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

        # Hourly-safe maintenance (subsystem T-subset fix). Runs every
        # hour: expire stale opportunities, clean up stuck plans,
        # prune Redis memory for terminated agents. The Arena's
        # 3-day stale-opportunities backlog was direct evidence this
        # block had no replacement before — only the daily-gated
        # budget reset below was wired, so the other three
        # MaintenanceService methods never ran.
        #
        # Deliberately separate from the daily-gated block below.
        # `run_all()` does NOT call `reset_daily_budgets` — running
        # that hourly would let agents consume 24x their intended
        # daily thinking budget. The cadence asymmetry is by design.
        #
        # WARNING-only failure handling is intentional. Unlike H
        # (regime review) and P (eval engine async), T-subset
        # maintenance failures have bounded downstream impact:
        # log-spam alone is the appropriate signal. See
        # DEFERRED_ITEMS_TRACKER.md entry "T-subset escalation
        # policy" for the design rationale.
        try:
            from src.agents.maintenance import MaintenanceService
            hourly_maint = MaintenanceService(self.db_session_factory)
            counts = await hourly_maint.run_all(redis_client=self.redis_client)
            self.log.info(
                "hourly_maintenance_completed",
                opportunities_expired=counts.get("opportunities_expired", 0),
                plans_cleaned=counts.get("plans_cleaned", 0),
                memory_pruned=counts.get("memory_pruned", 0),
            )
        except Exception as exc:
            self.log.warning("hourly_maintenance_failed", error=str(exc))

        # Daily budget reset (once per calendar day UTC)
        today = now.date()
        if self._last_budget_reset_date != today:
            try:
                from src.agents.maintenance import MaintenanceService
                maint = MaintenanceService(self.db_session_factory)
                reset_count = maint.reset_daily_budgets()
                self._last_budget_reset_date = today
                if reset_count > 0:
                    self.log.info("daily_budget_reset", agents_reset=reset_count)
            except Exception as exc:
                self.log.warning("daily_budget_reset_failed", error=str(exc))

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
                f"Treasury: C${summary['total_treasury']:.2f}\n"
                f"USDT/CAD Rate: {summary.get('usdt_cad_rate', 'N/A')}\n"
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
                    model=config.model_default,  # Haiku for cost efficiency
                    max_tokens=1000,
                    messages=[{"role": "user", "content": prompt}],
                )
                report_content = response.content[0].text

                await self.accountant.track_api_call(
                    agent_id=0,
                    model=config.model_default,  # Haiku for cost efficiency
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
                usdt_cad_rate=summary.get("usdt_cad_rate"),
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

        # Send daily report via email (no-op if SMTP not configured)
        if self.email_service is not None:
            try:
                await self.email_service.send_daily_report(
                    report_content, date.today().isoformat()
                )
            except Exception as exc:
                self.log.warning("daily_report_email_failed", error=str(exc))

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
            return [{"status": "skip", "reason": f"Insufficient capital: C${total:.2f}"}]

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
            f"Total capital deployed: C${available:.2f}",
            message_type=MessageType.SYSTEM,
            metadata={"gen1_agents": spawned},
            importance=1,
        )
        self.log.info("cold_start_complete", agents_spawned=len(spawned))
        return spawned

    async def _maybe_run_boot_sequence(self) -> dict | None:
        """Auto-trigger boot sequence when there are zero active agents.

        Uses the wave-based BootSequenceOrchestrator for proper
        orientation flow. Idempotent — safe to call every cycle.
        """
        with self.db_session_factory() as session:
            active_count = session.execute(
                select(func.count()).where(
                    Agent.status == "active",
                    Agent.id != 0,
                )
            ).scalar() or 0

            # Check for stuck initializing agents — orientation never completed
            stuck_count = session.execute(
                select(func.count()).where(
                    Agent.status == "initializing",
                    Agent.orientation_completed == False,
                    Agent.id != 0,
                )
            ).scalar() or 0

        if active_count > 0:
            return None

        # If agents are stuck initializing, re-run boot sequence
        # (it will skip spawning and go straight to orientation)
        if stuck_count > 0:
            self.log.info("boot_sequence_retry", reason=f"{stuck_count} agents stuck in initializing")

        if active_count == 0 and stuck_count == 0:
            # No agents at all — fresh boot
            pass

        self.log.info("boot_sequence_triggered", reason="zero_active_agents" if stuck_count == 0 else f"retry_orientation_for_{stuck_count}_agents")

        from src.agents.claude_client import ClaudeClient
        from src.agents.orientation import OrientationProtocol
        from src.genesis.boot_sequence import BootSequenceOrchestrator

        # Build ClaudeClient wrapper (orientation expects .call(), not raw SDK)
        claude_client = ClaudeClient(api_key=config.anthropic_api_key)

        # Orientation needs a db_session — use a dedicated session that the
        # boot sequence orchestrator will also use via its own session_factory.
        # The session here is a fallback; orient_agent uses _session_for(agent)
        # to pick up the agent's bound session when called from the orchestrator.
        with self.db_session_factory() as session:
            orientation = OrientationProtocol(
                db_session=session,
                claude_client=claude_client,
                config=config,
                agora_service=self.agora,
            )

            orchestrator = BootSequenceOrchestrator(
                db_session_factory=self.db_session_factory,
                orientation_protocol=orientation,
                agora_service=self.agora,
                economy_service=self.economy,
            )

            result = await orchestrator.run_boot_sequence()

        self.log.info(
            "boot_sequence_result",
            status=result.get("status"),
            spawned=len(result.get("agents_spawned", [])),
            oriented=len(result.get("agents_oriented", [])),
        )
        return result
