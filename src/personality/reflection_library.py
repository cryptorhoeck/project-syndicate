"""
Project Syndicate — Reflection Library Selector (Phase 3E)

Targeted "study sessions" during reflection cycles.
System offers relevant material when it detects weakness — agents don't choose.
"""

__version__ = "1.2.0"

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select, func
from sqlalchemy.orm import Session

from src.common.config import config
from src.common.models import Agent, LibraryEntry, StudyHistory

logger = logging.getLogger(__name__)


@dataclass
class ReflectionLibraryContent:
    """Content selected for injection during reflection cycle."""
    resource_type: str  # textbook_summary, post_mortem, strategy_record, pattern
    resource_id: str
    title: str
    content: str
    context_prompt: str  # Why this was selected
    weakest_metric: str


# Mapping: role → {metric → summary filename in data/library/summaries/}
WEAKNESS_TO_RESOURCE: dict[str, dict[str, str]] = {
    "scout": {
        "signal_quality": "technical_analysis.md",
        "intel_conversion": "strategy_categories.md",
        "thinking_efficiency": "thinking_efficiently.md",
    },
    "strategist": {
        "plan_approval_rate": "risk_management.md",
        "revision_rate": "strategy_categories.md",
        "thinking_efficiency": "thinking_efficiently.md",
    },
    "critic": {
        "approval_accuracy": "risk_management.md",
        "rejection_value": "strategy_categories.md",
        "thinking_efficiency": "thinking_efficiently.md",
    },
    "operator": {
        "sharpe": "risk_management.md",
        "true_pnl": "market_mechanics.md",
        "thinking_efficiency": "thinking_efficiently.md",
    },
}


class ReflectionLibrarySelector:
    """Selects Library content for reflection cycle study sessions."""

    def __init__(self) -> None:
        self.log = logger

    def select_for_reflection(
        self,
        session: Session,
        agent: Agent,
    ) -> ReflectionLibraryContent | None:
        """Select relevant Library content based on agent weakness.

        Synchronous — does only DB queries and file I/O, no need for async.
        """
        # 1. Get weakest metric from last evaluation scorecard
        scorecard = agent.evaluation_scorecard
        if not scorecard:
            return None

        metrics = scorecard.get("metrics", {})
        if not metrics:
            return None

        # Find weakest metric
        weakest_metric = None
        weakest_value = float("inf")
        for name, data in metrics.items():
            if isinstance(data, dict):
                raw = data.get("raw", data.get("normalized"))
            else:
                raw = data
            if raw is not None and raw < weakest_value:
                weakest_value = raw
                weakest_metric = name

        if not weakest_metric:
            return None

        # 2. Look up relevant resource
        role = agent.type
        role_resources = WEAKNESS_TO_RESOURCE.get(role, {})
        resource_file = role_resources.get(weakest_metric)

        if resource_file:
            # 3. Check study cooldown
            if not self._on_cooldown(session, agent.id, resource_file, agent.cycle_count):
                content = self._load_textbook_summary(resource_file)
                if content:
                    self._record_study(session, agent.id, "textbook_summary", resource_file, agent.cycle_count)
                    return ReflectionLibraryContent(
                        resource_type="textbook_summary",
                        resource_id=resource_file,
                        title=resource_file.replace(".md", "").replace("_", " ").title(),
                        content=content,
                        context_prompt=(
                            f"Library reading is available below. It was selected because your "
                            f"last evaluation identified {weakest_metric} as an area for growth. "
                            f"Studying costs nothing extra — it's part of this cycle."
                        ),
                        weakest_metric=weakest_metric,
                    )

        # 4. Fallback: look for Library archive entries
        fallback = self._find_archive_fallback(session, agent, weakest_metric)
        if fallback:
            return fallback

        return None

    def _on_cooldown(
        self,
        session: Session,
        agent_id: int,
        resource_id: str,
        current_cycle: int,
    ) -> bool:
        """Check if agent has studied this resource within cooldown period."""
        cooldown = config.reflection_library_cooldown
        # Cooldown is in reflections (every 10 cycles)
        cooldown_cycles = cooldown * config.reflection_every_n_cycles

        recent = session.execute(
            select(StudyHistory).where(
                StudyHistory.agent_id == agent_id,
                StudyHistory.resource_id == resource_id,
                StudyHistory.studied_at_cycle >= current_cycle - cooldown_cycles,
            )
        ).scalar_one_or_none()

        return recent is not None

    def _load_textbook_summary(self, filename: str) -> str | None:
        """Load textbook summary from data/library/summaries/.

        Falls back to the full textbook in data/library/textbooks/ (truncated)
        if no summary exists, matching by keyword in the filename stem.
        """
        import os
        from pathlib import Path

        summary_path = os.path.join("data", "library", "summaries", filename)
        try:
            with open(summary_path, encoding="utf-8") as f:
                return f.read()
        except FileNotFoundError:
            pass

        # Fallback: search textbooks/ for a file whose stem contains the summary keyword
        keyword = Path(filename).stem.lower()  # e.g. "technical_analysis"
        textbooks_dir = Path("data", "library", "textbooks")
        if textbooks_dir.exists():
            for tb in sorted(textbooks_dir.glob("*.md")):
                if keyword in tb.stem.lower():
                    content = tb.read_text(encoding="utf-8")
                    return content[:2000]  # Truncate for token budget

        self.log.warning("textbook_not_found", extra={"filename": filename})
        return None

    def _find_archive_fallback(
        self,
        session: Session,
        agent: Agent,
        weakest_metric: str,
    ) -> ReflectionLibraryContent | None:
        """Find relevant Library archive entry as fallback."""
        # Search for post-mortems or strategy records mentioning the weak area
        entries = session.execute(
            select(LibraryEntry).where(
                LibraryEntry.is_published == True,
                LibraryEntry.category.in_(["post_mortem", "strategy_record"]),
            ).order_by(LibraryEntry.created_at.desc()).limit(20)
        ).scalars().all()

        for entry in entries:
            resource_id = str(entry.id)
            if self._on_cooldown(session, agent.id, resource_id, agent.cycle_count):
                continue

            # Simple relevance check: does the entry mention the weak metric area?
            content_lower = (entry.content or "").lower()
            if weakest_metric.replace("_", " ") in content_lower:
                self._record_study(
                    session, agent.id,
                    entry.category, resource_id, agent.cycle_count,
                )
                summary = entry.summary or entry.content[:500]
                return ReflectionLibraryContent(
                    resource_type=entry.category,
                    resource_id=resource_id,
                    title=entry.title,
                    content=summary,
                    context_prompt=(
                        f"Library reading is available below. This {entry.category.replace('_', ' ')} "
                        f"was selected because your last evaluation identified {weakest_metric} "
                        f"as an area for growth."
                    ),
                    weakest_metric=weakest_metric,
                )

        return None

    def _record_study(
        self,
        session: Session,
        agent_id: int,
        resource_type: str,
        resource_id: str,
        cycle_number: int,
    ) -> None:
        """Record that agent studied a resource."""
        record = StudyHistory(
            agent_id=agent_id,
            resource_type=resource_type,
            resource_id=resource_id,
            studied_at_cycle=cycle_number,
        )
        session.add(record)
