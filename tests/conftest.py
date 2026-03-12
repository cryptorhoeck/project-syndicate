"""
Shared test fixtures for Project Syndicate.
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.common.models import Agent, Base, SystemState


@pytest.fixture
def db_engine():
    """Create an in-memory SQLite engine for testing."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine


@pytest.fixture
def db_session_factory(db_engine):
    """Create a sessionmaker bound to the test engine."""
    return sessionmaker(bind=db_engine)


@pytest.fixture
def seeded_db(db_session_factory):
    """Seed database with system state and a test agent."""
    with db_session_factory() as session:
        # System state
        state = SystemState(
            total_treasury=500.0,
            peak_treasury=500.0,
            current_regime="unknown",
            active_agent_count=0,
            alert_status="green",
        )
        session.add(state)

        # Test agent
        agent = Agent(
            name="Test-Agent-1",
            type="operator",
            status="active",
            capital_allocated=100.0,
            capital_current=100.0,
            thinking_budget_daily=0.50,
            thinking_budget_used_today=0.0,
            evaluation_count=5,
            profitable_evaluations=3,
        )
        session.add(agent)
        session.commit()

    return db_session_factory
