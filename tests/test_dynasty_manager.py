"""Tests for Dynasty Manager — Phase 3F."""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.common.models import Agent, Base, Dynasty, Lineage, SystemState
from src.dynasty.dynasty_manager import DynastyManager


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
        "capital_allocated": 100.0, "capital_current": 100.0,
        "cash_balance": 100.0, "reserved_cash": 0.0,
        "total_equity": 100.0, "realized_pnl": 0.0,
        "unrealized_pnl": 0.0, "total_fees_paid": 0.0,
        "position_count": 0, "cycle_count": 0,
        "total_true_pnl": 0.0, "total_gross_pnl": 0.0,
        "total_api_cost": 0.0, "evaluation_count": 0,
    }
    defaults.update(kwargs)
    agent = Agent(**defaults)
    session.add(agent)
    session.flush()
    return agent


# --- create_dynasty ---

@pytest.mark.asyncio
async def test_create_dynasty(db_session):
    """Dynasty should be created with correct founder info."""
    agent = _make_agent(db_session, name="Scout-Alpha", type="scout")
    mgr = DynastyManager()
    dynasty = await mgr.create_dynasty(db_session, agent)

    assert dynasty.id is not None
    assert dynasty.founder_id == agent.id
    assert dynasty.founder_name == "Scout-Alpha"
    assert dynasty.founder_role == "scout"
    assert dynasty.status == "active"
    assert dynasty.total_members == 1
    assert dynasty.living_members == 1
    assert dynasty.peak_members == 1
    assert agent.dynasty_id == dynasty.id


# --- record_birth ---

@pytest.mark.asyncio
async def test_record_birth_updates_dynasty(db_session):
    """Birth should increment member counts and update peak."""
    parent = _make_agent(db_session, name="Parent", generation=1)
    mgr = DynastyManager()
    dynasty = await mgr.create_dynasty(db_session, parent)

    offspring = _make_agent(db_session, name="Offspring", generation=2,
                            dynasty_id=dynasty.id, parent_id=parent.id)

    await mgr.record_birth(db_session, parent, offspring)

    assert dynasty.total_members == 2
    assert dynasty.living_members == 2
    assert dynasty.peak_members == 2
    assert dynasty.total_generations == 2


@pytest.mark.asyncio
async def test_record_birth_no_dynasty(db_session):
    """Should handle parent with no dynasty_id gracefully."""
    parent = _make_agent(db_session, name="Parent", dynasty_id=None)
    offspring = _make_agent(db_session, name="Offspring")

    mgr = DynastyManager()
    await mgr.record_birth(db_session, parent, offspring)
    # No crash — silent no-op


# --- record_death ---

@pytest.mark.asyncio
async def test_record_death_decrements_living(db_session):
    """Death should decrement living_members."""
    agent = _make_agent(db_session, name="Doomed")
    mgr = DynastyManager()
    dynasty = await mgr.create_dynasty(db_session, agent)

    # Add a second member so dynasty doesn't go extinct
    agent2 = _make_agent(db_session, name="Survivor", dynasty_id=dynasty.id)
    dynasty.total_members = 2
    dynasty.living_members = 2

    await mgr.record_death(db_session, agent)

    assert dynasty.living_members == 1
    assert dynasty.status == "active"


@pytest.mark.asyncio
async def test_record_death_causes_extinction(db_session):
    """When last member dies, dynasty should go extinct."""
    agent = _make_agent(db_session, name="LastOne")
    mgr = DynastyManager()
    dynasty = await mgr.create_dynasty(db_session, agent)

    assert dynasty.living_members == 1

    await mgr.record_death(db_session, agent)

    assert dynasty.living_members == 0
    assert dynasty.status == "extinct"
    assert dynasty.extinct_at is not None


# --- get_dynasty_concentration ---

@pytest.mark.asyncio
async def test_dynasty_concentration(db_session):
    """Should calculate dynasty share of total active agents."""
    mgr = DynastyManager()

    a1 = _make_agent(db_session, name="A1", status="active")
    dynasty = await mgr.create_dynasty(db_session, a1)

    a2 = _make_agent(db_session, name="A2", status="active", dynasty_id=dynasty.id)
    dynasty.living_members = 2

    a3 = _make_agent(db_session, name="A3", status="active")
    a4 = _make_agent(db_session, name="A4", status="active")

    # 2 out of 4 active = 50%
    concentration = await mgr.get_dynasty_concentration(db_session, dynasty.id)
    assert concentration == pytest.approx(0.5, abs=0.01)
