"""
Project Syndicate — The Warden

CRITICAL: Immutable safety layer. No LLM, no Claude API, pure code.
Runs as its own independent process on a 30-second loop.

Responsibilities:
  - Circuit breaker (75% loss from peak)
  - Black Swan Protocol (Yellow/Red alerts)
  - Per-agent loss limits (50% = instant kill)
  - Trade gate (approve/reject/hold trade requests)
  - Alert escalation with email notifications
"""

__version__ = "1.0.0"

import asyncio
import json
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional, TYPE_CHECKING

import redis
import structlog
from sqlalchemy import create_engine, select, update
from sqlalchemy.orm import Session, sessionmaker

from src.common.config import config
from src.common.models import Agent, InheritedPosition, Position, SystemState

if TYPE_CHECKING:
    from src.agora.agora_service import AgoraService

logger = structlog.get_logger()


class Warden:
    """The immutable risk layer. No AI, pure deterministic checks."""

    def __init__(
        self,
        db_session_factory: sessionmaker | None = None,
        agora_service: Optional["AgoraService"] = None,
    ) -> None:
        self.log = logger.bind(component="warden")

        # Database
        if db_session_factory:
            self.db_session_factory = db_session_factory
        else:
            engine = create_engine(config.database_url)
            self.db_session_factory = sessionmaker(bind=engine)

        # Redis
        self.redis = redis.Redis.from_url(
            config.redis_url, decode_responses=True,
            socket_timeout=10, socket_connect_timeout=5, retry_on_timeout=True,
        )

        # Configuration
        self.CIRCUIT_BREAKER_THRESHOLD = config.circuit_breaker_threshold
        self.YELLOW_ALERT_THRESHOLD = config.yellow_alert_threshold
        self.RED_ALERT_THRESHOLD = config.red_alert_threshold
        self.PER_AGENT_MAX_POSITION_PCT = config.per_agent_max_position_pct
        self.PER_AGENT_MAX_LOSS_PCT = config.per_agent_max_loss_pct
        self.TRADE_GATE_THRESHOLD = config.trade_gate_threshold

        # Agora (optional — Warden POSTS but NEVER READS)
        self._agora: Optional["AgoraService"] = agora_service

        # Current alert status. Default starts as unknown — the first
        # evaluate_trade call refreshes from the DB and either adopts the
        # canonical value or fails closed to RED. This prevents an
        # in-process Warden from accidentally approving trades during the
        # window between construction and the first DB read.
        self.alert_status: str = "green"
        self._safety_state_unknown: bool = False

        # 4-hour rolling treasury snapshots for drawdown calculation
        self._treasury_snapshots: list[tuple[datetime, float]] = []

        self.log.info(
            "warden_initialized",
            circuit_breaker=self.CIRCUIT_BREAKER_THRESHOLD,
            yellow=self.YELLOW_ALERT_THRESHOLD,
            red=self.RED_ALERT_THRESHOLD,
        )

    # ------------------------------------------------------------------
    # Main 30-Second Check Cycle
    # ------------------------------------------------------------------

    async def check_cycle(self) -> dict[str, Any]:
        """One full Warden check cycle. Returns cycle report."""
        cycle_start = time.monotonic()
        report: dict[str, Any] = {"timestamp": datetime.now(timezone.utc).isoformat()}

        try:
            # 1. Read system state
            state = self._get_system_state()
            if state is None:
                self.log.warning("no_system_state_found")
                return report

            treasury = state.total_treasury
            peak = state.peak_treasury
            old_alert = self.alert_status

            # Record treasury snapshot for 4-hour rolling window
            now = datetime.now(timezone.utc)
            self._treasury_snapshots.append((now, treasury))
            cutoff = now - timedelta(hours=4)
            self._treasury_snapshots = [
                (ts, val) for ts, val in self._treasury_snapshots if ts >= cutoff
            ]

            # 2. Check circuit breaker
            if peak > 0 and treasury / peak < (1.0 - self.CIRCUIT_BREAKER_THRESHOLD):
                self.alert_status = "circuit_breaker"
                report["circuit_breaker"] = True

            # 3. Check 4-hour rolling drawdown
            elif self._treasury_snapshots:
                max_in_window = max(val for _, val in self._treasury_snapshots)
                if max_in_window > 0:
                    drawdown = (max_in_window - treasury) / max_in_window
                    if drawdown >= self.RED_ALERT_THRESHOLD:
                        self.alert_status = "red"
                    elif drawdown >= self.YELLOW_ALERT_THRESHOLD:
                        self.alert_status = "yellow"
                    elif self.alert_status not in ("circuit_breaker",):
                        self.alert_status = "green"
                    report["rolling_drawdown_4h"] = round(drawdown, 4)

            # 4. Check per-agent loss limits
            flagged_agents = self._check_agent_losses()
            report["flagged_for_termination"] = flagged_agents

            # 5. Process pending trade requests
            processed = await self._process_trade_requests()
            report["trades_processed"] = len(processed)

            # 6. Handle alert status change
            if self.alert_status != old_alert:
                await self.escalate_alert(self.alert_status)
                report["alert_changed"] = {"from": old_alert, "to": self.alert_status}

            # 7. Update system state
            self._update_alert_status(self.alert_status)

            # 8. Update Warden heartbeat in Redis
            self.redis.set("warden:heartbeat", datetime.now(timezone.utc).isoformat(), ex=120)

        except Exception as exc:
            self.log.error("check_cycle_error", error=str(exc))
            report["error"] = str(exc)

        elapsed = time.monotonic() - cycle_start
        report["elapsed_ms"] = round(elapsed * 1000)
        self.log.info("check_cycle_complete", alert=self.alert_status, elapsed_ms=report["elapsed_ms"])
        return report

    # ------------------------------------------------------------------
    # Trade Gate
    # ------------------------------------------------------------------

    def _refresh_alert_status_from_db(self) -> None:
        """Read system_state.alert_status from the canonical DB row.

        The Warden process owns alert_status and writes it via
        `_update_alert_status` on its 30-second cycle. In-process Warden
        instances constructed inside the agent runtime (so trade-time
        evaluation can fire) MUST refresh from the DB or they'll evaluate
        every trade as "green" forever. This is the wiring fix from
        WIRING_AUDIT_REPORT.md subsystems K/L/M.

        FAIL-CLOSED-TO-RED: when the read fails OR the row is absent, we
        cannot determine the colony's safety state. A safety system that
        cannot determine state must assume worst-case. We set
        `_safety_state_unknown = True`; `evaluate_trade` checks that flag
        before any other gate and rejects the trade with a reason that
        cites unknown-state, not a real RED alert.

        AUTO-RECOVERY (NOT sticky): the unknown latch and the forced-red
        override are cleared on the next successful refresh. A single
        transient DB blip MUST NOT permanently halt the colony — that
        would be the DMS self-defeating-loop pattern in a new costume.
        Recovery is exercised end-to-end by
        `test_safety_unknown_auto_clears_on_db_recovery`. If you change
        the success path below, make sure you also keep the unconditional
        flag-clear + alert_status overwrite. War Room iteration 3 audit.
        """
        try:
            state = self._get_system_state()
        except Exception as exc:
            self._safety_state_unknown = True
            self.alert_status = "red"
            self.log.error(
                "warden_alert_refresh_failed_failing_closed",
                error=str(exc),
            )
            return

        if state is None or not state.alert_status:
            self._safety_state_unknown = True
            self.alert_status = "red"
            self.log.error(
                "warden_alert_refresh_no_state_row",
                state_present=state is not None,
            )
            return

        # Successful read clears the unknown flag and adopts the DB value.
        self._safety_state_unknown = False
        self.alert_status = state.alert_status

    async def evaluate_trade(self, trade_request: dict) -> dict:
        """Evaluate a trade request through the gate.

        Returns: {status: 'approved'/'rejected'/'held', reason: str, request_id: str}
        """
        # Sync alert_status with the canonical DB value before judging.
        # Fail-closed-to-red on read failure — see _refresh_alert_status_from_db.
        self._refresh_alert_status_from_db()

        request_id = trade_request.get("request_id", str(uuid.uuid4())[:12])
        agent_id = trade_request["agent_id"]
        amount = trade_request.get("amount", 0)
        price = trade_request.get("price", 0)
        trade_value = amount * price if price else amount

        result = {"request_id": request_id, "agent_id": agent_id}

        # 0. Safety state unknown (DB unreadable) — fail closed.
        # MUST come before all other gates: if we can't read state, we
        # cannot trust any subsequent check that depends on it.
        if getattr(self, "_safety_state_unknown", False):
            result["status"] = "rejected"
            result["reason"] = (
                "Safety state unknown (alert_status DB read failed) — "
                "failing closed to RED. No trades until canonical state is readable."
            )
            self.log.warning("trade_rejected_safety_state_unknown", **result)
            return result

        # 1. Circuit breaker — reject all
        if self.alert_status == "circuit_breaker":
            result["status"] = "rejected"
            result["reason"] = "CIRCUIT BREAKER active — all trading halted"
            self.log.warning("trade_rejected_circuit_breaker", **result)
            return result

        # 2. Red alert — reject all
        if self.alert_status == "red":
            result["status"] = "rejected"
            result["reason"] = "RED ALERT active — all trading halted for 24h"
            self.log.warning("trade_rejected_red_alert", **result)
            return result

        # 3. Yellow alert — hold all for review
        if self.alert_status == "yellow":
            result["status"] = "held"
            result["reason"] = "YELLOW ALERT — all trades require pre-approval"
            self.log.info("trade_held_yellow_alert", **result)
            return result

        # 4. Check agent position limits
        agent = self._get_agent(agent_id)
        if agent is None:
            result["status"] = "rejected"
            result["reason"] = f"Agent {agent_id} not found"
            return result

        agent_capital = agent.capital_current or agent.capital_allocated or 0
        # Phase 3C: use buying power (cash - reservations) when available
        buying_power = getattr(agent, "cash_balance", 0) - getattr(agent, "reserved_cash", 0)
        effective_capital = buying_power if buying_power > 0 else agent_capital

        if agent_capital > 0 and trade_value > agent_capital * self.PER_AGENT_MAX_POSITION_PCT:
            result["status"] = "rejected"
            result["reason"] = (
                f"Trade value ({trade_value:.2f}) exceeds {self.PER_AGENT_MAX_POSITION_PCT * 100}% "
                f"of agent capital ({agent_capital:.2f})"
            )
            self.log.warning("trade_rejected_position_limit", **result)
            return result

        # 5. Portfolio concentration check (before size approval)
        symbol = trade_request.get("symbol")
        if symbol and agent_capital > 0:
            concentration_result = self._check_concentration(
                agent_id, symbol, trade_value, agent_capital
            )
            if concentration_result:
                result.update(concentration_result)
                return result

        # 6. Large trade — hold for review
        if agent_capital > 0 and trade_value > agent_capital * self.TRADE_GATE_THRESHOLD:
            # Check: does agent have enough buying power?
            if trade_value > effective_capital:
                result["status"] = "rejected"
                result["reason"] = (
                    f"Trade value ({trade_value:.2f}) exceeds buying power ({effective_capital:.2f})"
                )
                return result
            result["status"] = "approved"
            result["reason"] = "Large trade — passed size review"
            self.log.info("trade_approved_large", **result)
            return result

        # 7. Small trade, no alerts — auto-approve
        result["status"] = "approved"
        result["reason"] = "Auto-approved (small trade, no alerts)"
        self.log.debug("trade_auto_approved", **result)
        return result

    def _check_concentration(
        self, agent_id: int, symbol: str,
        trade_value: float, agent_capital: float,
    ) -> dict | None:
        """Check portfolio concentration for a trade.

        Returns rejection/warning dict if threshold exceeded, None if OK.
        Hard limit: 50% in one position → REJECT.
        Warning: 35% → APPROVE with flag.
        """
        # Get existing exposure in this symbol
        existing_exposure = 0.0
        try:
            with self.db_session_factory() as session:
                positions = session.execute(
                    select(Position).where(
                        Position.agent_id == agent_id,
                        Position.symbol == symbol,
                        Position.status == "open",
                    )
                ).scalars().all()
                existing_exposure = sum(p.size_usd for p in positions)
        except Exception:
            pass

        total_deployed = existing_exposure
        projected = total_deployed + trade_value
        concentration_pct = projected / (agent_capital + trade_value) if (agent_capital + trade_value) > 0 else 0

        hard_limit = config.portfolio_concentration_hard_limit
        warning_threshold = config.portfolio_concentration_warning

        if concentration_pct >= hard_limit:
            self.log.warning(
                "trade_rejected_concentration",
                agent_id=agent_id, symbol=symbol,
                concentration=f"{concentration_pct:.1%}",
            )
            return {
                "status": "rejected",
                "reason": (
                    f"Portfolio concentration {concentration_pct:.1%} exceeds "
                    f"hard limit ({hard_limit:.0%}) for {symbol}"
                ),
            }

        if concentration_pct >= warning_threshold:
            self.log.info(
                "trade_approved_concentration_warning",
                agent_id=agent_id, symbol=symbol,
                concentration=f"{concentration_pct:.1%}",
            )
            return {
                "status": "approved",
                "reason": (
                    f"WARNING: concentration {concentration_pct:.1%} "
                    f"in {symbol} (threshold: {warning_threshold:.0%})"
                ),
                "concentration_warning": True,
            }

        return None  # No concentration issue

    # ------------------------------------------------------------------
    # Alert Escalation
    # ------------------------------------------------------------------

    async def escalate_alert(self, new_status: str) -> None:
        """Handle alert status change with appropriate actions."""
        self.log.critical(
            "alert_escalation",
            new_status=new_status,
        )

        # Post to Agora
        await self._post_alert(
            f"ALERT ESCALATION: {new_status.upper()}",
            metadata={"status": new_status, "timestamp": datetime.now(timezone.utc).isoformat()},
        )

        if new_status == "yellow":
            self.log.warning("YELLOW_ALERT: halving all position size limits")

        elif new_status == "red":
            self.log.critical("RED_ALERT: freezing all agents, stopping trading for 24h")
            self._freeze_all_agents()

        elif new_status == "circuit_breaker":
            self.log.critical("CIRCUIT_BREAKER: closing all positions, freezing everything")
            self._freeze_all_agents()
            self.log.critical("CIRCUIT_BREAKER_ACTIVATED — IMMEDIATE ACTION REQUIRED")

    # ------------------------------------------------------------------
    # Emergency Kill
    # ------------------------------------------------------------------

    async def emergency_kill_agent(self, agent_id: int, reason: str) -> None:
        """Terminate an agent that has hit loss limits."""
        self.log.critical("emergency_kill", agent_id=agent_id, reason=reason)

        with self.db_session_factory() as session:
            agent = session.get(Agent, agent_id)
            if agent is None:
                return

            agent.status = "terminated"
            agent.termination_reason = reason
            agent.terminated_at = datetime.now(timezone.utc)
            session.commit()

        # Post death notice to Agora
        await self._post_alert(
            f"EMERGENCY KILL: Agent {agent_id} — {reason}",
            metadata={"agent_id": agent_id, "reason": reason},
        )

    # ------------------------------------------------------------------
    # Internal Helpers
    # ------------------------------------------------------------------

    async def _post_alert(self, content: str, metadata: dict | None = None) -> None:
        """Post an alert to The Agora. Falls back to Redis pub/sub if no AgoraService."""
        if self._agora is not None:
            try:
                from src.agora.schemas import AgoraMessage, MessageType
                msg = AgoraMessage(
                    agent_id=0,
                    agent_name="Warden",
                    channel="system-alerts",
                    content=content,
                    message_type=MessageType.ALERT,
                    metadata=metadata or {},
                    importance=2,
                )
                await self._agora.post_message(msg)
                return
            except Exception as exc:
                self.log.warning("agora_post_failed_falling_back", error=str(exc))

        # Fallback: direct Redis publish
        alert_msg = json.dumps({
            "content": content,
            "metadata": metadata or {},
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        self.redis.publish("agora:system-alerts", alert_msg)

    def _get_system_state(self) -> SystemState | None:
        """Read current system state from database."""
        with self.db_session_factory() as session:
            return session.execute(select(SystemState).limit(1)).scalar_one_or_none()

    def _get_agent(self, agent_id: int) -> Agent | None:
        """Get agent record."""
        with self.db_session_factory() as session:
            return session.get(Agent, agent_id)

    def _check_agent_losses(self) -> list[int]:
        """Check all active agents for loss limit breaches. Returns IDs to terminate."""
        flagged = []
        with self.db_session_factory() as session:
            agents = session.execute(
                select(Agent).where(Agent.status == "active")
            ).scalars().all()

            for agent in agents:
                allocated = agent.capital_allocated or 0
                current = agent.capital_current or 0
                if allocated > 0 and current < allocated * (1.0 - self.PER_AGENT_MAX_LOSS_PCT):
                    flagged.append(agent.id)
                    self.log.warning(
                        "agent_loss_limit_breached",
                        agent_id=agent.id,
                        allocated=allocated,
                        current=current,
                        loss_pct=round((1.0 - current / allocated) * 100, 1),
                    )
        return flagged

    def _freeze_all_agents(self) -> None:
        """Set all active agents to frozen status."""
        with self.db_session_factory() as session:
            session.execute(
                update(Agent)
                .where(Agent.status == "active")
                .values(status="frozen")
            )
            session.commit()
        self.log.info("all_agents_frozen")

    def _update_alert_status(self, status: str) -> None:
        """Update alert_status in system_state."""
        with self.db_session_factory() as session:
            state = session.execute(select(SystemState).limit(1)).scalar_one_or_none()
            if state:
                state.alert_status = status
                session.commit()

    async def _process_trade_requests(self) -> list[dict]:
        """Read trade requests from Redis queue and process them."""
        processed = []
        # Read up to 100 requests per cycle
        for _ in range(100):
            raw = self.redis.lpop("trade_requests")
            if raw is None:
                break
            try:
                request = json.loads(raw)
                result = await self.evaluate_trade(request)
                # Push response to agent-specific queue
                agent_id = request.get("agent_id")
                self.redis.rpush(
                    f"trade_responses:{agent_id}",
                    json.dumps(result),
                )
                processed.append(result)
            except Exception as exc:
                self.log.error("trade_request_processing_error", error=str(exc))
        return processed
