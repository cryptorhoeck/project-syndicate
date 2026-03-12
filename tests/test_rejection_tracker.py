"""Tests for Rejection Tracker — Phase 3D."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.common.models import Agent, Base, Plan, RejectionTracking
from src.genesis.rejection_tracker import RejectionTracker


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

    # Seed critic agent
    agent = Agent(
        name="TestCritic", type="critic", status="active",
        capital_allocated=100, capital_current=100,
        cash_balance=100, reserved_cash=0, total_equity=100,
        realized_pnl=0, unrealized_pnl=0, total_fees_paid=0,
        position_count=0,
    )
    session.add(agent)

    # Seed strategist
    strat = Agent(
        name="TestStrat", type="strategist", status="active",
        capital_allocated=100, capital_current=100,
        cash_balance=100, reserved_cash=0, total_equity=100,
        realized_pnl=0, unrealized_pnl=0, total_fees_paid=0,
        position_count=0,
    )
    session.add(strat)
    session.flush()
    yield session
    session.close()


@pytest.fixture
def rejected_plan(db_session):
    plan = Plan(
        strategist_agent_id=2, strategist_agent_name="TestStrat",
        plan_name="Test Plan", market="BTC/USDT", direction="long",
        entry_conditions='{"stop_loss": 90.0, "take_profit": 120.0}',
        exit_conditions='{"stop_loss": 90.0, "take_profit": 120.0}',
        thesis="Test thesis", timeframe="1d",
        critic_agent_id=1, critic_verdict="rejected",
    )
    db_session.add(plan)
    db_session.flush()
    return plan


@pytest.mark.asyncio
async def test_track_rejection_creates_record(db_session, rejected_plan):
    """Tracking a rejection should create a RejectionTracking record."""
    tracker = RejectionTracker()
    result = await tracker.track_rejection(db_session, rejected_plan, 100.0)
    db_session.flush()

    assert result.id is not None
    assert result.plan_id == rejected_plan.id
    assert result.critic_id == 1
    assert result.market == "BTC/USDT"
    assert result.direction == "long"
    assert result.entry_price == 100.0
    assert result.status == "tracking"


@pytest.mark.asyncio
async def test_stop_loss_hit_critic_correct(db_session, rejected_plan):
    """If stop-loss is hit, the critic was correct to reject."""
    tracker = RejectionTracker()
    tracking = await tracker.track_rejection(db_session, rejected_plan, 100.0)

    # Override stop_loss for testing
    tracking.stop_loss = 90.0
    db_session.flush()

    # Mock price below stop loss
    mock_cache = MagicMock()
    mock_cache.get_ticker = AsyncMock(return_value=({"last": 85.0, "bid": 85.0}, True))
    tracker.price_cache = mock_cache

    result = await tracker.monitor_tracked_rejections(db_session)
    db_session.flush()
    assert result["completed"] == 1

    db_session.expire(tracking)
    assert tracking.status == "completed"
    assert tracking.outcome == "stop_loss_hit"
    assert tracking.critic_correct is True


@pytest.mark.asyncio
async def test_take_profit_hit_critic_wrong(db_session, rejected_plan):
    """If take-profit is hit, the critic was wrong to reject."""
    tracker = RejectionTracker()
    tracking = await tracker.track_rejection(db_session, rejected_plan, 100.0)
    tracking.take_profit = 120.0
    db_session.flush()

    # Mock price above take profit
    mock_cache = MagicMock()
    mock_cache.get_ticker = AsyncMock(return_value=({"last": 125.0, "bid": 125.0}, True))
    tracker.price_cache = mock_cache

    result = await tracker.monitor_tracked_rejections(db_session)
    assert result["completed"] == 1

    db_session.flush()
    db_session.expire(tracking)
    assert tracking.outcome == "take_profit_hit"
    assert tracking.critic_correct is False


@pytest.mark.asyncio
async def test_timeframe_expired_negative_pnl(db_session, rejected_plan):
    """Timeframe expiry with negative P&L means critic was correct."""
    tracker = RejectionTracker()
    tracking = await tracker.track_rejection(db_session, rejected_plan, 100.0)

    # Set check_until to the past
    tracking.check_until = datetime.now(timezone.utc) - timedelta(hours=1)
    tracking.stop_loss = None
    tracking.take_profit = None
    db_session.flush()

    # Price is lower than entry (negative P&L for long)
    mock_cache = MagicMock()
    mock_cache.get_ticker = AsyncMock(return_value=({"last": 95.0, "bid": 95.0}, True))
    tracker.price_cache = mock_cache

    result = await tracker.monitor_tracked_rejections(db_session)

    db_session.flush()
    db_session.expire(tracking)
    assert tracking.outcome == "timeframe_expired"
    assert tracking.critic_correct is True
    assert tracking.outcome_pnl_pct < 0


@pytest.mark.asyncio
async def test_rejection_score_calculation(db_session):
    """Test rejection score for a critic with mixed results."""
    now = datetime.now(timezone.utc)

    # Create some completed trackings
    for correct in [True, True, False, True]:
        t = RejectionTracking(
            plan_id=1, critic_id=1, market="BTC/USDT",
            direction="long", entry_price=100.0,
            rejected_at=now, check_until=now,
            status="completed", critic_correct=correct,
            completed_at=now,
        )
        db_session.add(t)
    db_session.flush()

    tracker = RejectionTracker()
    score = await tracker.get_critic_rejection_score(
        db_session, 1,
        datetime(2026, 1, 1, tzinfo=timezone.utc), now + timedelta(days=1),
    )
    assert score == pytest.approx(0.75)


@pytest.mark.asyncio
async def test_rejection_score_no_data(db_session):
    """No data should return neutral 0.5."""
    tracker = RejectionTracker()
    now = datetime.now(timezone.utc)
    score = await tracker.get_critic_rejection_score(
        db_session, 999,
        datetime(2026, 1, 1, tzinfo=timezone.utc), now,
    )
    assert score == 0.5
