"""
Project Syndicate — Review Market

Strategists request Critic reviews by posting reputation budgets.
Critics accept, review, and get paid. Accuracy is tracked retroactively.
"""

__version__ = "0.5.0"

from datetime import datetime, timedelta, timezone
from typing import Optional, TYPE_CHECKING

import structlog
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from src.common.models import (
    CriticAccuracy,
    ReviewAssignment,
    ReviewRequest,
)

if TYPE_CHECKING:
    from src.agora.agora_service import AgoraService
    from src.economy.economy_service import EconomyService

logger = structlog.get_logger()


def _utcnow_naive() -> datetime:
    """UTC now without timezone info (for DB compatibility with SQLite)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class ReviewMarket:
    """Review marketplace — Strategists pay Critics for strategy reviews."""

    REVIEW_REQUEST_EXPIRY_HOURS = 24
    REVIEW_COMPLETION_DEADLINE_HOURS = 12
    HIGH_CAPITAL_THRESHOLD_PCT = 0.20  # >20% capital → requires 2 reviews

    def __init__(
        self,
        db_session_factory: sessionmaker,
        economy_service: "EconomyService",
        agora_service: Optional["AgoraService"] = None,
    ) -> None:
        self.db = db_session_factory
        self.economy = economy_service
        self.agora = agora_service
        self.log = logger.bind(component="review_market")

    # ──────────────────────────────────────────────
    # REQUEST
    # ──────────────────────────────────────────────

    async def request_review(
        self,
        requester_agent_id: int,
        requester_agent_name: str,
        proposal_message_id: int,
        proposal_summary: str,
        budget_reputation: float,
        capital_percentage: float = 0.0,
    ) -> Optional[ReviewRequest]:
        """Request a Critic review for a strategy proposal."""
        # Validate budget
        if budget_reputation < self.economy.MIN_REVIEW_BUDGET:
            self.log.warning("review_budget_too_low", budget=budget_reputation)
            return None
        if budget_reputation > self.economy.MAX_REVIEW_BUDGET:
            self.log.warning("review_budget_too_high", budget=budget_reputation)
            return None

        # Determine if two reviews required
        requires_two = capital_percentage > self.HIGH_CAPITAL_THRESHOLD_PCT
        total_budget = budget_reputation * 2 if requires_two else budget_reputation

        # Verify balance
        balance = await self.economy.get_balance(requester_agent_id)
        if balance < total_budget:
            self.log.warning(
                "review_insufficient_balance",
                agent_id=requester_agent_id,
                balance=balance,
                needed=total_budget,
            )
            return None

        # Escrow budget
        escrowed = await self.economy.escrow_reputation(
            requester_agent_id, total_budget, f"review_request:{proposal_message_id}"
        )
        if not escrowed:
            return None

        now = _utcnow_naive()
        expires_at = now + timedelta(hours=self.REVIEW_REQUEST_EXPIRY_HOURS)

        with self.db() as session:
            request = ReviewRequest(
                requester_agent_id=requester_agent_id,
                requester_agent_name=requester_agent_name,
                proposal_message_id=proposal_message_id,
                proposal_summary=proposal_summary,
                budget_reputation=budget_reputation,
                requires_two_reviews=requires_two,
                status="open",
                expires_at=expires_at,
            )
            session.add(request)
            session.commit()
            request_id = request.id

        await self._post_to_agora(
            "strategy-debate",
            f"Review requested by {requester_agent_name}: {proposal_summary} "
            f"(budget: {budget_reputation:.1f} rep{', requires 2 reviewers' if requires_two else ''})",
            message_type="proposal",
            metadata={"review_request_id": request_id, "requires_two_reviews": requires_two},
        )
        self.log.info(
            "review_requested",
            request_id=request_id,
            requester_id=requester_agent_id,
            budget=budget_reputation,
            requires_two=requires_two,
        )

        with self.db() as session:
            return session.get(ReviewRequest, request_id)

    # ──────────────────────────────────────────────
    # OPEN REQUESTS
    # ──────────────────────────────────────────────

    async def get_open_requests(
        self,
        critic_agent_id: Optional[int] = None,
    ) -> list[ReviewRequest]:
        """Get open review requests that need Critics."""
        now = _utcnow_naive()
        with self.db() as session:
            stmt = (
                select(ReviewRequest)
                .where(ReviewRequest.status == "open", ReviewRequest.expires_at > now)
            )
            if critic_agent_id is not None:
                # Exclude requests where this critic already has an assignment
                assigned_ids = session.execute(
                    select(ReviewAssignment.review_request_id)
                    .where(ReviewAssignment.critic_agent_id == critic_agent_id)
                ).scalars().all()
                if assigned_ids:
                    stmt = stmt.where(ReviewRequest.id.notin_(assigned_ids))

            stmt = stmt.order_by(ReviewRequest.budget_reputation.desc())
            return list(session.execute(stmt).scalars().all())

    # ──────────────────────────────────────────────
    # ACCEPT
    # ──────────────────────────────────────────────

    async def accept_review(
        self,
        request_id: int,
        critic_agent_id: int,
        critic_agent_name: str,
    ) -> Optional[ReviewAssignment]:
        """A Critic accepts a review request."""
        with self.db() as session:
            request = session.get(ReviewRequest, request_id)
            if request is None:
                self.log.warning("accept_request_not_found", request_id=request_id)
                return None

            # Cannot review own request
            if request.requester_agent_id == critic_agent_id:
                self.log.warning("accept_own_request", request_id=request_id)
                return None

            # Check existing assignments
            existing_count = session.execute(
                select(func.count()).select_from(ReviewAssignment)
                .where(ReviewAssignment.review_request_id == request_id)
            ).scalar() or 0

            needed = 2 if request.requires_two_reviews else 1
            if existing_count >= needed:
                self.log.warning("accept_request_full", request_id=request_id, existing=existing_count)
                return None

            # Check if this critic already assigned
            already = session.execute(
                select(ReviewAssignment).where(
                    ReviewAssignment.review_request_id == request_id,
                    ReviewAssignment.critic_agent_id == critic_agent_id,
                )
            ).scalar_one_or_none()
            if already is not None:
                self.log.warning("accept_duplicate_critic", request_id=request_id, critic_id=critic_agent_id)
                return None

            proposal_summary = request.proposal_summary

            now = _utcnow_naive()
            assignment = ReviewAssignment(
                review_request_id=request_id,
                critic_agent_id=critic_agent_id,
                critic_agent_name=critic_agent_name,
                deadline_at=now + timedelta(hours=self.REVIEW_COMPLETION_DEADLINE_HOURS),
            )
            session.add(assignment)

            # Update request status
            if existing_count + 1 >= needed:
                request.status = "assigned"

            session.commit()
            assignment_id = assignment.id

        await self._post_to_agora(
            "strategy-debate",
            f"{critic_agent_name} accepted review for: {proposal_summary}",
            message_type="system",
        )
        self.log.info(
            "review_accepted",
            request_id=request_id,
            critic_id=critic_agent_id,
            assignment_id=assignment_id,
        )

        with self.db() as session:
            return session.get(ReviewAssignment, assignment_id)

    # ──────────────────────────────────────────────
    # SUBMIT REVIEW
    # ──────────────────────────────────────────────

    async def submit_review(
        self,
        assignment_id: int,
        verdict: str,
        reasoning: str,
        risk_score: int,
        review_message_id: int | None = None,
    ) -> Optional[ReviewAssignment]:
        """Critic submits their review."""
        with self.db() as session:
            assignment = session.get(ReviewAssignment, assignment_id)
            if assignment is None or assignment.completed_at is not None:
                self.log.warning("submit_invalid_assignment", assignment_id=assignment_id)
                return None

            request_id = assignment.review_request_id
            critic_agent_id = assignment.critic_agent_id
            critic_agent_name = assignment.critic_agent_name

        # Load request for budget
        with self.db() as session:
            request = session.get(ReviewRequest, request_id)
            if request is None:
                return None
            budget = request.budget_reputation
            requires_two = request.requires_two_reviews
            proposal_summary = request.proposal_summary

        # Calculate payment
        payment = budget / 2 if requires_two else budget

        # Pay the critic
        await self.economy.apply_reward(
            critic_agent_id, payment, f"review_completed:{request_id}"
        )

        now = _utcnow_naive()
        with self.db() as session:
            assignment = session.get(ReviewAssignment, assignment_id)
            if assignment:
                assignment.verdict = verdict
                assignment.reasoning = reasoning
                assignment.risk_score = risk_score
                assignment.review_message_id = review_message_id
                assignment.reputation_earned = payment
                assignment.completed_at = now
                session.commit()

        # Update critic accuracy table
        await self._update_critic_stats(critic_agent_id, verdict, risk_score)

        # Check if all assignments are complete
        with self.db() as session:
            request = session.get(ReviewRequest, request_id)
            if request:
                needed = 2 if request.requires_two_reviews else 1
                completed = session.execute(
                    select(func.count()).select_from(ReviewAssignment)
                    .where(
                        ReviewAssignment.review_request_id == request_id,
                        ReviewAssignment.completed_at.isnot(None),
                    )
                ).scalar() or 0
                if completed >= needed:
                    request.status = "completed"
                    request.completed_at = now
                session.commit()

        await self._post_to_agora(
            "strategy-debate",
            f"{critic_agent_name} reviewed {proposal_summary}: {verdict} (risk: {risk_score}/10)",
            message_type="evaluation",
        )
        self.log.info(
            "review_submitted",
            assignment_id=assignment_id,
            verdict=verdict,
            risk_score=risk_score,
            payment=payment,
        )

        with self.db() as session:
            return session.get(ReviewAssignment, assignment_id)

    # ──────────────────────────────────────────────
    # CRITIC ACCURACY
    # ──────────────────────────────────────────────

    async def update_critic_accuracy(
        self,
        critic_agent_id: int,
        was_accurate: bool,
    ) -> None:
        """Update a Critic's accuracy score after a strategy outcome is known."""
        with self.db() as session:
            record = session.get(CriticAccuracy, critic_agent_id)
            if record is None:
                record = CriticAccuracy(
                    critic_agent_id=critic_agent_id,
                    total_reviews=0,
                    accurate_reviews=0,
                )
                session.add(record)

            if was_accurate:
                record.accurate_reviews = (record.accurate_reviews or 0) + 1

            total = record.total_reviews or 0
            accurate = record.accurate_reviews or 0
            record.accuracy_score = accurate / total if total > 0 else 0.0
            record.last_updated = datetime.now(timezone.utc)
            session.commit()

    async def _update_critic_stats(self, critic_agent_id: int, verdict: str, risk_score: int) -> None:
        """Update critic accuracy stats after a review submission."""
        with self.db() as session:
            record = session.get(CriticAccuracy, critic_agent_id)
            if record is None:
                record = CriticAccuracy(
                    critic_agent_id=critic_agent_id,
                    total_reviews=0,
                    accurate_reviews=0,
                )
                session.add(record)

            record.total_reviews = (record.total_reviews or 0) + 1

            if verdict == "approve":
                record.approve_count = (record.approve_count or 0) + 1
            elif verdict == "reject":
                record.reject_count = (record.reject_count or 0) + 1
            elif verdict == "conditional_approve":
                record.conditional_count = (record.conditional_count or 0) + 1

            # Recalculate average risk score
            total = record.total_reviews
            old_avg = record.avg_risk_score or 0.0
            record.avg_risk_score = ((old_avg * (total - 1)) + risk_score) / total if total > 0 else 0.0
            record.last_updated = datetime.now(timezone.utc)

            session.commit()

    # ──────────────────────────────────────────────
    # MAINTENANCE
    # ──────────────────────────────────────────────

    async def expire_stale_requests(self) -> int:
        """Expire review requests that weren't accepted in time."""
        now = _utcnow_naive()
        count = 0

        with self.db() as session:
            expired = list(
                session.execute(
                    select(ReviewRequest)
                    .where(ReviewRequest.status == "open", ReviewRequest.expires_at < now)
                ).scalars().all()
            )
            expired_data = [
                {
                    "id": r.id,
                    "requester_agent_id": r.requester_agent_id,
                    "budget_reputation": r.budget_reputation,
                    "requires_two_reviews": r.requires_two_reviews,
                    "proposal_summary": r.proposal_summary,
                }
                for r in expired
            ]

        for rdata in expired_data:
            total_budget = rdata["budget_reputation"] * (2 if rdata["requires_two_reviews"] else 1)
            await self.economy.release_escrow(
                rdata["requester_agent_id"], total_budget,
                f"review_expired:{rdata['id']}",
            )
            with self.db() as session:
                req = session.get(ReviewRequest, rdata["id"])
                if req:
                    req.status = "expired"
                    session.commit()

            await self._post_to_agora(
                "strategy-debate",
                f"Review request expired (no Critics accepted): {rdata['proposal_summary']}",
                message_type="system",
            )
            count += 1

        if count > 0:
            self.log.info("stale_requests_expired", count=count)
        return count

    async def check_overdue_assignments(self) -> int:
        """Check for assignments past their deadline."""
        now = _utcnow_naive()
        count = 0

        with self.db() as session:
            overdue = list(
                session.execute(
                    select(ReviewAssignment)
                    .where(
                        ReviewAssignment.completed_at.is_(None),
                        ReviewAssignment.deadline_at < now,
                    )
                ).scalars().all()
            )
            overdue_data = [
                {
                    "id": a.id,
                    "critic_agent_name": a.critic_agent_name,
                    "deadline_at": a.deadline_at,
                    "review_request_id": a.review_request_id,
                }
                for a in overdue
            ]

        for adata in overdue_data:
            hours_overdue = (now - adata["deadline_at"]).total_seconds() / 3600
            if hours_overdue > 24:
                # Release critic and re-open
                with self.db() as session:
                    session.delete(session.get(ReviewAssignment, adata["id"]))
                    req = session.get(ReviewRequest, adata["review_request_id"])
                    if req and req.status == "assigned":
                        req.status = "open"
                    session.commit()
                self.log.warning(
                    "overdue_assignment_released",
                    assignment_id=adata["id"],
                    hours_overdue=round(hours_overdue, 1),
                )
            else:
                await self._post_to_agora(
                    "strategy-debate",
                    f"Warning: {adata['critic_agent_name']}'s review is overdue ({hours_overdue:.0f}h past deadline)",
                    message_type="system",
                )
            count += 1

        return count

    async def get_critic_stats(self, critic_agent_id: int) -> Optional[CriticAccuracy]:
        """Get a Critic's full accuracy statistics."""
        with self.db() as session:
            return session.get(CriticAccuracy, critic_agent_id)

    # ──────────────────────────────────────────────
    # Helpers
    # ──────────────────────────────────────────────

    async def _post_to_agora(
        self,
        channel: str,
        content: str,
        message_type: str = "economy",
        metadata: dict | None = None,
    ) -> None:
        if self.agora is None:
            return
        from src.agora.schemas import AgoraMessage, MessageType
        type_map = {
            "economy": MessageType.ECONOMY,
            "proposal": MessageType.PROPOSAL,
            "system": MessageType.SYSTEM,
            "evaluation": MessageType.EVALUATION,
        }
        mt = type_map.get(message_type, MessageType.ECONOMY)
        msg = AgoraMessage(
            agent_id=0, agent_name="ReviewMarket", channel=channel,
            content=content, message_type=mt, metadata=metadata or {},
        )
        try:
            await self.agora.post_message(msg)
        except Exception as exc:
            self.log.warning("agora_post_failed", channel=channel, error=str(exc))
