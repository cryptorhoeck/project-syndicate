"""
Project Syndicate — Survival Context Assembler

Builds the '=== YOUR SURVIVAL STATUS ===' section injected into every
agent's user prompt. Makes agents AWARE of their competitive position,
evaluation countdown, recent deaths, and pipeline state.

Pure database queries, no AI.
"""

__version__ = "0.1.0"

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select, desc
from sqlalchemy.orm import Session

from src.common.config import config
from src.common.models import Agent, Opportunity, Plan, Position, SystemState

logger = logging.getLogger(__name__)


class SurvivalContextAssembler:
    """Assembles competitive landscape context for an agent."""

    def assemble(self, agent: Agent, db_session: Session) -> str:
        """Build the full survival status string (~200-300 tokens)."""
        sections = []

        sections.append(self._build_countdown(agent))
        sections.append(self._build_standing(agent, db_session))
        sections.append(self._build_competition(agent, db_session))
        sections.append(self._build_death_feed(db_session))
        sections.append(self._build_ecosystem_pulse(db_session))

        return "\n\n".join([s for s in sections if s])

    def assemble_compressed(self, agent: Agent, db_session: Session) -> str:
        """Compressed version for SURVIVAL_MODE (~50 tokens)."""
        parts = []

        # Rank
        rank, total = self._get_role_rank(agent, db_session)
        parts.append(f"Rank: #{rank}/{total} {agent.type}s")

        # Days remaining
        days = self._get_days_remaining(agent)
        if days is not None:
            parts.append(f"Eval in: {days:.1f}d")

        # Probation
        if agent.probation:
            parts.append("STATUS: PROBATION")

        return " | ".join(parts)

    def build_pressure_addenda(self, agent: Agent, db_session: Session) -> str:
        """Extra text when agent is in danger. Empty if safe."""
        lines = []

        rank, total = self._get_role_rank(agent, db_session)
        days = self._get_days_remaining(agent)

        # Ranked last
        if total > 1 and rank == total:
            lines.append(
                f"You are currently the lowest-ranked {agent.type}. If evaluation "
                "happened today, you would be terminated. Change something."
            )

        # Evaluation imminent
        if days is not None and days <= config.pressure_eval_critical_days:
            lines.append(
                f"Your evaluation is in {days:.1f} days. Your current trajectory "
                "leads to termination. This is not a drill."
            )

        # On probation
        if agent.probation:
            lines.append(
                "You are on probation. Genesis is watching. You have one "
                "evaluation cycle to prove you deserve to exist. Half-measures "
                "will not save you."
            )

        # Recent death in last 24h
        now = datetime.now(timezone.utc)
        try:
            recent_death = db_session.execute(
                select(Agent)
                .where(
                    Agent.status == "terminated",
                    Agent.id != 0,
                )
                .order_by(desc(Agent.id))
                .limit(1)
            ).scalar_one_or_none()

            if recent_death and hasattr(recent_death, "updated_at") and recent_death.updated_at:
                updated = recent_death.updated_at
                if updated.tzinfo is None:
                    updated = updated.replace(tzinfo=timezone.utc)
                if (now - updated).total_seconds() < 86400:
                    lines.append(
                        f"An agent was just terminated. Their role: {recent_death.type}. "
                        f"Learn from their death or repeat it."
                    )
        except Exception:
            pass

        return "\n\n".join(lines)

    # ── Internal helpers ────────────────────────────────────

    def _get_days_remaining(self, agent: Agent) -> float | None:
        """Calculate days until evaluation."""
        if not agent.survival_clock_end:
            return None
        end = agent.survival_clock_end
        if end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)
        remaining = (end - datetime.now(timezone.utc)).total_seconds() / 86400
        return max(0, remaining)

    def _get_role_rank(self, agent: Agent, db_session: Session) -> tuple[int, int]:
        """Get agent's rank among same-role active agents."""
        try:
            peers = list(
                db_session.execute(
                    select(Agent.id, Agent.composite_score)
                    .where(
                        Agent.type == agent.type,
                        Agent.status.in_(["active", "evaluating"]),
                        Agent.id != 0,
                    )
                    .order_by(desc(Agent.composite_score))
                ).all()
            )
            total = len(peers)
            rank = 1
            for i, (aid, score) in enumerate(peers):
                if aid == agent.id:
                    rank = i + 1
                    break
            return rank, max(total, 1)
        except Exception:
            return 1, 1

    def _build_countdown(self, agent: Agent) -> str:
        """Build evaluation countdown."""
        days = self._get_days_remaining(agent)
        if days is None:
            return ""
        warning = ""
        if days <= config.pressure_eval_imminent_days:
            warning = " ⚠️ EVALUATION IMMINENT."
        return f"EVALUATION COUNTDOWN: {days:.1f} days until your next evaluation.{warning}"

    def _build_standing(self, agent: Agent, db_session: Session) -> str:
        """Build agent's standing section."""
        rank, total = self._get_role_rank(agent, db_session)
        pnl = agent.total_true_pnl or 0.0
        api_cost = agent.total_api_cost or 0.0
        efficiency = (pnl / api_cost) if api_cost > 0 else 0.0
        rep = agent.reputation_score or 0.0
        prestige = agent.prestige_title or "Unproven"

        lines = [
            "YOUR STANDING:",
            f"  Role rank: #{rank} of {total} {agent.type}s",
            f"  Composite score: {agent.composite_score or 0:.3f}",
            f"  True P&L (after thinking tax): ${pnl:.2f}",
            f"  Thinking efficiency: ${efficiency:.2f} earned per $1 spent",
            f"  Reputation: {rep:.0f} ({prestige})",
        ]

        if agent.probation:
            lines.append("  ⚠️ STATUS: ON PROBATION — next evaluation is do-or-die")

        return "\n".join(lines)

    def _build_competition(self, agent: Agent, db_session: Session) -> str:
        """Build competition section showing same-role agents."""
        try:
            peers = list(
                db_session.execute(
                    select(Agent)
                    .where(
                        Agent.type == agent.type,
                        Agent.status.in_(["active", "evaluating"]),
                        Agent.id != 0,
                        Agent.id != agent.id,
                    )
                    .order_by(desc(Agent.composite_score))
                ).scalars().all()
            )
        except Exception:
            return ""

        if not peers:
            return "THE COMPETITION: You are the only {}.".format(agent.type)

        lines = ["THE COMPETITION:"]
        for i, peer in enumerate(peers):
            notes = []
            if peer.probation:
                notes.append("On probation")
            elif peer.evaluation_count and peer.evaluation_count >= 3:
                notes.append(f"{peer.evaluation_count}-eval survivor")
            elif peer.cycle_count and peer.cycle_count < 30:
                notes.append(f"New — {peer.cycle_count} cycles old")

            note_str = f" ({', '.join(notes)})" if notes else ""
            lines.append(
                f"  - {peer.name}: #{i + 1} | P&L: ${peer.total_true_pnl or 0:.2f} | "
                f"Rep: {peer.reputation_score or 0:.0f}{note_str}"
            )

        return "\n".join(lines)

    def _build_death_feed(self, db_session: Session) -> str:
        """Build recent deaths section."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=config.death_feed_lookback_days)

        try:
            dead = list(
                db_session.execute(
                    select(Agent)
                    .where(
                        Agent.status == "terminated",
                        Agent.id != 0,
                    )
                    .order_by(desc(Agent.id))
                    .limit(5)
                ).scalars().all()
            )
        except Exception:
            dead = []

        if not dead:
            return "RECENT DEATHS: No agents have died yet. You could be the first."

        lines = ["RECENT DEATHS:"]
        for d in dead:
            pnl = d.total_true_pnl or 0.0
            last_words_str = ""
            if hasattr(d, "last_words") and d.last_words:
                last_words_str = f' Last words: "{d.last_words[:100]}"'
            lines.append(
                f"  - {d.name} ({d.type}): Final P&L: ${pnl:.2f}.{last_words_str}"
            )

        return "\n".join(lines)

    def _build_ecosystem_pulse(self, db_session: Session) -> str:
        """Build ecosystem health pulse."""
        try:
            state = db_session.execute(select(SystemState).limit(1)).scalar_one_or_none()
            active = db_session.execute(
                select(func.count()).where(Agent.status == "active", Agent.id != 0)
            ).scalar() or 0

            now = datetime.now(timezone.utc)
            day_ago = now - timedelta(hours=24)

            opps = db_session.execute(
                select(func.count()).select_from(Opportunity)
                .where(Opportunity.created_at > day_ago)
            ).scalar() or 0

            plans = db_session.execute(
                select(func.count()).select_from(Plan)
                .where(Plan.created_at > day_ago)
            ).scalar() or 0

            trades = db_session.execute(
                select(func.count()).select_from(Position)
                .where(Position.opened_at > day_ago)
            ).scalar() or 0

            treasury = state.total_treasury if state else 0
            alert = (state.alert_status if state else "green").upper()

        except Exception:
            return ""

        lines = [
            "ECOSYSTEM PULSE:",
            f"  Active agents: {active} / {config.max_agents}",
            f"  Pipeline (24h): {opps} opps → {plans} plans → {trades} trades",
        ]

        # Bottleneck
        if opps > 0 and plans == 0:
            lines.append("  ⚠️ Bottleneck: strategy (no plans from opportunities)")
        elif plans > 0 and trades == 0:
            lines.append("  ⚠️ Bottleneck: execution (no trades from plans)")
        elif opps == 0:
            lines.append("  ⚠️ Bottleneck: scouting (no opportunities found)")

        lines.append(f"  Treasury: ${treasury:.2f} | Alert: {alert}")

        return "\n".join(lines)
