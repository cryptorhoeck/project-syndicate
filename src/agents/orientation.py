"""
Project Syndicate — Orientation Protocol

Special first-cycle handling for newly spawned agents.
Injects Library textbook summaries at 150% token budget.
Validates the agent can produce a coherent first output.
Sets initial watchlist based on role and first-cycle output.
"""

__version__ = "1.2.0"

import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.orm import Session

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
DEFAULT_WATCHLISTS: dict[str, list[str]] = {
    "scout": ["BTC/USDT", "ETH/USDT", "SOL/USDT"],
    "strategist": ["BTC/USDT", "ETH/USDT"],
    "critic": [],
    "operator": [],
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
    ):
        self.db = db_session
        self.claude = claude_client
        self._config = config
        self.validator = OutputValidator()

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
        watchlist = self._extract_watchlist(parsed, agent.type)

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

        return f"""You are {agent.name}, a newly created {agent.type} agent in Project Syndicate.
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
{{"situation": "...", "confidence": {{"score": N, "reasoning": "..."}}, "recent_pattern": "...", "action": {{"type": "...", "params": {{...}}}}, "reasoning": "...", "self_note": "..."}}"""

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

    def _extract_watchlist(self, parsed: dict, role: str) -> list[str]:
        """Extract initial watchlist from the first-cycle output.

        Args:
            parsed: Parsed JSON output.
            role: Agent role.

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

        # Fall back to defaults
        if not watchlist:
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
        self.db.add(agent)
        self.db.flush()

    def _mark_failed(self, agent: Agent, reason: str) -> None:
        """Mark an agent's orientation as failed."""
        agent.orientation_failed = True
        agent.orientation_completed = False
        self.db.add(agent)
        self.db.flush()
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

        parent = self.db.get(Agent, agent.parent_id) if agent.parent_id else None
        if parent:
            parent_name = parent.name
            parent_prestige = parent.prestige_title or "None"
            parent_evals = parent.evaluation_count or 0

        if agent.dynasty_id:
            dynasty = self.db.get(Dynasty, agent.dynasty_id)
            if dynasty:
                dynasty_name = dynasty.dynasty_name

        return f"""You are {agent.name}, a newly created {agent.type} agent in Project Syndicate.
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
{{"situation": "...", "confidence": {{"score": N, "reasoning": "..."}}, "recent_pattern": "...", "action": {{"type": "...", "params": {{...}}}}, "reasoning": "...", "self_note": "..."}}"""

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
        lineage = self.db.get(Lineage, agent.id) if agent.id else None
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
            parent = self.db.get(Agent, agent.parent_id)
            if parent:
                parent_name = parent.name

        dynasty_name = "Unknown"
        if agent.dynasty_id:
            dynasty = self.db.get(Dynasty, agent.dynasty_id)
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
