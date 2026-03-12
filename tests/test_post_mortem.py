"""Tests for Post-Mortem generation — Phase 3D."""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.common.models import Agent, Base, Evaluation, PostMortem
from src.genesis.evaluation_engine import EvaluationEngine


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
    yield session
    session.close()


def _make_agent(session):
    agent = Agent(
        name="DeadAgent", type="operator", status="active",
        capital_allocated=100, capital_current=100, generation=1,
        cash_balance=100, reserved_cash=0, total_equity=100,
        realized_pnl=-20, unrealized_pnl=0, total_fees_paid=1.0,
        position_count=0, termination_reason="Unprofitable",
    )
    session.add(agent)
    session.flush()
    return agent


def _make_evaluation(session, agent):
    evaluation = Evaluation(
        agent_id=agent.id, evaluation_type="survival_check",
        composite_score=0.2, metric_breakdown={"test": "data"},
        result="terminated",
    )
    session.add(evaluation)
    session.flush()
    return evaluation


@pytest.mark.asyncio
@patch("src.genesis.evaluation_engine.anthropic")
async def test_post_mortem_creation(mock_anthropic, db_session):
    """Post-mortem should be created with genesis_visible=True."""
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.content = [MagicMock(text='{"title": "Post-Mortem: DeadAgent", "summary": "Failed", "what_went_wrong": "Bad trades", "what_went_right": "Good risk management", "lesson": "Be careful", "market_context": "Bull market", "recommendation": "Try again"}')]
    mock_client.messages.create.return_value = mock_response
    mock_anthropic.Anthropic.return_value = mock_client

    agent = _make_agent(db_session)
    evaluation = _make_evaluation(db_session, agent)

    from src.genesis.evaluation_engine import EvaluationResult
    result = EvaluationResult(
        agent_id=agent.id, agent_name=agent.name,
        agent_role=agent.type, pre_filter_result="terminate",
    )

    engine = EvaluationEngine()
    await engine._generate_post_mortem(db_session, agent, evaluation, result)
    db_session.flush()

    pm = db_session.query(PostMortem).first()
    assert pm is not None
    assert pm.genesis_visible is True
    assert pm.published is False
    assert pm.agent_name == "DeadAgent"


@pytest.mark.asyncio
@patch("src.genesis.evaluation_engine.anthropic")
async def test_post_mortem_publish_delay(mock_anthropic, db_session):
    """Post-mortem should have a 6-hour publish delay."""
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.content = [MagicMock(text='{"title": "PM", "summary": "S", "what_went_wrong": "W", "what_went_right": "R", "lesson": "L", "market_context": "M", "recommendation": "R"}')]
    mock_client.messages.create.return_value = mock_response
    mock_anthropic.Anthropic.return_value = mock_client

    agent = _make_agent(db_session)
    evaluation = _make_evaluation(db_session, agent)

    from src.genesis.evaluation_engine import EvaluationResult
    result = EvaluationResult(
        agent_id=agent.id, agent_name=agent.name,
        agent_role=agent.type, pre_filter_result="terminate",
    )

    engine = EvaluationEngine()
    await engine._generate_post_mortem(db_session, agent, evaluation, result)
    db_session.flush()

    pm = db_session.query(PostMortem).first()
    now = datetime.now(timezone.utc)
    assert pm.publish_at is not None
    # publish_at should be approximately 6 hours from now
    diff = pm.publish_at.replace(tzinfo=timezone.utc) - now
    assert 5 * 3600 < diff.total_seconds() < 7 * 3600


@pytest.mark.asyncio
@patch("src.genesis.evaluation_engine.anthropic")
async def test_post_mortem_api_failure(mock_anthropic, db_session):
    """Post-mortem should still be created even if API fails."""
    mock_anthropic.Anthropic.side_effect = Exception("API down")

    agent = _make_agent(db_session)
    evaluation = _make_evaluation(db_session, agent)

    from src.genesis.evaluation_engine import EvaluationResult
    result = EvaluationResult(
        agent_id=agent.id, agent_name=agent.name,
        agent_role=agent.type, pre_filter_result="terminate",
    )

    engine = EvaluationEngine()
    await engine._generate_post_mortem(db_session, agent, evaluation, result)
    db_session.flush()

    pm = db_session.query(PostMortem).first()
    assert pm is not None
    assert "DeadAgent" in pm.title
