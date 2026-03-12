"""Tests for the Boot Sequence Orchestrator."""

__version__ = "0.8.0"

import pytest
from unittest.mock import MagicMock, AsyncMock
from dataclasses import dataclass

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.common.models import Agent, Base, BootSequenceLog, Lineage, SystemState
from src.genesis.boot_sequence import BootSequenceOrchestrator, SPAWN_WAVES, GEN1_SURVIVAL_DAYS


@pytest.fixture
def db_factory():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)

    # Seed system state with treasury
    with factory() as session:
        state = SystemState(
            total_treasury=500.0,
            peak_treasury=500.0,
            current_regime="unknown",
            active_agent_count=0,
            alert_status="green",
        )
        session.add(state)
        session.commit()

    return factory


@pytest.fixture
def mock_orientation():
    """Mock orientation that always succeeds."""
    orient = MagicMock()
    orient.orient_agent = AsyncMock(return_value=MagicMock(
        success=True,
        initial_watchlist=["BTC/USDT", "ETH/USDT"],
        api_cost=0.005,
    ))
    return orient


class TestWaveDefinitions:
    def test_three_waves(self):
        assert len(SPAWN_WAVES) == 3

    def test_wave_1_has_scouts(self):
        wave1 = SPAWN_WAVES[1]
        assert len(wave1) == 2
        assert all(s["type"] == "scout" for s in wave1)

    def test_wave_2_has_strategist(self):
        wave2 = SPAWN_WAVES[2]
        assert len(wave2) == 1
        assert wave2[0]["type"] == "strategist"

    def test_wave_3_has_critic_and_operator(self):
        wave3 = SPAWN_WAVES[3]
        assert len(wave3) == 2
        types = {s["type"] for s in wave3}
        assert types == {"critic", "operator"}

    def test_total_agents_is_five(self):
        total = sum(len(specs) for specs in SPAWN_WAVES.values())
        assert total == 5


class TestWavePreconditions:
    def test_wave_1_always_allowed(self, db_factory, mock_orientation):
        orch = BootSequenceOrchestrator(db_factory, mock_orientation)
        assert orch._check_wave_preconditions(1)

    def test_wave_2_needs_oriented_scouts(self, db_factory, mock_orientation):
        orch = BootSequenceOrchestrator(db_factory, mock_orientation)
        # No scouts yet → should fail
        assert not orch._check_wave_preconditions(2)

    def test_wave_3_needs_oriented_strategist(self, db_factory, mock_orientation):
        orch = BootSequenceOrchestrator(db_factory, mock_orientation)
        assert not orch._check_wave_preconditions(3)


class TestSpawnAgent:
    def test_spawns_with_correct_attributes(self, db_factory, mock_orientation):
        orch = BootSequenceOrchestrator(db_factory, mock_orientation)
        spec = {"name": "Scout-Alpha", "type": "scout", "mandate": "Test"}

        with db_factory() as session:
            agent = orch._spawn_agent(session, spec, wave_num=1)
            session.commit()

            assert agent.name == "Scout-Alpha"
            assert agent.type == "scout"
            assert agent.generation == 1
            assert agent.status == "initializing"
            assert agent.spawn_wave == 1
            assert agent.capital_allocated > 0
            assert agent.survival_clock_start is not None
            assert agent.survival_clock_end is not None

    def test_creates_lineage(self, db_factory, mock_orientation):
        orch = BootSequenceOrchestrator(db_factory, mock_orientation)
        spec = {"name": "Scout-Alpha", "type": "scout", "mandate": "Test"}

        with db_factory() as session:
            agent = orch._spawn_agent(session, spec, wave_num=1)
            session.commit()

            lineage = session.execute(
                select(Lineage).where(Lineage.agent_id == agent.id)
            ).scalar_one_or_none()
            assert lineage is not None
            assert lineage.generation == 1
            assert lineage.parent_id is None


class TestBootSequenceFlow:
    @pytest.mark.asyncio
    async def test_full_boot_sequence(self, db_factory, mock_orientation):
        orch = BootSequenceOrchestrator(db_factory, mock_orientation)
        result = await orch.run_boot_sequence()

        assert result["status"] == "complete"
        assert len(result["agents_spawned"]) == 5
        assert 1 in result["waves_completed"]
        assert 2 in result["waves_completed"]
        assert 3 in result["waves_completed"]

    @pytest.mark.asyncio
    async def test_idempotent_boot(self, db_factory, mock_orientation):
        orch = BootSequenceOrchestrator(db_factory, mock_orientation)
        await orch.run_boot_sequence()

        # Run again — should detect completion
        result = await orch.run_boot_sequence()
        assert result["status"] == "already_complete"

    @pytest.mark.asyncio
    async def test_orientation_failure_stops_sequence(self, db_factory):
        # Orientation that fails
        mock_orient = MagicMock()
        mock_orient.orient_agent = AsyncMock(return_value=MagicMock(
            success=False,
            initial_watchlist=[],
            api_cost=0.003,
            failure_reason="validation_failed",
        ))

        orch = BootSequenceOrchestrator(db_factory, mock_orient)
        result = await orch.run_boot_sequence()

        assert result["status"] == "orientation_failure"
        assert len(result["orientation_failures"]) > 0


class TestBootLogs:
    @pytest.mark.asyncio
    async def test_logs_spawn_events(self, db_factory, mock_orientation):
        orch = BootSequenceOrchestrator(db_factory, mock_orientation)
        await orch.run_boot_sequence()

        with db_factory() as session:
            logs = session.execute(
                select(BootSequenceLog).where(
                    BootSequenceLog.event_type == "spawn"
                )
            ).scalars().all()
            assert len(logs) == 5

    @pytest.mark.asyncio
    async def test_logs_orientation_events(self, db_factory, mock_orientation):
        orch = BootSequenceOrchestrator(db_factory, mock_orientation)
        await orch.run_boot_sequence()

        with db_factory() as session:
            logs = session.execute(
                select(BootSequenceLog).where(
                    BootSequenceLog.event_type.in_(["orientation_start", "orientation_complete"])
                )
            ).scalars().all()
            assert len(logs) >= 10  # 5 starts + 5 completions


class TestBootStatus:
    @pytest.mark.asyncio
    async def test_get_status(self, db_factory, mock_orientation):
        orch = BootSequenceOrchestrator(db_factory, mock_orientation)
        await orch.run_boot_sequence()

        status = orch.get_boot_status()
        assert status["total_spawned"] == 5
        assert status["total_oriented"] == 5
        assert len(status["waves"]) == 3
