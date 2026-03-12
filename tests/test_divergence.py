"""Tests for Divergence Calculator — Phase 3E."""

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.common.models import (
    Agent, Base, BehavioralProfile, DivergenceScore, SystemState,
)
from src.personality.divergence import (
    DivergenceCalculator, DivergenceResult, cosine_distance,
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
        "position_count": 0, "cycle_count": 0,
        "total_true_pnl": 0.0, "total_gross_pnl": 0.0,
        "total_api_cost": 0.0, "evaluation_count": 0,
    }
    defaults.update(kwargs)
    agent = Agent(**defaults)
    session.add(agent)
    session.flush()
    return agent


def _make_profile(session, agent_id, **kwargs):
    defaults = {
        "agent_id": agent_id,
        "risk_appetite_score": 0.5,
        "market_focus_entropy": 0.6,
        "decision_style_score": 0.4,
        "collaboration_score": 0.5,
        "learning_velocity_score": 0.7,
        "resilience_score": 0.5,
        "is_complete": True,
    }
    defaults.update(kwargs)
    profile = BehavioralProfile(**defaults)
    session.add(profile)
    session.flush()
    return profile


# --- Cosine distance function ---

def test_cosine_distance_identical():
    """Identical vectors should have distance 0.0."""
    assert cosine_distance([1, 2, 3], [1, 2, 3]) == pytest.approx(0.0, abs=1e-6)


def test_cosine_distance_orthogonal():
    """Orthogonal vectors should have distance 1.0."""
    assert cosine_distance([1, 0], [0, 1]) == pytest.approx(1.0, abs=1e-6)


def test_cosine_distance_different():
    """Different vectors should have distance between 0 and 1."""
    d = cosine_distance([0.1, 0.5, 0.9], [0.9, 0.5, 0.1])
    assert 0.0 < d < 1.0


def test_cosine_distance_empty():
    """Empty vectors should return 1.0 (max distance)."""
    assert cosine_distance([], []) == 1.0


def test_cosine_distance_zero_vector():
    """Zero vector should return 1.0."""
    assert cosine_distance([0, 0, 0], [1, 2, 3]) == 1.0


# --- Pairwise computation ---

@pytest.mark.asyncio
async def test_pairwise_same_role(db_session):
    """Divergence computed for same-role agent pairs."""
    agent_a = _make_agent(db_session, name="Op-1", type="operator")
    agent_b = _make_agent(db_session, name="Op-2", type="operator")

    _make_profile(db_session, agent_a.id,
                  risk_appetite_score=0.2, market_focus_entropy=0.3,
                  decision_style_score=0.4)
    _make_profile(db_session, agent_b.id,
                  risk_appetite_score=0.8, market_focus_entropy=0.7,
                  decision_style_score=0.6)

    calc = DivergenceCalculator()
    results = await calc.compute_pairwise(db_session, role="operator")

    assert len(results) == 1
    r = results[0]
    assert isinstance(r, DivergenceResult)
    assert r.agent_a_id in (agent_a.id, agent_b.id)
    assert r.agent_b_id in (agent_a.id, agent_b.id)
    assert r.role == "operator"
    assert 0.0 <= r.score <= 1.0
    assert r.comparable_metrics >= 3


@pytest.mark.asyncio
async def test_pairwise_different_role_excluded(db_session):
    """Agents of different roles should not be compared."""
    agent_a = _make_agent(db_session, name="Op-1", type="operator")
    agent_b = _make_agent(db_session, name="Scout-1", type="scout")

    _make_profile(db_session, agent_a.id)
    _make_profile(db_session, agent_b.id)

    calc = DivergenceCalculator()
    results = await calc.compute_pairwise(db_session, role="operator")

    # Only 1 operator, no pairs
    assert len(results) == 0


@pytest.mark.asyncio
async def test_pairwise_no_profile_skipped(db_session):
    """Agents without profiles are skipped."""
    agent_a = _make_agent(db_session, name="Op-1", type="operator")
    agent_b = _make_agent(db_session, name="Op-2", type="operator")

    # Only agent_a has a profile
    _make_profile(db_session, agent_a.id)

    calc = DivergenceCalculator()
    results = await calc.compute_pairwise(db_session, role="operator")

    assert len(results) == 0


# --- Store snapshot ---

@pytest.mark.asyncio
async def test_store_snapshot(db_session):
    """Divergence results are stored in database."""
    agent_a = _make_agent(db_session, name="Op-1", type="operator")
    agent_b = _make_agent(db_session, name="Op-2", type="operator")

    results = [DivergenceResult(
        agent_a_id=agent_a.id,
        agent_b_id=agent_b.id,
        role="operator",
        score=0.35,
        comparable_metrics=5,
    )]

    calc = DivergenceCalculator()
    await calc.store_snapshot(db_session, results, evaluation_id=None)
    db_session.flush()

    stored = db_session.query(DivergenceScore).all()
    assert len(stored) == 1
    assert stored[0].divergence_score == pytest.approx(0.35)
    assert stored[0].comparable_metrics == 5


# --- Low divergence detection ---

@pytest.mark.asyncio
async def test_low_divergence_detected(db_session):
    """Near-identical profiles produce low divergence score."""
    agent_a = _make_agent(db_session, name="Op-1", type="operator")
    agent_b = _make_agent(db_session, name="Op-2", type="operator")

    _make_profile(db_session, agent_a.id,
                  risk_appetite_score=0.5, market_focus_entropy=0.5,
                  decision_style_score=0.5, collaboration_score=0.5,
                  learning_velocity_score=0.5, resilience_score=0.5)
    _make_profile(db_session, agent_b.id,
                  risk_appetite_score=0.51, market_focus_entropy=0.49,
                  decision_style_score=0.50, collaboration_score=0.51,
                  learning_velocity_score=0.50, resilience_score=0.49)

    calc = DivergenceCalculator()
    results = await calc.compute_pairwise(db_session, role="operator")

    assert len(results) == 1
    # Very similar profiles → low divergence
    assert results[0].score < 0.15
