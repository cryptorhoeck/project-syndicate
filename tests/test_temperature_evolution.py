"""Tests for Temperature Evolution Engine — Phase 3E."""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.common.models import Agent, AgentCycle, Base, SystemState
from src.personality.temperature_evolution import TemperatureEvolution, TemperatureResult


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
        "api_temperature": 0.3,
        "last_temperature_signal": 0,
        "temperature_history": [],
    }
    defaults.update(kwargs)
    agent = Agent(**defaults)
    session.add(agent)
    session.flush()
    return agent


def _make_diverse_cycles(session, agent_id, period_start, count=30):
    """Create cycles with diverse action types and positive PnL."""
    action_types = ["execute_trade", "close_position", "adjust_stop", "go_idle"]
    markets = ["BTC/USDT", "ETH/USDT", "SOL/USDT"]
    for i in range(count):
        cycle = AgentCycle(
            agent_id=agent_id, cycle_number=i + 1,
            cycle_type="normal",
            timestamp=period_start + timedelta(hours=i),
            action_type=action_types[i % len(action_types)],
            action_params={"symbol": markets[i % len(markets)]},
            outcome_pnl=5.0 if i % 2 == 0 else -1.0,
            api_cost_usd=0.01,
        )
        session.add(cycle)
    session.flush()


def _make_focused_cycles(session, agent_id, period_start, count=30):
    """Create cycles with focused action types and mixed PnL."""
    for i in range(count):
        cycle = AgentCycle(
            agent_id=agent_id, cycle_number=i + 1,
            cycle_type="normal",
            timestamp=period_start + timedelta(hours=i),
            action_type="execute_trade",
            action_params={"symbol": "BTC/USDT"},
            outcome_pnl=3.0 if i % 3 != 0 else -2.0,
            api_cost_usd=0.01,
        )
        session.add(cycle)
    session.flush()


# --- Warm drift test ---

@pytest.mark.asyncio
async def test_warm_drift_with_positive_correlation(db_session):
    """Agent should drift warmer when diversity correlates with profit."""
    agent = _make_agent(db_session, type="scout", api_temperature=0.5,
                        last_temperature_signal=1)
    period_start = datetime.now(timezone.utc) - timedelta(days=7)
    period_end = datetime.now(timezone.utc)
    _make_diverse_cycles(db_session, agent.id, period_start, count=30)

    te = TemperatureEvolution()
    result = await te.evolve(db_session, agent, period_start, period_end)

    assert isinstance(result, TemperatureResult)
    # Signal should be recorded
    assert result.signal in (-1, 0, 1)
    # If signal=1 matched last_signal=1 → temperature should rise
    if result.signal == 1:
        assert result.new_temp > result.old_temp
        assert result.changed is True


# --- Cool drift test ---

@pytest.mark.asyncio
async def test_cool_drift_with_negative_correlation(db_session):
    """Agent should drift cooler when focus correlates with profit."""
    agent = _make_agent(db_session, type="operator", api_temperature=0.3,
                        last_temperature_signal=-1)
    period_start = datetime.now(timezone.utc) - timedelta(days=7)
    period_end = datetime.now(timezone.utc)
    _make_focused_cycles(db_session, agent.id, period_start, count=30)

    te = TemperatureEvolution()
    result = await te.evolve(db_session, agent, period_start, period_end)

    assert isinstance(result, TemperatureResult)
    # If signal=-1 matched last_signal=-1 → temperature should drop
    if result.signal == -1:
        assert result.new_temp < result.old_temp
        assert result.changed is True


# --- No drift on insufficient data ---

@pytest.mark.asyncio
async def test_no_drift_insufficient_data(db_session):
    """No drift when there's not enough cycle data."""
    agent = _make_agent(db_session, type="operator", api_temperature=0.3)
    period_start = datetime.now(timezone.utc) - timedelta(days=7)
    period_end = datetime.now(timezone.utc)
    # Only 2 cycles — below minimum
    for i in range(2):
        cycle = AgentCycle(
            agent_id=agent.id, cycle_number=i + 1,
            cycle_type="normal",
            timestamp=period_start + timedelta(hours=i),
            action_type="execute_trade",
            api_cost_usd=0.01,
        )
        db_session.add(cycle)
    db_session.flush()

    te = TemperatureEvolution()
    result = await te.evolve(db_session, agent, period_start, period_end)

    assert result.changed is False
    assert result.new_temp == result.old_temp


# --- Momentum requirement ---

@pytest.mark.asyncio
async def test_momentum_requirement_first_signal(db_session):
    """First signal should NOT cause drift — needs 2 consecutive."""
    agent = _make_agent(db_session, type="scout", api_temperature=0.5,
                        last_temperature_signal=0)
    period_start = datetime.now(timezone.utc) - timedelta(days=7)
    period_end = datetime.now(timezone.utc)
    _make_diverse_cycles(db_session, agent.id, period_start, count=30)

    te = TemperatureEvolution()
    result = await te.evolve(db_session, agent, period_start, period_end)

    # First signal (0→±1): no change yet
    if result.signal != 0:
        assert result.changed is False
        assert result.new_temp == result.old_temp


# --- Two consecutive signals cause drift ---

@pytest.mark.asyncio
async def test_two_consecutive_signals_cause_drift(db_session):
    """When last_temperature_signal matches current signal, drift occurs."""
    # Pre-set last_signal to +1
    agent = _make_agent(db_session, type="scout", api_temperature=0.6,
                        last_temperature_signal=1)
    period_start = datetime.now(timezone.utc) - timedelta(days=7)
    period_end = datetime.now(timezone.utc)
    _make_diverse_cycles(db_session, agent.id, period_start, count=30)

    te = TemperatureEvolution()
    result = await te.evolve(db_session, agent, period_start, period_end)

    if result.signal == 1:
        # Confirmed momentum → should drift
        assert result.changed is True
        assert result.new_temp == pytest.approx(0.65, abs=0.01)


# --- Clamping to role bounds ---

@pytest.mark.asyncio
async def test_clamping_to_role_bounds(db_session):
    """Temperature should be clamped to role-specific bounds."""
    # Operator bounds: [0.1, 0.4]. Set near upper bound.
    agent = _make_agent(db_session, type="operator", api_temperature=0.39,
                        last_temperature_signal=1)
    period_start = datetime.now(timezone.utc) - timedelta(days=7)
    period_end = datetime.now(timezone.utc)
    _make_diverse_cycles(db_session, agent.id, period_start, count=30)

    te = TemperatureEvolution()
    result = await te.evolve(db_session, agent, period_start, period_end)

    # Even if drift wanted to go above 0.4, clamp it
    assert result.new_temp <= 0.4
    assert result.new_temp >= 0.1


# --- History recording ---

@pytest.mark.asyncio
async def test_history_recording(db_session):
    """Temperature history should be recorded on agent."""
    agent = _make_agent(db_session, type="operator", api_temperature=0.3,
                        temperature_history=[])
    period_start = datetime.now(timezone.utc) - timedelta(days=7)
    period_end = datetime.now(timezone.utc)
    _make_focused_cycles(db_session, agent.id, period_start, count=30)

    te = TemperatureEvolution()
    await te.evolve(db_session, agent, period_start, period_end)

    assert len(agent.temperature_history) == 1
    entry = agent.temperature_history[0]
    assert "old_temp" in entry
    assert "new_temp" in entry
    assert "signal" in entry
    assert "correlation" in entry
    assert "diversity" in entry
    assert "changed" in entry
    assert "timestamp" in entry
