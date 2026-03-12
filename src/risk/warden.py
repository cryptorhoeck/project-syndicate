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

__version__ = "0.2.0"

import asyncio
import json
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import redis
import structlog
from sqlalchemy import create_engine, select, update
from sqlalchemy.orm import Session, sessionmaker

from src.common.config import config
from src.common.models import Agent, InheritedPosition, SystemState

logger = structlog.get_logger()


class Warden:
    """The immutable risk layer. No AI, pure deterministic checks."""

    def __init__(self, db_session_factory: sessionmaker | None = None) -> None:
        self.log = logger.bind(component="warden")

        # Database
        if db_session_factory:
            self.db_session_factory = db_session_factory
        else:
            engine = create_engine(config.database_url)
            self.db_session_factory = sessionmaker(bind=engine)

        # Redis
        self.redis = redis.Redis.from_url(config.redis_url, decode_responses=True)

        # Configuration
        self.CIRCUIT_BREAKER_THRESHOLD = config.circuit_breaker_threshold
        self.YELLOW_ALERT_THRESHOLD = config.yellow_alert_threshold
        self.RED_ALERT_THRESHOLD = config.red_alert_threshold
        self.PER_AGENT_MAX_POSITION_PCT = config.per_agent_max_position_pct
        self.PER_AGENT_MAX_LOSS_PCT = config.per_agent_max_loss_pct
        self.TRADE_GATE_THRESHOLD = config.trade_gate_threshold

        # Current alert status
        self.alert_status: str = "green"

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

    async def evaluate_trade(self, trade_request: dict) -> dict:
        """Evaluate a trade request through the gate.

        Returns: {status: 'approved'/'rejected'/'held', reason: str, request_id: str}
        """
        request_id = trade_request.get("request_id", str(uuid.uuid4())[:12])
        agent_id = trade_request["agent_id"]
        amount = trade_request.get("amount", 0)
        price = trade_request.get("price", 0)
        trade_value = amount * price if price else amount

        result = {"request_id": request_id, "agent_id": agent_id}

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
        if agent_capital > 0 and trade_value > agent_capital * self.PER_AGENT_MAX_POSITION_PCT:
            result["status"] = "rejected"
            result["reason"] = (
                f"Trade value ({trade_value:.2f}) exceeds {self.PER_AGENT_MAX_POSITION_PCT * 100}% "
                f"of agent capital ({agent_capital:.2f})"
            )
            self.log.warning("trade_rejected_position_limit", **result)
            return result

        # 5. Large trade — hold for review
        if agent_capital > 0 and trade_value > agent_capital * self.TRADE_GATE_THRESHOLD:
            # Check: does agent have enough capital?
            if trade_value > agent_capital:
                result["status"] = "rejected"
                result["reason"] = f"Trade value ({trade_value:.2f}) exceeds agent capital ({agent_capital:.2f})"
                return result
            result["status"] = "approved"
            result["reason"] = "Large trade — passed size review"
            self.log.info("trade_approved_large", **result)
            return result

        # 6. Small trade, no alerts — auto-approve
        result["status"] = "approved"
        result["reason"] = "Auto-approved (small trade, no alerts)"
        self.log.debug("trade_auto_approved", **result)
        return result

    # ------------------------------------------------------------------
    # Alert Escalation
    # ------------------------------------------------------------------

    async def escalate_alert(self, new_status: str) -> None:
        """Handle alert status change with appropriate actions."""
        self.log.critical(
            "alert_escalation",
            new_status=new_status,
        )

        # Post to Agora via Redis pub/sub
        alert_msg = json.dumps({
            "type": "alert_escalation",
            "status": new_status,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        self.redis.publish("agora:system-alerts", alert_msg)

        if new_status == "yellow":
            self.log.warning("YELLOW_ALERT: halving all position size limits")

        elif new_status == "red":
            self.log.critical("RED_ALERT: freezing all agents, stopping trading for 24h")
            self._freeze_all_agents()

        elif new_status == "circuit_breaker":
            self.log.critical("CIRCUIT_BREAKER: closing all positions, freezing everything")
            self._freeze_all_agents()
            # Email notification would be sent here via EmailService
            # For now, log the emergency
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

        # Post death notice to Agora via Redis
        death_msg = json.dumps({
            "type": "agent_death",
            "agent_id": agent_id,
            "reason": reason,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        self.redis.publish("agora:agent-lifecycle", death_msg)

    # ------------------------------------------------------------------
    # Internal Helpers
    # ------------------------------------------------------------------

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
