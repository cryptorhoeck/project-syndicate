"""
Project Syndicate — Action Executor

Phase 4 (ACT) of the OODA loop.
Routes validated actions to the appropriate system:
  Scout → Agora broadcasts
  Strategist → Plan database + Agora
  Critic → Plan status updates + Agora
  Operator → Warden trade queue (placeholder for Phase 3C)
  Universal → go_idle (log only)
"""

__version__ = "0.7.0"

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from src.common.models import Agent, Message

logger = logging.getLogger(__name__)


@dataclass
class ActionResult:
    """Result of executing an action."""
    success: bool
    action_type: str
    details: str = ""
    cost: float = 0.0


class ActionExecutor:
    """Executes validated agent actions by routing to the correct subsystem."""

    def __init__(self, db_session: Session, agora_service=None, warden=None):
        """
        Args:
            db_session: SQLAlchemy session.
            agora_service: Optional AgoraService for posting messages.
            warden: Optional Warden for trade gate processing.
        """
        self.db = db_session
        self.agora = agora_service
        self.warden = warden

    async def execute(self, agent: Agent, parsed_output: dict) -> ActionResult:
        """Execute a validated action.

        Args:
            agent: The agent performing the action.
            parsed_output: The validated JSON output from the API call.

        Returns:
            ActionResult with success/failure and details.
        """
        action = parsed_output.get("action", {})
        action_type = action.get("type", "go_idle")
        params = action.get("params", {})

        try:
            handler = self._get_handler(action_type)
            return await handler(agent, action_type, params)
        except Exception as e:
            logger.error(f"Action execution error: {e}", extra={
                "agent_id": agent.id, "action": action_type,
            })
            return ActionResult(
                success=False,
                action_type=action_type,
                details=f"Execution error: {e}",
            )

    def _get_handler(self, action_type: str):
        """Map action type to handler method."""
        handlers = {
            # Scout
            "broadcast_opportunity": self._handle_broadcast,
            "request_deeper_analysis": self._handle_broadcast,
            "update_watchlist": self._handle_update_watchlist,
            # Strategist
            "propose_plan": self._handle_broadcast,
            "revise_plan": self._handle_broadcast,
            "request_scout_intel": self._handle_broadcast,
            # Critic
            "approve_plan": self._handle_broadcast,
            "reject_plan": self._handle_broadcast,
            "request_revision": self._handle_broadcast,
            "flag_risk": self._handle_flag_risk,
            # Operator
            "execute_trade": self._handle_trade_placeholder,
            "adjust_position": self._handle_trade_placeholder,
            "close_position": self._handle_trade_placeholder,
            "hedge": self._handle_trade_placeholder,
            # Universal
            "go_idle": self._handle_go_idle,
        }
        return handlers.get(action_type, self._handle_go_idle)

    async def _handle_broadcast(
        self, agent: Agent, action_type: str, params: dict
    ) -> ActionResult:
        """Handle actions that broadcast to Agora channels."""
        channel_map = {
            "broadcast_opportunity": "market-intel",
            "request_deeper_analysis": "agent-chat",
            "propose_plan": "strategy-proposals",
            "revise_plan": "strategy-proposals",
            "request_scout_intel": "agent-chat",
            "approve_plan": "strategy-proposals",
            "reject_plan": "strategy-proposals",
            "request_revision": "strategy-proposals",
        }
        channel = channel_map.get(action_type, "agent-chat")

        # Build message content
        summary = self._summarize_action(action_type, params)

        # Post to Agora if available, otherwise write directly to DB
        if self.agora:
            try:
                await self.agora.post_message(
                    agent_id=agent.id,
                    agent_name=agent.name,
                    channel=channel,
                    content=summary,
                    message_type=self._action_to_message_type(action_type),
                    metadata={"action_type": action_type, "params": params},
                )
            except Exception as e:
                logger.warning(f"Agora post failed, writing directly: {e}")
                self._write_message_directly(agent, channel, summary, action_type, params)
        else:
            self._write_message_directly(agent, channel, summary, action_type, params)

        return ActionResult(
            success=True,
            action_type=action_type,
            details=f"Broadcast to {channel}: {summary[:100]}",
        )

    async def _handle_update_watchlist(
        self, agent: Agent, action_type: str, params: dict
    ) -> ActionResult:
        """Handle watchlist updates."""
        current = agent.watched_markets or []
        add = params.get("add_markets", [])
        remove = params.get("remove_markets", [])

        updated = list(set(current + add) - set(remove))
        agent.watched_markets = updated
        self.db.add(agent)
        self.db.flush()

        return ActionResult(
            success=True,
            action_type=action_type,
            details=f"Watchlist updated: +{add}, -{remove} → {updated}",
        )

    async def _handle_flag_risk(
        self, agent: Agent, action_type: str, params: dict
    ) -> ActionResult:
        """Handle risk flags — post to system-alerts."""
        severity = params.get("severity", "medium")
        desc = params.get("description", "Risk flagged")
        content = f"⚠️ RISK FLAG ({severity}): {desc}"

        importance = {"low": 0, "medium": 1, "high": 2, "critical": 2}.get(severity, 1)

        if self.agora:
            try:
                await self.agora.post_message(
                    agent_id=agent.id,
                    agent_name=agent.name,
                    channel="system-alerts",
                    content=content,
                    message_type="alert",
                    importance=importance,
                    metadata={"action_type": action_type, "params": params},
                )
            except Exception:
                self._write_message_directly(
                    agent, "system-alerts", content, action_type, params, importance
                )
        else:
            self._write_message_directly(
                agent, "system-alerts", content, action_type, params, importance
            )

        return ActionResult(
            success=True,
            action_type=action_type,
            details=f"Risk flag: {severity} — {desc[:100]}",
        )

    async def _handle_trade_placeholder(
        self, agent: Agent, action_type: str, params: dict
    ) -> ActionResult:
        """Placeholder for trade actions — logs the request.

        Real Paper Trading engine is Phase 3C. For now, log and return mock result.
        """
        logger.info(
            "trade_action_placeholder",
            extra={"agent_id": agent.id, "action": action_type, "params": params},
        )

        # Post the intent to Agora
        summary = self._summarize_action(action_type, params)
        if self.agora:
            try:
                await self.agora.post_message(
                    agent_id=agent.id,
                    agent_name=agent.name,
                    channel="trade-signals",
                    content=f"[PAPER] {summary}",
                    message_type="trade",
                    metadata={"action_type": action_type, "params": params, "paper": True},
                )
            except Exception:
                self._write_message_directly(
                    agent, "trade-signals", f"[PAPER] {summary}", action_type, params
                )
        else:
            self._write_message_directly(
                agent, "trade-signals", f"[PAPER] {summary}", action_type, params
            )

        return ActionResult(
            success=True,
            action_type=action_type,
            details=f"Trade placeholder: {action_type} — queued for Phase 3C",
        )

    async def _handle_go_idle(
        self, agent: Agent, action_type: str, params: dict
    ) -> ActionResult:
        """Handle idle action — log only."""
        reason = params.get("reason", "no reason given")
        logger.debug(f"Agent {agent.name} going idle: {reason}")
        return ActionResult(
            success=True,
            action_type="go_idle",
            details=f"Idle: {reason}",
        )

    def _write_message_directly(
        self, agent: Agent, channel: str, content: str,
        action_type: str, params: dict, importance: int = 0
    ) -> None:
        """Write a message directly to the database (fallback when Agora unavailable)."""
        msg = Message(
            agent_id=agent.id,
            agent_name=agent.name,
            channel=channel,
            content=content,
            message_type=self._action_to_message_type(action_type),
            importance=importance,
            metadata_json={"action_type": action_type, "params": params},
        )
        self.db.add(msg)
        self.db.flush()

    @staticmethod
    def _action_to_message_type(action_type: str) -> str:
        """Map action types to Agora message types."""
        mapping = {
            "broadcast_opportunity": "signal",
            "request_deeper_analysis": "chat",
            "propose_plan": "proposal",
            "revise_plan": "proposal",
            "request_scout_intel": "chat",
            "approve_plan": "evaluation",
            "reject_plan": "evaluation",
            "request_revision": "evaluation",
            "flag_risk": "alert",
            "execute_trade": "trade",
            "adjust_position": "trade",
            "close_position": "trade",
            "hedge": "trade",
            "go_idle": "thought",
        }
        return mapping.get(action_type, "chat")

    @staticmethod
    def _summarize_action(action_type: str, params: dict) -> str:
        """Create a human-readable summary of an action."""
        if action_type == "broadcast_opportunity":
            market = params.get("market", "?")
            signal = params.get("signal", "?")
            urgency = params.get("urgency", "?")
            details = params.get("details", "")
            return f"Opportunity: {market} — {signal} (urgency: {urgency}). {details}"

        if action_type == "propose_plan":
            name = params.get("plan_name", "?")
            market = params.get("market", "?")
            direction = params.get("direction", "?")
            return f"Plan: {name} — {direction} {market}. {params.get('thesis', '')[:200]}"

        if action_type in ("approve_plan", "reject_plan", "request_revision"):
            plan_id = params.get("plan_id", "?")
            verb = action_type.replace("_", " ").title()
            detail = params.get("assessment", params.get("reasons", params.get("issues", "")))
            return f"{verb} plan #{plan_id}: {detail[:200]}"

        if action_type == "execute_trade":
            market = params.get("market", "?")
            direction = params.get("direction", "?")
            size = params.get("position_size_usd", "?")
            return f"Trade: {direction} {market} ${size}"

        if action_type == "flag_risk":
            risk_type = params.get("risk_type", "?")
            severity = params.get("severity", "?")
            desc = params.get("description", "")
            return f"Risk ({risk_type}/{severity}): {desc[:200]}"

        # Generic fallback
        return f"{action_type}: {json.dumps(params)[:200]}"
