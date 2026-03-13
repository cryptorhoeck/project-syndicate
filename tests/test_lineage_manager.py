"""Tests for Lineage Manager — Phase 3F."""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.common.models import (
    Agent, Base, Dynasty, Evaluation, Lineage, SystemState,
)
from src.dynasty.lineage_manager import LineageManager


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


def _make_dynasty(session, founder):
    dynasty = Dynasty(
        founder_id=founder.id, founder_name=founder.name,
        founder_role=founder.type, dynasty_name=f"Dynasty {founder.name}",
        status="active", total_generations=1, total_members=1,
        living_members=1, peak_members=1,
    )
    session.add(dynasty)
    session.flush()
    founder.dynasty_id = dynasty.id
    session.add(founder)
    return dynasty


# --- create_lineage_record (fresh) ---

@pytest.mark.asyncio
async def test_create_fresh_lineage(db_session):
    """Creates a new lineage record for a Gen 1 agent."""
    agent = _make_agent(db_session, name="Scout-Alpha")
    mgr = LineageManager()
    lineage = await mgr.create_lineage_record(db_session, agent)

    assert lineage.agent_id == agent.id
    assert lineage.agent_name == "Scout-Alpha"
    assert lineage.generation == 1
    assert lineage.parent_id is None
    assert lineage.lineage_path == str(agent.id)


# --- create_lineage_record (offspring with parent) ---

@pytest.mark.asyncio
async def test_create_offspring_lineage(db_session):
    """Creates offspring lineage with parent chain and lineage_path."""
    parent = _make_agent(db_session, name="Parent", generation=1)
    dynasty = _make_dynasty(db_session, parent)

    # Create parent's lineage record first
    parent_lineage = Lineage(
        agent_id=parent.id, agent_name=parent.name,
        generation=1, lineage_path=str(parent.id),
        dynasty_id=dynasty.id,
    )
    db_session.add(parent_lineage)
    db_session.flush()

    offspring = _make_agent(
        db_session, name="Offspring", generation=2,
        parent_id=parent.id, dynasty_id=dynasty.id,
        api_temperature=0.55,
    )

    mgr = LineageManager()
    lineage = await mgr.create_lineage_record(
        db_session, offspring, parent=parent,
        mutations={"temp_adj": 0.02},
        founding_directive="What are you missing?",
    )

    assert lineage.parent_id == parent.id
    assert lineage.generation == 2
    assert lineage.lineage_path == f"{parent.id}/{offspring.id}"
    assert lineage.mutations_applied == {"temp_adj": 0.02}
    assert lineage.founding_directive == "What are you missing?"
    assert lineage.inherited_temperature == 0.55


# --- create_lineage_record (update existing) ---

@pytest.mark.asyncio
async def test_update_existing_lineage(db_session):
    """Should update an existing lineage record (boot sequence compat)."""
    agent = _make_agent(db_session, name="Scout-Alpha", dynasty_id=None)

    existing = Lineage(
        agent_id=agent.id, agent_name=agent.name,
        generation=1, lineage_path=str(agent.id),
    )
    db_session.add(existing)
    db_session.flush()

    mgr = LineageManager()
    updated = await mgr.create_lineage_record(
        db_session, agent,
        founding_directive="Explore something",
    )

    # Should be same record, not a new one
    assert updated.agent_id == existing.agent_id
    assert updated.founding_directive == "Explore something"


# --- record_death ---

@pytest.mark.asyncio
async def test_record_death(db_session):
    """Death should populate death fields on lineage record."""
    agent = _make_agent(
        db_session, name="Dying",
        created_at=datetime.now(timezone.utc) - timedelta(days=5),
        termination_reason="underperforming",
        composite_score=0.35,
        realized_pnl=-10.0,
        prestige_title="Apprentice",
    )
    lineage = Lineage(
        agent_id=agent.id, agent_name=agent.name,
        generation=1, lineage_path=str(agent.id),
    )
    db_session.add(lineage)
    db_session.flush()

    mgr = LineageManager()
    await mgr.record_death(db_session, agent)

    assert lineage.died_at is not None
    assert lineage.cause_of_death == "underperforming"
    assert lineage.lifespan_days is not None
    assert lineage.lifespan_days > 4.9
    assert lineage.final_composite == 0.35
    assert lineage.final_pnl == -10.0
    assert lineage.final_prestige == "Apprentice"


# --- get_family_tree ---

@pytest.mark.asyncio
async def test_get_family_tree(db_session):
    """Should build hierarchical tree structure."""
    founder = _make_agent(db_session, name="Founder", generation=1)
    dynasty = _make_dynasty(db_session, founder)

    child1 = _make_agent(db_session, name="Child-1", generation=2,
                         dynasty_id=dynasty.id, parent_id=founder.id)
    child2 = _make_agent(db_session, name="Child-2", generation=2,
                         dynasty_id=dynasty.id, parent_id=founder.id)

    for a in [founder, child1, child2]:
        db_session.add(Lineage(
            agent_id=a.id, agent_name=a.name,
            generation=a.generation,
            parent_id=a.parent_id,
            dynasty_id=dynasty.id,
            lineage_path=str(a.id),
        ))
    db_session.flush()

    mgr = LineageManager()
    tree = await mgr.get_family_tree(db_session, dynasty.id)

    assert len(tree) == 1  # One root
    assert tree[0]["agent_name"] == "Founder"
    assert len(tree[0]["children"]) == 2


# --- get_ancestors ---

@pytest.mark.asyncio
async def test_get_ancestors(db_session):
    """Should return ancestor chain up to specified depth."""
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

    mgr = LineageManager()
    ancestors = await mgr.get_ancestors(db_session, a3.id, depth=3)

    assert len(ancestors) == 2  # Gen2 and Gen1
    assert ancestors[0].agent_id == a2.id
    assert ancestors[1].agent_id == a1.id
