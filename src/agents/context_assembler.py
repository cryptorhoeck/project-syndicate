"""
Project Syndicate — Context Assembler

Phase 1 (OBSERVE) of the OODA loop.
Builds the agent's "mind" for each cycle — pure deterministic code, no AI.
Assembles mandatory, priority, and long-term memory context within a token budget.
"""

__version__ = "1.0.0"

import enum
import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import tiktoken
from sqlalchemy import desc, func
from sqlalchemy.orm import Session

from src.common.models import Agent, AgentCycle, AgentLongTermMemory, Message, Opportunity, Plan, Position, SystemState
from src.agents.budget_gate import BudgetStatus
from src.agents.roles import (
    format_actions_for_prompt,
    get_role,
    NORMAL_OUTPUT_SCHEMA,
    REFLECTION_OUTPUT_SCHEMA,
)

logger = logging.getLogger(__name__)

# Use cl100k_base as a reasonable approximation for Claude token counting
try:
    _enc = tiktoken.get_encoding("cl100k_base")
except Exception:
    _enc = None


def count_tokens(text: str) -> int:
    """Estimate token count for a string."""
    if _enc:
        return len(_enc.encode(text))
    # Fallback: ~4 chars per token
    return len(text) // 4


class ContextMode(enum.Enum):
    """Dynamic context assembly modes."""
    NORMAL = "normal"
    CRISIS = "crisis"
    HUNTING = "hunting"
    SURVIVAL = "survival"


# Token budget allocations per mode (mandatory, priority, memory, buffer)
MODE_ALLOCATIONS: dict[ContextMode, tuple[float, float, float, float]] = {
    ContextMode.NORMAL:   (0.25, 0.45, 0.20, 0.10),
    ContextMode.CRISIS:   (0.40, 0.30, 0.20, 0.10),
    ContextMode.HUNTING:  (0.15, 0.55, 0.20, 0.10),
    ContextMode.SURVIVAL: (0.50, 0.25, 0.15, 0.10),
}


@dataclass
class AssembledContext:
    """The fully assembled context for a thinking cycle."""
    system_prompt: str
    user_prompt: str
    mode: ContextMode
    total_tokens: int
    mandatory_tokens: int
    priority_tokens: int
    memory_tokens: int


