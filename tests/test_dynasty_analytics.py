"""Tests for Dynasty Analytics — Phase 3F."""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.common.models import (
    Agent, Base, Dynasty, Evaluation, Lineage, SystemState,
)
from src.dynasty.dynasty_analytics import DynastyAnalytics


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
        "name": "TestAgent", "type": "scout", "status": "active",
        "generation": 1,
        "capital_allocated": 100.0, "capital_current": 100.0,
        "cash_balance": 100.0, "reserved_cash": 0.0,
        "total_equity": 100.0, "realized_pnl": 10.0,
        "unrealized_pnl": 0.0, "total_fees_paid": 0.0,
        "position_count": 0, "cycle_count": 20,
        "total_true_pnl": 10.0, "total_gross_pnl": 10.0,
        "total_api_cost": 0.0, "evaluation_count": 5,
        "composite_score": 0.6,
    }
    defaults.update(kwargs)
    agent = Agent(**defaults)
    session.add(agent)
    session.flush()
    return agent


def _make_dynasty(session, founder):
    dynasty = Dynasty(
        founder_id=founder.id, founder_name=founder.name,
        founder_role=founder.type, dynasty_name=f"Dynasty {founder.name}",
        status="active", total_generations=1, total_members=1,
        living_members=1, peak_members=1, total_pnl=10.0,
    )
    session.add(dynasty)
    session.flush()
    founder.dynasty_id = dynasty.id
    session.add(founder)
    return dynasty


# --- dynasty_performance ---

@pytest.mark.asyncio
async def test_dynasty_performance_report(db_session):
    """Should return a report for an existing dynasty."""
    founder = _make_agent(db_session, name="Founder")
    dynasty = _make_dynasty(db_session, founder)

    analytics = DynastyAnalytics()
    report = await analytics.dynasty_performance(db_session, dynasty.id)

    assert report is not None
    assert report.dynasty_name == "Dynasty Founder"
    assert report.total_pnl == 10.0
    assert report.total_members == 1


@pytest.mark.asyncio
async def test_dynasty_performance_not_found(db_session):
    """Should return None for nonexistent dynasty."""
    analytics = DynastyAnalytics()
    report = await analytics.dynasty_performance(db_session, 9999)
    assert report is None


# --- generational_improvement ---

@pytest.mark.asyncio
async def test_generational_improvement_positive(db_session):
    """Offspring with higher composite should show positive improvement."""
    parent = _make_agent(db_session, name="Parent", generation=1,
                         composite_score=0.50)
    dynasty = _make_dynasty(db_session, parent)

    # Parent's evaluation
    db_session.add(Evaluation(
        agent_id=parent.id, agent_name=parent.name, agent_role=parent.type,
        evaluation_type="survival_check", composite_score=0.50,
    ))

    offspring = _make_agent(db_session, name="Offspring", generation=2,
                            parent_id=parent.id, dynasty_id=dynasty.id,
                            composite_score=0.65)

    # Offspring's evaluation
    db_session.add(Evaluation(
        agent_id=offspring.id, agent_name=offspring.name,
        agent_role=offspring.type, evaluation_type="survival_check",
        composite_score=0.65,
    ))

    # Lineage record with parent composite
    db_session.add(Lineage(
        agent_id=offspring.id, agent_name=offspring.name,
        parent_id=parent.id, generation=2,
        dynasty_id=dynasty.id, lineage_path=f"{parent.id}/{offspring.id}",
        parent_composite_at_reproduction=0.50,
    ))
    db_session.flush()

    analytics = DynastyAnalytics()
    improvement = await analytics.generational_improvement(db_session, dynasty.id)

    # (0.65 - 0.50) / 0.50 = 0.30
    assert improvement == pytest.approx(0.30, abs=0.05)


# --- lineage_knowledge_depth ---

@pytest.mark.asyncio
async def test_lineage_knowledge_depth(db_session):
    """Should count generations of knowledge."""
    a1 = _make_agent(db_session, name="Gen1", generation=1)
    a2 = _make_agent(db_session, name="Gen2", generation=2, parent_id=a1.id)
    a3 = _make_agent(db_session, name="Gen3", generation=3, parent_id=a2.id)

    for a, pid in [(a1, None), (a2, a1.id), (a3, a2.id)]:
        db_session.add(Lineage(
            agent_id=a.id, agent_name=a.name,
            generation=a.generation, parent_id=pid,
            lineage_path=str(a.id),
        ))
    db_session.flush()

    analytics = DynastyAnalytics()
    depth = await analytics.lineage_knowledge_depth(db_session, a3.id)

    assert depth == 3  # self + parent + grandparent
