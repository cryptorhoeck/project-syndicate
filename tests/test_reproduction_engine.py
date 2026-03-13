"""Tests for Reproduction Engine — Phase 3F."""

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.common.models import (
    Agent, AgentLongTermMemory, AgentRelationship, Base, Dynasty, Lineage,
    SystemState,
)
from src.dynasty.dynasty_manager import DynastyManager
from src.dynasty.lineage_manager import LineageManager
from src.dynasty.reproduction import (
    ReproductionDecision, ReproductionEngine, ReproductionResult,
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
        "name": "Parent", "type": "scout", "status": "active",
        "generation": 1,
        "capital_allocated": 100.0, "capital_current": 120.0,
        "cash_balance": 120.0, "reserved_cash": 0.0,
        "total_equity": 120.0,
        "realized_pnl": 20.0, "unrealized_pnl": 0.0,
        "total_fees_paid": 2.0,
        "position_count": 0, "cycle_count": 50,
        "total_true_pnl": 18.0, "total_gross_pnl": 20.0,
        "total_api_cost": 2.0, "evaluation_count": 12,
        "composite_score": 0.65,
        "prestige_title": "Expert",
        "api_temperature": 0.55,
        "thinking_budget_daily": 0.50,
        "watched_markets": ["BTC/USDT", "ETH/USDT"],
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
    # Lineage record for parent
    session.add(Lineage(
        agent_id=founder.id, agent_name=founder.name,
        generation=1, lineage_path=str(founder.id),
        dynasty_id=dynasty.id,
    ))
    session.flush()
    return dynasty


# ============================================================
# Eligibility tests
# ============================================================

def test_no_candidates_when_prestige_too_low(db_session):
    """Agents below Expert prestige should not be eligible."""
    _make_agent(db_session, name="Noob", prestige_title="Apprentice")
    engine = ReproductionEngine()
    candidates = engine._get_eligible_candidates(db_session)
    assert len(candidates) == 0


def test_candidate_eligible_with_expert_prestige(db_session):
    """Expert prestige + top 50% composite + positive P&L = eligible."""
    parent = _make_agent(db_session)
    _make_dynasty(db_session, parent)
    engine = ReproductionEngine()
    candidates = engine._get_eligible_candidates(db_session)
    assert len(candidates) >= 1
    assert candidates[0].id == parent.id


def test_no_candidates_when_negative_pnl(db_session):
    """Agents with negative P&L should not be eligible."""
    _make_agent(db_session, realized_pnl=-10.0, unrealized_pnl=-5.0)
    engine = ReproductionEngine()
    candidates = engine._get_eligible_candidates(db_session)
    assert len(candidates) == 0


def test_no_candidates_on_cooldown(db_session):
    """Agents on cooldown should not be eligible."""
    # SQLite stores naive datetimes; the code compares with timezone-aware now()
    # so we use a naive future datetime here for SQLite compatibility
    _make_agent(
        db_session,
        reproduction_cooldown_until=datetime.utcnow() + timedelta(days=7),
    )
    engine = ReproductionEngine()
    candidates = engine._get_eligible_candidates(db_session)
    assert len(candidates) == 0


def test_candidate_past_cooldown_eligible(db_session):
    """Agents past cooldown should be eligible."""
    parent = _make_agent(
        db_session,
        reproduction_cooldown_until=datetime.now(timezone.utc) - timedelta(hours=1),
    )
    _make_dynasty(db_session, parent)
    engine = ReproductionEngine()
    candidates = engine._get_eligible_candidates(db_session)
    assert len(candidates) >= 1


# ============================================================
# Concentration tests
# ============================================================

@pytest.mark.asyncio
async def test_concentration_blocks_above_hard_limit(db_session):
    """Should block reproduction above dynasty_concentration_hard_limit."""
    parent = _make_agent(db_session, name="BigDynasty")
    dynasty = _make_dynasty(db_session, parent)
    # Make this dynasty the only active agents (100% concentration)

    engine = ReproductionEngine()
    blocked, conc, warning = await engine._check_concentration(db_session, parent)

    # 1 agent / 1 total = 100%, above 40% limit
    assert blocked is True
    assert conc > 0.40


@pytest.mark.asyncio
async def test_concentration_warning_above_threshold(db_session):
    """Should return warning above dynasty_concentration_warning."""
    parent = _make_agent(db_session, name="Parent")
    dynasty = _make_dynasty(db_session, parent)
    # Add other agents to dilute below hard limit but above warning
    for i in range(2):
        _make_agent(db_session, name=f"Other-{i}", status="active")

    engine = ReproductionEngine()
    blocked, conc, warning = await engine._check_concentration(db_session, parent)

    # 1/3 ≈ 33% → above 25% warning, below 40% hard limit
    assert blocked is False
    assert warning is not None
    assert "Dynasty" in warning


@pytest.mark.asyncio
async def test_concentration_ok_when_diluted(db_session):
    """No block or warning when dynasty is small share of ecosystem."""
    parent = _make_agent(db_session, name="Parent")
    dynasty = _make_dynasty(db_session, parent)
    # Add enough agents to dilute to < 25%
    for i in range(5):
        _make_agent(db_session, name=f"Other-{i}", status="active")

    engine = ReproductionEngine()
    blocked, conc, warning = await engine._check_concentration(db_session, parent)

    # 1/6 ≈ 16.7% → below 25% warning
    assert blocked is False
    assert warning is None


# ============================================================
# Memory inheritance tests
# ============================================================

@pytest.mark.asyncio
async def test_memory_inheritance_discount(db_session):
    """Inherited memories should have 75% confidence discount."""
    parent = _make_agent(db_session, name="MemParent")
    offspring = _make_agent(db_session, name="MemChild", generation=2, parent_id=parent.id)

    # Parent memory with confidence 1.0
    mem = AgentLongTermMemory(
        agent_id=parent.id, memory_type="market_lesson",
        content="BTC retraces after 20% rally",
        confidence=1.0, source="self", is_active=True,
        created_at=datetime.now(timezone.utc) - timedelta(days=5),
    )
    db_session.add(mem)
    db_session.flush()

    engine = ReproductionEngine()
    count = await engine._transfer_memories(db_session, parent.id, offspring.id)

    assert count == 1

    # Check offspring memory
    inherited = db_session.execute(
        AgentLongTermMemory.__table__.select().where(
            AgentLongTermMemory.agent_id == offspring.id,
        )
    ).fetchall()
    assert len(inherited) == 1
    # 1.0 * 0.75 = 0.75 (no age decay — only 5 days old)
    assert inherited[0].confidence == pytest.approx(0.75, abs=0.01)
    assert inherited[0].source == "parent"


@pytest.mark.asyncio
async def test_memory_age_decay(db_session):
    """Memories older than 30 days should have additional age decay."""
    parent = _make_agent(db_session, name="OldMemParent")
    offspring = _make_agent(db_session, name="OldMemChild", generation=2, parent_id=parent.id)

    # Old memory (60 days old → 30 days of decay at 0.95^30)
    mem = AgentLongTermMemory(
        agent_id=parent.id, memory_type="market_lesson",
        content="Old wisdom",
        confidence=1.0, source="self", is_active=True,
        created_at=datetime.now(timezone.utc) - timedelta(days=60),
    )
    db_session.add(mem)
    db_session.flush()

    engine = ReproductionEngine()
    await engine._transfer_memories(db_session, parent.id, offspring.id)

    inherited = db_session.execute(
        AgentLongTermMemory.__table__.select().where(
            AgentLongTermMemory.agent_id == offspring.id,
        )
    ).fetchall()
    assert len(inherited) == 1
    # 1.0 * 0.75 * 0.95^30 ≈ 0.75 * 0.2146 ≈ 0.161
    assert inherited[0].confidence < 0.75
    assert inherited[0].confidence >= 0.10  # above floor


@pytest.mark.asyncio
async def test_memory_confidence_floor(db_session):
    """Inherited memory confidence should never go below the floor (0.10)."""
    parent = _make_agent(db_session, name="VeryOldParent")
    offspring = _make_agent(db_session, name="VeryOldChild", generation=2, parent_id=parent.id)

    # Very old memory with low confidence
    mem = AgentLongTermMemory(
        agent_id=parent.id, memory_type="market_lesson",
        content="Ancient wisdom",
        confidence=0.2, source="grandparent", is_active=True,
        created_at=datetime.now(timezone.utc) - timedelta(days=200),
    )
    db_session.add(mem)
    db_session.flush()

    engine = ReproductionEngine()
    await engine._transfer_memories(db_session, parent.id, offspring.id)

    inherited = db_session.execute(
        AgentLongTermMemory.__table__.select().where(
            AgentLongTermMemory.agent_id == offspring.id,
        )
    ).fetchall()
    assert len(inherited) == 1
    assert inherited[0].confidence == pytest.approx(0.10)
    # Source should be "grandparent" since parent's source was "grandparent"
    assert inherited[0].source == "grandparent"


# ============================================================
# Trust inheritance tests
# ============================================================

@pytest.mark.asyncio
async def test_trust_inheritance_blended(db_session):
    """Inherited trust should be 50% blend with neutral prior."""
    parent = _make_agent(db_session, name="TrustParent")
    offspring = _make_agent(db_session, name="TrustChild", generation=2, parent_id=parent.id)
    other = _make_agent(db_session, name="OtherAgent")

    rel = AgentRelationship(
        agent_id=parent.id, target_agent_id=other.id,
        target_agent_name="OtherAgent",
        trust_score=0.9, interaction_count=10,
        positive_outcomes=8, negative_outcomes=2,
    )
    db_session.add(rel)
    db_session.flush()

    engine = ReproductionEngine()
    count = await engine._transfer_relationships(db_session, parent.id, offspring.id)

    assert count == 1

    offspring_rels = db_session.execute(
        AgentRelationship.__table__.select().where(
            AgentRelationship.agent_id == offspring.id,
        )
    ).fetchall()
    assert len(offspring_rels) == 1
    # 0.9 * 0.5 + 0.5 * 0.5 = 0.45 + 0.25 = 0.70
    assert offspring_rels[0].trust_score == pytest.approx(0.70, abs=0.01)
    assert offspring_rels[0].interaction_count == 0


# ============================================================
# Build offspring tests
# ============================================================

@pytest.mark.asyncio
async def test_build_offspring_basic(db_session):
    """Should create offspring with correct parentage and generation."""
    parent = _make_agent(db_session, name="BuildParent")
    dynasty = _make_dynasty(db_session, parent)

    decision = ReproductionDecision(
        should_reproduce=True,
        offspring_name="BuildParent-II",
        mutations={
            "watchlist_changes": {"add": ["SOL/USDT"], "remove": []},
            "temperature_adjustment": 0.02,
            "founding_directive": "What emerging markets show promise?",
        },
    )

    engine = ReproductionEngine()
    offspring = await engine._build_offspring(db_session, parent, decision)

    assert offspring.id is not None
    assert offspring.name == "BuildParent-II"
    assert offspring.type == parent.type
    assert offspring.generation == 2
    assert offspring.parent_id == parent.id
    assert offspring.dynasty_id == dynasty.id
    assert offspring.founding_directive == "What emerging markets show promise?"
    assert offspring.founding_directive_consumed is False
    assert "SOL/USDT" in offspring.watched_markets


@pytest.mark.asyncio
async def test_build_offspring_temperature_mutation(db_session):
    """Offspring temperature should be mutated from parent's."""
    parent = _make_agent(db_session, name="TempParent", api_temperature=0.55)
    _make_dynasty(db_session, parent)

    decision = ReproductionDecision(
        should_reproduce=True,
        offspring_name="TempChild",
        mutations={"temperature_adjustment": 0.02},
    )

    engine = ReproductionEngine()
    offspring = await engine._build_offspring(db_session, parent, decision)

    # 0.55 + 0.02 = 0.57
    assert offspring.api_temperature == pytest.approx(0.57, abs=0.001)


@pytest.mark.asyncio
async def test_build_offspring_temperature_clamped(db_session):
    """Offspring temperature should be clamped to role bounds."""
    # Scout bounds: [0.3, 0.9]
    parent = _make_agent(db_session, name="HotScout", type="scout",
                         api_temperature=0.89)
    _make_dynasty(db_session, parent)

    decision = ReproductionDecision(
        should_reproduce=True,
        offspring_name="HotChild",
        mutations={"temperature_adjustment": 0.05},  # would go to 0.94
    )

    engine = ReproductionEngine()
    offspring = await engine._build_offspring(db_session, parent, decision)

    assert offspring.api_temperature <= 0.9


@pytest.mark.asyncio
async def test_build_offspring_posthumous(db_session):
    """Posthumous birth should set the flag correctly."""
    parent = _make_agent(db_session, name="DeadParent", status="terminated")
    _make_dynasty(db_session, parent)

    decision = ReproductionDecision(
        should_reproduce=True,
        offspring_name="Orphan",
        mutations={},
    )

    engine = ReproductionEngine()
    offspring = await engine._build_offspring(
        db_session, parent, decision, posthumous=True,
    )

    assert offspring.posthumous_birth is True


@pytest.mark.asyncio
async def test_build_offspring_updates_parent_cooldown(db_session):
    """Parent should get reproduction cooldown after spawning offspring."""
    parent = _make_agent(db_session, name="CooldownParent")
    _make_dynasty(db_session, parent)

    decision = ReproductionDecision(
        should_reproduce=True,
        offspring_name="CooldownChild",
        mutations={},
    )

    engine = ReproductionEngine()
    await engine._build_offspring(db_session, parent, decision)

    assert parent.offspring_count == 1
    assert parent.last_reproduction_at is not None
    assert parent.reproduction_cooldown_until is not None
    assert parent.reproduction_cooldown_until > datetime.now(timezone.utc)


# ============================================================
# Full cycle tests (mocking Genesis API)
# ============================================================

@pytest.mark.asyncio
async def test_check_and_reproduce_system_alert_blocks(db_session):
    """No reproduction during system alerts."""
    from sqlalchemy import select
    state = db_session.execute(select(SystemState)).scalar_one()
    state.alert_status = "red"
    db_session.add(state)
    db_session.flush()

    engine = ReproductionEngine()
    result = await engine.check_and_reproduce(db_session)

    assert result.reproduced is False
    assert result.reason == "system_in_alert"


@pytest.mark.asyncio
async def test_check_and_reproduce_no_candidates(db_session):
    """No reproduction when no agents meet criteria."""
    # No agents at all
    engine = ReproductionEngine()
    result = await engine.check_and_reproduce(db_session)

    assert result.reproduced is False
    assert result.reason == "no_eligible_candidates"
