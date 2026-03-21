"""
Project Syndicate — Reproduction Engine (Phase 3F)

Handles agent reproduction: eligibility, Genesis AI mutation decisions,
offspring building, memory/trust inheritance, and orientation.
"""

__version__ = "1.2.0"

import json
import logging
import random
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import anthropic
from sqlalchemy import select, func
from sqlalchemy.orm import Session

from src.common.config import config
from src.common.models import (
    Agent, AgentLongTermMemory, AgentRelationship, SystemState,
)
from src.dynasty.dynasty_manager import DynastyManager
from src.dynasty.lineage_manager import LineageManager

logger = logging.getLogger(__name__)


@dataclass
class ReproductionDecision:
    """Result of Genesis's reproduction decision."""
    should_reproduce: bool
    reasoning: str = ""
    offspring_name: str = ""
    mutations: dict = field(default_factory=dict)
    proactive_termination: dict | None = None


@dataclass
class ReproductionResult:
    """Result of a reproduction attempt."""
    reproduced: bool
    reason: str = ""
    offspring: Agent | None = None
    parent: Agent | None = None


class ReproductionEngine:
    """Handles the full reproduction lifecycle."""

    PRESTIGE_ELIGIBLE = ("Expert", "Master", "Grandmaster")

    def __init__(
        self,
        dynasty_manager: DynastyManager | None = None,
        lineage_manager: LineageManager | None = None,
    ) -> None:
        self.log = logger
        self.dynasty_mgr = dynasty_manager or DynastyManager()
        self.lineage_mgr = lineage_manager or LineageManager()

    async def check_and_reproduce(
        self, session: Session, agora_service=None,
    ) -> ReproductionResult:
        """Called during Genesis evaluation cycle, step 7."""
        # Check system alert status
        state = session.execute(select(SystemState)).scalars().first()
        if state and state.alert_status in ("yellow", "red", "circuit_breaker"):
            return ReproductionResult(reproduced=False, reason="system_in_alert")

        candidates = self._get_eligible_candidates(session)
        if not candidates:
            return ReproductionResult(reproduced=False, reason="no_eligible_candidates")

        # Process top candidate only (one per cycle)
        parent = candidates[0]

        # Dynasty concentration check
        blocked, concentration, warning = await self._check_concentration(session, parent)
        if blocked:
            return ReproductionResult(
                reproduced=False,
                reason=f"dynasty_concentration_blocked ({concentration:.0%})",
            )

        # Check available slots
        active_count = session.execute(
            select(func.count()).select_from(Agent).where(Agent.status == "active")
        ).scalar() or 0
        available_slots = config.max_agents - active_count

        if available_slots <= 0:
            # At capacity — still proceed, Genesis may recommend proactive termination
            pass

        # Ask Genesis
        decision = await self._genesis_reproduction_decision(
            session, parent, concentration, warning, active_count, available_slots,
        )

        if not decision.should_reproduce:
            self.log.info(f"Genesis denied reproduction for {parent.name}: {decision.reasoning}")
            return ReproductionResult(reproduced=False, reason=decision.reasoning)

        # Check if parent died this cycle (posthumous reproduction)
        parent_alive = parent.status == "active"

        # Build the offspring
        offspring = await self._build_offspring(
            session, parent, decision,
            posthumous=not parent_alive,
            agora_service=agora_service,
        )

        return ReproductionResult(reproduced=True, offspring=offspring, parent=parent)

    def _get_eligible_candidates(self, session: Session) -> list[Agent]:
        """Get all agents meeting reproduction requirements, ranked by composite."""
        now = datetime.now(timezone.utc)
        candidates = []

        active_agents = session.execute(
            select(Agent).where(Agent.status.in_(["active", "terminated"]))
        ).scalars().all()

        # Get role medians
        role_medians = self._get_role_medians(session)

        for agent in active_agents:
            # Must have eligible prestige
            if agent.prestige_title not in self.PRESTIGE_ELIGIBLE:
                continue

            # Must be in top 50% of role
            median = role_medians.get(agent.type, 0)
            if (agent.composite_score or 0) < median:
                continue

            # Must have positive P&L (or attributed)
            pnl = (agent.realized_pnl or 0) + (agent.unrealized_pnl or 0)
            if pnl <= 0:
                continue

            # Check cooldown (handle both naive and aware datetimes)
            if agent.reproduction_cooldown_until:
                cooldown = agent.reproduction_cooldown_until
                if cooldown.tzinfo is None:
                    cooldown = cooldown.replace(tzinfo=timezone.utc)
                if cooldown > now:
                    continue

            # Check treasury availability
            min_capital = 50.0 if agent.type == "operator" else config.min_spawn_capital
            state = session.execute(select(SystemState)).scalars().first()
            if state:
                available = state.total_treasury * (1 - config.treasury_reserve_ratio)
                if available < min_capital:
                    continue

            candidates.append(agent)

        return sorted(candidates, key=lambda a: a.composite_score or 0, reverse=True)

    def _get_role_medians(self, session: Session) -> dict[str, float]:
        """Get median composite score per role."""
        medians: dict[str, float] = {}
        for role in ["scout", "strategist", "critic", "operator"]:
            scores = session.execute(
                select(Agent.composite_score).where(
                    Agent.type == role,
                    Agent.status == "active",
                )
            ).scalars().all()
            if scores:
                sorted_scores = sorted(s for s in scores if s is not None)
                if sorted_scores:
                    mid = len(sorted_scores) // 2
                    medians[role] = sorted_scores[mid]
        return medians

    async def _check_concentration(
        self, session: Session, parent: Agent,
    ) -> tuple[bool, float, str | None]:
        """Check dynasty concentration. Returns (blocked, concentration, warning)."""
        if not parent.dynasty_id:
            return False, 0.0, None

        concentration = await self.dynasty_mgr.get_dynasty_concentration(
            session, parent.dynasty_id,
        )

        if concentration > config.dynasty_concentration_hard_limit:
            return True, concentration, None

        warning = None
        if concentration > config.dynasty_concentration_warning:
            from src.common.models import Dynasty
            dynasty = session.get(Dynasty, parent.dynasty_id)
            d_name = dynasty.dynasty_name if dynasty else "Unknown"
            warning = (
                f"Warning: {d_name} is {concentration:.0%} of ecosystem. "
                f"Consider whether more offspring from this lineage adds diversity."
            )

        return False, concentration, warning

    async def _genesis_reproduction_decision(
        self,
        session: Session,
        parent: Agent,
        concentration: float,
        concentration_warning: str | None,
        active_count: int,
        available_slots: int,
    ) -> ReproductionDecision:
        """Ask Genesis via Claude API whether reproduction should proceed."""
        from src.common.models import Dynasty

        dynasty = session.get(Dynasty, parent.dynasty_id) if parent.dynasty_id else None
        d_name = dynasty.dynasty_name if dynasty else "No Dynasty"
        d_gens = dynasty.total_generations if dynasty else 1
        d_living = dynasty.living_members if dynasty else 1

        # Role distribution
        role_counts = {}
        for role in ["scout", "strategist", "critic", "operator"]:
            count = session.execute(
                select(func.count()).select_from(Agent).where(
                    Agent.type == role, Agent.status == "active",
                )
            ).scalar() or 0
            role_counts[role] = count
        role_dist = ", ".join(f"{r}: {c}" for r, c in role_counts.items())

        population_note = ""
        if available_slots <= 0:
            # Find lowest performer
            lowest = session.execute(
                select(Agent).where(Agent.status == "active")
                .order_by(Agent.composite_score.asc())
                .limit(1)
            ).scalar_one_or_none()
            if lowest:
                population_note = (
                    f"The ecosystem is at capacity ({active_count}/{config.max_agents}). "
                    f"Lowest composite: {lowest.name} ({lowest.composite_score:.3f}). "
                    f"Would the offspring likely outperform this agent?"
                )
        elif available_slots <= 2:
            population_note = "Near capacity. Only highest-priority reproductions."

        prompt = f"""PARENT: {parent.name} ({parent.type}, Gen {parent.generation}, {parent.prestige_title})
Composite: {parent.composite_score:.3f}, evaluated {parent.evaluation_count} times

Parent temperature: {parent.api_temperature}
True P&L: ${(parent.realized_pnl or 0) + (parent.unrealized_pnl or 0):.2f}

Dynasty: {d_name}, {d_living} living, {d_gens} generation(s)
Dynasty concentration: {concentration:.0%} of ecosystem
{concentration_warning or ''}

Ecosystem state:
- {active_count} agents ({role_dist})
- Available slots: {available_slots} of {config.max_agents}
{population_note}
- Market regime: {self._get_regime(session)}

Respond in JSON:
{{
    "should_reproduce": true or false,
    "reasoning": "why or why not (under 150 words)",
    "offspring_name": "suggested name",
    "mutations": {{
        "watchlist_changes": {{"add": [...], "remove": [...]}},
        "temperature_adjustment": null or float,
        "founding_directive": "a QUESTION for the offspring to explore"
    }},
    "proactive_termination": null or {{"agent_id": int, "reasoning": "why"}}
}}"""

        try:
            client = anthropic.Anthropic(api_key=config.anthropic_api_key)
            response = client.messages.create(
                model=config.model_sonnet,
                max_tokens=800,
                system="You are Genesis, the immortal overseer of an AI trading ecosystem. Decide whether reproduction benefits the ecosystem and suggest mutations.",
                messages=[{"role": "user", "content": prompt}],
            )

            text = response.content[0].text
            json_match = re.search(r'\{.*\}', text, re.DOTALL)
            if json_match:
                data = json.loads(json_match.group())
            else:
                return ReproductionDecision(
                    should_reproduce=False,
                    reasoning="Failed to parse Genesis response",
                )

            return ReproductionDecision(
                should_reproduce=data.get("should_reproduce", False),
                reasoning=data.get("reasoning", ""),
                offspring_name=data.get("offspring_name", f"{parent.name}-II"),
                mutations=data.get("mutations", {}),
                proactive_termination=data.get("proactive_termination"),
            )

        except Exception as e:
            self.log.error(f"Reproduction decision API failed: {e}")
            # Fallback: approve reproduction with simple mutations
            return ReproductionDecision(
                should_reproduce=True,
                reasoning=f"API fallback — auto-approved for {parent.prestige_title} parent",
                offspring_name=f"{parent.name}-II",
                mutations={
                    "watchlist_changes": {"add": [], "remove": []},
                    "temperature_adjustment": None,
                    "founding_directive": "What opportunities has your parent been missing?",
                },
            )

    async def _build_offspring(
        self,
        session: Session,
        parent: Agent,
        decision: ReproductionDecision,
        posthumous: bool = False,
        agora_service=None,
    ) -> Agent:
        """Build and initialize the offspring agent."""
        now = datetime.now(timezone.utc)

        # Temperature mutation
        parent_temp = parent.api_temperature or 0.5
        temp_adj = decision.mutations.get("temperature_adjustment")
        if temp_adj is not None:
            temp = parent_temp + temp_adj
        else:
            temp = parent_temp + random.uniform(
                -config.temperature_mutation_range,
                config.temperature_mutation_range,
            )
        # Clamp to role bounds
        bounds = self._get_temp_bounds(parent.type)
        temp = max(bounds[0], min(bounds[1], temp))

        # Apply watchlist mutations
        watchlist = list(parent.watched_markets or [])
        wl_changes = decision.mutations.get("watchlist_changes", {})
        for market in wl_changes.get("add", []):
            if market not in watchlist:
                watchlist.append(market)
        for market in wl_changes.get("remove", []):
            if market in watchlist:
                watchlist.remove(market)

        # Allocate capital
        min_capital = 50.0 if parent.type == "operator" else config.min_spawn_capital
        state = session.execute(select(SystemState)).scalars().first()
        capital = min_capital
        if state:
            available = state.total_treasury * (1 - config.treasury_reserve_ratio)
            capital = min(min_capital, available)

        founding_directive = decision.mutations.get("founding_directive")

        # Create offspring
        offspring = Agent(
            name=decision.offspring_name,
            type=parent.type,
            status="active",
            generation=parent.generation + 1,
            parent_id=parent.id,
            dynasty_id=parent.dynasty_id,
            capital_allocated=capital,
            capital_current=capital,
            cash_balance=capital,
            total_equity=capital,
            thinking_budget_daily=parent.thinking_budget_daily,
            api_temperature=round(temp, 3),
            survival_clock_start=now,
            survival_clock_end=now + timedelta(days=config.offspring_survival_clock_days),
            watched_markets=watchlist,
            initial_watchlist=watchlist,
            founding_directive=founding_directive,
            founding_directive_consumed=False,
            posthumous_birth=posthumous,
        )
        session.add(offspring)
        session.flush()

        # Deduct capital from treasury
        if state:
            state.total_treasury -= capital
            session.add(state)

        # Transfer long-term memory with inheritance discount + age decay
        inherited_count = await self._transfer_memories(session, parent.id, offspring.id)

        # Transfer trust relationships at 50% strength
        await self._transfer_relationships(session, parent.id, offspring.id)

        # Create lineage record
        await self.lineage_mgr.create_lineage_record(
            session, offspring, parent=parent,
            mutations=decision.mutations,
            founding_directive=founding_directive,
        )

        # Update dynasty stats
        await self.dynasty_mgr.record_birth(session, parent, offspring)

        # Update parent
        if parent.status == "active":
            parent.last_reproduction_at = now
            parent.offspring_count = (parent.offspring_count or 0) + 1
            parent.reproduction_cooldown_until = now + timedelta(
                days=config.reproduction_cooldown_evals * 14,
            )
            session.add(parent)

        # Agora announcement
        if agora_service:
            try:
                from src.common.models import Dynasty
                dynasty = session.get(Dynasty, parent.dynasty_id) if parent.dynasty_id else None
                d_name = dynasty.dynasty_name if dynasty else "No Dynasty"

                announcement = {
                    "event": "REPRODUCTION",
                    "parent": parent.name,
                    "offspring": offspring.name,
                    "generation": offspring.generation,
                    "dynasty": d_name,
                    "mutations": decision.mutations,
                    "inherited_memories": inherited_count,
                    "inherited_temperature": round(temp, 3),
                    "posthumous": posthumous,
                }
                await agora_service.post_message(
                    agent_id=0, agent_name="Genesis",
                    channel="genesis-log",
                    content=json.dumps(announcement),
                    message_type="system",
                    importance=4,
                )
            except Exception as e:
                self.log.debug(f"Agora birth announcement failed: {e}")

        self.log.info(
            f"Offspring {offspring.name} (Gen {offspring.generation}) born from "
            f"{parent.name}{' (posthumous)' if posthumous else ''}"
        )

        return offspring

    async def _transfer_memories(
        self, session: Session, parent_id: int, offspring_id: int,
    ) -> int:
        """Copy parent's long-term memories with inheritance discount and age decay."""
        now = datetime.now(timezone.utc)

        parent_memories = session.execute(
            select(AgentLongTermMemory).where(
                AgentLongTermMemory.agent_id == parent_id,
                AgentLongTermMemory.is_active == True,
            )
        ).scalars().all()

        count = 0
        for mem in parent_memories:
            confidence = mem.confidence * config.memory_inheritance_discount

            # Age decay for memories older than threshold
            if mem.created_at:
                age_days = (now - mem.created_at).total_seconds() / 86400
                if age_days > config.memory_age_decay_start_days:
                    decay = config.memory_age_decay_factor ** (
                        age_days - config.memory_age_decay_start_days
                    )
                    confidence *= decay

            confidence = max(confidence, config.memory_confidence_floor)

            # Determine source label
            source = "grandparent" if mem.source in ("parent", "grandparent") else "parent"

            offspring_mem = AgentLongTermMemory(
                agent_id=offspring_id,
                memory_type=mem.memory_type,
                content=mem.content,
                confidence=confidence,
                source=source,
                source_cycle=mem.source_cycle,
                times_confirmed=0,
                times_contradicted=0,
                is_active=True,
            )
            session.add(offspring_mem)
            count += 1

        return count

    async def _transfer_relationships(
        self, session: Session, parent_id: int, offspring_id: int,
    ) -> int:
        """Copy parent's trust relationships at 50% strength."""
        parent_rels = session.execute(
            select(AgentRelationship).where(
                AgentRelationship.agent_id == parent_id,
                AgentRelationship.archived == False,
            )
        ).scalars().all()

        count = 0
        factor = config.trust_inheritance_factor
        for rel in parent_rels:
            # 50% blend with neutral prior
            inherited_trust = rel.trust_score * factor + 0.5 * (1 - factor)

            offspring_rel = AgentRelationship(
                agent_id=offspring_id,
                target_agent_id=rel.target_agent_id,
                target_agent_name=rel.target_agent_name,
                trust_score=inherited_trust,
                interaction_count=0,
                positive_outcomes=0,
                negative_outcomes=0,
                last_assessment=f"Inherited from parent at {inherited_trust:.2f} trust",
            )
            session.add(offspring_rel)
            count += 1

        return count

    def _get_temp_bounds(self, role: str) -> tuple[float, float]:
        """Get temperature bounds for a role."""
        bounds_map = {
            "scout": config.temperature_bounds_scout,
            "strategist": config.temperature_bounds_strategist,
            "critic": config.temperature_bounds_critic,
            "operator": config.temperature_bounds_operator,
        }
        bounds = bounds_map.get(role, [0.1, 0.9])
        return (bounds[0], bounds[1])

    def _get_regime(self, session: Session) -> str:
        """Get current market regime."""
        state = session.execute(select(SystemState)).scalars().first()
        return state.current_regime if state else "unknown"
