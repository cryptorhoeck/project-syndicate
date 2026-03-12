"""Tests for the Day-10 Health Check."""

__version__ = "0.8.0"

import pytest
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.common.models import Agent, Base, BootSequenceLog
from src.genesis.health_check import (
    HealthCheckService,
    MIN_CYCLES_BY_DAY_10,
    MAX_IDLE_RATE,
    MAX_VALIDATION_FAIL_RATE,
    MAX_AVG_CYCLE_COST,
)


@pytest.fixture
def db_factory():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    return factory


def _create_agent(session, **kwargs):
    now = datetime.now(timezone.utc)
    defaults = dict(
        name="Scout-Alpha", type="scout", status="active",
        generation=1, capital_allocated=100.0, capital_current=95.0,
        thinking_budget_daily=0.50, thinking_budget_used_today=0.10,
        cycle_count=50, idle_rate=0.3, validation_fail_rate=0.05,
        avg_cycle_cost=0.005,
        survival_clock_start=now - timedelta(days=11),
        survival_clock_end=now + timedelta(days=10),
        orientation_completed=True, spawn_wave=1,
    )
    defaults.update(kwargs)
    agent = Agent(**defaults)
    session.add(agent)
    session.commit()
    return agent


class TestHealthCheckDue:
    def test_agent_due_at_day_10(self, db_factory):
        svc = HealthCheckService(db_factory)
        with db_factory() as session:
            agent = _create_agent(session)
            now = datetime.now(timezone.utc)
            assert svc._is_due_for_check(agent, now)

    def test_agent_not_due_at_day_5(self, db_factory):
        svc = HealthCheckService(db_factory)
        now = datetime.now(timezone.utc)
        with db_factory() as session:
            agent = _create_agent(session,
                                  survival_clock_start=now - timedelta(days=5))
            assert not svc._is_due_for_check(agent, now)

    def test_no_survival_clock(self, db_factory):
        svc = HealthCheckService(db_factory)
        with db_factory() as session:
            agent = _create_agent(session, survival_clock_start=None)
            now = datetime.now(timezone.utc)
            assert not svc._is_due_for_check(agent, now)


class TestHealthyAgent:
    @pytest.mark.asyncio
    async def test_healthy_agent_passes(self, db_factory):
        with db_factory() as session:
            _create_agent(session, id=1, cycle_count=50, idle_rate=0.3,
                          validation_fail_rate=0.05, avg_cycle_cost=0.005)

        svc = HealthCheckService(db_factory)
        results = await svc.run_health_checks()

        assert len(results) == 1
        assert results[0].passed
        assert len(results[0].issues) == 0


class TestUnhealthyAgent:
    @pytest.mark.asyncio
    async def test_low_cycle_count(self, db_factory):
        with db_factory() as session:
            _create_agent(session, id=1, cycle_count=5)

        svc = HealthCheckService(db_factory)
        results = await svc.run_health_checks()

        assert len(results) == 1
        assert any("Low cycle count" in issue for issue in results[0].issues)

    @pytest.mark.asyncio
    async def test_high_idle_rate(self, db_factory):
        with db_factory() as session:
            _create_agent(session, id=1, idle_rate=0.95)

        svc = HealthCheckService(db_factory)
        results = await svc.run_health_checks()

        assert any("High idle rate" in issue for issue in results[0].issues)

    @pytest.mark.asyncio
    async def test_high_validation_fail_rate(self, db_factory):
        with db_factory() as session:
            _create_agent(session, id=1, validation_fail_rate=0.40)

        svc = HealthCheckService(db_factory)
        results = await svc.run_health_checks()

        assert any("High validation fail rate" in issue for issue in results[0].issues)

    @pytest.mark.asyncio
    async def test_high_cycle_cost(self, db_factory):
        with db_factory() as session:
            _create_agent(session, id=1, avg_cycle_cost=0.03)

        svc = HealthCheckService(db_factory)
        results = await svc.run_health_checks()

        assert any("High avg cycle cost" in issue for issue in results[0].issues)

    @pytest.mark.asyncio
    async def test_multiple_issues_shorten_clock(self, db_factory):
        with db_factory() as session:
            agent = _create_agent(session, id=1,
                                  cycle_count=5, idle_rate=0.95,
                                  validation_fail_rate=0.40)
            original_end = agent.survival_clock_end

        svc = HealthCheckService(db_factory)
        results = await svc.run_health_checks()

        assert not results[0].passed
        assert "survival_clock_shortened_3d" in results[0].actions_taken
        assert "budget_reduced_25pct" in results[0].actions_taken

    @pytest.mark.asyncio
    async def test_minor_issue_extends_clock(self, db_factory):
        # Two issues = not passed but minor
        with db_factory() as session:
            _create_agent(session, id=1, cycle_count=5, idle_rate=0.95)

        svc = HealthCheckService(db_factory)
        results = await svc.run_health_checks()

        assert not results[0].passed
        assert "survival_clock_extended_3d" in results[0].actions_taken


class TestHealthCheckMarking:
    @pytest.mark.asyncio
    async def test_marks_health_check_fields(self, db_factory):
        with db_factory() as session:
            _create_agent(session, id=1)

        svc = HealthCheckService(db_factory)
        await svc.run_health_checks()

        with db_factory() as session:
            agent = session.get(Agent, 1)
            assert agent.health_check_passed is not None
            assert agent.health_check_at is not None

    @pytest.mark.asyncio
    async def test_already_checked_skipped(self, db_factory):
        with db_factory() as session:
            _create_agent(session, id=1, health_check_passed=True,
                          health_check_at=datetime.now(timezone.utc))

        svc = HealthCheckService(db_factory)
        results = await svc.run_health_checks()
        assert len(results) == 0  # Already checked


class TestSingleAgentCheck:
    @pytest.mark.asyncio
    async def test_check_single(self, db_factory):
        with db_factory() as session:
            _create_agent(session, id=1)

        svc = HealthCheckService(db_factory)
        result = await svc.check_single_agent(1)
        assert result is not None
        assert result.agent_id == 1

    @pytest.mark.asyncio
    async def test_check_nonexistent(self, db_factory):
        svc = HealthCheckService(db_factory)
        result = await svc.check_single_agent(9999)
        assert result is None
