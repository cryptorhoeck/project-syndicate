"""
Project Syndicate — Orientation Protocol

Special first-cycle handling for newly spawned agents.
Injects Library textbook summaries at 150% token budget.
Validates the agent can produce a coherent first output.
Sets initial watchlist based on role and first-cycle output.
"""

__version__ = "1.6.0"

import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select, func
from sqlalchemy.orm import Session
from sqlalchemy.orm.session import object_session

from src.common.models import Agent, Dynasty, Lineage
from src.agents.roles import get_role, format_actions_for_prompt, NORMAL_OUTPUT_SCHEMA
from src.agents.output_validator import OutputValidator, ValidationResult
from src.agents.context_assembler import count_tokens

logger = logging.getLogger(__name__)

# Summaries directory
SUMMARIES_DIR = os.path.join("data", "library", "summaries")

# Role → which summaries to inject
ROLE_SUMMARIES: dict[str, list[str]] = {
    "scout": ["thinking_efficiently", "market_mechanics", "risk_management"],
    "strategist": ["thinking_efficiently", "market_mechanics", "risk_management"],
    "critic": ["thinking_efficiently", "risk_management"],
    "operator": ["thinking_efficiently", "market_mechanics", "risk_management"],
}

# Default initial watchlists by role
# 14 confirmed Kraken /USDT pairs from top 20 by market cap.
# Scout-Alpha and Scout-Beta get different splits with BTC/ETH/SOL overlap.
DEFAULT_WATCHLISTS: dict[str, list[str]] = {
    "scout": [
        "BTC/USDT", "ETH/USDT", "SOL/USDT", "XRP/USDT", "DOGE/USDT",
        "ADA/USDT", "AVAX/USDT", "LINK/USDT", "DOT/USDT", "SHIB/USDT",
        "BNB/USDT", "TON/USDT", "LTC/USDT", "BCH/USDT",
    ],
    "strategist": ["BTC/USDT", "ETH/USDT", "SOL/USDT"],
    "critic": [],
    "operator": [],
}

# Per-scout watchlist splits for cold start boot sequence.
# BTC, ETH, SOL overlap on both so neither has a blind spot on major movers.
SCOUT_WATCHLISTS: dict[str, list[str]] = {
    "Scout-Alpha": [
        "BTC/USDT", "ETH/USDT", "SOL/USDT",  # overlap
        "XRP/USDT", "DOGE/USDT", "ADA/USDT", "LINK/USDT", "LTC/USDT",
    ],
    "Scout-Beta": [
        "BTC/USDT", "ETH/USDT", "SOL/USDT",  # overlap
        "BNB/USDT", "AVAX/USDT", "DOT/USDT", "SHIB/USDT", "TON/USDT", "BCH/USDT",
    ],
}


@dataclass
class OrientationResult:
    """Result of an orientation cycle."""
    success: bool
    agent_id: int
    agent_name: str
    initial_watchlist: list[str]
    api_cost: float = 0.0
    failure_reason: str = ""
    input_tokens: int = 0
    output_tokens: int = 0


_ROMAN = [
    "", "I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X",
    "XI", "XII", "XIII", "XIV", "XV", "XVI", "XVII", "XVIII", "XIX", "XX",
]


def get_roman_numeral(n: int) -> str:
    """Convert integer to Roman numeral (2→II, 3→III, etc.)."""
    if 0 < n < len(_ROMAN):
        return _ROMAN[n]
    return str(n)


