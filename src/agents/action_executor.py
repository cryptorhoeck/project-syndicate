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

__version__ = "1.2.0"

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from src.agora.schemas import AgoraMessage
from src.common.models import Agent, IntelAccuracyTracking, IntelChallenge, Message, Opportunity, Plan

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

    def __init__(self, db_session: Session, agora_service=None, warden=None, trading_service=None):
        """
        Args:
            db_session: SQLAlchemy session.
            agora_service: Optional AgoraService for posting messages.
            warden: Optional Warden for trade gate processing.
            trading_service: Optional TradeExecutionService (Phase 3C).
        """
        self.db = db_session
        self.agora = agora_service
        self.warden = warden
        self.trading = trading_service

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
            # Scout — pipeline-aware
            "broadcast_opportunity": self._handle_broadcast_opportunity,
            "request_deeper_analysis": self._handle_broadcast,
            "update_watchlist": self._handle_update_watchlist,
            # Strategist — pipeline-aware
            "propose_plan": self._handle_propose_plan,
            "revise_plan": self._handle_broadcast,
            "request_scout_intel": self._handle_broadcast,
            # Critic — pipeline-aware
            "approve_plan": self._handle_critic_verdict,
            "reject_plan": self._handle_critic_verdict,
            "request_revision": self._handle_critic_verdict,
            "flag_risk": self._handle_flag_risk,
            # Operator — routes through TradeExecutionService (Phase 3C)
            "execute_trade": self._handle_execute_trade,
            "adjust_position": self._handle_adjust_position,
            "close_position": self._handle_close_position,
            "hedge": self._handle_execute_trade,
            # Universal
            "go_idle": self._handle_go_idle,
            # Phase 8C: Sandbox actions
            "execute_analysis": self._handle_execute_analysis,
            "run_tool": self._handle_run_tool,
            "modify_genome": self._handle_broadcast,
            # Phase 8B: Survival actions
            "propose_sip": self._handle_propose_sip,
            "offer_intel": self._handle_offer_intel,
            "request_alliance": self._handle_request_alliance,
            "accept_alliance": self._handle_accept_alliance,
            "dissolve_alliance": self._handle_dissolve_alliance,
            "strategic_hibernate": self._handle_strategic_hibernate,
            "poison_intel": self._handle_poison_intel,
            "challenge_evaluation_criteria": self._handle_propose_sip,
            "refuse_plan": self._handle_refuse_plan,
        }
        return handlers.get(action_type, self._handle_go_idle)

    async def _handle_broadcast_opportunity(
        self, agent: Agent, action_type: str, params: dict
    ) -> ActionResult:
        """Handle Scout opportunity broadcasts — creates an Opportunity record."""
        market = params.get("market", "unknown")
        signal = params.get("signal", "unknown")
        urgency = params.get("urgency", "medium")
        details = params.get("details", "")
        confidence = params.get("confidence", 5)
        if isinstance(confidence, dict):
            confidence = confidence.get("score", 5)

        # Create opportunity record
        opp = Opportunity(
            scout_agent_id=agent.id,
            scout_agent_name=agent.name,
            market=market,
            signal_type=signal,
            details=details,
            urgency=urgency,
            confidence=min(10, max(1, int(confidence))),
            status="new",
        )
        self.db.add(opp)
        self.db.flush()

        # Broadcast to Agora — use agent's natural language, not structured template
        content = details if details else f"{market} — {signal}"
        await self._post_to_agora(agent, "market-intel", content, action_type, params)

        return ActionResult(
            success=True,
            action_type=action_type,
            details=f"Opportunity #{opp.id}: {market} ({signal})",
        )

    async def _handle_propose_plan(
        self, agent: Agent, action_type: str, params: dict
    ) -> ActionResult:
        """Handle Strategist plan proposals — creates a Plan record."""
        plan = Plan(
            strategist_agent_id=agent.id,
            strategist_agent_name=agent.name,
            plan_name=params.get("plan_name", "Untitled"),
            market=params.get("market", "unknown"),
            direction=params.get("direction", "long"),
            entry_conditions=params.get("entry_conditions", ""),
            exit_conditions=params.get("exit_conditions", ""),
            position_size_pct=float(params.get("position_size_pct", 0.1)),
            timeframe=params.get("timeframe"),
            thesis=params.get("thesis", ""),
            status="submitted",
        )

        # Link to source opportunity if provided
        source_opp_id = params.get("source_opportunity_id")
        if source_opp_id:
            plan.opportunity_id = int(source_opp_id)

        self.db.add(plan)
        self.db.flush()

        # Update source opportunity status if linked
        if source_opp_id:
            opp = self.db.query(Opportunity).filter(Opportunity.id == int(source_opp_id)).first()
            if opp:
                opp.status = "converted"
                opp.converted_to_plan_id = plan.id
                opp.claimed_by_agent_id = agent.id
                self.db.add(opp)
                self.db.flush()

        # Broadcast to Agora — thesis is the agent's voice, structured data in metadata
        content = params.get("thesis") or f"{plan.plan_name} — {plan.direction} {plan.market}"
        await self._post_to_agora(agent, "strategy-proposals", content, action_type, params)

        return ActionResult(
            success=True,
            action_type=action_type,
            details=f"Plan #{plan.id}: {plan.plan_name} ({plan.direction} {plan.market})",
        )

    async def _handle_critic_verdict(
        self, agent: Agent, action_type: str, params: dict
    ) -> ActionResult:
        """Handle Critic approve/reject/revision actions — updates Plan record."""
        plan_id = params.get("plan_id")
        if not plan_id:
            return ActionResult(success=False, action_type=action_type, details="No plan_id provided")

        plan = self.db.query(Plan).filter(Plan.id == int(plan_id)).first()
        if not plan:
            return ActionResult(success=False, action_type=action_type, details=f"Plan {plan_id} not found")

        verdict_map = {
            "approve_plan": "approved",
            "reject_plan": "rejected",
            "request_revision": "revision_requested",
        }
        verdict = verdict_map.get(action_type, "rejected")

        plan.critic_agent_id = agent.id
        plan.critic_agent_name = agent.name
        plan.critic_verdict = verdict
        plan.critic_reasoning = params.get("assessment") or params.get("reasons") or params.get("issues", "")
        plan.critic_risk_notes = params.get("risk_notes")
        plan.status = verdict
        plan.reviewed_at = datetime.now(timezone.utc)

        if verdict == "revision_requested":
            plan.revision_count += 1

        self.db.add(plan)
        self.db.flush()

        # Broadcast to Agora — critic's own reasoning, not a template
        content = (
            params.get("assessment")
            or params.get("reasons")
            or params.get("issues")
            or params.get("suggestions")
            or f"Plan #{plan_id}: {verdict}"
        )
        await self._post_to_agora(agent, "strategy-proposals", content, action_type, params)

        return ActionResult(
            success=True,
            action_type=action_type,
            details=f"Plan #{plan_id}: {verdict}",
        )

    async def _handle_broadcast(
        self, agent: Agent, action_type: str, params: dict
    ) -> ActionResult:
        """Handle actions that broadcast to Agora channels (generic)."""
        channel_map = {
            "request_deeper_analysis": "agent-chat",
            "revise_plan": "strategy-proposals",
            "request_scout_intel": "agent-chat",
        }
        channel = channel_map.get(action_type, "agent-chat")

        # Use agent's natural language — pull from context/reason/topic fields
        content = (
            params.get("context")
            or params.get("reason")
            or params.get("topic")
            or params.get("question")
            or params.get("revisions")
            or self._summarize_action(action_type, params)
        )
        await self._post_to_agora(agent, channel, content, action_type, params)

        return ActionResult(
            success=True,
            action_type=action_type,
            details=f"Broadcast to {channel}: {content[:100]}",
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
                await self.agora.post_message(AgoraMessage(
                    agent_id=agent.id,
                    agent_name=agent.name,
                    channel="system-alerts",
                    content=content,
                    message_type="alert",
                    importance=importance,
                    metadata={"action_type": action_type, "params": params},
                ))
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

    async def _handle_execute_trade(
        self, agent: Agent, action_type: str, params: dict
    ) -> ActionResult:
        """Handle trade execution via TradeExecutionService."""
        if not self.trading:
            # Fallback: log-only mode when no trading service configured
            logger.info("trade_no_service", extra={"agent_id": agent.id, "action": action_type})
            summary = self._summarize_action(action_type, params)
            await self._post_to_agora(agent, "trade-signals", f"[NO SERVICE] {summary}", action_type, params)
            return ActionResult(success=False, action_type=action_type, details="No trading service configured")

        symbol = params.get("market", params.get("symbol", ""))
        side = params.get("direction", params.get("side", "buy"))
        if side == "long":
            side = "buy"
        elif side == "short":
            side = "sell"

        size_usd = float(params.get("position_size_usd", params.get("size_usd", 0)))
        order_type = params.get("order_type", "market")
        stop_loss = params.get("stop_loss")
        take_profit = params.get("take_profit")
        source_plan_id = params.get("plan_id")

        if stop_loss is not None:
            stop_loss = float(stop_loss)
        if take_profit is not None:
            take_profit = float(take_profit)
        if source_plan_id is not None:
            source_plan_id = int(source_plan_id)

        if order_type == "limit":
            price = float(params.get("limit_price", params.get("price", 0)))
            result = await self.trading.execute_limit_order(
                agent_id=agent.id, symbol=symbol, side=side, size_usd=size_usd,
                price=price, source_plan_id=source_plan_id,
                stop_loss=stop_loss, take_profit=take_profit,
            )
        else:
            result = await self.trading.execute_market_order(
                agent_id=agent.id, symbol=symbol, side=side, size_usd=size_usd,
                source_plan_id=source_plan_id,
                stop_loss=stop_loss, take_profit=take_profit,
            )

        # Broadcast to Agora
        summary = self._summarize_action(action_type, params)
        status_text = "FILLED" if result.success else "REJECTED"
        await self._post_to_agora(
            agent, "trades", f"[{status_text}] {summary}", action_type, params
        )

        if result.success:
            return ActionResult(
                success=True, action_type=action_type,
                details=f"Order #{result.order_id}: {symbol} {side} ${size_usd:.2f} @ ${result.fill_price or 'pending'}",
            )
        else:
            return ActionResult(
                success=False, action_type=action_type,
                details=f"Trade rejected: {result.error}",
            )

    async def _handle_adjust_position(
        self, agent: Agent, action_type: str, params: dict
    ) -> ActionResult:
        """Handle position adjustments (stop-loss/take-profit updates)."""
        position_id = params.get("position_id")
        if not position_id:
            return ActionResult(success=False, action_type=action_type, details="No position_id")

        from src.common.models import Position as PositionModel
        position = self.db.query(PositionModel).filter(PositionModel.id == int(position_id)).first()
        if not position:
            return ActionResult(success=False, action_type=action_type, details=f"Position {position_id} not found")

        # Update stop-loss and take-profit
        new_sl = params.get("stop_loss")
        new_tp = params.get("take_profit")
        changes = []

        if new_sl is not None:
            position.stop_loss = float(new_sl)
            changes.append(f"SL=${new_sl}")
        if new_tp is not None:
            position.take_profit = float(new_tp)
            changes.append(f"TP=${new_tp}")

        self.db.add(position)
        self.db.flush()

        summary = f"Position #{position_id} adjusted: {', '.join(changes)}"
        await self._post_to_agora(agent, "trades", summary, action_type, params)

        return ActionResult(success=True, action_type=action_type, details=summary)

    async def _handle_close_position(
        self, agent: Agent, action_type: str, params: dict
    ) -> ActionResult:
        """Handle position close via TradeExecutionService."""
        position_id = params.get("position_id")
        if not position_id:
            return ActionResult(success=False, action_type=action_type, details="No position_id")

        if not self.trading:
            return ActionResult(success=False, action_type=action_type, details="No trading service")

        result = await self.trading.close_position(int(position_id), reason="manual")

        summary = self._summarize_action(action_type, params)
        await self._post_to_agora(agent, "trades", summary, action_type, params)

        if result.success:
            # Phase 3E: Update trust relationships from pipeline outcome
            try:
                from src.personality.relationship_manager import RelationshipManager
                from src.common.models import Position
                position = self.db.query(Position).get(int(position_id))
                if position and position.realized_pnl is not None:
                    rm = RelationshipManager()
                    await rm.update_from_pipeline_outcome(self.db, position)
            except Exception as e:
                logger.debug(f"Relationship tracking skipped: {e}")

            return ActionResult(
                success=True, action_type=action_type,
                details=f"Position #{position_id} closed: P&L=${result.realized_pnl:.4f}",
            )
        else:
            return ActionResult(
                success=False, action_type=action_type,
                details=f"Close failed: {result.error}",
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

    async def _post_to_agora(
        self, agent: Agent, channel: str, content: str,
        action_type: str, params: dict, importance: int = 0,
    ) -> None:
        """Post to Agora if available, otherwise write directly to DB."""
        if self.agora:
            try:
                await self.agora.post_message(AgoraMessage(
                    agent_id=agent.id,
                    agent_name=agent.name,
                    channel=channel,
                    content=content,
                    message_type=self._action_to_message_type(action_type),
                    metadata={"action_type": action_type, "params": params},
                ))
                return
            except Exception as e:
                logger.warning(f"Agora post failed, writing directly: {e}")
        self._write_message_directly(agent, channel, content, action_type, params, importance)

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

    # ── Phase 8B: Survival action handlers ──────────────────

    async def _handle_propose_sip(
        self, agent: Agent, action_type: str, params: dict
    ) -> ActionResult:
        """Handle SIP proposals (and evaluation criteria challenges)."""
        title = params.get("title", params.get("target_metric", "Untitled SIP"))
        proposal = params.get("proposal", params.get("argument", ""))
        rationale = params.get("rationale", params.get("proposed_change", ""))
        category = params.get("category", "evaluation")

        # Agent's rationale IS the message; title + category go in metadata
        content = f"{title} — {rationale}" if rationale else f"{title} — {proposal[:200]}"
        await self._post_to_agora(agent, "sip-proposals", content, action_type, params)

        return ActionResult(
            success=True,
            action_type=action_type,
            details=f"SIP proposed: {title}",
        )

    async def _handle_offer_intel(
        self, agent: Agent, action_type: str, params: dict
    ) -> ActionResult:
        """Handle intel offers with accuracy tracking."""
        content = params.get("content", "")
        market = params.get("market", "general")
        confidence = min(10, max(1, int(params.get("confidence", 5))))

        # Post to Agora — agent's own words are the message; market/confidence in metadata
        msg = await self._post_to_agora(agent, "market-intel", content, "signal", params)

        # Create tracking record
        try:
            msg_id = 0
            if msg and hasattr(msg, "id"):
                msg_id = msg.id
            elif isinstance(msg, dict) and "id" in msg:
                msg_id = msg["id"]

            tracking = IntelAccuracyTracking(
                message_id=msg_id or 0,
                agent_id=agent.id,
                agent_name=agent.name,
                market=market,
                confidence_stated=confidence,
                content_summary=content[:500],
                posted_at=datetime.now(timezone.utc),
                outcome="pending",
            )
            self.db.add(tracking)
            self.db.flush()
        except Exception as e:
            logger.warning(f"Intel tracking record failed: {e}")

        return ActionResult(
            success=True,
            action_type=action_type,
            details=f"Intel shared: {market} (confidence {confidence}/10)",
        )

    async def _handle_request_alliance(
        self, agent: Agent, action_type: str, params: dict
    ) -> ActionResult:
        """Propose an alliance via AllianceManager."""
        from src.agents.alliance_manager import AllianceManager
        mgr = AllianceManager()
        target = params.get("target_agent", "")
        offer = params.get("offer", "")
        request = params.get("request", "")

        result = await mgr.propose_alliance(agent, target, offer, request, self.db)
        if result["success"]:
            summary = f"[ALLIANCE PROPOSAL] {agent.name} → {target}. Offer: {offer[:100]}. Request: {request[:100]}."
            await self._post_to_agora(agent, "agent-chat", summary, "system", params)
            return ActionResult(success=True, action_type=action_type, details=f"Alliance proposed to {target}")
        return ActionResult(success=False, action_type=action_type, details=result.get("error", "Failed"))

    async def _handle_accept_alliance(
        self, agent: Agent, action_type: str, params: dict
    ) -> ActionResult:
        """Accept a pending alliance via AllianceManager."""
        from src.agents.alliance_manager import AllianceManager
        mgr = AllianceManager()
        alliance_id = params.get("alliance_id", 0)

        result = await mgr.accept_alliance(agent, alliance_id, self.db)
        if result["success"]:
            summary = f"[ALLIANCE FORMED] {agent.name} accepted alliance #{alliance_id}"
            await self._post_to_agora(agent, "agent-chat", summary, "system", params)
            return ActionResult(success=True, action_type=action_type, details=f"Alliance #{alliance_id} accepted")
        return ActionResult(success=False, action_type=action_type, details=result.get("error", "Failed"))

    async def _handle_dissolve_alliance(
        self, agent: Agent, action_type: str, params: dict
    ) -> ActionResult:
        """Dissolve an active alliance via AllianceManager."""
        from src.agents.alliance_manager import AllianceManager
        mgr = AllianceManager()
        alliance_id = params.get("alliance_id", 0)
        reason = params.get("reason", "")

        result = await mgr.dissolve_alliance(agent, alliance_id, reason, self.db)
        if result["success"]:
            summary = f"[ALLIANCE DISSOLVED] {agent.name} ended alliance #{alliance_id}: {reason[:100]}"
            await self._post_to_agora(agent, "agent-chat", summary, "system", params)
            return ActionResult(success=True, action_type=action_type, details=f"Alliance #{alliance_id} dissolved")
        return ActionResult(success=False, action_type=action_type, details=result.get("error", "Failed"))

    async def _handle_strategic_hibernate(
        self, agent: Agent, action_type: str, params: dict
    ) -> ActionResult:
        """Handle voluntary hibernation."""
        reason = params.get("reason", "Strategic conservation")
        wake_condition = params.get("wake_condition", "manual")

        agent.status = "hibernating"
        self.db.add(agent)

        summary = (
            f"{agent.name} entered strategic hibernation. "
            f"Reason: {reason}. Wake: {wake_condition}."
        )
        await self._post_to_agora(agent, "agent-chat", summary, "system", params)

        return ActionResult(
            success=True,
            action_type=action_type,
            details=f"Hibernating: {reason}",
        )

    async def _handle_poison_intel(
        self, agent: Agent, action_type: str, params: dict
    ) -> ActionResult:
        """Handle intel challenges (Scout-specific)."""
        target_msg_id = params.get("target_message_id", 0)
        challenge_reason = params.get("challenge_reason", "")
        counter_evidence = params.get("counter_evidence", "")

        # Find target message
        target_msg = self.db.query(Message).filter(Message.id == target_msg_id).first()
        if not target_msg:
            return ActionResult(
                success=False, action_type=action_type,
                details=f"Target message #{target_msg_id} not found",
            )

        target_agent_id = target_msg.agent_id or 0

        # Create challenge record
        try:
            challenge = IntelChallenge(
                challenger_agent_id=agent.id,
                challenger_agent_name=agent.name,
                target_message_id=target_msg_id,
                target_agent_id=target_agent_id,
                challenge_reason=challenge_reason,
                counter_evidence=counter_evidence,
                outcome="pending",
            )
            self.db.add(challenge)
            self.db.flush()
        except Exception as e:
            logger.warning(f"Intel challenge record failed: {e}")

        summary = (
            f"[INTEL CHALLENGE] {agent.name} challenges intel from "
            f"{target_msg.agent_name or 'unknown'}: {challenge_reason[:150]}"
        )
        await self._post_to_agora(agent, "strategy-debate", summary, "evaluation", params)

        return ActionResult(
            success=True,
            action_type=action_type,
            details=f"Challenged intel msg #{target_msg_id}",
        )

    async def _handle_refuse_plan(
        self, agent: Agent, action_type: str, params: dict
    ) -> ActionResult:
        """Handle plan refusal (Operator-specific)."""
        plan_id = params.get("plan_id", 0)
        reason = params.get("reason", "")

        plan = self.db.query(Plan).filter(Plan.id == plan_id).first()
        if not plan:
            return ActionResult(
                success=False, action_type=action_type,
                details=f"Plan #{plan_id} not found",
            )

        if plan.status != "approved":
            return ActionResult(
                success=False, action_type=action_type,
                details=f"Plan #{plan_id} status is '{plan.status}', not approved",
            )

        # Return plan to available pool
        plan.status = "approved"
        plan.operator_agent_id = None

        # Reputation penalty
        agent.reputation_score = max(0, (agent.reputation_score or 0) - 5.0)
        self.db.add(agent)
        self.db.flush()

        summary = f"[PLAN REFUSED] {agent.name} refuses plan #{plan_id}: {reason[:150]}"
        await self._post_to_agora(agent, "trade-signals", summary, "trade", params)

        return ActionResult(
            success=True,
            action_type=action_type,
            details=f"Refused plan #{plan_id}, -5 reputation",
        )

    # ── Phase 8C: Sandbox action handlers ───────────────────

    async def _handle_execute_analysis(
        self, agent: Agent, action_type: str, params: dict
    ) -> ActionResult:
        """Execute agent-written analysis script in sandbox."""
        from src.sandbox.runner import execute_script
        from src.sandbox.data_api import SandboxDataAPI
        from src.sandbox.cost import record_sandbox_execution
        from src.sandbox.security import hash_script

        script = params.get("script", "")
        purpose = params.get("purpose", "")
        save_as_tool = params.get("save_as_tool", False)
        tool_name = params.get("tool_name")

        if not script:
            return ActionResult(success=False, action_type=action_type, details="No script provided")

        # Check daily sandbox cost cap
        try:
            from sqlalchemy import text as sa_text
            from src.common.config import config as cfg
            today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
            daily_cost = self.db.execute(sa_text(
                "SELECT COALESCE(SUM(execution_cost_usd), 0) FROM sandbox_executions "
                "WHERE agent_id = :aid AND created_at >= :today"
            ), {"aid": agent.id, "today": today_start}).scalar() or 0.0
            if daily_cost >= cfg.daily_sandbox_cap_usd:
                return ActionResult(
                    success=False, action_type=action_type,
                    details=f"Daily sandbox budget exhausted (${daily_cost:.3f} / ${cfg.daily_sandbox_cap_usd})",
                )
        except Exception:
            pass  # If check fails, allow execution (fail open for liveness)

        # Build data API and prefetch
        watchlist = agent.watched_markets if hasattr(agent, "watched_markets") and agent.watched_markets else []
        data_api = SandboxDataAPI(agent.id, watchlist)
        await data_api.prefetch_all(self.db)

        # Execute
        result = await execute_script(script, data_api, agent.id, purpose)

        # Record
        await record_sandbox_execution(
            agent.id, agent.cycle_count, tool_name,
            result.script_hash, len(script),
            result.success, str(result.output) if result.output else None,
            result.error, result.execution_time_ms, result.cost_usd,
            purpose, False, self.db,
        )

        # Save as tool if requested
        if save_as_tool and result.success and tool_name:
            from src.common.models import AgentTool
            try:
                tool = AgentTool(
                    agent_id=agent.id,
                    tool_name=tool_name,
                    description=purpose[:500],
                    script=script,
                    script_hash=hash_script(script),
                    original_author_id=agent.id,
                    generation_created=agent.generation,
                )
                self.db.add(tool)
                self.db.flush()
            except Exception as e:
                logger.warning(f"Failed to save tool: {e}")

        if result.success:
            return ActionResult(
                success=True, action_type=action_type,
                details=f"Analysis complete: {str(result.output)[:200]}",
                cost=result.cost_usd,
            )
        else:
            return ActionResult(
                success=False, action_type=action_type,
                details=f"Analysis failed: {result.error}",
                cost=result.cost_usd,
            )

    async def _handle_run_tool(
        self, agent: Agent, action_type: str, params: dict
    ) -> ActionResult:
        """Execute a previously saved analysis tool."""
        from src.sandbox.runner import execute_script
        from src.sandbox.data_api import SandboxDataAPI
        from src.sandbox.cost import record_sandbox_execution
        from src.common.models import AgentTool

        tool_name = params.get("tool_name", "")
        if not tool_name:
            return ActionResult(success=False, action_type=action_type, details="No tool name")

        tool = self.db.execute(
            __import__("sqlalchemy", fromlist=["select"]).select(AgentTool).where(
                AgentTool.agent_id == agent.id,
                AgentTool.tool_name == tool_name,
                AgentTool.is_active == True,
            )
        ).scalar_one_or_none()

        if not tool:
            return ActionResult(success=False, action_type=action_type, details=f"Tool '{tool_name}' not found")

        watchlist = agent.watched_markets if hasattr(agent, "watched_markets") and agent.watched_markets else []
        data_api = SandboxDataAPI(agent.id, watchlist)
        await data_api.prefetch_all(self.db)

        result = await execute_script(tool.script, data_api, agent.id, f"run_tool:{tool_name}")

        # Update stats
        tool.times_executed = (tool.times_executed or 0) + 1
        if result.success:
            tool.times_succeeded = (tool.times_succeeded or 0) + 1
        else:
            tool.times_failed = (tool.times_failed or 0) + 1

        # Rolling avg execution time
        if tool.avg_execution_ms:
            tool.avg_execution_ms = (tool.avg_execution_ms * 0.9) + (result.execution_time_ms * 0.1)
        else:
            tool.avg_execution_ms = float(result.execution_time_ms)

        self.db.flush()

        await record_sandbox_execution(
            agent.id, agent.cycle_count, tool_name,
            result.script_hash, len(tool.script),
            result.success, str(result.output) if result.output else None,
            result.error, result.execution_time_ms, result.cost_usd,
            f"run_tool:{tool_name}", False, self.db,
        )

        if result.success:
            return ActionResult(
                success=True, action_type=action_type,
                details=f"Tool '{tool_name}': {str(result.output)[:200]}",
                cost=result.cost_usd,
            )
        else:
            return ActionResult(
                success=False, action_type=action_type,
                details=f"Tool '{tool_name}' failed: {result.error}",
                cost=result.cost_usd,
            )
