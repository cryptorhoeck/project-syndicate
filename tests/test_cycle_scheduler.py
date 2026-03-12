"""Tests for the Cycle Scheduler module."""

__version__ = "0.7.0"

import pytest
from datetime import datetime, timedelta, timezone
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.common.models import Agent, Base
from src.agents.cycle_scheduler import CycleScheduler, CyclePriority


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

    # Seed agents
    session.add(Agent(
        id=1, name="Scout-Alpha", type="scout", status="active",
        generation=1, last_cycle_at=None,
    ))
    session.add(Agent(
        id=2, name="Strategist-Beta", type="strategist", status="active",
        generation=1, last_cycle_at=datetime.now(timezone.utc) - timedelta(seconds=120),
    ))
    session.add(Agent(
        id=3, name="Critic-Gamma", type="critic", status="active",
        generation=1, last_cycle_at=datetime.now(timezone.utc) - timedelta(seconds=10),
    ))
    session.add(Agent(
        id=4, name="Dead-Agent", type="scout", status="terminated",
        generation=1,
    ))
    session.commit()
    yield session
    session.close()


class TestScheduling:
    def test_schedule_new_agent(self, db_session):
        scheduler = CycleScheduler(db_session)
        agent = db_session.query(Agent).get(1)
        result = scheduler.schedule_cycle(agent)
        assert result.queued
        assert result.reason == "scheduled"

    def test_schedule_after_cooldown(self, db_session):
        scheduler = CycleScheduler(db_session)
        agent = db_session.query(Agent).get(2)
        result = scheduler.schedule_cycle(agent)
        assert result.queued

    def test_cooldown_enforced(self, db_session):
        scheduler = CycleScheduler(db_session)
        agent = db_session.query(Agent).get(3)
        result = scheduler.schedule_cycle(agent)
        assert not result.queued
        assert "cooldown" in result.reason

    def test_terminated_agent_rejected(self, db_session):
        scheduler = CycleScheduler(db_session)
        agent = db_session.query(Agent).get(4)
        result = scheduler.schedule_cycle(agent)
        assert not result.queued
        assert "terminated" in result.reason


class TestInterrupts:
    def test_interrupt_wakes_strategists(self, db_session):
        scheduler = CycleScheduler(db_session)
        queued = scheduler.handle_interrupt("opportunity_broadcast")
        assert 2 in queued  # Strategist-Beta

    def test_interrupt_wakes_critics_on_plan(self, db_session):
        scheduler = CycleScheduler(db_session)
        # Critic has cooldown, so this will be blocked
        queued = scheduler.handle_interrupt("plan_submitted")
        assert 3 not in queued  # in cooldown

    def test_warden_alert_wakes_all(self, db_session):
        scheduler = CycleScheduler(db_session)
        queued = scheduler.handle_interrupt("warden_alert")
        # Scout (no cooldown) and Strategist (past cooldown) should be queued
        assert 1 in queued
        assert 2 in queued

    def test_mention_wakes_specific_agent(self, db_session):
        scheduler = CycleScheduler(db_session)
        queued = scheduler.handle_interrupt("agent_mentioned", target_agent_id=1)
        assert queued == [1]


class TestQueue:
    def test_queue_and_pop(self, db_session):
        scheduler = CycleScheduler(db_session)
        agent = db_session.query(Agent).get(1)
        scheduler.schedule_cycle(agent, priority=CyclePriority.SCHEDULED)

        next_id = scheduler.get_next()
        assert next_id == 1

    def test_priority_ordering(self, db_session):
        scheduler = CycleScheduler(db_session)
        agent1 = db_session.query(Agent).get(1)
        agent2 = db_session.query(Agent).get(2)

        scheduler.schedule_cycle(agent1, priority=CyclePriority.SCHEDULED)
        scheduler.schedule_cycle(agent2, priority=CyclePriority.CRITICAL)

        # Critical should come first
        next_id = scheduler.get_next()
        assert next_id == 2

    def test_empty_queue_returns_none(self, db_session):
        scheduler = CycleScheduler(db_session)
        assert scheduler.get_next() is None

    def test_queue_size(self, db_session):
        scheduler = CycleScheduler(db_session)
        assert scheduler.queue_size() == 0

        agent = db_session.query(Agent).get(1)
        scheduler.schedule_cycle(agent)
        assert scheduler.queue_size() == 1

    def test_clear_queue(self, db_session):
        scheduler = CycleScheduler(db_session)
        agent = db_session.query(Agent).get(1)
        scheduler.schedule_cycle(agent)
        scheduler.clear_queue()
        assert scheduler.queue_size() == 0


class TestCycleInterval:
    def test_scout_interval(self, db_session):
        scheduler = CycleScheduler(db_session)
        agent = db_session.query(Agent).get(1)
        assert scheduler.get_cycle_interval(agent) == 300

    def test_strategist_interval(self, db_session):
        scheduler = CycleScheduler(db_session)
        agent = db_session.query(Agent).get(2)
        assert scheduler.get_cycle_interval(agent) == 900
