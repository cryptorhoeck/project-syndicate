"""Tests for the Budget Gate module."""

__version__ = "0.7.0"

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.common.models import Agent, AgentCycle, Base
from src.agents.budget_gate import BudgetGate, BudgetStatus


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

    # Seed an agent
    session.add(Agent(
        id=1, name="Scout-Alpha", type="scout", status="active",
        thinking_budget_daily=0.50, thinking_budget_used_today=0.0,
        generation=1,
    ))
    session.commit()
    yield session
    session.close()


class TestBudgetGateNormal:
    def test_normal_when_budget_healthy(self, db_session):
        agent = db_session.query(Agent).get(1)
        gate = BudgetGate(db_session)
        result = gate.check(agent)
        assert result.status == BudgetStatus.NORMAL
        assert result.remaining_budget == 0.50
        assert result.reason == "ok"

    def test_normal_with_some_usage(self, db_session):
        agent = db_session.query(Agent).get(1)
        agent.thinking_budget_used_today = 0.10
        db_session.add(agent)
        db_session.commit()

        gate = BudgetGate(db_session)
        result = gate.check(agent)
        assert result.status == BudgetStatus.NORMAL
        assert result.remaining_budget == pytest.approx(0.40, abs=0.01)


class TestBudgetGateSurvival:
    def test_survival_when_budget_low(self, db_session):
        agent = db_session.query(Agent).get(1)
        # Default estimate is 0.005, survival < 3x = 0.015
        agent.thinking_budget_used_today = 0.49  # remaining = 0.01
        db_session.add(agent)
        db_session.commit()

        gate = BudgetGate(db_session)
        result = gate.check(agent)
        assert result.status == BudgetStatus.SURVIVAL_MODE
        assert result.reason == "budget_low"


class TestBudgetGateSkip:
    def test_skip_when_budget_exhausted(self, db_session):
        agent = db_session.query(Agent).get(1)
        agent.thinking_budget_used_today = 0.50  # remaining = 0.0
        db_session.add(agent)
        db_session.commit()

        gate = BudgetGate(db_session)
        result = gate.check(agent)
        assert result.status == BudgetStatus.SKIP_CYCLE
        assert result.reason == "budget_exhausted"

    def test_skip_when_remaining_less_than_estimate(self, db_session):
        agent = db_session.query(Agent).get(1)
        agent.thinking_budget_used_today = 0.498  # remaining = 0.002 < default 0.005
        db_session.add(agent)
        db_session.commit()

        gate = BudgetGate(db_session)
        result = gate.check(agent)
        assert result.status == BudgetStatus.SKIP_CYCLE


class TestRollingAverage:
    def test_rolling_avg_from_cycle_history(self, db_session):
        agent = db_session.query(Agent).get(1)

        # Add 5 cycles with known costs
        for i in range(5):
            db_session.add(AgentCycle(
                agent_id=1, cycle_number=i, cycle_type="normal",
                context_mode="normal", api_cost_usd=0.01,
            ))
        db_session.commit()

        gate = BudgetGate(db_session)
        avg = gate._get_rolling_avg_cost(1)
        assert avg == pytest.approx(0.01, abs=0.001)

    def test_rolling_avg_default_when_no_history(self, db_session):
        gate = BudgetGate(db_session)
        avg = gate._get_rolling_avg_cost(1)
        assert avg == BudgetGate.DEFAULT_ESTIMATED_COST
