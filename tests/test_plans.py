"""Tests for the Plans Manager."""

__version__ = "0.8.0"

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.common.models import Agent, Base, Plan
from src.agents.plans import PlanManager


@pytest.fixture
def db_session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    session = factory()

    session.add(Agent(
        id=1, name="Strategist-Prime", type="strategist", status="active",
        generation=1, capital_allocated=100.0, capital_current=100.0,
        thinking_budget_daily=0.50, thinking_budget_used_today=0.0,
    ))
    session.add(Agent(
        id=2, name="Critic-One", type="critic", status="active",
        generation=1, capital_allocated=100.0, capital_current=100.0,
        thinking_budget_daily=0.50, thinking_budget_used_today=0.0,
    ))
    session.add(Agent(
        id=3, name="Operator-Genesis", type="operator", status="active",
        generation=1, capital_allocated=100.0, capital_current=100.0,
        thinking_budget_daily=0.50, thinking_budget_used_today=0.0,
    ))
    session.commit()
    yield session
    session.close()


def _create_plan(db_session):
    strat = db_session.query(Agent).get(1)
    mgr = PlanManager(db_session)
    return mgr.create_plan(
        strategist=strat,
        plan_name="SOL Long Breakout",
        market="SOL/USDT",
        direction="long",
        entry_conditions="Break above $150",
        exit_conditions="TP $165, SL $140",
        thesis="SOL showing strong momentum",
        position_size_pct=0.15,
        timeframe="4h",
    )


class TestCreatePlan:
    def test_creates_draft(self, db_session):
        plan = _create_plan(db_session)
        assert plan.id is not None
        assert plan.status == "draft"
        assert plan.strategist_agent_id == 1
        assert plan.market == "SOL/USDT"
        assert plan.direction == "long"

    def test_fields_set_correctly(self, db_session):
        plan = _create_plan(db_session)
        assert plan.plan_name == "SOL Long Breakout"
        assert plan.position_size_pct == 0.15
        assert plan.thesis == "SOL showing strong momentum"


class TestPlanLifecycle:
    def test_submit_for_review(self, db_session):
        plan = _create_plan(db_session)
        mgr = PlanManager(db_session)
        result = mgr.submit_for_review(plan.id)
        assert result is not None
        assert result.status == "submitted"
        assert result.submitted_at is not None

    def test_assign_critic(self, db_session):
        plan = _create_plan(db_session)
        mgr = PlanManager(db_session)
        mgr.submit_for_review(plan.id)
        critic = db_session.query(Agent).get(2)
        result = mgr.assign_critic(plan.id, critic)
        assert result is not None
        assert result.status == "under_review"
        assert result.critic_agent_id == 2

    def test_approve_plan(self, db_session):
        plan = _create_plan(db_session)
        mgr = PlanManager(db_session)
        mgr.submit_for_review(plan.id)
        critic = db_session.query(Agent).get(2)
        mgr.assign_critic(plan.id, critic)
        result = mgr.record_verdict(plan.id, "approved", "Good plan")
        assert result is not None
        assert result.status == "approved"
        assert result.critic_verdict == "approved"

    def test_reject_plan(self, db_session):
        plan = _create_plan(db_session)
        mgr = PlanManager(db_session)
        mgr.submit_for_review(plan.id)
        critic = db_session.query(Agent).get(2)
        mgr.assign_critic(plan.id, critic)
        result = mgr.record_verdict(plan.id, "rejected", "Too risky")
        assert result is not None
        assert result.status == "rejected"

    def test_request_revision(self, db_session):
        plan = _create_plan(db_session)
        mgr = PlanManager(db_session)
        mgr.submit_for_review(plan.id)
        critic = db_session.query(Agent).get(2)
        mgr.assign_critic(plan.id, critic)
        result = mgr.record_verdict(plan.id, "revision_requested", "Fix stop loss")
        assert result is not None
        assert result.status == "revision_requested"
        assert result.revision_count == 1

    def test_resubmit_after_revision(self, db_session):
        plan = _create_plan(db_session)
        mgr = PlanManager(db_session)
        mgr.submit_for_review(plan.id)
        critic = db_session.query(Agent).get(2)
        mgr.assign_critic(plan.id, critic)
        mgr.record_verdict(plan.id, "revision_requested", "Fix it")
        result = mgr.resubmit_plan(plan.id)
        assert result is not None
        assert result.status == "submitted"
        assert result.critic_agent_id is None  # Cleared for re-review

    def test_assign_operator(self, db_session):
        plan = _create_plan(db_session)
        mgr = PlanManager(db_session)
        mgr.submit_for_review(plan.id)
        critic = db_session.query(Agent).get(2)
        mgr.assign_critic(plan.id, critic)
        mgr.record_verdict(plan.id, "approved", "Good")
        operator = db_session.query(Agent).get(3)
        result = mgr.assign_operator(plan.id, operator)
        assert result is not None
        assert result.status == "executing"
        assert result.operator_agent_id == 3

    def test_complete_plan(self, db_session):
        plan = _create_plan(db_session)
        mgr = PlanManager(db_session)
        mgr.submit_for_review(plan.id)
        critic = db_session.query(Agent).get(2)
        mgr.assign_critic(plan.id, critic)
        mgr.record_verdict(plan.id, "approved", "Good")
        operator = db_session.query(Agent).get(3)
        mgr.assign_operator(plan.id, operator)
        result = mgr.complete_plan(plan.id)
        assert result is not None
        assert result.status == "completed"
        assert result.completed_at is not None


class TestInvalidTransitions:
    def test_cannot_submit_non_draft(self, db_session):
        plan = _create_plan(db_session)
        mgr = PlanManager(db_session)
        mgr.submit_for_review(plan.id)
        # Try to submit again
        result = mgr.submit_for_review(plan.id)
        assert result is None

    def test_cannot_approve_draft(self, db_session):
        plan = _create_plan(db_session)
        mgr = PlanManager(db_session)
        result = mgr.record_verdict(plan.id, "approved", "Too early")
        assert result is None

    def test_cannot_execute_unapproved(self, db_session):
        plan = _create_plan(db_session)
        mgr = PlanManager(db_session)
        operator = db_session.query(Agent).get(3)
        result = mgr.assign_operator(plan.id, operator)
        assert result is None

    def test_invalid_verdict(self, db_session):
        plan = _create_plan(db_session)
        mgr = PlanManager(db_session)
        result = mgr.record_verdict(plan.id, "maybe", "dunno")
        assert result is None


class TestQueries:
    def test_get_plans_for_review(self, db_session):
        plan = _create_plan(db_session)
        mgr = PlanManager(db_session)
        mgr.submit_for_review(plan.id)
        pending = mgr.get_plans_for_review()
        assert len(pending) == 1
        assert pending[0].id == plan.id

    def test_get_approved_plans(self, db_session):
        plan = _create_plan(db_session)
        mgr = PlanManager(db_session)
        mgr.submit_for_review(plan.id)
        critic = db_session.query(Agent).get(2)
        mgr.assign_critic(plan.id, critic)
        mgr.record_verdict(plan.id, "approved", "Good")
        approved = mgr.get_approved_plans()
        assert len(approved) == 1

    def test_format_for_context(self, db_session):
        plan = _create_plan(db_session)
        mgr = PlanManager(db_session)
        text = mgr.format_for_context([plan])
        assert "SOL Long Breakout" in text
        assert "PLANS" in text
