"""Tests for Behavioral Profile Calculator — Phase 3E."""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.common.models import (
    Agent, AgentCycle, Base, BehavioralProfile, Evaluation,
    MarketRegime, Position, SystemState,
)
from src.personality.behavioral_profile import (
    BehavioralProfileCalculator, classify, TIER_DISTANCES,
    RISK_LABELS, RISK_THRESHOLDS,
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


def _make_positions(session, agent_id, count=10, profitable_pct=0.5):
    """Create closed positions for an operator agent."""
    now = datetime.now(timezone.utc)
    for i in range(count):
        pnl = 5.0 if i < count * profitable_pct else -3.0
        pos = Position(
            agent_id=agent_id, agent_name="TestOp",
            symbol="BTC/USDT", side="long",
            entry_price=100.0, current_price=100.0,
            quantity=0.1, size_usd=10.0,
            stop_loss=95.0, take_profit=110.0,
            status="closed", close_reason="manual",
            realized_pnl=pnl,
            closed_at=now - timedelta(hours=count - i),
            execution_venue="paper",
        )
        session.add(pos)
    session.flush()


def _make_cycles(session, agent_id, count=20, include_actions=True):
    """Create agent cycles."""
    now = datetime.now(timezone.utc)
    for i in range(count):
        cycle = AgentCycle(
            agent_id=agent_id, cycle_number=i + 1,
            cycle_type="normal",
            timestamp=now - timedelta(hours=count - i),
            action_type="execute_trade" if include_actions and i % 3 != 0 else "go_idle",
            action_params={"symbol": "BTC/USDT"} if include_actions else None,
            confidence_score=5 + (i % 5),
            reasoning="Test reasoning " * 10,
            api_cost_usd=0.01,
        )
        session.add(cycle)
    session.flush()


# --- Classify function tests ---

def test_classify_low_score():
    result = classify(0.1, RISK_THRESHOLDS, RISK_LABELS)
    assert result == "ultra_conservative"


def test_classify_mid_score():
    result = classify(0.5, RISK_THRESHOLDS, RISK_LABELS)
    assert result == "moderate"


def test_classify_high_score():
    result = classify(0.85, RISK_THRESHOLDS, RISK_LABELS)
    assert result == "reckless"


def test_classify_boundary():
    result = classify(0.2, RISK_THRESHOLDS, RISK_LABELS)
    assert result == "conservative"


def test_classify_max():
    result = classify(1.0, RISK_THRESHOLDS, RISK_LABELS)
    assert result == "reckless"


# --- Profile computation tests ---

@pytest.mark.asyncio
async def test_risk_appetite_from_positions(db_session):
    agent = _make_agent(db_session, type="operator")
    _make_positions(db_session, agent.id, count=12, profitable_pct=0.5)
    _make_cycles(db_session, agent.id, count=20)

    calc = BehavioralProfileCalculator()
    profile = await calc.compute(db_session, agent.id)

    assert profile.risk_appetite_score is not None
    assert profile.risk_appetite_label is not None
    assert profile.risk_appetite_label in RISK_LABELS


@pytest.mark.asyncio
async def test_insufficient_data_returns_none(db_session):
    """Profile trait with insufficient data returns None."""
    agent = _make_agent(db_session, type="operator")
    # Only 3 positions — below threshold of 10
    _make_positions(db_session, agent.id, count=3)

    calc = BehavioralProfileCalculator()
    profile = await calc.compute(db_session, agent.id)

    assert profile.risk_appetite_score is None
    assert profile.risk_appetite_label is None


@pytest.mark.asyncio
async def test_non_operator_risk_appetite_na(db_session):
    """Non-operator agents get no risk_appetite score."""
    agent = _make_agent(db_session, type="scout", name="TestScout")
    _make_cycles(db_session, agent.id, count=25)

    calc = BehavioralProfileCalculator()
    profile = await calc.compute(db_session, agent.id)

    assert profile.risk_appetite_score is None


@pytest.mark.asyncio
async def test_market_focus_entropy(db_session):
    """Market focus computed from action distribution."""
    agent = _make_agent(db_session, type="scout", name="TestScout")
    now = datetime.now(timezone.utc)
    # Create diverse market actions
    for i in range(25):
        markets = ["BTC/USDT", "ETH/USDT", "SOL/USDT"]
        cycle = AgentCycle(
            agent_id=agent.id, cycle_number=i + 1,
            cycle_type="normal",
            timestamp=now - timedelta(hours=25 - i),
            action_type="broadcast_opportunity",
            action_params={"symbol": markets[i % 3]},
            api_cost_usd=0.01,
        )
        db_session.add(cycle)
    db_session.flush()

    calc = BehavioralProfileCalculator()
    profile = await calc.compute(db_session, agent.id)

    assert profile.market_focus_entropy is not None
    assert profile.market_focus_data is not None
    # 3 markets evenly distributed → high entropy
    assert profile.market_focus_entropy > 0.8


@pytest.mark.asyncio
async def test_decision_style_classification(db_session):
    """Decision style computed from reasoning length and confidence."""
    agent = _make_agent(db_session, type="operator")
    now = datetime.now(timezone.utc)
    # Create action cycles with consistent pattern
    for i in range(20):
        cycle = AgentCycle(
            agent_id=agent.id, cycle_number=i + 1,
            cycle_type="normal",
            timestamp=now - timedelta(hours=20 - i),
            action_type="execute_trade",
            action_params={"symbol": "BTC/USDT"},
            confidence_score=5,  # consistent confidence
            reasoning="Short reasoning.",
            api_cost_usd=0.01,
        )
        db_session.add(cycle)
    db_session.flush()

    calc = BehavioralProfileCalculator()
    profile = await calc.compute(db_session, agent.id)

    assert profile.decision_style_score is not None
    assert profile.decision_style_label is not None


@pytest.mark.asyncio
async def test_learning_velocity_from_evaluations(db_session):
    """Learning velocity computed from evaluation score trend."""
    agent = _make_agent(db_session, evaluation_count=3)

    # Create evaluations with improving scores
    for i, score in enumerate([0.3, 0.5, 0.7]):
        ev = Evaluation(
            agent_id=agent.id, evaluation_type="survival_check",
            composite_score=score,
            evaluated_at=datetime.now(timezone.utc) - timedelta(days=14 * (2 - i)),
        )
        db_session.add(ev)
    db_session.flush()

    calc = BehavioralProfileCalculator()
    profile = await calc.compute(db_session, agent.id)

    assert profile.learning_velocity_score is not None
    assert profile.learning_velocity_label is not None
    # Improving scores → fast learner
    assert profile.learning_velocity_score > 0.5


@pytest.mark.asyncio
async def test_resilience_from_loss_recovery(db_session):
    """Resilience computed from loss-to-recovery cycle counts."""
    agent = _make_agent(db_session, type="operator")
    now = datetime.now(timezone.utc)

    # Create alternating loss/win positions with quick recovery
    for i in range(8):
        pnl = -5.0 if i % 2 == 0 else 5.0
        pos = Position(
            agent_id=agent.id, agent_name="TestOp",
            symbol="BTC/USDT", side="long",
            entry_price=100.0, current_price=100.0,
            quantity=0.1, size_usd=10.0,
            status="closed", close_reason="manual",
            realized_pnl=pnl,
            closed_at=now - timedelta(hours=8 - i),
            execution_venue="paper",
        )
        db_session.add(pos)

    # Add cycles between losses and recoveries
    for i in range(10):
        cycle = AgentCycle(
            agent_id=agent.id, cycle_number=i + 1,
            cycle_type="normal",
            timestamp=now - timedelta(hours=10 - i),
            action_type="execute_trade",
            api_cost_usd=0.01,
        )
        db_session.add(cycle)
    db_session.flush()

    calc = BehavioralProfileCalculator()
    profile = await calc.compute(db_session, agent.id)

    assert profile.resilience_score is not None
    assert profile.resilience_label is not None


@pytest.mark.asyncio
async def test_profile_is_complete_flag(db_session):
    """Profile is_complete is True only when all traits have data."""
    agent = _make_agent(db_session, type="scout", name="TestScout")
    # Scout won't have risk_appetite data, so is_complete should be False
    _make_cycles(db_session, agent.id, count=5)

    calc = BehavioralProfileCalculator()
    profile = await calc.compute(db_session, agent.id)

    # With minimal data, most traits won't have data
    assert profile.is_complete is False


@pytest.mark.asyncio
async def test_regime_context_populated(db_session):
    """Dominant regime and distribution are populated."""
    agent = _make_agent(db_session)
    now = datetime.now(timezone.utc)

    # Add market regimes
    for regime, count in [("bull", 5), ("crab", 3), ("bear", 2)]:
        for i in range(count):
            mr = MarketRegime(
                regime=regime,
                detected_at=now - timedelta(hours=i),
                btc_price=50000.0,
                btc_ma_20=49000.0,
                btc_ma_50=48000.0,
                btc_volatility_30d=0.05,
                btc_dominance=45.0,
                total_market_cap=2e12,
            )
            db_session.add(mr)
    db_session.flush()

    calc = BehavioralProfileCalculator()
    profile = await calc.compute(db_session, agent.id)

    assert profile.dominant_regime == "bull"
    assert profile.regime_distribution is not None
    assert "bull" in profile.regime_distribution


# --- Drift detection tests ---

def test_drift_detection_2_tier_shift(db_session):
    """2+ tier shift should be flagged."""
    agent = _make_agent(db_session)

    prev = BehavioralProfile(
        agent_id=agent.id,
        risk_appetite_score=0.1, risk_appetite_label="ultra_conservative",
        decision_style_score=0.5, decision_style_label="deliberate",
    )
    curr = BehavioralProfile(
        agent_id=agent.id,
        risk_appetite_score=0.7, risk_appetite_label="aggressive",
        decision_style_score=0.5, decision_style_label="deliberate",
    )

    calc = BehavioralProfileCalculator()
    flags = calc.detect_drift(prev, curr)

    assert len(flags) == 1
    assert flags[0].trait == "risk_appetite"
    assert flags[0].tier_distance >= 2


def test_drift_detection_1_tier_not_flagged(db_session):
    """1 tier shift should NOT be flagged."""
    agent = _make_agent(db_session)

    prev = BehavioralProfile(
        agent_id=agent.id,
        risk_appetite_score=0.3, risk_appetite_label="conservative",
    )
    curr = BehavioralProfile(
        agent_id=agent.id,
        risk_appetite_score=0.5, risk_appetite_label="moderate",
    )

    calc = BehavioralProfileCalculator()
    flags = calc.detect_drift(prev, curr)

    assert len(flags) == 0


def test_drift_detection_insufficient_data_skipped(db_session):
    """Drift check skips traits with None labels."""
    agent = _make_agent(db_session)

    prev = BehavioralProfile(
        agent_id=agent.id,
        risk_appetite_score=None, risk_appetite_label=None,
    )
    curr = BehavioralProfile(
        agent_id=agent.id,
        risk_appetite_score=0.9, risk_appetite_label="reckless",
    )

    calc = BehavioralProfileCalculator()
    flags = calc.detect_drift(prev, curr)

    assert len(flags) == 0
