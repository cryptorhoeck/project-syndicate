"""
Project Syndicate — Plans Manager

Manages the plan lifecycle in the Strategist → Critic → Operator pipeline:
  - Strategists create plans from opportunities
  - Plans are submitted for Critic review
  - Critics approve, reject, or request revisions
  - Approved plans are picked up by Operators for execution
"""

__version__ = "1.0.0"

import logging
from datetime import datetime, timezone

from sqlalchemy import desc
from sqlalchemy.orm import Session

from src.common.models import Agent, Plan

logger = logging.getLogger(__name__)

# Valid status transitions
VALID_TRANSITIONS = {
    "draft": ["submitted"],
    "submitted": ["under_review"],
    "under_review": ["approved", "rejected", "revision_requested"],
    "revision_requested": ["submitted"],
    "approved": ["executing"],
    "executing": ["completed"],
    "rejected": [],  # terminal
    "completed": [],  # terminal
}


class PlanManager:
    """Manages the Strategist → Critic → Operator plan pipeline."""

    def __init__(self, db_session: Session):
        self.db = db_session

    def create_plan(
        self,
        strategist: Agent,
        plan_name: str,
        market: str,
        direction: str,
        entry_conditions: str,
        exit_conditions: str,
        thesis: str,
        position_size_pct: float = 0.1,
        timeframe: str | None = None,
        opportunity_id: int | None = None,
    ) -> Plan:
        """Create a new trading plan.

        Args:
            strategist: The Strategist agent.
            plan_name: Descriptive name.
            market: Trading pair.
            direction: long/short.
            entry_conditions: When to enter.
            exit_conditions: Take profit and stop loss.
            thesis: Core reasoning.
            position_size_pct: % of capital to risk.
            timeframe: Expected duration.
            opportunity_id: Source opportunity if any.

        Returns:
            The created Plan record.
        """
        plan = Plan(
            strategist_agent_id=strategist.id,
            strategist_agent_name=strategist.name,
            opportunity_id=opportunity_id,
            plan_name=plan_name,
            market=market,
            direction=direction,
            entry_conditions=entry_conditions,
            exit_conditions=exit_conditions,
            position_size_pct=position_size_pct,
            timeframe=timeframe,
            thesis=thesis,
            status="draft",
        )
        self.db.add(plan)
        self.db.flush()
        logger.info(f"Plan created: {plan_name} by {strategist.name}")
        return plan

    def submit_for_review(self, plan_id: int) -> Plan | None:
        """Submit a draft plan for Critic review.

        Args:
            plan_id: The plan to submit.

        Returns:
            The updated Plan, or None if not found or wrong status.
        """
        plan = self._get_and_validate_transition(plan_id, "submitted")
        if not plan:
            return None

        plan.status = "submitted"
        plan.submitted_at = datetime.now(timezone.utc)
        self.db.add(plan)
        self.db.flush()
        logger.info(f"Plan {plan_id} submitted for review")
        return plan

    def assign_critic(self, plan_id: int, critic: Agent) -> Plan | None:
        """Assign a Critic to review a submitted plan.

        Args:
            plan_id: The plan.
            critic: The Critic agent.

        Returns:
            The updated Plan, or None if not available.
        """
        plan = self._get_and_validate_transition(plan_id, "under_review")
        if not plan:
            return None

        plan.status = "under_review"
        plan.critic_agent_id = critic.id
        plan.critic_agent_name = critic.name
        self.db.add(plan)
        self.db.flush()
        logger.info(f"Critic {critic.name} assigned to plan {plan_id}")
        return plan

    def record_verdict(
        self,
        plan_id: int,
        verdict: str,
        reasoning: str,
        risk_notes: str | None = None,
    ) -> Plan | None:
        """Record a Critic's verdict on a plan.

        Args:
            plan_id: The plan.
            verdict: approved/rejected/revision_requested.
            reasoning: Critic's reasoning.
            risk_notes: Optional risk notes for the Operator.

        Returns:
            The updated Plan, or None.
        """
        if verdict not in ("approved", "rejected", "revision_requested"):
            logger.warning(f"Invalid verdict: {verdict}")
            return None

        plan = self._get_and_validate_transition(plan_id, verdict)
        if not plan:
            return None

        plan.status = verdict
        plan.critic_verdict = verdict
        plan.critic_reasoning = reasoning
        plan.critic_risk_notes = risk_notes
        plan.reviewed_at = datetime.now(timezone.utc)

        if verdict == "revision_requested":
            plan.revision_count += 1

        self.db.add(plan)
        self.db.flush()

        # Phase 3D: Track rejected plans for counterfactual simulation
        if verdict == "rejected":
            self._track_rejection(plan)

        logger.info(f"Plan {plan_id}: verdict = {verdict}")
        return plan

    def assign_operator(self, plan_id: int, operator: Agent) -> Plan | None:
        """Assign an Operator to execute an approved plan.

        Args:
            plan_id: The plan.
            operator: The Operator agent.

        Returns:
            The updated Plan, or None if not approved.
        """
        plan = self._get_and_validate_transition(plan_id, "executing")
        if not plan:
            return None

        plan.status = "executing"
        plan.operator_agent_id = operator.id
        plan.operator_agent_name = operator.name
        self.db.add(plan)
        self.db.flush()
        logger.info(f"Operator {operator.name} executing plan {plan_id}")
        return plan

    def complete_plan(self, plan_id: int) -> Plan | None:
        """Mark a plan as completed.

        Args:
            plan_id: The plan.

        Returns:
            The updated Plan, or None.
        """
        plan = self._get_and_validate_transition(plan_id, "completed")
        if not plan:
            return None

        plan.status = "completed"
        plan.completed_at = datetime.now(timezone.utc)
        self.db.add(plan)
        self.db.flush()
        logger.info(f"Plan {plan_id} completed")
        return plan

    def resubmit_plan(self, plan_id: int) -> Plan | None:
        """Resubmit a plan after revision.

        Args:
            plan_id: The plan.

        Returns:
            The updated Plan, or None.
        """
        plan = self._get_and_validate_transition(plan_id, "submitted")
        if not plan:
            return None

        plan.status = "submitted"
        plan.submitted_at = datetime.now(timezone.utc)
        # Clear previous critic assignment for re-review
        plan.critic_agent_id = None
        plan.critic_agent_name = None
        plan.critic_verdict = None
        plan.critic_reasoning = None
        plan.critic_risk_notes = None
        plan.reviewed_at = None
        self.db.add(plan)
        self.db.flush()
        logger.info(f"Plan {plan_id} resubmitted (revision #{plan.revision_count})")
        return plan

    def get_plans_for_review(self, limit: int = 10) -> list[Plan]:
        """Get plans awaiting Critic review.

        Returns:
            List of submitted plans, oldest first.
        """
        return (
            self.db.query(Plan)
            .filter(Plan.status == "submitted")
            .order_by(Plan.submitted_at)
            .limit(limit)
            .all()
        )

    def get_approved_plans(self, limit: int = 10) -> list[Plan]:
        """Get approved plans awaiting Operator execution.

        Returns:
            List of approved plans.
        """
        return (
            self.db.query(Plan)
            .filter(Plan.status == "approved")
            .order_by(Plan.reviewed_at)
            .limit(limit)
            .all()
        )

    def get_by_strategist(self, strategist_id: int, limit: int = 20) -> list[Plan]:
        """Get plans created by a specific Strategist."""
        return (
            self.db.query(Plan)
            .filter(Plan.strategist_agent_id == strategist_id)
            .order_by(desc(Plan.created_at))
            .limit(limit)
            .all()
        )

    def get_active_plans_for_operator(self, operator_id: int) -> list[Plan]:
        """Get plans currently being executed by a specific Operator."""
        return (
            self.db.query(Plan)
            .filter(
                Plan.operator_agent_id == operator_id,
                Plan.status == "executing",
            )
            .all()
        )

    def format_for_context(self, plans: list[Plan], role: str = "strategist") -> str:
        """Format plans for inclusion in agent context.

        Args:
            plans: List of plans to format.
            role: The viewing role (affects detail level).

        Returns:
            Formatted string.
        """
        if not plans:
            return "No active plans."

        lines = ["=== PLANS ==="]
        for plan in plans:
            lines.append(
                f"  #{plan.id} [{plan.status}] {plan.plan_name} — "
                f"{plan.direction} {plan.market} ({plan.position_size_pct:.0%})"
            )
            if role == "critic" and plan.thesis:
                lines.append(f"    Thesis: {plan.thesis[:200]}")
            if plan.critic_verdict:
                lines.append(f"    Critic: {plan.critic_verdict} — {(plan.critic_reasoning or '')[:100]}")

        return "\n".join(lines)

    def _track_rejection(self, plan: Plan) -> None:
        """Track a rejected plan for counterfactual simulation."""
        try:
            from src.common.models import RejectionTracking
            from datetime import timedelta

            now = datetime.now(timezone.utc)

            # Parse timeframe
            timeframe_hours = {"1h": 1, "4h": 4, "1d": 24, "1w": 168}
            hours = timeframe_hours.get(plan.timeframe or "1d", 24)

            # Try to extract stop/TP from exit conditions
            stop_loss = take_profit = None
            try:
                import json
                exit_data = json.loads(plan.exit_conditions) if plan.exit_conditions else {}
                stop_loss = exit_data.get("stop_loss")
                take_profit = exit_data.get("take_profit")
            except Exception:
                pass

            tracking = RejectionTracking(
                plan_id=plan.id,
                critic_id=plan.critic_agent_id,
                market=plan.market,
                direction=plan.direction,
                entry_price=0.0,  # Will be populated by price cache if available
                stop_loss=stop_loss,
                take_profit=take_profit,
                timeframe=plan.timeframe or "1d",
                rejected_at=now,
                check_until=now + timedelta(hours=hours),
                status="tracking",
            )
            self.db.add(tracking)
            self.db.flush()
            logger.info(f"Rejection tracking created for plan {plan.id}")
        except Exception as e:
            logger.warning(f"Failed to track rejection for plan {plan.id}: {e}")

    def _get_and_validate_transition(
        self, plan_id: int, target_status: str
    ) -> Plan | None:
        """Get a plan and validate the status transition.

        Args:
            plan_id: The plan ID.
            target_status: The desired new status.

        Returns:
            The Plan if transition is valid, None otherwise.
        """
        plan = self.db.query(Plan).filter(Plan.id == plan_id).first()
        if not plan:
            logger.warning(f"Plan {plan_id} not found")
            return None

        valid_targets = VALID_TRANSITIONS.get(plan.status, [])
        if target_status not in valid_targets:
            logger.warning(
                f"Invalid plan transition: {plan.status} → {target_status} "
                f"(plan {plan_id})"
            )
            return None

        return plan
