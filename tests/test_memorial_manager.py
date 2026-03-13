"""Tests for Memorial Manager — Phase 3F."""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.common.models import (
    Agent, Base, Dynasty, Evaluation, Memorial, SystemState,
)
from src.dynasty.memorial_manager import MemorialManager


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

    state = SystemState(
        total_treasury=1000.0, peak_treasury=1000.0,
        current_regime="bull", alert_status="green",
    )
    session.add(state)
    session.flush()
    yield session
    session.close()


def _make_agent(session, **kwargs):
    defaults = {
        "name": "Fallen-One", "type": "operator", "status": "terminated",
        "generation": 1,
        "capital_allocated": 100.0, "capital_current": 50.0,
        "cash_balance": 50.0, "reserved_cash": 0.0,
        "total_equity": 50.0,
        "realized_pnl": -30.0, "unrealized_pnl": -20.0,
        "total_fees_paid": 5.0,
        "position_count": 0, "cycle_count": 42,
        "total_true_pnl": -50.0, "total_gross_pnl": -45.0,
        "total_api_cost": 5.0, "evaluation_count": 4,
        "termination_reason": "underperforming",
        "prestige_title": "Journeyman",
        "created_at": datetime.now(timezone.utc) - timedelta(days=10),
    }
    defaults.update(kwargs)
    agent = Agent(**defaults)
    session.add(agent)
    session.flush()
    return agent


# --- create_memorial basic ---

@pytest.mark.asyncio
async def test_create_memorial_basic(db_session):
    """Should create a memorial with correct fields."""
    agent = _make_agent(db_session)

    # Add dynasty
    dynasty = Dynasty(
        founder_id=agent.id, founder_name=agent.name,
        founder_role=agent.type, dynasty_name="Dynasty Fallen-One",
        status="active", total_generations=1, total_members=1,
        living_members=0, peak_members=1,
    )
    db_session.add(dynasty)
    db_session.flush()
    agent.dynasty_id = dynasty.id

    mgr = MemorialManager()
    memorial = await mgr.create_memorial(db_session, agent)

    assert memorial.id is not None
    assert memorial.agent_id == agent.id
    assert memorial.agent_name == "Fallen-One"
    assert memorial.agent_role == "operator"
    assert memorial.dynasty_name == "Dynasty Fallen-One"
    assert memorial.generation == 1
    assert memorial.lifespan_days > 9.9
    assert memorial.cause_of_death == "underperforming"
    assert memorial.total_cycles == 42
    assert memorial.final_prestige == "Journeyman"
    assert memorial.final_pnl == -50.0


# --- create_memorial with evaluation metrics ---

@pytest.mark.asyncio
async def test_memorial_with_evaluation_metrics(db_session):
    """Should extract best/worst metrics from evaluation breakdown."""
    agent = _make_agent(db_session)

    evaluation = Evaluation(
        agent_id=agent.id, agent_name=agent.name, agent_role=agent.type,
        evaluation_type="survival_check",
        composite_score=0.35,
        genesis_decision="terminate",
        metric_breakdown={
            "sharpe": {"raw": -0.5, "normalized": 0.15},
            "true_pnl": {"raw": -10.0, "normalized": 0.1},
            "consistency": {"raw": 0.8, "normalized": 0.85},
        },
    )
    db_session.add(evaluation)
    db_session.flush()

    mgr = MemorialManager()
    memorial = await mgr.create_memorial(db_session, agent, evaluation=evaluation)

    assert memorial.best_metric_name == "consistency"
    assert memorial.best_metric_value == pytest.approx(0.85)
    assert memorial.worst_metric_name == "true_pnl"
    assert memorial.worst_metric_value == pytest.approx(0.1)
