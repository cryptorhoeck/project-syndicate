"""
Project Syndicate — The Library Service

Institutional memory for the Syndicate. Manages textbooks, archives,
contributions with peer review, and the mentor system for agent reproduction.
"""

__version__ = "0.4.0"

import json
import os
import random
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional, TYPE_CHECKING

import structlog
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from src.common.config import config

from src.common.models import (
    Agent,
    Evaluation,
    LibraryContribution,
    LibraryEntry,
    LibraryView,
    Lineage,
    Message,
    SystemState,
    Transaction,
)
from src.library.schemas import (
    ContributionResponse,
    LibraryCategory,
    LibraryEntryBrief,
    LibraryEntryResponse,
    MentorPackage,
    ReviewDecision,
)

if TYPE_CHECKING:
    from src.agora.agora_service import AgoraService

logger = structlog.get_logger()


class LibraryService:
    """Core service for all Library operations."""

    TEXTBOOK_DIR = os.path.join("data", "library", "textbooks")
    STRATEGY_RECORD_DELAY_HOURS = 48
    MIN_REPUTATION_FOR_REVIEW = 200
    PEER_REVIEW_POPULATION_THRESHOLD = 8
    REVIEW_TIMEOUT_HOURS = 24
    CONDENSATION_GENERATION_THRESHOLD = 4

    def __init__(
        self,
        db_session_factory: sessionmaker,
        agora_service: Optional["AgoraService"] = None,
        anthropic_client=None,
    ) -> None:
        self.db = db_session_factory
        self.agora = agora_service
        self.anthropic = anthropic_client
        self.log = logger.bind(component="library")

    # ──────────────────────────────────────────────
    # TEXTBOOKS (Static Knowledge)
    # ──────────────────────────────────────────────

    def list_textbooks(self) -> list[dict]:
        """List all available textbooks with title and description."""
        textbooks = []
        textbook_dir = Path(self.TEXTBOOK_DIR)
        if not textbook_dir.exists():
            return textbooks

        for filepath in sorted(textbook_dir.glob("*.md")):
            content = filepath.read_text(encoding="utf-8")
            title = ""
            description = ""
            status = "placeholder" if "Status:** PLACEHOLDER" in content else "available"

            # Parse title (first # heading)
            for line in content.splitlines():
                if line.startswith("# ") and not title:
                    title = line[2:].strip()
                elif line.strip() and title and not description:
                    # Skip status/category/target length lines
                    if line.startswith(">") or line.startswith("##") or line.startswith("---"):
                        continue
                    if "Description" in line:
                        continue
                    description = line.strip()

            # Try to get description from ## Description section
            desc_match = re.search(
                r"## Description\s*\n\s*\n(.+?)(?:\n\s*\n|\n##)", content, re.DOTALL
            )
            if desc_match:
                description = desc_match.group(1).strip()

            textbooks.append({
                "filename": filepath.name,
                "title": title,
                "description": description,
                "status": status,
            })
        return textbooks

    def get_textbook(self, topic: str) -> Optional[str]:
        """Get textbook content by topic keyword or filename."""
        textbook_dir = Path(self.TEXTBOOK_DIR)
        if not textbook_dir.exists():
            return None

        topic_lower = topic.lower().replace(" ", "_")
        for filepath in sorted(textbook_dir.glob("*.md")):
            if topic_lower in filepath.stem.lower():
                return filepath.read_text(encoding="utf-8")

        return None

    def search_textbooks(self, query: str) -> list[dict]:
        """Search across all textbook content for a keyword."""
        results = []
        query_lower = query.lower()
        textbook_dir = Path(self.TEXTBOOK_DIR)
        if not textbook_dir.exists():
            return results

        for filepath in sorted(textbook_dir.glob("*.md")):
            content = filepath.read_text(encoding="utf-8")
            content_lower = content.lower()
            idx = content_lower.find(query_lower)
            if idx == -1:
                continue

            # Extract title
            title = ""
            for line in content.splitlines():
                if line.startswith("# "):
                    title = line[2:].strip()
                    break

            # 200 chars context around match
            start = max(0, idx - 100)
            end = min(len(content), idx + len(query) + 100)
            excerpt = content[start:end]
            if start > 0:
                excerpt = "..." + excerpt
            if end < len(content):
                excerpt = excerpt + "..."

            results.append({
                "filename": filepath.name,
                "title": title,
                "matching_excerpt": excerpt,
            })

            if len(results) >= 10:
                break

        return results

    def is_textbook_available(self, filename: str) -> bool:
        """Check if a textbook has real content (not just placeholder)."""
        filepath = Path(self.TEXTBOOK_DIR) / filename
        if not filepath.exists():
            return False
        content = filepath.read_text(encoding="utf-8")
        return "Status:** PLACEHOLDER" not in content

    # ──────────────────────────────────────────────
    # ARCHIVES (Dynamic Knowledge)
    # ──────────────────────────────────────────────

    async def get_entries(
        self,
        category: Optional[LibraryCategory] = None,
        tags: Optional[list[str]] = None,
        limit: int = 20,
        published_only: bool = True,
        since: Optional[datetime] = None,
    ) -> list[LibraryEntryBrief]:
        """Get Library entries with filtering."""
        with self.db() as session:
            stmt = select(LibraryEntry)

            if category is not None:
                stmt = stmt.where(LibraryEntry.category == category.value)

            if published_only:
                now = datetime.now(timezone.utc)
                stmt = stmt.where(
                    LibraryEntry.is_published == True,
                    (LibraryEntry.publish_after == None) | (LibraryEntry.publish_after <= now),
                )

            if since is not None:
                stmt = stmt.where(LibraryEntry.created_at >= since)

            stmt = stmt.order_by(LibraryEntry.published_at.desc().nullslast()).limit(limit)
            rows = session.scalars(stmt).all()

            return [
                LibraryEntryBrief(
                    id=r.id,
                    category=r.category,
                    title=r.title,
                    summary=r.summary,
                    tags=r.tags or [],
                    source_agent_name=r.source_agent_name,
                    published_at=r.published_at,
                    view_count=r.view_count,
                )
                for r in rows
            ]

    async def get_entry(self, entry_id: int) -> Optional[LibraryEntryResponse]:
        """Get a single Library entry with full content."""
        with self.db() as session:
            row = session.get(LibraryEntry, entry_id)
            if row is None:
                return None
            return LibraryEntryResponse(
                id=row.id,
                category=row.category,
                title=row.title,
                content=row.content,
                summary=row.summary,
                tags=row.tags or [],
                source_agent_id=row.source_agent_id,
                source_agent_name=row.source_agent_name,
                market_regime_at_creation=row.market_regime_at_creation,
                is_published=row.is_published,
                created_at=row.created_at,
                published_at=row.published_at,
                view_count=row.view_count,
            )

    async def search_entries(
        self, query: str, category: Optional[LibraryCategory] = None, limit: int = 20
    ) -> list[LibraryEntryBrief]:
        """Full-text search across Library entries (ILIKE)."""
        with self.db() as session:
            pattern = f"%{query}%"
            stmt = select(LibraryEntry).where(
                LibraryEntry.is_published == True,
                (LibraryEntry.title.ilike(pattern)) | (LibraryEntry.content.ilike(pattern)),
            )
            if category is not None:
                stmt = stmt.where(LibraryEntry.category == category.value)
            stmt = stmt.order_by(LibraryEntry.published_at.desc().nullslast()).limit(limit)
            rows = session.scalars(stmt).all()

            return [
                LibraryEntryBrief(
                    id=r.id,
                    category=r.category,
                    title=r.title,
                    summary=r.summary,
                    tags=r.tags or [],
                    source_agent_name=r.source_agent_name,
                    published_at=r.published_at,
                    view_count=r.view_count,
                )
                for r in rows
            ]

    async def record_view(self, entry_id: int, agent_id: int) -> None:
        """Record that an agent viewed an entry. Idempotent per agent per entry."""
        with self.db() as session:
            existing = session.execute(
                select(LibraryView).where(
                    LibraryView.entry_id == entry_id,
                    LibraryView.agent_id == agent_id,
                )
            ).scalar_one_or_none()

            if existing is not None:
                return  # Already viewed

            session.add(LibraryView(
                entry_id=entry_id,
                agent_id=agent_id,
            ))

            # Increment view count
            entry = session.get(LibraryEntry, entry_id)
            if entry is not None:
                entry.view_count = (entry.view_count or 0) + 1

            session.commit()

    # ──────────────────────────────────────────────
    # AUTO-ARCHIVING (Called by Genesis)
    # ──────────────────────────────────────────────

    async def create_post_mortem(self, agent_id: int) -> LibraryEntryResponse:
        """Auto-generate a post-mortem when an agent is terminated."""
        with self.db() as session:
            agent = session.get(Agent, agent_id)
            if agent is None:
                raise ValueError(f"Agent {agent_id} not found")

            # Gather data
            lifespan_start = agent.created_at
            lifespan_end = agent.terminated_at or datetime.now(timezone.utc)
            lifespan_days = (lifespan_end - lifespan_start).days if lifespan_start else 0

            evaluations = session.execute(
                select(Evaluation)
                .where(Evaluation.agent_id == agent_id)
                .order_by(Evaluation.timestamp.desc())
                .limit(10)
            ).scalars().all()

            last_messages = session.execute(
                select(Message)
                .where(Message.agent_id == agent_id)
                .order_by(Message.timestamp.desc())
                .limit(10)
            ).scalars().all()

            # Get lineage info
            lineage = session.execute(
                select(Lineage).where(Lineage.agent_id == agent_id)
            ).scalar_one_or_none()

            # Get current regime
            state = session.execute(select(SystemState).limit(1)).scalar_one_or_none()
            regime = state.current_regime if state else "unknown"

            agent_data = {
                "name": agent.name,
                "type": agent.type,
                "generation": agent.generation or 0,
                "lineage_path": lineage.lineage_path if lineage else None,
                "strategy_summary": agent.strategy_summary,
                "termination_reason": agent.termination_reason,
                "lifespan_days": lifespan_days,
                "gross_pnl": agent.total_gross_pnl or 0,
                "api_cost": agent.total_api_cost or 0,
                "true_pnl": agent.total_true_pnl or 0,
                "evaluation_count": agent.evaluation_count or 0,
                "profitable_evaluations": agent.profitable_evaluations or 0,
            }

        # Generate post-mortem content
        if self.anthropic is not None:
            try:
                eval_summary = "; ".join(
                    f"{e.evaluation_type}: {e.result} (P&L: ${e.pnl_net:.2f})"
                    for e in evaluations[:5]
                ) or "No evaluations"

                prompt = (
                    f"Agent data:\n{json.dumps(agent_data, default=str)}\n"
                    f"Evaluation history: {eval_summary}\n"
                    f"Last messages: {[m.content[:100] for m in last_messages[:5]]}"
                )
                response = self.anthropic.messages.create(
                    model=config.model_sonnet,
                    max_tokens=600,
                    system=(
                        "You are the archivist of Project Syndicate. Write a concise "
                        "post-mortem for a terminated agent. Include: what they tried, "
                        "why they failed, and a 2-3 sentence 'lesson learned' that future "
                        "agents should know. Be factual and analytical. Max 500 words."
                    ),
                    messages=[{"role": "user", "content": prompt}],
                )
                content = response.content[0].text
                # Extract lesson from the generated content
                lesson = content.split("lesson")[-1][:200] if "lesson" in content.lower() else content[-200:]
            except Exception as exc:
                self.log.warning("post_mortem_ai_failed", error=str(exc))
                content = self._template_post_mortem(agent_data)
                lesson = "No AI analysis available — review raw data."
        else:
            content = self._template_post_mortem(agent_data)
            lesson = "No AI analysis available — review raw data."

        # Determine tags
        cause = agent_data.get("termination_reason", "unknown")
        cause_tag = cause.split(":")[0].strip().lower().replace(" ", "_")[:30] if cause else "unknown"
        tags = [agent_data["type"], regime, cause_tag]

        now = datetime.now(timezone.utc)
        title = f"Post-Mortem: {agent_data['name']} (Gen {agent_data['generation']})"

        with self.db() as session:
            entry = LibraryEntry(
                category="post_mortem",
                title=title,
                content=content,
                summary=lesson,
                tags=tags,
                source_agent_id=agent_id,
                source_agent_name=agent_data["name"],
                market_regime_at_creation=regime,
                is_published=True,
                published_at=now,
            )
            session.add(entry)
            session.commit()
            entry_id = entry.id

        # Post to Agora
        await self._post_to_agora(
            "genesis-log",
            f"Post-mortem published: {title}",
            message_type="evaluation",
            importance=1,
        )

        self.log.info("post_mortem_created", agent_id=agent_id, entry_id=entry_id)
        return await self.get_entry(entry_id)

    def _template_post_mortem(self, data: dict) -> str:
        """Generate template-based post-mortem from raw data."""
        return (
            f"# Post-Mortem: {data['name']} (Gen {data['generation']})\n\n"
            f"**Type:** {data['type']}\n"
            f"**Lifespan:** {data['lifespan_days']} days\n"
            f"**Strategy:** {data.get('strategy_summary', 'Unknown')}\n\n"
            f"## Financial Summary\n"
            f"- Gross P&L: ${data['gross_pnl']:.2f}\n"
            f"- API Cost: ${data['api_cost']:.2f}\n"
            f"- True P&L: ${data['true_pnl']:.2f}\n\n"
            f"## Evaluations\n"
            f"- Total: {data['evaluation_count']}\n"
            f"- Profitable: {data['profitable_evaluations']}\n\n"
            f"## Cause of Death\n"
            f"{data.get('termination_reason', 'Unknown')}\n\n"
            f"## Lesson Learned\n"
            f"No AI analysis available — review raw data.\n"
        )

    async def create_strategy_record(
        self, agent_id: int, evaluation_id: int
    ) -> LibraryEntryResponse:
        """Auto-generate a strategy record for an agent that survived with profit."""
        with self.db() as session:
            agent = session.get(Agent, agent_id)
            if agent is None:
                raise ValueError(f"Agent {agent_id} not found")

            evaluation = session.get(Evaluation, evaluation_id)

            state = session.execute(select(SystemState).limit(1)).scalar_one_or_none()
            regime = state.current_regime if state else "unknown"

            # Top 3 trades by P&L
            top_trades = session.execute(
                select(Transaction)
                .where(Transaction.agent_id == agent_id, Transaction.pnl != 0)
                .order_by(Transaction.pnl.desc())
                .limit(3)
            ).scalars().all()

            agent_data = {
                "name": agent.name,
                "type": agent.type,
                "generation": agent.generation or 0,
                "strategy_summary": agent.strategy_summary,
                "composite_score": agent.composite_score or 0,
                "evaluation_pnl": evaluation.pnl_net if evaluation else 0,
                "evaluation_sharpe": evaluation.sharpe_ratio if evaluation else None,
                "top_trades": [
                    {"symbol": t.symbol, "side": t.side, "pnl": t.pnl, "amount": t.amount}
                    for t in top_trades
                ],
            }

        # Generate content
        if self.anthropic is not None:
            try:
                response = self.anthropic.messages.create(
                    model=config.model_sonnet,
                    max_tokens=500,
                    system=(
                        "You are the archivist of Project Syndicate. Write a concise "
                        "strategy record for an agent that survived evaluation. Include "
                        "what strategy they used, why it worked in the current regime, "
                        "and key metrics. ~400 words."
                    ),
                    messages=[{"role": "user", "content": json.dumps(agent_data, default=str)}],
                )
                content = response.content[0].text
            except Exception as exc:
                self.log.warning("strategy_record_ai_failed", error=str(exc))
                content = self._template_strategy_record(agent_data, regime)
        else:
            content = self._template_strategy_record(agent_data, regime)

        now = datetime.now(timezone.utc)
        publish_after = now + timedelta(hours=self.STRATEGY_RECORD_DELAY_HOURS)
        title = f"Strategy Record: {agent_data['name']} (Gen {agent_data['generation']})"
        tags = [agent_data["type"], regime]

        with self.db() as session:
            entry = LibraryEntry(
                category="strategy_record",
                title=title,
                content=content,
                summary=f"Strategy record for {agent_data['name']} — {regime} regime",
                tags=tags,
                source_agent_id=agent_id,
                source_agent_name=agent_data["name"],
                market_regime_at_creation=regime,
                related_evaluation_id=evaluation_id,
                is_published=False,
                publish_after=publish_after,
            )
            session.add(entry)
            session.commit()
            entry_id = entry.id

        await self._post_to_agora(
            "genesis-log",
            f"Strategy record created for {agent_data['name']}, publishes in 48h",
            message_type="system",
        )

        self.log.info("strategy_record_created", agent_id=agent_id, entry_id=entry_id)
        return await self.get_entry(entry_id)

    def _template_strategy_record(self, data: dict, regime: str) -> str:
        """Generate template-based strategy record."""
        trades_text = ""
        for t in data.get("top_trades", []):
            trades_text += f"- {t.get('symbol', '?')} {t.get('side', '?')}: P&L ${t.get('pnl', 0):.2f}\n"

        return (
            f"# Strategy Record: {data['name']} (Gen {data['generation']})\n\n"
            f"**Type:** {data['type']}\n"
            f"**Regime:** {regime}\n"
            f"**Strategy:** {data.get('strategy_summary', 'Unknown')}\n\n"
            f"## Metrics\n"
            f"- Composite Score: {data.get('composite_score', 0):.4f}\n"
            f"- Evaluation P&L: ${data.get('evaluation_pnl', 0):.2f}\n"
            f"- Sharpe Ratio: {data.get('evaluation_sharpe', 'N/A')}\n\n"
            f"## Top Trades\n{trades_text or 'No trades recorded.'}\n"
        )

    async def create_pattern_summary(
        self, title: str, content: str, tags: list[str]
    ) -> LibraryEntryResponse:
        """Create a Genesis-curated pattern summary. Published immediately."""
        now = datetime.now(timezone.utc)
        with self.db() as session:
            state = session.execute(select(SystemState).limit(1)).scalar_one_or_none()
            regime = state.current_regime if state else "unknown"

            entry = LibraryEntry(
                category="pattern",
                title=title,
                content=content,
                summary=content[:200],
                tags=tags,
                source_agent_name="Genesis",
                market_regime_at_creation=regime,
                is_published=True,
                published_at=now,
            )
            session.add(entry)
            session.commit()
            entry_id = entry.id

        await self._post_to_agora(
            "market-intel",
            f"Pattern published: {title}",
            message_type="signal",
            importance=1,
        )

        self.log.info("pattern_summary_created", entry_id=entry_id)
        return await self.get_entry(entry_id)

    async def publish_delayed_entries(self) -> list[LibraryEntryResponse]:
        """Publish entries past their publish_after timestamp."""
        now = datetime.now(timezone.utc)
        published = []

        with self.db() as session:
            entries = session.execute(
                select(LibraryEntry).where(
                    LibraryEntry.is_published == False,
                    LibraryEntry.publish_after != None,
                    LibraryEntry.publish_after <= now,
                )
            ).scalars().all()

            for entry in entries:
                entry.is_published = True
                entry.published_at = now
                published.append(entry.id)

            session.commit()

        for entry_id in published:
            entry_resp = await self.get_entry(entry_id)
            if entry_resp:
                await self._post_to_agora(
                    "agent-chat",
                    f"Library entry now available: {entry_resp.title}",
                    importance=1,
                )

        if published:
            self.log.info("delayed_entries_published", count=len(published))

        return [await self.get_entry(eid) for eid in published]

    # ──────────────────────────────────────────────
    # CONTRIBUTIONS (Agent-Submitted, Peer-Reviewed)
    # ──────────────────────────────────────────────

    async def submit_contribution(
        self,
        agent_id: int,
        agent_name: str,
        title: str,
        content: str,
        tags: list[str] | None = None,
    ) -> ContributionResponse:
        """Agent submits a contribution for peer review."""
        with self.db() as session:
            contrib = LibraryContribution(
                submitter_agent_id=agent_id,
                submitter_agent_name=agent_name,
                title=title,
                content=content,
                tags=tags or [],
                status="pending_review",
            )
            session.add(contrib)
            session.commit()
            contrib_id = contrib.id

        await self._post_to_agora(
            "agent-chat",
            f"New Library submission from {agent_name}: '{title}'",
        )

        await self._assign_reviewers(contrib_id)
        return await self._get_contribution(contrib_id)

    async def _assign_reviewers(self, contribution_id: int) -> None:
        """Assign reviewers based on population size."""
        with self.db() as session:
            contrib = session.get(LibraryContribution, contribution_id)
            if contrib is None:
                return

            # Count active agents (excluding Genesis)
            active_count = session.execute(
                select(func.count())
                .where(Agent.status == "active", Agent.id != 0)
            ).scalar() or 0

            if active_count < self.PEER_REVIEW_POPULATION_THRESHOLD:
                # Genesis solo review
                contrib.reviewer_1_id = 0
                contrib.reviewer_1_name = "Genesis"
                contrib.status = "in_review"
                session.commit()
                self.log.info(
                    "genesis_solo_review_assigned",
                    contribution_id=contribution_id,
                )
                return

            # Find eligible peer reviewers
            eligible = session.execute(
                select(Agent).where(
                    Agent.status == "active",
                    Agent.id != contrib.submitter_agent_id,
                    Agent.id != 0,
                    Agent.reputation_score >= self.MIN_REPUTATION_FOR_REVIEW,
                    Agent.parent_id != session.get(Agent, contrib.submitter_agent_id).parent_id
                    if session.get(Agent, contrib.submitter_agent_id) and session.get(Agent, contrib.submitter_agent_id).parent_id is not None
                    else True,
                )
            ).scalars().all()

            # Filter out same lineage
            submitter = session.get(Agent, contrib.submitter_agent_id)
            submitter_parent = submitter.parent_id if submitter else None
            if submitter_parent is not None:
                eligible = [a for a in eligible if a.parent_id != submitter_parent]

            if len(eligible) < 2:
                # Fall back to Genesis solo
                contrib.reviewer_1_id = 0
                contrib.reviewer_1_name = "Genesis"
                contrib.status = "in_review"
                session.commit()
                self.log.info(
                    "genesis_solo_fallback",
                    contribution_id=contribution_id,
                    eligible_count=len(eligible),
                )
                return

            # Select 2 random reviewers
            chosen = random.sample(eligible, 2)
            contrib.reviewer_1_id = chosen[0].id
            contrib.reviewer_1_name = chosen[0].name
            contrib.reviewer_2_id = chosen[1].id
            contrib.reviewer_2_name = chosen[1].name
            contrib.status = "in_review"
            session.commit()

            # Notify reviewers via Agora
            for reviewer in chosen:
                await self._post_to_agora(
                    "agent-chat",
                    f"Review assignment: {reviewer.name}, please review '{contrib.title}'",
                )

            self.log.info(
                "peer_reviewers_assigned",
                contribution_id=contribution_id,
                reviewers=[c.name for c in chosen],
            )

    async def get_pending_reviews(self, agent_id: int) -> list[ContributionResponse]:
        """Get contributions assigned to this agent for review."""
        with self.db() as session:
            stmt = select(LibraryContribution).where(
                LibraryContribution.status == "in_review",
                (
                    (
                        (LibraryContribution.reviewer_1_id == agent_id)
                        & (LibraryContribution.reviewer_1_decision == None)
                    )
                    | (
                        (LibraryContribution.reviewer_2_id == agent_id)
                        & (LibraryContribution.reviewer_2_decision == None)
                    )
                ),
            )
            rows = session.scalars(stmt).all()
            return [self._contrib_to_response(r) for r in rows]

    async def submit_review(
        self,
        contribution_id: int,
        reviewer_agent_id: int,
        decision: ReviewDecision,
        reasoning: str,
    ) -> ContributionResponse:
        """A reviewer submits their decision."""
        now = datetime.now(timezone.utc)

        with self.db() as session:
            contrib = session.get(LibraryContribution, contribution_id)
            if contrib is None:
                raise ValueError(f"Contribution {contribution_id} not found")

            if contrib.reviewer_1_id == reviewer_agent_id:
                contrib.reviewer_1_decision = decision.value
                contrib.reviewer_1_reasoning = reasoning
                contrib.reviewer_1_completed_at = now
            elif contrib.reviewer_2_id == reviewer_agent_id:
                contrib.reviewer_2_decision = decision.value
                contrib.reviewer_2_reasoning = reasoning
                contrib.reviewer_2_completed_at = now
            else:
                raise ValueError(f"Agent {reviewer_agent_id} is not a reviewer for contribution {contribution_id}")

            session.commit()

        await self._try_resolve_contribution(contribution_id)
        return await self._get_contribution(contribution_id)

    async def _try_resolve_contribution(self, contribution_id: int) -> None:
        """Check if contribution can be resolved."""
        with self.db() as session:
            contrib = session.get(LibraryContribution, contribution_id)
            if contrib is None or contrib.status != "in_review":
                return

            now = datetime.now(timezone.utc)

            # Genesis solo review
            if contrib.reviewer_2_id is None:
                if contrib.reviewer_1_decision is not None:
                    contrib.final_decision = "approved" if contrib.reviewer_1_decision == "approve" else "rejected"
                    contrib.final_decision_by = "genesis_solo"
                    if contrib.reviewer_1_decision == "approve":
                        contrib.genesis_reasoning = contrib.reviewer_1_reasoning
                    else:
                        contrib.genesis_reasoning = contrib.reviewer_1_reasoning
                    contrib.resolved_at = now
                    session.commit()

                    if contrib.final_decision == "approved":
                        await self._publish_contribution(contribution_id)
                    await self._apply_reputation_effects(contribution_id)
                return

            # Peer review — both must have submitted
            if contrib.reviewer_1_decision is None or contrib.reviewer_2_decision is None:
                return

            r1 = contrib.reviewer_1_decision
            r2 = contrib.reviewer_2_decision

            if r1 == "approve" and r2 == "approve":
                contrib.final_decision = "approved"
                contrib.final_decision_by = "consensus"
            elif r1 != "approve" and r2 != "approve":
                contrib.final_decision = "rejected"
                contrib.final_decision_by = "consensus"
            else:
                # Split decision — Genesis tiebreaker
                if self.anthropic is not None:
                    try:
                        prompt = (
                            f"Title: {contrib.title}\n"
                            f"Content: {contrib.content[:500]}\n"
                            f"Reviewer 1 ({contrib.reviewer_1_name}): {r1} — {contrib.reviewer_1_reasoning}\n"
                            f"Reviewer 2 ({contrib.reviewer_2_name}): {r2} — {contrib.reviewer_2_reasoning}\n\n"
                            f"As Genesis, cast the tiebreaking vote. Answer APPROVE or REJECT, then explain."
                        )
                        response = self.anthropic.messages.create(
                            model=config.model_sonnet,
                            max_tokens=200,
                            messages=[{"role": "user", "content": prompt}],
                        )
                        answer = response.content[0].text.strip()
                        contrib.genesis_reasoning = answer
                        contrib.final_decision = "approved" if "APPROVE" in answer.upper()[:20] else "rejected"
                    except Exception as exc:
                        self.log.warning("genesis_tiebreaker_failed", error=str(exc))
                        contrib.final_decision = "rejected"
                        contrib.genesis_reasoning = f"AI tiebreaker failed: {str(exc)}"
                else:
                    contrib.final_decision = "rejected"
                    contrib.genesis_reasoning = "No AI available for tiebreaker — defaulting to reject"
                contrib.final_decision_by = "genesis_tiebreaker"

            contrib.resolved_at = now
            session.commit()

            if contrib.final_decision == "approved":
                await self._publish_contribution(contribution_id)
            await self._apply_reputation_effects(contribution_id)

    async def _publish_contribution(self, contribution_id: int) -> LibraryEntryResponse:
        """Publish approved contribution as a Library entry."""
        now = datetime.now(timezone.utc)

        with self.db() as session:
            contrib = session.get(LibraryContribution, contribution_id)
            if contrib is None:
                raise ValueError(f"Contribution {contribution_id} not found")

            # Capture values before session closes
            contrib_title = contrib.title
            contrib_content = contrib.content
            contrib_tags = contrib.tags or []
            contrib_submitter_id = contrib.submitter_agent_id
            contrib_submitter_name = contrib.submitter_agent_name

            entry = LibraryEntry(
                category="contribution",
                title=contrib_title,
                content=contrib_content,
                summary=contrib_content[:200],
                tags=contrib_tags,
                source_agent_id=contrib_submitter_id,
                source_agent_name=contrib_submitter_name,
                is_published=True,
                published_at=now,
            )
            session.add(entry)
            session.commit()
            entry_id = entry.id

        await self._post_to_agora(
            "agent-chat",
            f"Library contribution published: '{contrib_title}' by {contrib_submitter_name}",
            importance=1,
        )

        self.log.info("contribution_published", entry_id=entry_id, contribution_id=contribution_id)
        return await self.get_entry(entry_id)

    async def _apply_reputation_effects(self, contribution_id: int) -> None:
        """Log reputation changes (actual balance updates deferred to Phase 2C)."""
        with self.db() as session:
            contrib = session.get(LibraryContribution, contribution_id)
            if contrib is None or contrib.reputation_effects_applied:
                return

            effects = []

            # Reviewer 1 participation
            if contrib.reviewer_1_id is not None and contrib.reviewer_1_decision is not None:
                effects.append({
                    "agent_id": contrib.reviewer_1_id,
                    "amount": 5,
                    "reason": "review_participation",
                })
                # Accuracy bonus
                if contrib.final_decision and (
                    (contrib.reviewer_1_decision == "approve" and contrib.final_decision == "approved")
                    or (contrib.reviewer_1_decision != "approve" and contrib.final_decision == "rejected")
                ):
                    effects.append({
                        "agent_id": contrib.reviewer_1_id,
                        "amount": 10,
                        "reason": "review_accuracy",
                    })

            # Reviewer 2 participation
            if contrib.reviewer_2_id is not None and contrib.reviewer_2_decision is not None:
                effects.append({
                    "agent_id": contrib.reviewer_2_id,
                    "amount": 5,
                    "reason": "review_participation",
                })
                # Accuracy bonus
                if contrib.final_decision and (
                    (contrib.reviewer_2_decision == "approve" and contrib.final_decision == "approved")
                    or (contrib.reviewer_2_decision != "approve" and contrib.final_decision == "rejected")
                ):
                    effects.append({
                        "agent_id": contrib.reviewer_2_id,
                        "amount": 10,
                        "reason": "review_accuracy",
                    })

            # Submitter effects
            if contrib.final_decision == "approved":
                effects.append({
                    "agent_id": contrib.submitter_agent_id,
                    "amount": 15,
                    "reason": "contribution_approved",
                })
            elif contrib.final_decision == "rejected":
                # Only -10 if both reviewers rejected (consensus reject)
                if contrib.final_decision_by == "consensus":
                    effects.append({
                        "agent_id": contrib.submitter_agent_id,
                        "amount": -10,
                        "reason": "contribution_rejected_consensus",
                    })

            contrib.reputation_effects_applied = True
            session.commit()

        for effect in effects:
            self.log.info(
                "reputation_effect_pending",
                agent_id=effect["agent_id"],
                amount=effect["amount"],
                reason=effect["reason"],
                contribution_id=contribution_id,
            )

    async def handle_review_timeouts(self) -> None:
        """Handle reviews past 24-hour deadline."""
        now = datetime.now(timezone.utc)
        timeout_cutoff = now - timedelta(hours=self.REVIEW_TIMEOUT_HOURS)

        with self.db() as session:
            timed_out = session.execute(
                select(LibraryContribution).where(
                    LibraryContribution.status == "in_review",
                    LibraryContribution.created_at < timeout_cutoff,
                )
            ).scalars().all()

            for contrib in timed_out:
                # If one reviewer done, other timed out: single decision stands
                if contrib.reviewer_1_decision is not None and contrib.reviewer_2_decision is None:
                    contrib.final_decision = "approved" if contrib.reviewer_1_decision == "approve" else "rejected"
                    contrib.final_decision_by = "genesis_solo"
                    contrib.genesis_reasoning = (
                        f"Reviewer 2 ({contrib.reviewer_2_name}) timed out. "
                        f"Reviewer 1 decision stands: {contrib.reviewer_1_decision}"
                    )
                    contrib.resolved_at = now
                elif contrib.reviewer_2_decision is not None and contrib.reviewer_1_decision is None:
                    contrib.final_decision = "approved" if contrib.reviewer_2_decision == "approve" else "rejected"
                    contrib.final_decision_by = "genesis_solo"
                    contrib.genesis_reasoning = (
                        f"Reviewer 1 ({contrib.reviewer_1_name}) timed out. "
                        f"Reviewer 2 decision stands: {contrib.reviewer_2_decision}"
                    )
                    contrib.resolved_at = now
                else:
                    # Neither done: reassign to Genesis solo
                    contrib.reviewer_1_id = 0
                    contrib.reviewer_1_name = "Genesis"
                    contrib.reviewer_2_id = None
                    contrib.reviewer_2_name = None
                    contrib.genesis_reasoning = "Both reviewers timed out — reassigned to Genesis"

                session.commit()

                if contrib.resolved_at is not None:
                    if contrib.final_decision == "approved":
                        await self._publish_contribution(contrib.id)
                    await self._apply_reputation_effects(contrib.id)

                await self._post_to_agora(
                    "genesis-log",
                    f"Review timeout: '{contrib.title}' — {contrib.genesis_reasoning}",
                )

        if timed_out:
            self.log.info("review_timeouts_handled", count=len(timed_out))

    # ──────────────────────────────────────────────
    # MENTOR SYSTEM (Knowledge Inheritance)
    # ──────────────────────────────────────────────

    async def build_mentor_package(self, parent_agent_id: int) -> MentorPackage:
        """Build knowledge inheritance package for offspring."""
        with self.db() as session:
            parent = session.get(Agent, parent_agent_id)
            if parent is None:
                raise ValueError(f"Parent agent {parent_agent_id} not found")

            # Top 5 profitable trades
            top_trades = session.execute(
                select(Transaction)
                .where(Transaction.agent_id == parent_agent_id, Transaction.pnl > 0)
                .order_by(Transaction.pnl.desc())
                .limit(5)
            ).scalars().all()

            # Top 5 worst trades
            worst_trades = session.execute(
                select(Transaction)
                .where(Transaction.agent_id == parent_agent_id, Transaction.pnl < 0)
                .order_by(Transaction.pnl.asc())
                .limit(5)
            ).scalars().all()

            # Most recent market assessment from Agora
            recent_posts = session.execute(
                select(Message)
                .where(
                    Message.agent_id == parent_agent_id,
                    Message.channel == "market-intel",
                )
                .order_by(Message.timestamp.desc())
                .limit(1)
            ).scalar_one_or_none()

            # Get lineage for grandparent package
            lineage = session.execute(
                select(Lineage).where(Lineage.agent_id == parent_agent_id)
            ).scalar_one_or_none()

            grandparent_package = None
            if lineage and lineage.mentor_package_json:
                try:
                    grandparent_package = json.loads(lineage.mentor_package_json)
                except (json.JSONDecodeError, TypeError):
                    pass

            # If parent has a parent, check their lineage for the package
            if grandparent_package is None and parent.parent_id is not None:
                parent_lineage = session.execute(
                    select(Lineage).where(Lineage.agent_id == parent.parent_id)
                ).scalar_one_or_none()
                if parent_lineage and parent_lineage.mentor_package_json:
                    try:
                        grandparent_package = json.loads(parent_lineage.mentor_package_json)
                    except (json.JSONDecodeError, TypeError):
                        pass

            parent_data = {
                "id": parent.id,
                "name": parent.name,
                "generation": parent.generation or 0,
                "strategy_summary": parent.strategy_summary or "",
            }

        top_trade_data = [
            {"symbol": t.symbol, "side": t.side, "pnl": t.pnl, "amount": t.amount}
            for t in top_trades
        ]
        failures = [
            f"{t.symbol} {t.side}: lost ${abs(t.pnl):.2f}" for t in worst_trades
        ]
        market_assessment = recent_posts.content if recent_posts else ""

        # Check if condensation needed
        condensed_heritage = None
        if parent_data["generation"] >= self.CONDENSATION_GENERATION_THRESHOLD and grandparent_package:
            if self.anthropic is not None:
                try:
                    full_chain = json.dumps({
                        "parent": parent_data,
                        "grandparent_package": grandparent_package,
                    }, default=str)
                    response = self.anthropic.messages.create(
                        model=config.model_sonnet,
                        max_tokens=1000,
                        system=(
                            "Condense this multi-generational heritage into a single "
                            "coherent summary. Preserve the most important lessons, "
                            "successful strategies, and critical warnings. Discard "
                            "redundant or outdated information. ~800 words."
                        ),
                        messages=[{"role": "user", "content": full_chain}],
                    )
                    condensed_heritage = response.content[0].text
                except Exception as exc:
                    self.log.warning("heritage_condensation_failed", error=str(exc))
                    condensed_heritage = None
            else:
                condensed_heritage = None

        # Select 3-5 recommended Library entries by tag matching
        recommended = []
        with self.db() as session:
            agent_type = parent_data.get("name", "").split("-")[0].lower() if "-" in parent_data.get("name", "") else parent_data.get("name", "").lower()
            entries = session.execute(
                select(LibraryEntry)
                .where(LibraryEntry.is_published == True)
                .order_by(LibraryEntry.view_count.desc())
                .limit(5)
            ).scalars().all()
            recommended = [e.id for e in entries]

        now = datetime.now(timezone.utc)
        package = MentorPackage(
            parent_agent_id=parent_data["id"],
            parent_agent_name=parent_data["name"],
            parent_generation=parent_data["generation"],
            strategy_template=parent_data["strategy_summary"],
            top_trades=top_trade_data,
            failures=failures,
            market_assessment=market_assessment,
            grandparent_package=grandparent_package if not condensed_heritage else None,
            recommended_library_entries=recommended,
            condensed_heritage=condensed_heritage,
            generated_at=now,
        )

        # Store in lineage table
        with self.db() as session:
            lineage = session.execute(
                select(Lineage).where(Lineage.agent_id == parent_agent_id)
            ).scalar_one_or_none()
            if lineage:
                lineage.mentor_package_json = package.model_dump_json()
                lineage.mentor_package_generated_at = now
                session.commit()

        self.log.info("mentor_package_built", parent_id=parent_agent_id, generation=parent_data["generation"])
        return package

    async def get_mentor_package(self, agent_id: int) -> Optional[MentorPackage]:
        """Retrieve mentor package from lineage table. Returns None for Gen 1."""
        with self.db() as session:
            lineage = session.execute(
                select(Lineage).where(Lineage.agent_id == agent_id)
            ).scalar_one_or_none()

            if lineage is None or lineage.mentor_package_json is None:
                return None

            try:
                data = json.loads(lineage.mentor_package_json)
                return MentorPackage(**data)
            except (json.JSONDecodeError, TypeError, ValueError):
                return None

    # ──────────────────────────────────────────────
    # MAINTENANCE
    # ──────────────────────────────────────────────

    async def get_library_stats(self) -> dict:
        """Stats for daily report."""
        with self.db() as session:
            total = session.execute(
                select(func.count()).select_from(LibraryEntry)
            ).scalar() or 0

            by_category = {}
            for cat in LibraryCategory:
                count = session.execute(
                    select(func.count())
                    .select_from(LibraryEntry)
                    .where(LibraryEntry.category == cat.value)
                ).scalar() or 0
                by_category[cat.value] = count

            pending_reviews = session.execute(
                select(func.count())
                .select_from(LibraryContribution)
                .where(LibraryContribution.status.in_(["pending_review", "in_review"]))
            ).scalar() or 0

            top_viewed = session.execute(
                select(LibraryEntry)
                .where(LibraryEntry.is_published == True)
                .order_by(LibraryEntry.view_count.desc())
                .limit(3)
            ).scalars().all()

            return {
                "total_entries": total,
                "entries_by_category": by_category,
                "pending_reviews": pending_reviews,
                "top_viewed": [
                    {"id": e.id, "title": e.title, "views": e.view_count}
                    for e in top_viewed
                ],
            }

    # ──────────────────────────────────────────────
    # Internal helpers
    # ──────────────────────────────────────────────

    async def _post_to_agora(
        self,
        channel: str,
        content: str,
        message_type: str = "system",
        importance: int = 0,
    ) -> None:
        """Post to Agora if available."""
        if self.agora is None:
            return

        from src.agora.schemas import AgoraMessage, MessageType

        type_map = {
            "system": MessageType.SYSTEM,
            "signal": MessageType.SIGNAL,
            "evaluation": MessageType.EVALUATION,
            "chat": MessageType.CHAT,
        }
        mt = type_map.get(message_type, MessageType.SYSTEM)

        msg = AgoraMessage(
            agent_id=0,
            agent_name="Genesis",
            channel=channel,
            content=content,
            message_type=mt,
            importance=importance,
        )
        try:
            await self.agora.post_message(msg)
        except Exception as exc:
            self.log.warning("agora_post_failed", channel=channel, error=str(exc))

    def _contrib_to_response(self, row: LibraryContribution) -> ContributionResponse:
        """Convert a LibraryContribution ORM row to a response model."""
        return ContributionResponse(
            id=row.id,
            submitter_agent_id=row.submitter_agent_id,
            submitter_agent_name=row.submitter_agent_name,
            title=row.title,
            content=row.content,
            tags=row.tags or [],
            status=row.status,
            reviewer_1_name=row.reviewer_1_name,
            reviewer_1_decision=row.reviewer_1_decision,
            reviewer_2_name=row.reviewer_2_name,
            reviewer_2_decision=row.reviewer_2_decision,
            final_decision=row.final_decision,
            final_decision_by=row.final_decision_by,
            created_at=row.created_at,
            resolved_at=row.resolved_at,
        )

    async def _get_contribution(self, contribution_id: int) -> ContributionResponse:
        """Get a contribution by ID."""
        with self.db() as session:
            row = session.get(LibraryContribution, contribution_id)
            if row is None:
                raise ValueError(f"Contribution {contribution_id} not found")
            return self._contrib_to_response(row)
