"""Tests for Relationship Manager — Phase 3E."""

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.common.models import (
    Agent, AgentRelationship, Base, SystemState,
)
from src.personality.relationship_manager import (
    RelationshipManager, _POSITIVE_WORDS, _NEGATIVE_WORDS,
)


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
        "name": "TestAgent", "type": "operator", "status": "active",
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


# --- Record interaction ---

@pytest.mark.asyncio
async def test_record_positive_interaction(db_session):
    """Recording a positive interaction creates relationship and updates trust."""
    agent = _make_agent(db_session, name="Op-1")
    target = _make_agent(db_session, name="Scout-1", type="scout")

    rm = RelationshipManager()
    rel = await rm.record_interaction(db_session, agent.id, target.id, "positive")

    assert rel.agent_id == agent.id
    assert rel.target_agent_id == target.id
    assert rel.interaction_count == 1
    assert rel.positive_outcomes == 1
    assert rel.negative_outcomes == 0
    assert rel.trust_score > 0.5  # Prior is 0.5, positive should lift it


@pytest.mark.asyncio
async def test_record_negative_interaction(db_session):
    """Recording a negative interaction decreases trust."""
    agent = _make_agent(db_session, name="Op-1")
    target = _make_agent(db_session, name="Scout-1", type="scout")

    rm = RelationshipManager()
    rel = await rm.record_interaction(db_session, agent.id, target.id, "negative")

    assert rel.interaction_count == 1
    assert rel.negative_outcomes == 1
    assert rel.trust_score < 0.5  # Negative should lower from prior


@pytest.mark.asyncio
async def test_multiple_interactions_accumulate(db_session):
    """Multiple interactions accumulate on the same relationship."""
    agent = _make_agent(db_session, name="Op-1")
    target = _make_agent(db_session, name="Scout-1", type="scout")

    rm = RelationshipManager()
    await rm.record_interaction(db_session, agent.id, target.id, "positive")
    await rm.record_interaction(db_session, agent.id, target.id, "positive")
    rel = await rm.record_interaction(db_session, agent.id, target.id, "negative")

    assert rel.interaction_count == 3
    assert rel.positive_outcomes == 2
    assert rel.negative_outcomes == 1


# --- Trust calculation ---

@pytest.mark.asyncio
async def test_trust_converges_with_evidence(db_session):
    """Trust should move toward 1.0 with consistent positive interactions."""
    agent = _make_agent(db_session, name="Op-1")
    target = _make_agent(db_session, name="Scout-1", type="scout")

    rm = RelationshipManager()
    rel = None
    for _ in range(10):
        rel = await rm.record_interaction(db_session, agent.id, target.id, "positive")

    assert rel.trust_score > 0.8


@pytest.mark.asyncio
async def test_trust_drops_with_negative_evidence(db_session):
    """Trust should drop with consistent negative interactions."""
    agent = _make_agent(db_session, name="Op-1")
    target = _make_agent(db_session, name="Scout-1", type="scout")

    rm = RelationshipManager()
    rel = None
    for _ in range(10):
        rel = await rm.record_interaction(db_session, agent.id, target.id, "negative")

    assert rel.trust_score < 0.2


# --- Self-note sentiment ---

@pytest.mark.asyncio
async def test_update_from_self_note_positive(db_session):
    """Self-note mentioning an agent with positive words creates positive relationship."""
    agent = _make_agent(db_session, name="Op-1")
    target = _make_agent(db_session, name="Scout-Alpha", type="scout")

    rm = RelationshipManager()
    note = "Scout-Alpha provided excellent and reliable signals this cycle."
    updated = await rm.update_from_self_note(db_session, agent.id, note)

    assert len(updated) == 1
    assert updated[0].target_agent_id == target.id
    assert updated[0].positive_outcomes == 1


@pytest.mark.asyncio
async def test_update_from_self_note_negative(db_session):
    """Self-note mentioning an agent with negative words creates negative relationship."""
    agent = _make_agent(db_session, name="Op-1")
    target = _make_agent(db_session, name="Scout-Beta", type="scout")

    rm = RelationshipManager()
    note = "Scout-Beta provided misleading and inaccurate intel that led to loss."
    updated = await rm.update_from_self_note(db_session, agent.id, note)

    assert len(updated) == 1
    assert updated[0].negative_outcomes == 1


@pytest.mark.asyncio
async def test_update_from_self_note_neutral_skipped(db_session):
    """Self-note with neutral mention of agent should not create relationship."""
    agent = _make_agent(db_session, name="Op-1")
    target = _make_agent(db_session, name="Scout-Gamma", type="scout")

    rm = RelationshipManager()
    note = "Noted that Scout-Gamma also monitors BTC."
    updated = await rm.update_from_self_note(db_session, agent.id, note)

    assert len(updated) == 0


# --- Archive dead agent relationships ---

@pytest.mark.asyncio
async def test_archive_dead_agent_relationships(db_session):
    """Archiving marks all relationships involving dead agent."""
    agent = _make_agent(db_session, name="Op-1")
    target = _make_agent(db_session, name="Op-2")
    other = _make_agent(db_session, name="Op-3")

    rm = RelationshipManager()
    # agent trusts target; other trusts target
    await rm.record_interaction(db_session, agent.id, target.id, "positive")
    await rm.record_interaction(db_session, other.id, target.id, "positive")
    db_session.flush()

    # Target dies
    await rm.archive_dead_agent_relationships(db_session, target.id)
    db_session.flush()

    # Check all relationships involving target are archived
    rels = db_session.query(AgentRelationship).filter(
        AgentRelationship.target_agent_id == target.id
    ).all()
    for rel in rels:
        assert rel.archived is True
        assert rel.archive_reason == "target_agent_terminated"


# --- Trust summary ---

@pytest.mark.asyncio
async def test_get_trust_summary(db_session):
    """Trust summary returns formatted relationships above min interactions."""
    agent = _make_agent(db_session, name="Op-1")
    target = _make_agent(db_session, name="Scout-1", type="scout")

    rm = RelationshipManager()
    # Record enough interactions to meet threshold
    for _ in range(3):
        await rm.record_interaction(db_session, agent.id, target.id, "positive")
    db_session.flush()

    summary = await rm.get_trust_summary(db_session, agent.id)

    assert len(summary) >= 1
    entry = summary[0]
    assert "agent_name" in entry
    assert "trust" in entry
    assert "status" in entry
    assert entry["status"] in ("trusted", "neutral", "distrusted")
