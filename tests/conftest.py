"""
Shared test fixtures for Project Syndicate.
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.common.models import Agent, Base, SystemState


class MockCurrencyService:
    """Mock CurrencyService that returns rate=1.0 (no conversion).

    Used by existing tests so that CAD conversion doesn't change
    previously-expected numeric values.
    """

    def __init__(self, rate: float = 1.0):
        self._rate = rate

    def get_usdt_cad_rate(self) -> float:
        return self._rate

    def get_usd_cad_rate(self) -> float:
        return self._rate

    def usdt_to_cad(self, amount: float) -> float:
        return round(amount * self._rate, 6)

    def cad_to_usdt(self, amount: float) -> float:
        if self._rate == 0:
            return 0.0
        return round(amount / self._rate, 6)

    def usd_to_cad(self, amount: float) -> float:
        return round(amount * self._rate, 6)

    def invalidate_cache(self) -> None:
        pass


@pytest.fixture
def mock_currency():
    """Mock CurrencyService with 1:1 rate (no conversion)."""
    return MockCurrencyService(rate=1.0)


@pytest.fixture
def mock_currency_realistic():
    """Mock CurrencyService with realistic 1.38 CAD/USDT rate."""
    return MockCurrencyService(rate=1.38)


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
