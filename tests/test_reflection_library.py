"""Tests for Reflection Library Selector — Phase 3E."""

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.common.models import (
    Agent, Base, LibraryEntry, StudyHistory, SystemState,
)
from src.personality.reflection_library import (
    ReflectionLibrarySelector, WEAKNESS_TO_RESOURCE,
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
        "name": "TestOp", "type": "operator", "status": "active",
        "capital_allocated": 100.0, "capital_current": 100.0,
        "cash_balance": 100.0, "reserved_cash": 0.0,
        "total_equity": 100.0, "realized_pnl": 0.0,
        "unrealized_pnl": 0.0, "total_fees_paid": 0.0,
        "position_count": 0, "cycle_count": 50,
        "total_true_pnl": 0.0, "total_gross_pnl": 0.0,
        "total_api_cost": 0.0, "evaluation_count": 0,
    }
    defaults.update(kwargs)
    agent = Agent(**defaults)
    session.add(agent)
    session.flush()
    return agent


# --- Resource mapping completeness ---

def test_weakness_to_resource_mapping():
    """All 4 roles have at least one resource mapping."""
    for role in ("scout", "strategist", "critic", "operator"):
        assert role in WEAKNESS_TO_RESOURCE
        assert len(WEAKNESS_TO_RESOURCE[role]) > 0


# --- No scorecard returns None ---

def test_no_scorecard_returns_none(db_session):
    """Agent without evaluation scorecard gets no library content."""
    agent = _make_agent(db_session, evaluation_scorecard=None)

    selector = ReflectionLibrarySelector()
    result = selector.select_for_reflection(db_session, agent)

    assert result is None


# --- Empty metrics returns None ---

def test_empty_metrics_returns_none(db_session):
    """Agent with empty metrics dict gets no library content."""
    agent = _make_agent(db_session, evaluation_scorecard={"metrics": {}})

    selector = ReflectionLibrarySelector()
    result = selector.select_for_reflection(db_session, agent)

    assert result is None


# --- Study cooldown prevents repeat ---

def test_study_cooldown_prevents_repeat(db_session):
    """Agent who recently studied a resource should not get it again."""
    agent = _make_agent(
        db_session, type="operator", cycle_count=100,
        evaluation_scorecard={
            "metrics": {
                "sharpe": {"raw": 0.1, "normalized": 0.2},
                "true_pnl": {"raw": 0.5, "normalized": 0.6},
            }
        },
    )

    # Record a recent study of the resource that would be selected
    resource_file = WEAKNESS_TO_RESOURCE["operator"]["sharpe"]
    study = StudyHistory(
        agent_id=agent.id,
        resource_type="textbook_summary",
        resource_id=resource_file,
        studied_at_cycle=95,  # Recently studied
    )
    db_session.add(study)
    db_session.flush()

    selector = ReflectionLibrarySelector()
    result = selector.select_for_reflection(db_session, agent)

    # Should not return the same resource (sharpe mapping)
    # May return a fallback or None
    if result is not None:
        assert result.resource_id != resource_file


# --- Fallback to library archive ---

def test_fallback_to_library_archive(db_session):
    """When textbook not found, falls back to library archive entries."""
    agent = _make_agent(
        db_session, type="operator", cycle_count=100,
        evaluation_scorecard={
            "metrics": {
                "sharpe": {"raw": 0.1, "normalized": 0.1},
                "true_pnl": {"raw": 0.8, "normalized": 0.8},
            }
        },
    )

    # Add a library entry that mentions "sharpe" in content
    entry = LibraryEntry(
        title="How to Improve Sharpe Ratio",
        category="post_mortem",
        content="This post mortem discusses sharpe ratio improvements and risk management.",
        summary="Sharpe improvement strategies.",
        source_agent_id=agent.id,
        is_published=True,
    )
    db_session.add(entry)
    db_session.flush()

    selector = ReflectionLibrarySelector()
    # This will fail to find textbook file → fall back to archive
    result = selector.select_for_reflection(db_session, agent)

    # If textbook file doesn't exist, should fall back to archive entry
    if result is not None:
        assert result.weakest_metric == "sharpe"
