"""Integration tests for Death Protocol — Phase 3F.

Tests the full death sequence through the evaluation engine:
lineage death, dynasty death, memorial creation, dynasty P&L update.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.common.models import (
    Agent, Base, Dynasty, Evaluation, Lineage, Memorial, SystemState,
)
from src.dynasty.dynasty_manager import DynastyManager
from src.dynasty.lineage_manager import LineageManager
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
        "name": "Doomed", "type": "operator", "status": "active",
        "generation": 1,
        "capital_allocated": 100.0, "capital_current": 50.0,
        "cash_balance": 50.0, "reserved_cash": 0.0,
        "total_equity": 50.0,
        "realized_pnl": -30.0, "unrealized_pnl": -20.0,
        "total_fees_paid": 5.0,
        "position_count": 0, "cycle_count": 30,
        "total_true_pnl": -50.0, "total_gross_pnl": -45.0,
        "total_api_cost": 5.0, "evaluation_count": 3,
        "composite_score": 0.2,
        "prestige_title": "Apprentice",
        "termination_reason": "underperforming",
        "created_at": datetime.now(timezone.utc) - timedelta(days=7),
    }
    defaults.update(kwargs)
    agent = Agent(**defaults)
    session.add(agent)
    session.flush()
    return agent


# --- Full death sequence through individual managers ---

@pytest.mark.asyncio
async def test_death_sequence_lineage_dynasty_memorial(db_session):
    """Full death sequence: lineage, dynasty, memorial all updated."""
    agent = _make_agent(db_session)

    # Set up dynasty
    dynasty = Dynasty(
        founder_id=agent.id, founder_name=agent.name,
        founder_role=agent.type, dynasty_name="Dynasty Doomed",
        status="active", total_generations=1, total_members=1,
        living_members=1, peak_members=1,
        founded_at=datetime.now(timezone.utc) - timedelta(days=7),
    )
    db_session.add(dynasty)
    db_session.flush()
    agent.dynasty_id = dynasty.id

    # Lineage record
    lineage = Lineage(
        agent_id=agent.id, agent_name=agent.name,
        generation=1, lineage_path=str(agent.id),
        dynasty_id=dynasty.id,
    )
    db_session.add(lineage)
    db_session.flush()

    # Evaluation that triggered death
    evaluation = Evaluation(
        agent_id=agent.id, agent_name=agent.name, agent_role=agent.type,
        evaluation_type="survival_check",
        composite_score=0.2,
        genesis_decision="terminate",
        metric_breakdown={
            "sharpe": {"normalized": 0.1},
            "true_pnl": {"normalized": 0.05},
            "consistency": {"normalized": 0.3},
        },
    )
    db_session.add(evaluation)
    db_session.flush()

    # Execute death sequence (same order as evaluation_engine._terminate_agent)
    lineage_mgr = LineageManager()
    dynasty_mgr = DynastyManager()
    memorial_mgr = MemorialManager()

    await lineage_mgr.record_death(db_session, agent, evaluation)
    await dynasty_mgr.record_death(db_session, agent)
    await memorial_mgr.create_memorial(db_session, agent, evaluation)
    await dynasty_mgr.update_dynasty_pnl(db_session, dynasty.id)

    # Verify lineage
    assert lineage.died_at is not None
    assert lineage.cause_of_death == "underperforming"
    assert lineage.lifespan_days is not None
    assert lineage.final_composite == 0.2
    assert lineage.final_pnl == -50.0

    # Verify dynasty
    assert dynasty.living_members == 0
    assert dynasty.status == "extinct"
    assert dynasty.extinct_at is not None
    assert dynasty.total_pnl == -50.0

    # Verify memorial
    memorials = db_session.query(Memorial).all()
    assert len(memorials) == 1
    mem = memorials[0]
    assert mem.agent_name == "Doomed"
    assert mem.dynasty_name == "Dynasty Doomed"
    assert mem.cause_of_death == "underperforming"
    assert mem.final_pnl == -50.0


# --- Dynasty survives when other members alive ---

@pytest.mark.asyncio
async def test_death_does_not_extinct_dynasty_with_survivors(db_session):
    """Dynasty should survive when other members are alive."""
    agent1 = _make_agent(db_session, name="Doomed-1")
    agent2 = _make_agent(db_session, name="Survivor", status="active",
                         realized_pnl=10.0, unrealized_pnl=0.0)

    dynasty = Dynasty(
        founder_id=agent1.id, founder_name=agent1.name,
        founder_role=agent1.type, dynasty_name="Dynasty Shared",
        status="active", total_generations=1, total_members=2,
        living_members=2, peak_members=2,
        founded_at=datetime.now(timezone.utc) - timedelta(days=7),
    )
    db_session.add(dynasty)
    db_session.flush()
    agent1.dynasty_id = dynasty.id
    agent2.dynasty_id = dynasty.id

    dynasty_mgr = DynastyManager()
    await dynasty_mgr.record_death(db_session, agent1)

    assert dynasty.living_members == 1
    assert dynasty.status == "active"


# --- Memorial notable achievement ---

@pytest.mark.asyncio
async def test_memorial_notable_achievement(db_session):
    """Memorial should capture notable achievement."""
    agent = _make_agent(
        db_session, name="Achiever",
        prestige_title="Expert",
        evaluation_count=12,
        realized_pnl=50.0, unrealized_pnl=10.0,
        offspring_count=2,
    )

    memorial_mgr = MemorialManager()
    memorial = await memorial_mgr.create_memorial(db_session, agent)

    assert memorial.notable_achievement is not None
    assert "Expert" in memorial.notable_achievement
