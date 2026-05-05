"""Tests for the Maintenance Tasks module."""

__version__ = "0.8.0"

import pytest
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.common.models import Agent, Base, Opportunity, Plan
from src.agents.maintenance import MaintenanceService


@pytest.fixture
def db_factory():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)

    with factory() as session:
        session.add(Agent(
            id=1, name="Scout-Alpha", type="scout", status="active",
            generation=1, capital_allocated=100.0, capital_current=100.0,
            thinking_budget_daily=0.50, thinking_budget_used_today=0.20,
        ))
        session.add(Agent(
            id=2, name="Strategist-Prime", type="strategist", status="active",
            generation=1, capital_allocated=100.0, capital_current=100.0,
            thinking_budget_daily=0.50, thinking_budget_used_today=0.35,
        ))
        session.add(Agent(
            id=3, name="Dead-Agent", type="scout", status="terminated",
            generation=1, capital_allocated=0.0, capital_current=0.0,
            thinking_budget_daily=0.50, thinking_budget_used_today=0.0,
        ))
        session.commit()

    return factory


class TestExpireStaleOpportunities:
    def test_expires_past_ttl(self, db_factory):
        now = datetime.now(timezone.utc)
        with db_factory() as session:
            opp = Opportunity(
                scout_agent_id=1, scout_agent_name="Scout-Alpha",
                market="SOL/USDT", signal_type="breakout",
                details="test", status="new",
                expires_at=now - timedelta(hours=1),
            )
            session.add(opp)
            session.commit()

        svc = MaintenanceService(db_factory)
        count = svc.expire_stale_opportunities()
        assert count == 1

        with db_factory() as session:
            opp = session.get(Opportunity, 1)
            assert opp.status == "expired"

    def test_does_not_expire_fresh(self, db_factory):
        now = datetime.now(timezone.utc)
        with db_factory() as session:
            opp = Opportunity(
                scout_agent_id=1, scout_agent_name="Scout-Alpha",
                market="SOL/USDT", signal_type="breakout",
                details="test", status="new",
                expires_at=now + timedelta(hours=5),
            )
            session.add(opp)
            session.commit()

        svc = MaintenanceService(db_factory)
        count = svc.expire_stale_opportunities()
        assert count == 0


class TestCleanupStalePlans:
    def test_stale_submitted_reverted(self, db_factory):
        with db_factory() as session:
            plan = Plan(
                strategist_agent_id=2, strategist_agent_name="Strategist-Prime",
                plan_name="Test Plan", market="BTC/USDT", direction="long",
                entry_conditions="Break $70k", exit_conditions="TP $75k",
                thesis="Bullish", status="submitted",
                submitted_at=datetime.now(timezone.utc) - timedelta(hours=25),
            )
            session.add(plan)
            session.commit()

        svc = MaintenanceService(db_factory)
        count = svc.cleanup_stale_plans()
        assert count == 1

        with db_factory() as session:
            plan = session.get(Plan, 1)
            assert plan.status == "draft"

    def test_fresh_submitted_kept(self, db_factory):
        with db_factory() as session:
            plan = Plan(
                strategist_agent_id=2, strategist_agent_name="Strategist-Prime",
                plan_name="Test Plan", market="BTC/USDT", direction="long",
                entry_conditions="Break $70k", exit_conditions="TP $75k",
                thesis="Bullish", status="submitted",
                submitted_at=datetime.now(timezone.utc) - timedelta(hours=2),
            )
            session.add(plan)
            session.commit()

        svc = MaintenanceService(db_factory)
        count = svc.cleanup_stale_plans()
        assert count == 0


class TestResetDailyBudgets:
    def test_resets_active_agents(self, db_factory):
        svc = MaintenanceService(db_factory)
        count = svc.reset_daily_budgets()
        assert count == 2  # Only active agents (not terminated)

        with db_factory() as session:
            agent1 = session.get(Agent, 1)
            agent2 = session.get(Agent, 2)
            assert agent1.thinking_budget_used_today == 0.0
            assert agent2.thinking_budget_used_today == 0.0

    def test_does_not_reset_terminated(self, db_factory):
        svc = MaintenanceService(db_factory)
        svc.reset_daily_budgets()

        with db_factory() as session:
            dead = session.get(Agent, 3)
            assert dead.thinking_budget_used_today == 0.0  # Was 0 anyway


class TestPruneTerminatedMemory:
    def test_prunes_when_redis_available(self, db_factory):
        from unittest.mock import MagicMock
        mock_redis = MagicMock()
        mock_redis.exists.return_value = True

        svc = MaintenanceService(db_factory)
        count = svc.prune_terminated_agent_memory(redis_client=mock_redis)
        assert count == 1  # Agent 3 is terminated
        mock_redis.delete.assert_called_once_with("agent:3:recent_cycles")

    def test_no_action_without_redis(self, db_factory):
        svc = MaintenanceService(db_factory)
        count = svc.prune_terminated_agent_memory(redis_client=None)
        assert count == 0


class TestRunAll:
    @pytest.mark.asyncio
    async def test_runs_hourly_safe_tasks(self, db_factory):
        """Option B contract (subsystem T-subset hotfix, 2026-05-04):
        run_all runs the THREE hourly-safe maintenance tasks. It does
        NOT call reset_daily_budgets — that one stays at its
        existing daily-gated call site in
        Genesis._maybe_run_hourly_maintenance because resetting the
        thinking budget hourly would let agents consume up to 24x
        their intended daily cap. The dict keys reflect the new
        contract: opportunities_expired, plans_cleaned, memory_pruned.
        For exhaustive coverage of the new contract see
        tests/test_maintenance_run_all_wiring.py.
        """
        svc = MaintenanceService(db_factory)
        results = await svc.run_all()
        assert set(results.keys()) == {
            "opportunities_expired",
            "plans_cleaned",
            "memory_pruned",
        }
        # And the budget-reset key MUST NOT appear — its presence
        # would mean reset_daily_budgets was wrongly invoked.
        assert "budget_resets" not in results
        assert "expired_opportunities" not in results  # old key shape
        assert "stale_plans" not in results  # old key shape