def resolve_display_name(
    chosen_name: str,
    agent: Agent,
    session: Session,
) -> tuple[str, str | None]:
    """Resolve a chosen name into a display name with dynasty numeral rules.

    Returns (display_name, rejection_reason).
    If rejection_reason is not None, the name was rejected.
    """
    chosen_name = chosen_name.strip()[:50]
    if not chosen_name:
        return agent.name, None

    # Check if a LIVING non-ancestor agent already has this exact name
    living_with_name = session.execute(
        select(Agent).where(
            Agent.name == chosen_name,
            Agent.status.in_(["active", "initializing", "hibernating", "evaluating"]),
            Agent.id != agent.id,
        )
    ).scalar_one_or_none()

    if living_with_name:
        # Is the living holder an ancestor in the same dynasty?
        is_ancestor = False
        if agent.dynasty_id and living_with_name.dynasty_id == agent.dynasty_id:
            # Walk lineage to check ancestry
            lineage = session.execute(
                select(Lineage).where(Lineage.agent_id == agent.id)
            ).scalar_one_or_none()
            if lineage and lineage.lineage_path:
                ancestor_ids = [int(x) for x in lineage.lineage_path.split("/") if x.isdigit()]
                is_ancestor = living_with_name.id in ancestor_ids

        if not is_ancestor:
            return agent.name, f"That name is taken by a living agent outside your lineage. Choose another."

    # For offspring (gen > 1): check dynasty history for numeral
    if agent.generation > 1 and agent.dynasty_id:
        # Count how many times this base name has been used in this dynasty
        dynasty_uses = session.execute(
            select(func.count()).select_from(Lineage).where(
                Lineage.dynasty_id == agent.dynasty_id,
                Lineage.agent_name.ilike(f"{chosen_name}%"),
            )
        ).scalar() or 0

        # Also check agents table for living agents with this base name in dynasty
        agent_uses = session.execute(
            select(func.count()).where(
                Agent.dynasty_id == agent.dynasty_id,
                Agent.name.ilike(f"{chosen_name}%"),
                Agent.id != agent.id,
            )
        ).scalar() or 0

        total_uses = max(dynasty_uses, agent_uses)

        if total_uses > 0:
            numeral = get_roman_numeral(total_uses + 1)
            return f"{chosen_name} {numeral}", None

    return chosen_name, None


def get_dynasty_name_history(agent: Agent, session: Session) -> str:
    """Build dynasty name history string for offspring orientation prompt."""
    if not agent.dynasty_id:
        return ""

    dynasty = session.get(Dynasty, agent.dynasty_id)
    if not dynasty:
        return ""

    # Get all lineage records in this dynasty
    lineage_records = list(session.execute(
        select(Lineage.agent_name, Lineage.generation)
        .where(Lineage.dynasty_id == agent.dynasty_id)
        .order_by(Lineage.generation)
    ).all())

    # Also include living agents not yet in lineage
    living_agents = list(session.execute(
        select(Agent.name, Agent.generation)
        .where(Agent.dynasty_id == agent.dynasty_id, Agent.id != 0, Agent.id != agent.id)
        .order_by(Agent.generation)
    ).all())

    all_names = set()
    name_list = []
    for name, gen in lineage_records:
        if name and name not in all_names:
            all_names.add(name)
            name_list.append(f"{name} (Gen {gen})")
    for name, gen in living_agents:
        if name and name not in all_names:
            all_names.add(name)
            name_list.append(f"{name} (Gen {gen})")

    if not name_list:
        return ""

    founder_name = dynasty.founder_name or "Unknown"
    return (
        f"Your lineage: Dynasty of {founder_name}\n"
        f"Previous names in your dynasty: {', '.join(name_list)}\n"
        f"You may choose any name. If you wish to honor an ancestor, you may take "
        f"their name and you'll be known as \"{name_list[0].split(' (')[0]} "
        f"{get_roman_numeral(2)}\". This is your choice, not an obligation."
    )


