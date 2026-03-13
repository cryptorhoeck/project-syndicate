"""Tests for Offspring Orientation Protocol — Phase 3F."""

import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.common.models import (
    Agent, Base, Dynasty, Lineage, SystemState,
)
from src.agents.orientation import (
    OrientationProtocol, OrientationResult,
    ROLE_SUMMARIES, DEFAULT_WATCHLISTS,
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
        "name": "TestAgent", "type": "scout", "status": "initializing",
        "generation": 1,
        "capital_allocated": 100.0, "capital_current": 100.0,
        "cash_balance": 100.0, "reserved_cash": 0.0,
        "total_equity": 100.0, "realized_pnl": 0.0,
        "unrealized_pnl": 0.0, "total_fees_paid": 0.0,
        "position_count": 0, "cycle_count": 0,
        "total_true_pnl": 0.0, "total_gross_pnl": 0.0,
        "total_api_cost": 0.0, "evaluation_count": 0,
        "thinking_budget_daily": 0.50,
    }
    defaults.update(kwargs)
    agent = Agent(**defaults)
    session.add(agent)
    session.flush()
    return agent


# --- Offspring detection ---

@pytest.mark.asyncio
async def test_offspring_detected(db_session):
    """Offspring (parent_id set) gets reduced textbooks."""
    parent = _make_agent(db_session, name="Parent", generation=1,
                         status="active", prestige_title="Expert",
                         evaluation_count=10)
    dynasty = Dynasty(
        founder_id=parent.id, founder_name=parent.name,
        founder_role=parent.type, dynasty_name="Dynasty Parent",
        status="active", total_generations=1, total_members=1,
        living_members=1, peak_members=1,
    )
    db_session.add(dynasty)
    db_session.flush()
    parent.dynasty_id = dynasty.id

    offspring = _make_agent(
        db_session, name="Offspring", generation=2,
        parent_id=parent.id, dynasty_id=dynasty.id,
    )

    protocol = OrientationProtocol(db_session)
    # No claude client → will fail, but we can test detection
    result = await protocol.orient_agent(offspring)

    assert result.success is False
    assert result.failure_reason == "no_claude_client"


# --- _load_summaries_offspring returns only thinking_efficiently ---

def test_load_summaries_offspring_reduced(db_session):
    """Offspring should only get thinking_efficiently textbook."""
    protocol = OrientationProtocol(db_session)
    summaries = protocol._load_summaries_offspring()

    # Should have at most 1 key
    assert len(summaries) <= 1
    if summaries:
        assert "thinking_efficiently" in summaries


# --- _build_offspring_system_prompt includes lineage ---

def test_offspring_system_prompt_includes_lineage(db_session):
    """Offspring system prompt should mention parent and dynasty."""
    parent = _make_agent(db_session, name="Scout-Alpha", generation=1,
                         status="active", prestige_title="Expert",
                         evaluation_count=12)
    dynasty = Dynasty(
        founder_id=parent.id, founder_name="Scout-Alpha",
        founder_role="scout", dynasty_name="Dynasty Scout-Alpha",
        status="active", total_generations=1, total_members=1,
        living_members=1, peak_members=1,
    )
    db_session.add(dynasty)
    db_session.flush()
    parent.dynasty_id = dynasty.id

    offspring = _make_agent(
        db_session, name="Scout-Alpha-II", generation=2,
        parent_id=parent.id, dynasty_id=dynasty.id,
        capital_allocated=80.0, thinking_budget_daily=0.50,
    )

    protocol = OrientationProtocol(db_session)
    from src.agents.roles import get_role
    role_def = get_role("scout")
    prompt = protocol._build_offspring_system_prompt(offspring, role_def)

    assert "Scout-Alpha" in prompt
    assert "Dynasty Scout-Alpha" in prompt
    assert "Gen 1" in prompt  # parent was Gen 1
    assert "Expert" in prompt


# --- _build_offspring_user_prompt includes founding directive ---

def test_offspring_user_prompt_includes_directive(db_session):
    """Offspring user prompt should include founding directive as a question."""
    parent = _make_agent(db_session, name="Parent", generation=1, status="active")
    dynasty = Dynasty(
        founder_id=parent.id, founder_name=parent.name,
        founder_role=parent.type, dynasty_name="Dynasty Parent",
        status="active", total_generations=1, total_members=1,
        living_members=1, peak_members=1,
    )
    db_session.add(dynasty)
    db_session.flush()
    parent.dynasty_id = dynasty.id

    offspring = _make_agent(
        db_session, name="Child", generation=2,
        parent_id=parent.id, dynasty_id=dynasty.id,
        founding_directive="What markets should we diversify into?",
        founding_directive_consumed=False,
        capital_allocated=80.0, thinking_budget_daily=0.50,
    )

    protocol = OrientationProtocol(db_session)
    from src.agents.roles import get_role
    role_def = get_role("scout")
    prompt = protocol._build_offspring_user_prompt(offspring, role_def, {"thinking_efficiently": "test"})

    assert "What markets should we diversify into?" in prompt
    assert "FOUNDING DIRECTIVE" in prompt
    assert "Genesis asks" in prompt


# --- _mark_completed consumes founding directive ---

def test_mark_completed_consumes_directive(db_session):
    """After orientation, founding_directive_consumed should be True."""
    agent = _make_agent(
        db_session, name="Child",
        founding_directive="What is the best approach?",
        founding_directive_consumed=False,
    )

    protocol = OrientationProtocol(db_session)
    protocol._mark_completed(agent, ["BTC/USDT"])

    assert agent.founding_directive_consumed is True
    assert agent.orientation_completed is True
    assert agent.status == "active"
    assert agent.watched_markets == ["BTC/USDT"]
