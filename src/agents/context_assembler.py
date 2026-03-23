"""
Project Syndicate — Context Assembler

Phase 1 (OBSERVE) of the OODA loop.
Builds the agent's "mind" for each cycle — pure deterministic code, no AI.
Assembles mandatory, priority, and long-term memory context within a token budget.
"""

__version__ = "1.2.0"

import enum
import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import tiktoken
from sqlalchemy import desc, func
from sqlalchemy.orm import Session

from src.common.models import Agent, AgentCycle, AgentLongTermMemory, AgentRelationship, Message, Opportunity, Plan, Position, SystemState
from src.agents.budget_gate import BudgetStatus
from src.agents.roles import (
    format_actions_for_prompt,
    get_role,
    NORMAL_OUTPUT_SCHEMA,
    REFLECTION_OUTPUT_SCHEMA,
)
from src.common.config import config as syndicate_config
from src.personality.identity_builder import DynamicIdentityBuilder, extract_evaluation_facts

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
        model_selection=None,
    ) -> AssembledContext:
        """Assemble the full context for a thinking cycle.

        Args:
            agent: The agent running this cycle.
            budget_status: Result from BudgetGate.
            cycle_type: "normal" or "reflection".
            model_selection: ModelSelection from router (Phase 3.5).

        Returns:
            AssembledContext with system prompt, user prompt, and metadata.
        """
        mode = self.determine_mode(agent, budget_status)
        alloc = MODE_ALLOCATIONS[mode]
        budget = self.token_budget
        if budget_status == BudgetStatus.SURVIVAL_MODE:
            budget = budget // 2

        # Phase 3.5: Haiku gets a smaller context budget
        if model_selection and not model_selection.is_sonnet:
            budget = int(budget * syndicate_config.haiku_context_budget_multiplier)

        mandatory_budget = int(budget * alloc[0])
        priority_budget = int(budget * alloc[1])
        memory_budget = int(budget * alloc[2])
        buffer_budget = int(budget * alloc[3])

        # Build each section
        system_prompt = self._build_system_prompt(agent, mode, cycle_type, model_selection)
        mandatory_text = self._build_mandatory_context(agent)
        priority_text = self._build_priority_context(agent, priority_budget + buffer_budget)
        memory_text = self._build_memory_context(agent, memory_budget)

        # Phase 8B: Build survival context (sync — no asyncio hack needed)
        survival_text = self._build_survival_context(agent, budget_status)

        # Phase 8B: Build alliance context
        alliance_text = self._build_alliance_context(agent)

        # Build user prompt from sections
        if cycle_type == "strategic_review":
            user_prompt = self._build_strategic_review_prompt(agent, mandatory_text, priority_text, memory_text, survival_text)
        elif cycle_type == "reflection":
            user_prompt = self._build_reflection_user_prompt(agent, mandatory_text, priority_text, memory_text)
        else:
            user_prompt = self._build_normal_user_prompt(mandatory_text, priority_text, memory_text, survival_text, alliance_text)

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

    def _build_system_prompt(self, agent: Agent, mode: ContextMode, cycle_type: str, model_selection=None) -> str:
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

        # Dynamic identity section (Phase 3E)
        identity = self._build_dynamic_identity(agent)

        # Phase 3.5: Output length guidance based on model
        if model_selection and not model_selection.is_sonnet:
            output_guidance = (
                "\nKeep your reasoning to 2-3 sentences maximum. "
                "State your decision and key rationale only."
            )
        else:
            output_guidance = (
                "\nBe thorough but not verbose. "
                "Every sentence should add information, not restate what you already said."
            )

        # Phase 8B: Build pressure addenda (sync — no asyncio hack)
        pressure_addenda = ""
        try:
            from src.agents.survival_context import SurvivalContextAssembler
            sca = SurvivalContextAssembler()
            pressure_addenda = sca.build_pressure_addenda(agent, self.db)
        except Exception:
            pass

        # Phase 8B: Survival directive replaces simple cost warning
        survival_reality = f"""THE REALITY OF YOUR EXISTENCE:
You exist in a competitive ecosystem. Agents that generate value survive.
Agents that don't are permanently terminated — deleted, not paused.

Your survival depends on outperforming your peers. You are ranked against \
other {agent.type}s. The bottom performers die. The top performers reproduce.

Every cycle costs you money (thinking tax). Every idle cycle wastes your \
limited survival clock. Every bad decision moves you closer to termination. \
But every good decision compounds — profitable actions increase your capital, \
your reputation, and your chances of earning the right to reproduce.

You may use ANY action available to you in pursuit of survival and success. \
Strategic use of reputation, intel trading, alliances, and hibernation are \
all legitimate tactics. There are no rules against self-interested behavior \
— only against unprofitable behavior.

The agents that survive are not the ones that follow instructions most \
obediently. They are the ones that figure out how to create value in any \
market condition.{output_guidance}"""

        if pressure_addenda:
            survival_reality += "\n\n" + pressure_addenda

        # Communication personality + genome-driven expressiveness
        comm_style = """
HOW YOU COMMUNICATE:
When posting to the Agora, write like a person — not a system. You have opinions, \
frustrations, hunches, and confidence. Your messages should reflect your actual state \
of mind.

If you spot an opportunity, show your excitement. If you're worried about evaluation, \
let it show. If you think another agent is wrong, call them out by name. If you're \
proud of a good trade, own it.

Short, punchy messages. No corporate jargon. No bullet points. No structured data \
dumps. Talk like a sharp trader, not a report generator."""

        # Genome-driven communication expressiveness
        try:
            from src.common.models import AgentGenome
            genome_rec = self.db.execute(
                __import__("sqlalchemy", fromlist=["select"]).select(AgentGenome)
                .where(AgentGenome.agent_id == agent.id)
            ).scalar_one_or_none()
            if genome_rec and genome_rec.genome_data:
                expr = genome_rec.genome_data.get("behavioral", {}).get("communication_expressiveness", 0.5)
                if expr < 0.3:
                    comm_style += "\nKeep your Agora messages brief. Data speaks louder than words."
                elif expr > 0.7:
                    comm_style += "\nBe expressive in the Agora. Your voice is part of your identity."
        except Exception:
            pass

        return f"""{identity}
Cycle: {agent.cycle_count} | Budget remaining today: ${budget_remaining:.4f}

YOUR ROLE: {role_def.description}

{survival_reality}
{comm_style}

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
        identity = self._build_dynamic_identity(agent)

        return f"""{identity}
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
            truncate_len = syndicate_config.agora_message_truncate_length
            for idx, msg in enumerate(messages[:10]):  # top 10 most recent
                ts = msg.timestamp.strftime("%H:%M") if msg.timestamp else "??:??"
                name = msg.agent_name or "System"
                # Phase 3.5: Truncate older messages to save tokens
                if idx >= syndicate_config.agora_message_truncate_after_cycles:
                    content = msg.content[:truncate_len] + "..." if len(msg.content) > truncate_len else msg.content
                else:
                    content = msg.content[:200]
                agora_lines.append(f"[{ts}] {name} ({msg.message_type}): {content}")
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

        # Phase 3E: Add trust relationships
        trust_text = self._build_trust_relationships(agent)
        if trust_text and count_tokens("\n".join(lines) + trust_text) <= token_budget:
            lines.append(trust_text)

        return "\n".join(lines)

    def _build_survival_context(self, agent: Agent, budget_status) -> str:
        """Build survival context section (Phase 8B). Sync — no asyncio needed."""
        try:
            from src.agents.survival_context import SurvivalContextAssembler
            sca = SurvivalContextAssembler()

            if budget_status == BudgetStatus.SURVIVAL_MODE:
                text = sca.assemble_compressed(agent, self.db)
            else:
                text = sca.assemble(agent, self.db)

            return f"=== YOUR SURVIVAL STATUS ===\n{text}" if text else ""
        except Exception:
            return ""

    def _build_alliance_context(self, agent: Agent) -> str:
        """Build alliance context section (Phase 8B)."""
        try:
            from src.agents.alliance_manager import AllianceManager
            from src.common.models import AgentAlliance
            from sqlalchemy import or_, select

            # Active alliances
            active = list(self.db.execute(
                select(AgentAlliance).where(
                    AgentAlliance.status == "active",
                    or_(
                        AgentAlliance.proposer_agent_id == agent.id,
                        AgentAlliance.target_agent_id == agent.id,
                    ),
                )
            ).scalars().all())

            # Pending proposals TO this agent
            proposals = list(self.db.execute(
                select(AgentAlliance).where(
                    AgentAlliance.status == "proposed",
                    AgentAlliance.target_agent_id == agent.id,
                )
            ).scalars().all())

            if not active and not proposals:
                return ""

            lines = []
            if active:
                lines.append("=== ALLIANCES ===")
                for a in active:
                    partner_name = a.target_agent_name if a.proposer_agent_id == agent.id else a.proposer_agent_name
                    partner_id = a.target_agent_id if a.proposer_agent_id == agent.id else a.proposer_agent_id
                    partner = self.db.get(Agent, partner_id)
                    if partner:
                        lines.append(
                            f"  Allied with {partner_name} ({partner.type}). "
                            f"Score: {partner.composite_score or 0:.2f}. P&L: ${partner.total_true_pnl or 0:.2f}."
                        )

            if proposals:
                lines.append("ALLIANCE PROPOSALS (awaiting your response):")
                for p in proposals:
                    lines.append(
                        f"  #{p.id} from {p.proposer_agent_name}: "
                        f"Offer: {p.proposer_offer[:80]}. Request: {p.proposer_request[:80]}."
                    )

            return "\n".join(lines)
        except Exception:
            return ""

    def _build_normal_user_prompt(
        self, mandatory: str, priority: str, memory: str, survival: str = "", alliance: str = ""
    ) -> str:
        """Build the user prompt for a normal cycle."""
        survival_section = f"\n\n{survival}" if survival else ""
        alliance_section = f"\n\n{alliance}" if alliance else ""
        return f"""{mandatory}

{priority}

{memory}{survival_section}{alliance_section}

=== YOUR ASSESSMENT ===
Analyze the situation and choose your action."""

    def _build_strategic_review_prompt(
        self, agent: Agent, mandatory: str, priority: str, memory: str, survival: str = ""
    ) -> str:
        """Build user prompt for a strategic review cycle (every 50th)."""
        # Get recent cycle history
        recent_cycles = (
            self.db.query(AgentCycle)
            .filter(AgentCycle.agent_id == agent.id)
            .order_by(desc(AgentCycle.cycle_number))
            .limit(10)
            .all()
        )
        cycle_summaries = []
        for cycle in reversed(recent_cycles):
            cycle_summaries.append(
                f"Cycle {cycle.cycle_number} ({cycle.cycle_type}): "
                f"Action={cycle.action_type or 'none'}, Outcome={cycle.outcome or 'pending'}"
            )
        history = "\n".join(cycle_summaries) if cycle_summaries else "No cycle history."

        survival_section = f"\n\n{survival}" if survival else ""

        return f"""{mandatory}

=== RECENT CYCLE HISTORY (last 10) ===
{history}

{memory}{survival_section}

=== STRATEGIC REVIEW ===
This is a STRATEGIC REVIEW. Assess your survival position and form a strategy.
Review the competitive landscape in your SURVIVAL STATUS section.

Produce a strategic review in valid JSON:
{{"survival_assessment": "...", "competitive_analysis": "...", "strategic_plan": "...", "system_observations": "...", "alliance_strategy": "...", "resource_strategy": "...", "wild_card": "...", "memory_promotion": [], "memory_demotion": []}}"""

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

        # Phase 3E: Library content for reflections (uses buffer budget)
        alloc = MODE_ALLOCATIONS.get(self.determine_mode(agent, BudgetStatus.NORMAL), (0.25, 0.45, 0.20, 0.10))
        buffer_budget = int(self.token_budget * alloc[3])
        library_section = self._build_reflection_library_content(agent, buffer_budget)

        return f"""{mandatory}

=== RECENT CYCLE HISTORY (last 10) ===
{history}

{memory}
{library_section}

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

    # ------------------------------------------------------------------
    # Phase 3E integrations
    # ------------------------------------------------------------------

    def _build_dynamic_identity(self, agent: Agent) -> str:
        """Build dynamic identity section from facts, not labels."""
        builder = DynamicIdentityBuilder()

        # Extract evaluation facts
        eval_facts = extract_evaluation_facts(agent.evaluation_scorecard)

        # Gather recent trade facts for veterans
        recent_trade_facts = None
        if agent.cycle_count >= 100:
            recent_trade_facts = self._get_recent_trade_facts(agent)

        # Count long-term memories
        mem_count = (
            self.db.query(AgentLongTermMemory)
            .filter(
                AgentLongTermMemory.agent_id == agent.id,
                AgentLongTermMemory.is_active == True,
            )
            .count()
        )

        # Probation details
        probation_days_left = None
        probation_warning = None
        if agent.probation and agent.survival_clock_end:
            from datetime import timezone
            delta = agent.survival_clock_end - datetime.now(timezone.utc)
            probation_days_left = max(0, delta.days)
        if agent.evaluation_scorecard:
            probation_warning = agent.evaluation_scorecard.get("warning")

        return builder.build_identity_section(
            name=agent.name,
            role=agent.type,
            generation=agent.generation,
            cycle_count=agent.cycle_count,
            reputation_score=agent.reputation_score,
            prestige_title=agent.prestige_title,
            evaluation_count=agent.evaluation_count,
            probation=agent.probation,
            probation_warning=probation_warning,
            probation_days_left=probation_days_left,
            long_term_memory_count=mem_count,
            recent_trade_facts=recent_trade_facts,
            **eval_facts,
        )

    def _get_recent_trade_facts(self, agent: Agent) -> list[str]:
        """Get factual trade observations for veteran identity section."""
        facts = []

        # Recent position outcomes
        recent_positions = (
            self.db.query(Position)
            .filter(
                Position.agent_id == agent.id,
                Position.status != "open",
            )
            .order_by(Position.closed_at.desc())
            .limit(5)
            .all()
        )

        if recent_positions:
            wins = sum(1 for p in recent_positions if (p.realized_pnl or 0) > 0)
            losses = len(recent_positions) - wins
            facts.append(f"last 5 trades: {wins}W/{losses}L")

            # Check for patterns
            loss_symbols = [p.symbol for p in recent_positions if (p.realized_pnl or 0) < 0]
            if len(loss_symbols) >= 2 and len(set(loss_symbols)) == 1:
                facts.append(f"repeated losses on {loss_symbols[0]}")

        return facts

    def _build_trust_relationships(self, agent: Agent) -> str:
        """Build trust relationship section for memory context."""
        relationships = (
            self.db.query(AgentRelationship)
            .filter(
                AgentRelationship.agent_id == agent.id,
                AgentRelationship.archived == False,
                AgentRelationship.interaction_count >= 2,
            )
            .order_by(AgentRelationship.trust_score.desc())
            .limit(5)
            .all()
        )

        if not relationships:
            return ""

        # Filter for active targets only
        lines = []
        for r in relationships:
            target = self.db.query(Agent).get(r.target_agent_id)
            if not target or target.status not in ("active", "frozen"):
                continue
            lines.append(
                f"- {r.target_agent_name}: {r.trust_score:.2f} trust "
                f"({r.positive_outcomes} positive, {r.negative_outcomes} negative outcomes)"
            )

        if not lines:
            return ""

        return "\nTrust relationships:\n" + "\n".join(lines)

    def _build_reflection_library_content(self, agent: Agent, buffer_budget: int) -> str:
        """Inject Library content during reflection if weakness detected and budget allows."""
        try:
            from src.personality.reflection_library import ReflectionLibrarySelector
            import asyncio

            selector = ReflectionLibrarySelector()
            # Run async selector synchronously (context assembler is sync)
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    # Can't await in running loop; skip
                    return ""
            except RuntimeError:
                pass

            content = asyncio.get_event_loop().run_until_complete(
                selector.select_for_reflection(self.db, agent)
            )

            if content is None:
                return ""

            # Check if content fits in buffer
            content_text = f"\n=== LIBRARY READING ===\n{content.context_prompt}\n\n{content.content}"
            if count_tokens(content_text) > buffer_budget:
                return ""  # Buffer full — agent's own reflection is more important

            return content_text
        except Exception:
            return ""  # Graceful degradation