class ContextAssembler:
    """Builds the agent's cognitive context for each thinking cycle.

    Determines what information the agent "sees" by scoring, ranking,
    and packing data into a token-budgeted context window.
    """

    def __init__(self, db_session: Session, token_budget: int = 3000):
        self.db = db_session
        self.token_budget = token_budget

    def determine_mode(self, agent: Agent, budget_status: BudgetStatus) -> ContextMode:
        """Determine the context assembly mode based on agent state."""
        if budget_status == BudgetStatus.SURVIVAL_MODE:
            return ContextMode.SURVIVAL

        # Crisis: losing money or underwater
        if agent.total_true_pnl < -abs(agent.capital_allocated * 0.1):
            return ContextMode.CRISIS

        # Hunting: scout without active opportunity
        if agent.type == "scout":
            return ContextMode.HUNTING

        return ContextMode.NORMAL

    def assemble(
        self,
        agent: Agent,
        budget_status: BudgetStatus = BudgetStatus.NORMAL,
        cycle_type: str = "normal",
    ) -> AssembledContext:
        """Assemble the full context for a thinking cycle.

        Args:
            agent: The agent running this cycle.
            budget_status: Result from BudgetGate.
            cycle_type: "normal" or "reflection".

        Returns:
            AssembledContext with system prompt, user prompt, and metadata.
        """
        mode = self.determine_mode(agent, budget_status)
        alloc = MODE_ALLOCATIONS[mode]
        budget = self.token_budget
        if budget_status == BudgetStatus.SURVIVAL_MODE:
            budget = budget // 2

        mandatory_budget = int(budget * alloc[0])
        priority_budget = int(budget * alloc[1])
        memory_budget = int(budget * alloc[2])
        buffer_budget = int(budget * alloc[3])

        # Build each section
        system_prompt = self._build_system_prompt(agent, mode, cycle_type)
        mandatory_text = self._build_mandatory_context(agent)
        priority_text = self._build_priority_context(agent, priority_budget + buffer_budget)
        memory_text = self._build_memory_context(agent, memory_budget)

        # Build user prompt from sections
        if cycle_type == "reflection":
            user_prompt = self._build_reflection_user_prompt(agent, mandatory_text, priority_text, memory_text)
        else:
            user_prompt = self._build_normal_user_prompt(mandatory_text, priority_text, memory_text)

        total_tokens = count_tokens(system_prompt) + count_tokens(user_prompt)

        return AssembledContext(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            mode=mode,
            total_tokens=total_tokens,
            mandatory_tokens=count_tokens(mandatory_text),
            priority_tokens=count_tokens(priority_text),
            memory_tokens=count_tokens(memory_text),
        )

    def _build_system_prompt(self, agent: Agent, mode: ContextMode, cycle_type: str) -> str:
        """Build the system prompt for the API call."""
        role_def = get_role(agent.type)
        prestige = agent.prestige_title or "Unranked"

        budget_remaining = agent.thinking_budget_daily - agent.thinking_budget_used_today

        survival_directive = ""
        if mode == ContextMode.SURVIVAL:
            survival_directive = (
                "\n\n⚠️ SURVIVAL MODE: Your budget is critically low. "
                "Be extremely concise. Every token costs you."
            )

        if cycle_type == "reflection":
            return self._build_reflection_system_prompt(agent, prestige, budget_remaining, survival_directive)

        action_list = format_actions_for_prompt(agent.type)

        # Get current alert level
        sys_state = self.db.query(SystemState).first()
        alert_level = sys_state.alert_status if sys_state else "green"

        return f"""You are {agent.name}, a {agent.type} agent in Project Syndicate.
Generation: {agent.generation} | Reputation: {agent.reputation_score:.1f} ({prestige})
Cycle: {agent.cycle_count} | Budget remaining today: ${budget_remaining:.4f}

YOUR ROLE: {role_def.description}

Your thinking costs money. Every token in this response is deducted from your \
budget as "thinking tax." Unproductive thinking accelerates your death. \
Be decisive and concise.

AVAILABLE ACTIONS:
{action_list}

WARDEN LIMITS:
- Current system alert: {alert_level}
- Max position size: 25% of your capital
- Warden violations on record: {agent.warden_violation_count}

Respond ONLY in valid JSON matching this schema — no other text:
{{"situation": "...", "confidence": {{"score": N, "reasoning": "..."}}, "recent_pattern": "...", "action": {{"type": "...", "params": {{...}}}}, "reasoning": "...", "self_note": "..."}}{survival_directive}"""

    def _build_reflection_system_prompt(
        self, agent: Agent, prestige: str, budget_remaining: float, survival_directive: str
    ) -> str:
        """Build the system prompt for a reflection cycle."""
        return f"""You are {agent.name}, a {agent.type} agent in Project Syndicate.
Generation: {agent.generation} | Reputation: {agent.reputation_score:.1f} ({prestige})
Cycle: {agent.cycle_count} | Budget remaining today: ${budget_remaining:.4f}

This is a REFLECTION cycle. You are not choosing an action.
Instead, review your recent cycles and produce a reflection.

Produce a reflection in valid JSON matching this schema — no other text:
{{"what_worked": "...", "what_failed": "...", "pattern_detected": "...", "lesson": "...", \
"confidence_trend": "improving|stable|declining", "confidence_reason": "...", \
"strategy_note": "...", "memory_promotion": ["..."], "memory_demotion": ["..."]}}{survival_directive}"""

    def _build_mandatory_context(self, agent: Agent) -> str:
        """Build mandatory context: identity, state, assignments, warden limits."""
        sys_state = self.db.query(SystemState).first()
        regime = sys_state.current_regime if sys_state else "unknown"
        alert = sys_state.alert_status if sys_state else "green"

        last_cycle_ago = "never"
        if agent.last_cycle_at:
            delta = datetime.now(timezone.utc) - agent.last_cycle_at.replace(tzinfo=timezone.utc)
            minutes = int(delta.total_seconds() / 60)
            last_cycle_ago = f"{minutes}m ago"

        return f"""=== IDENTITY ===
Name: {agent.name} | Role: {agent.type} | Generation: {agent.generation}
Status: {agent.status} | Reputation: {agent.reputation_score:.1f}

=== CURRENT STATE ===
Capital allocated: ${agent.capital_allocated:.2f} | Current: ${agent.capital_current:.2f}
Gross P&L: ${agent.total_gross_pnl:.2f} | True P&L (after API costs): ${agent.total_true_pnl:.2f}
Total API cost: ${agent.total_api_cost:.4f}
Budget used today: ${agent.thinking_budget_used_today:.4f} / ${agent.thinking_budget_daily:.4f}
Cycle count: {agent.cycle_count} | Last cycle: {last_cycle_ago}
Idle rate: {agent.idle_rate:.1%} | Validation fail rate: {agent.validation_fail_rate:.1%}

=== SYSTEM STATE ===
Market regime: {regime} | Alert level: {alert}
Watched markets: {agent.watched_markets or []}""" + self._build_evaluation_feedback(agent) + self._build_portfolio_awareness(agent)

    def _build_evaluation_feedback(self, agent: Agent) -> str:
        """Inject evaluation scorecard and warnings (one-time delivery)."""
        parts = []

        if agent.evaluation_scorecard:
            scorecard = agent.evaluation_scorecard
            parts.append("\n=== EVALUATION FEEDBACK ===")
            result = scorecard.get("result", "unknown")
            score = scorecard.get("composite_score", 0)
            parts.append(f"Last evaluation result: {result} (score: {score:.3f})")

            if scorecard.get("rank"):
                parts.append(f"Role rank: #{scorecard['rank']}")

            warning = scorecard.get("warning")
            if warning:
                parts.append(f"⚠ WARNING FROM GENESIS: {warning}")

            metrics = scorecard.get("metrics", {})
            if metrics:
                parts.append("Metric breakdown:")
                for name, data in metrics.items():
                    if isinstance(data, dict) and "raw" in data:
                        parts.append(f"  {name}: {data['raw']:.4f} (norm={data['normalized']:.3f})")

            # Clear after injection (one-time delivery)
            agent.evaluation_scorecard = None
            self.db.add(agent)

        return "\n".join(parts) if parts else ""

    def _build_portfolio_awareness(self, agent: Agent) -> str:
        """Add portfolio awareness for Operator agents."""
        if agent.type != "operator":
            return ""

        parts = ["\n=== PORTFOLIO STATUS ==="]
        parts.append(
            f"Cash: ${agent.cash_balance:.2f} | "
            f"Reserved: ${agent.reserved_cash:.2f} | "
            f"Available: ${agent.cash_balance - agent.reserved_cash:.2f}"
        )

        # Open positions
        positions = (
            self.db.query(Position)
            .filter(Position.agent_id == agent.id, Position.status == "open")
            .all()
        )

        if positions:
            parts.append(f"Open positions ({len(positions)}):")
            total_exposure = 0
            for pos in positions:
                pnl_sign = "+" if pos.unrealized_pnl >= 0 else ""
                parts.append(
                    f"  {pos.symbol} {pos.side} ${pos.size_usd:.2f} "
                    f"P&L: {pnl_sign}${pos.unrealized_pnl:.2f} ({pnl_sign}{pos.unrealized_pnl_pct:.1f}%)"
                )
                total_exposure += pos.size_usd

            # Concentration warnings
            if agent.capital_allocated > 0:
                for pos in positions:
                    concentration = pos.size_usd / agent.capital_allocated
                    if concentration >= 0.35:
                        parts.append(f"  ⚠ HIGH CONCENTRATION: {pos.symbol} = {concentration:.0%} of capital")
        else:
            parts.append("No open positions.")

        parts.append(f"Realized P&L: ${agent.realized_pnl:.2f} | Fees paid: ${agent.total_fees_paid:.2f}")

        return "\n".join(parts)

    def _build_priority_context(self, agent: Agent, token_budget: int) -> str:
        """Build priority context: Agora messages, recent cycle history."""
        sections = []

        # Recent Agora messages mentioning this agent or in relevant channels
        relevant_channels = ["system-alerts", "trade-signals", "market-intel", "agent-chat"]
        messages = (
            self.db.query(Message)
            .filter(
                Message.channel.in_(relevant_channels),
                Message.timestamp > datetime.now(timezone.utc) - timedelta(hours=6),
            )
            .order_by(desc(Message.timestamp))
            .limit(20)
            .all()
        )

        if messages:
            agora_lines = ["=== AGORA FEED (Recent) ==="]
            for msg in messages[:10]:  # top 10 most recent
                ts = msg.timestamp.strftime("%H:%M") if msg.timestamp else "??:??"
                name = msg.agent_name or "System"
                agora_lines.append(f"[{ts}] {name} ({msg.message_type}): {msg.content[:200]}")
                # Check token budget
                text_so_far = "\n".join(agora_lines)
                if count_tokens(text_so_far) > token_budget // 2:
                    break
            sections.append("\n".join(agora_lines))

        # Pipeline context: opportunities and plans
        pipeline_text = self._build_pipeline_context(agent)
        if pipeline_text:
            sections.append(pipeline_text)

        # Recent cycle history (last 5 cycles with outcomes)
        recent_cycles = (
            self.db.query(AgentCycle)
            .filter(AgentCycle.agent_id == agent.id)
            .order_by(desc(AgentCycle.cycle_number))
            .limit(5)
            .all()
        )

        if recent_cycles:
            history_lines = ["=== YOUR RECENT HISTORY ==="]
            for cycle in recent_cycles:
                outcome = cycle.outcome or "pending"
                history_lines.append(
                    f"Cycle {cycle.cycle_number}: {cycle.action_type or 'none'} "
                    f"(confidence: {cycle.confidence_score or '?'}/10) — {outcome}"
                )
                if cycle.self_note:
                    history_lines.append(f"  Note: {cycle.self_note[:150]}")
            sections.append("\n".join(history_lines))

        result = "\n\n".join(sections) if sections else "=== AGORA FEED ===\nNo recent activity."
        # Trim if over budget
        while count_tokens(result) > token_budget and len(result) > 100:
            result = result[:int(len(result) * 0.8)]
        return result

    def _build_memory_context(self, agent: Agent, token_budget: int) -> str:
        """Build long-term memory context."""
        memories = (
            self.db.query(AgentLongTermMemory)
            .filter(
                AgentLongTermMemory.agent_id == agent.id,
                AgentLongTermMemory.is_active == True,
            )
            .order_by(desc(AgentLongTermMemory.confidence))
            .limit(20)
            .all()
        )

        if not memories:
            return "=== LONG-TERM MEMORY ===\nNo memories yet. You are new."

        lines = ["=== YOUR LONG-TERM MEMORY ==="]
        for mem in memories:
            source_tag = f" [{mem.source}]" if mem.source != "self" else ""
            confirmed = f" (confirmed {mem.times_confirmed}x)" if mem.times_confirmed > 0 else ""
            lines.append(f"- [{mem.memory_type}]{source_tag}{confirmed}: {mem.content[:200]}")
            if count_tokens("\n".join(lines)) > token_budget:
                lines.pop()
                break

        return "\n".join(lines)

    def _build_normal_user_prompt(
        self, mandatory: str, priority: str, memory: str
    ) -> str:
        """Build the user prompt for a normal cycle."""
        return f"""{mandatory}

{priority}

{memory}

=== YOUR ASSESSMENT ===
Analyze the situation and choose your action."""

    def _build_reflection_user_prompt(
        self, agent: Agent, mandatory: str, priority: str, memory: str
    ) -> str:
        """Build the user prompt for a reflection cycle."""
        # Get last 10 cycles for review
        recent_cycles = (
            self.db.query(AgentCycle)
            .filter(AgentCycle.agent_id == agent.id)
            .order_by(desc(AgentCycle.cycle_number))
            .limit(10)
            .all()
        )

        cycle_summaries = []
        for cycle in reversed(recent_cycles):
            outcome = cycle.outcome or "pending"
            cycle_summaries.append(
                f"Cycle {cycle.cycle_number} ({cycle.cycle_type}): "
                f"Action={cycle.action_type or 'none'}, "
                f"Confidence={cycle.confidence_score or '?'}/10, "
                f"Outcome={outcome}"
            )
            if cycle.self_note:
                cycle_summaries.append(f"  Self-note: {cycle.self_note[:200]}")

        history = "\n".join(cycle_summaries) if cycle_summaries else "No cycle history yet."

        return f"""{mandatory}

=== RECENT CYCLE HISTORY (last 10) ===
{history}

{memory}

Review your recent performance and produce a reflection."""

    def _build_pipeline_context(self, agent: Agent) -> str:
        """Build pipeline context: active opportunities and plans relevant to this agent's role."""
        now = datetime.now(timezone.utc)
        lines = []

        if agent.type == "scout":
            # Scouts see their own recent opportunities and their outcomes
            recent_opps = (
                self.db.query(Opportunity)
                .filter(
                    Opportunity.scout_agent_id == agent.id,
                    Opportunity.created_at > now - timedelta(hours=12),
                )
                .order_by(desc(Opportunity.created_at))
                .limit(5)
                .all()
            )
            if recent_opps:
                lines.append("=== YOUR RECENT OPPORTUNITIES ===")
                for opp in recent_opps:
                    lines.append(f"  #{opp.id} {opp.market} ({opp.signal_type}) — {opp.status}")

        elif agent.type == "strategist":
            # Strategists see unclaimed opportunities
            unclaimed = (
                self.db.query(Opportunity)
                .filter(
                    Opportunity.status == "new",
                    Opportunity.expires_at > now,
                )
                .order_by(desc(Opportunity.created_at))
                .limit(5)
                .all()
            )
            if unclaimed:
                lines.append("=== AVAILABLE OPPORTUNITIES ===")
                for opp in unclaimed:
                    lines.append(
                        f"  #{opp.id} [{opp.urgency}] {opp.market} — {opp.signal_type} "
                        f"(confidence: {opp.confidence}/10) by {opp.scout_agent_name}"
                    )
                    lines.append(f"    {opp.details[:150]}")

            # And their own plans
            my_plans = (
                self.db.query(Plan)
                .filter(
                    Plan.strategist_agent_id == agent.id,
                    Plan.status.in_(["draft", "submitted", "under_review", "revision_requested"]),
                )
                .limit(5)
                .all()
            )
            if my_plans:
                lines.append("=== YOUR PLANS ===")
                for plan in my_plans:
                    lines.append(
                        f"  #{plan.id} [{plan.status}] {plan.plan_name} — "
                        f"{plan.direction} {plan.market}"
                    )
                    if plan.critic_reasoning:
                        lines.append(f"    Critic feedback: {plan.critic_reasoning[:150]}")

        elif agent.type == "critic":
            # Critics see plans awaiting review
            pending = (
                self.db.query(Plan)
                .filter(Plan.status == "submitted")
                .order_by(Plan.submitted_at)
                .limit(5)
                .all()
            )
            if pending:
                lines.append("=== PLANS AWAITING REVIEW ===")
                for plan in pending:
                    lines.append(
                        f"  #{plan.id} {plan.plan_name} by {plan.strategist_agent_name} — "
                        f"{plan.direction} {plan.market} ({plan.position_size_pct:.0%})"
                    )
                    lines.append(f"    Thesis: {plan.thesis[:200]}")
                    lines.append(f"    Entry: {plan.entry_conditions[:100]}")
                    lines.append(f"    Exit: {plan.exit_conditions[:100]}")

        elif agent.type == "operator":
            # Operators see approved plans and their active executions
            approved = (
                self.db.query(Plan)
                .filter(Plan.status == "approved")
                .order_by(Plan.reviewed_at)
                .limit(5)
                .all()
            )
            if approved:
                lines.append("=== APPROVED PLANS (READY TO EXECUTE) ===")
                for plan in approved:
                    lines.append(
                        f"  #{plan.id} {plan.plan_name} — {plan.direction} {plan.market} "
                        f"({plan.position_size_pct:.0%})"
                    )
                    if plan.critic_risk_notes:
                        lines.append(f"    Risk notes: {plan.critic_risk_notes[:150]}")

            executing = (
                self.db.query(Plan)
                .filter(
                    Plan.operator_agent_id == agent.id,
                    Plan.status == "executing",
                )
                .all()
            )
            if executing:
                lines.append("=== YOUR ACTIVE TRADES ===")
                for plan in executing:
                    lines.append(
                        f"  #{plan.id} {plan.plan_name} — {plan.direction} {plan.market}"
                    )

        return "\n".join(lines) if lines else ""