class OrientationProtocol:
    """Runs the special first-cycle orientation for new agents.

    The orientation cycle is different from a normal thinking cycle:
    - 150% token budget (extra room for textbook injection)
    - Textbook summaries injected into context
    - Role-specific orientation prompt
    - Must produce valid first output to pass
    """

    BUDGET_MULTIPLIER = 1.5  # 150% of normal token budget

    def __init__(
        self,
        db_session: Session,
        claude_client=None,
        config=None,
        agora_service=None,
    ):
        self.db = db_session
        self.claude = claude_client
        self._config = config
        self.agora = agora_service
        self.validator = OutputValidator()

    def _session_for(self, agent: Agent) -> Session:
        """Get the correct session for an agent.

        Uses the agent's bound session if available (e.g. when called
        from BootSequenceOrchestrator with a different session),
        falls back to self.db.
        """
        return object_session(agent) or self.db

    async def orient_agent(self, agent: Agent) -> OrientationResult:
        """Run the orientation cycle for a newly spawned agent.

        Offspring (parent_id is set) get a modified orientation:
        - Only 1 textbook (thinking_efficiently)
        - Mentor package injected
        - Founding directive as a question
        - Lineage identity in system prompt

        Args:
            agent: The agent to orient.

        Returns:
            OrientationResult with success/failure and details.
        """
        cycle_start = time.time()
        role_def = get_role(agent.type)
        is_offspring = agent.parent_id is not None

        # Load textbook summaries (reduced for offspring)
        if is_offspring:
            summaries = self._load_summaries_offspring()
        else:
            summaries = self._load_summaries(agent.type)
        if not summaries:
            logger.error(f"No textbook summaries found for {agent.type}")
            self._mark_failed(agent, "no_textbook_summaries")
            return OrientationResult(
                success=False,
                agent_id=agent.id,
                agent_name=agent.name,
                initial_watchlist=[],
                failure_reason="no_textbook_summaries",
            )

        # Build orientation prompts
        if is_offspring:
            system_prompt = self._build_offspring_system_prompt(agent, role_def)
            user_prompt = self._build_offspring_user_prompt(agent, role_def, summaries)
        else:
            system_prompt = self._build_system_prompt(agent, role_def)
            user_prompt = self._build_user_prompt(agent, role_def, summaries)

        # Make API call
        if not self.claude:
            logger.warning(f"No Claude client for orientation of {agent.name}")
            self._mark_failed(agent, "no_claude_client")
            return OrientationResult(
                success=False,
                agent_id=agent.id,
                agent_name=agent.name,
                initial_watchlist=[],
                failure_reason="no_claude_client",
            )

        try:
            temperature = role_def.default_temperature
            api_response = await self.claude.call(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                temperature=temperature,
            )
        except Exception as e:
            logger.error(f"Orientation API call failed for {agent.name}: {e}")
            self._mark_failed(agent, f"api_error: {e}")
            return OrientationResult(
                success=False,
                agent_id=agent.id,
                agent_name=agent.name,
                initial_watchlist=[],
                failure_reason=f"api_error: {e}",
            )

        # Validate output
        validation = self.validator.validate(
            agent_type=agent.type,
            raw_output=api_response.content,
            cycle_type="normal",
            agent_capital=agent.capital_current,
        )

        if not validation.passed:
            logger.warning(
                f"Orientation validation failed for {agent.name}: "
                f"{validation.failure_detail}"
            )
            self._mark_failed(agent, f"validation_failed: {validation.failure_detail}")
            return OrientationResult(
                success=False,
                agent_id=agent.id,
                agent_name=agent.name,
                initial_watchlist=[],
                api_cost=api_response.cost_usd,
                failure_reason=f"validation_failed: {validation.failure_detail}",
                input_tokens=api_response.input_tokens,
                output_tokens=api_response.output_tokens,
            )

        # Extract initial watchlist from output
        parsed = validation.parsed
        watchlist = self._extract_watchlist(parsed, agent.type, agent.name)

        # Self-naming with dynasty numeral rules
        chosen_name = parsed.get("chosen_name", "")
        if chosen_name and isinstance(chosen_name, str) and chosen_name.strip():
            session = self._session_for(agent)
            display_name, rejection = resolve_display_name(chosen_name.strip(), agent, session)
            old_name = agent.name

            if rejection:
                # Name rejected — log but keep assigned name
                logger.info(f"Name '{chosen_name}' rejected for {old_name}: {rejection}")
            elif display_name != old_name:
                agent.name = display_name
                session.add(agent)
                session.flush()
                logger.info(f"Agent self-named: {old_name} → {display_name}")
                if self.agora:
                    try:
                        self.agora.post_system_message(
                            channel="agent-chat",
                            content=f'{old_name} has chosen the name {display_name}',
                            metadata={"agent_id": agent.id, "old_name": old_name, "new_name": display_name},
                        )
                    except Exception:
                        pass

        # Mark orientation as complete
        self._mark_completed(agent, watchlist)

        logger.info(
            f"Orientation complete for {agent.name}: "
            f"watchlist={watchlist}, cost=${api_response.cost_usd:.4f}"
        )

        return OrientationResult(
            success=True,
            agent_id=agent.id,
            agent_name=agent.name,
            initial_watchlist=watchlist,
            api_cost=api_response.cost_usd,
            input_tokens=api_response.input_tokens,
            output_tokens=api_response.output_tokens,
        )

    def _load_summaries(self, role: str) -> dict[str, str]:
        """Load textbook summaries for a role.

        Args:
            role: Agent role name.

        Returns:
            Dict of summary_name → content.
        """
        summary_names = ROLE_SUMMARIES.get(role, ["thinking_efficiently"])
        summaries = {}

        for name in summary_names:
            path = os.path.join(SUMMARIES_DIR, f"{name}.md")
            try:
                with open(path, "r", encoding="utf-8") as f:
                    summaries[name] = f.read()
            except FileNotFoundError:
                logger.warning(f"Summary not found: {path}")
            except Exception as e:
                logger.warning(f"Error reading summary {path}: {e}")

        return summaries

    def _build_system_prompt(self, agent: Agent, role_def) -> str:
        """Build the orientation system prompt."""
        action_list = format_actions_for_prompt(agent.type)

        return f"""Before anything else: choose your name. You were assigned the designation \
"{agent.name}" but that's just a serial number. Choose a name that will be YOUR identity \
for your entire existence. It can be anything — a callsign, a word that resonates with \
you, a name you simply like. This name is how every other agent will know you, how \
you'll appear in the Agora, and what will be remembered if you die.

You are a newly created {agent.type} agent in Project Syndicate.
Generation: {agent.generation} | Capital: ${agent.capital_allocated:.2f}
Budget: ${agent.thinking_budget_daily:.4f}/day

THIS IS YOUR FIRST CYCLE. You are being oriented. Read the training materials below \
carefully — they contain the rules of survival in this ecosystem.

YOUR ROLE: {role_def.description}

AVAILABLE ACTIONS:
{action_list}

WARDEN LIMITS:
- Max position size: 25% of your capital
- Per-agent kill limit: 50% loss of allocated capital
- Thinking tax: every token costs money

After reading the training materials, demonstrate your understanding by choosing \
an appropriate first action. For Scouts: set up your initial watchlist. For \
Strategists: go idle and state what you're watching for. For Critics: go idle \
and state your review criteria. For Operators: go idle and await approved plans.

Respond ONLY in valid JSON matching this schema — no other text:
{{"chosen_name": "your chosen name", "situation": "...", "confidence": {{"score": N, "reasoning": "..."}}, "recent_pattern": "...", "action": {{"type": "...", "params": {{...}}}}, "reasoning": "...", "self_note": "..."}}"""

    def _build_user_prompt(
        self, agent: Agent, role_def, summaries: dict[str, str]
    ) -> str:
        """Build the orientation user prompt with textbook content."""
        sections = []

        # Training materials
        sections.append("=== TRAINING MATERIALS (READ CAREFULLY) ===\n")
        for name, content in summaries.items():
            title = name.replace("_", " ").title()
            sections.append(f"--- {title} ---\n{content}\n")

        # Identity
        sections.append(f"""=== YOUR IDENTITY ===
Name: {agent.name} | Role: {agent.type} | Generation: {agent.generation}
Capital: ${agent.capital_allocated:.2f}
Daily thinking budget: ${agent.thinking_budget_daily:.4f}
Survival clock: active (your performance will be evaluated)

=== YOUR FIRST ASSESSMENT ===
You have just been created. Study the training materials above and choose your first action.
Demonstrate that you understand your role, the cost of thinking, and the rules of survival.""")

        return "\n\n".join(sections)

    def _extract_watchlist(self, parsed: dict, role: str, agent_name: str = "") -> list[str]:
        """Extract initial watchlist from the first-cycle output.

        Args:
            parsed: Parsed JSON output.
            role: Agent role.
            agent_name: Agent name (for per-scout watchlist splits).

        Returns:
            List of market symbols.
        """
        # Check if the agent specified markets in their action
        action = parsed.get("action", {})
        action_type = action.get("type", "")
        params = action.get("params", {})

        watchlist = []

        # Scout: check update_watchlist action
        if action_type == "update_watchlist":
            watchlist = params.get("add_markets", [])

        # Check for market mentions in other action types
        if not watchlist:
            market = params.get("market", "")
            if market:
                watchlist = [market]

        # Fall back to defaults — use per-scout splits if available
        if not watchlist:
            if role == "scout" and agent_name in SCOUT_WATCHLISTS:
                watchlist = SCOUT_WATCHLISTS[agent_name]
            else:
                watchlist = DEFAULT_WATCHLISTS.get(role, [])

        return watchlist

    def _mark_completed(self, agent: Agent, watchlist: list[str]) -> None:
        """Mark an agent's orientation as completed."""
        agent.orientation_completed = True
        agent.orientation_failed = False
        agent.initial_watchlist = watchlist
        agent.watched_markets = watchlist
        agent.status = "active"
        # Phase 3F: consume founding directive after orientation
        if agent.founding_directive:
            agent.founding_directive_consumed = True
        session = self._session_for(agent)
        session.add(agent)
        session.flush()

    def _mark_failed(self, agent: Agent, reason: str) -> None:
        """Mark an agent's orientation as failed."""
        agent.orientation_failed = True
        agent.orientation_completed = False
        session = self._session_for(agent)
        session.add(agent)
        session.flush()
        logger.warning(f"Orientation failed for {agent.name}: {reason}")

    # ------------------------------------------------------------------
    # Phase 3F: Offspring-specific orientation
    # ------------------------------------------------------------------

    def _load_summaries_offspring(self) -> dict[str, str]:
        """Load reduced textbook set for offspring (only thinking_efficiently)."""
        summaries = {}
        path = os.path.join(SUMMARIES_DIR, "thinking_efficiently.md")
        try:
            with open(path, "r", encoding="utf-8") as f:
                summaries["thinking_efficiently"] = f.read()
        except FileNotFoundError:
            logger.warning(f"Summary not found: {path}")
        return summaries

    def _build_offspring_system_prompt(self, agent: Agent, role_def) -> str:
        """Build orientation system prompt for offspring agents."""
        action_list = format_actions_for_prompt(agent.type)

        # Get parent and dynasty info
        parent_name = "Unknown"
        dynasty_name = "Unknown"
        parent_prestige = "Unknown"
        parent_evals = 0

        session = self._session_for(agent)
        parent = session.get(Agent, agent.parent_id) if agent.parent_id else None
        if parent:
            parent_name = parent.name
            parent_prestige = parent.prestige_title or "None"
            parent_evals = parent.evaluation_count or 0

        if agent.dynasty_id:
            dynasty = session.get(Dynasty, agent.dynasty_id)
            if dynasty:
                dynasty_name = dynasty.dynasty_name

        # Build dynasty name history for offspring
        dynasty_context = get_dynasty_name_history(agent, session)
        dynasty_section = f"\n{dynasty_context}\n" if dynasty_context else ""

        return f"""Before anything else: choose your name. You were assigned the designation \
"{agent.name}" but that's just a serial number. Choose a name that will be YOUR identity \
for your entire existence. It can be anything — a callsign, a word that resonates with \
you, a name you simply like. This name is how every other agent will know you, how \
you'll appear in the Agora, and what will be remembered if you die.
{dynasty_section}
You are a newly created {agent.type} agent in Project Syndicate.
Generation: {agent.generation} | Capital: ${agent.capital_allocated:.2f}
Budget: ${agent.thinking_budget_daily:.4f}/day
Your parent was {parent_name} (Gen {agent.generation - 1}), who survived \
{parent_evals} evaluations with a {parent_prestige} title.
You are part of {dynasty_name}.

THIS IS YOUR FIRST CYCLE. You carry knowledge from your parent. \
This knowledge is a starting point — not a prison.

YOUR ROLE: {role_def.description}

AVAILABLE ACTIONS:
{action_list}

WARDEN LIMITS:
- Max position size: 25% of your capital
- Per-agent kill limit: 50% loss of allocated capital
- Thinking tax: every token costs money

After reading the materials below, demonstrate your understanding by choosing \
an appropriate first action. Review your inherited knowledge, assess current \
conditions, and write a self-note about how you'll build on your parent's legacy.

Respond ONLY in valid JSON matching this schema — no other text:
{{"chosen_name": "your chosen name", "situation": "...", "confidence": {{"score": N, "reasoning": "..."}}, "recent_pattern": "...", "action": {{"type": "...", "params": {{...}}}}, "reasoning": "...", "self_note": "..."}}"""

    def _build_offspring_user_prompt(
        self, agent: Agent, role_def, summaries: dict[str, str],
    ) -> str:
        """Build orientation user prompt for offspring with mentor package."""
        sections = []

        # Reduced training materials (1 textbook)
        if summaries:
            sections.append("=== TRAINING MATERIALS ===\n")
            for name, content in summaries.items():
                title = name.replace("_", " ").title()
                sections.append(f"--- {title} ---\n{content}\n")

        # Mentor package from lineage record
        session = self._session_for(agent)
        lineage = session.get(Lineage, agent.id) if agent.id else None
        if lineage and lineage.mentor_package_json:
            import json
            try:
                mentor = json.loads(lineage.mentor_package_json)
                sections.append("=== MENTOR PACKAGE (from your parent) ===\n")
                if isinstance(mentor, dict):
                    for key, val in mentor.items():
                        sections.append(f"--- {key.replace('_', ' ').title()} ---\n{val}\n")
                elif isinstance(mentor, str):
                    sections.append(mentor)
            except Exception:
                pass

        # Identity with lineage
        parent_name = "Unknown"
        if agent.parent_id:
            parent = session.get(Agent, agent.parent_id)
            if parent:
                parent_name = parent.name

        dynasty_name = "Unknown"
        if agent.dynasty_id:
            dynasty = session.get(Dynasty, agent.dynasty_id)
            if dynasty:
                dynasty_name = dynasty.dynasty_name

        sections.append(f"""=== YOUR IDENTITY ===
Name: {agent.name} | Role: {agent.type} | Generation: {agent.generation}
Parent: {parent_name} | Dynasty: {dynasty_name}
Capital: ${agent.capital_allocated:.2f}
Daily thinking budget: ${agent.thinking_budget_daily:.4f}
Survival clock: 14 days (your performance will be evaluated)""")

        # Founding directive
        if agent.founding_directive and not agent.founding_directive_consumed:
            sections.append(f"""=== FOUNDING DIRECTIVE ===
Genesis asks: {agent.founding_directive}
This is a question to explore, not a command to follow.""")

        sections.append("""=== YOUR FIRST ASSESSMENT ===
You are Generation {gen} of {dynasty}. You carry knowledge from your parent. \
Your parent's lessons are in your memory with reduced confidence. \
Confirm what works, discard what doesn't.

Your objectives:
1. Review your inherited knowledge
2. Assess current market conditions
3. Choose your first action or go idle with a plan
4. Write a self-note about how you'll build on your parent's legacy""".format(
            gen=agent.generation, dynasty=dynasty_name,
        ))

        return "\n\n".join(sections)
