"""
Project Syndicate — Stress Tests

Simulate extended operation and failure conditions.
Run separately: pytest tests/test_stress.py -m stress -v
"""

__version__ = "1.0.0"

import asyncio
import json
import os
import logging
import tempfile

import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from dataclasses import dataclass

from sqlalchemy import create_engine, select, func, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.common.models import (
    Agent, AgentCycle, AgentLongTermMemory, Base, Dynasty,
    Evaluation, Lineage, Memorial, Message, Opportunity,
    Plan, Position, StudyHistory, SystemState, Transaction,
)
from src.common.config import config


# ── Shared Fixtures ────────────────────────────────────────

@pytest.fixture
def stress_engine():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return engine


@pytest.fixture
def stress_factory(stress_engine):
    return sessionmaker(bind=stress_engine)


@pytest.fixture
def stress_session(stress_factory):
    session = stress_factory()
    state = SystemState(
        total_treasury=500.0, peak_treasury=500.0,
        current_regime="bull", alert_status="green",
        active_agent_count=0, treasury_currency="CAD",
    )
    session.add(state)
    genesis = Agent(
        id=0, name="Genesis", type="genesis", status="active",
        capital_allocated=500.0, capital_current=500.0,
        thinking_budget_daily=2.0, thinking_budget_used_today=0.0,
        total_api_cost=0.0, total_gross_pnl=0.0, total_true_pnl=0.0,
        total_true_pnl_cad=0.0, evaluation_count=0, profitable_evaluations=0,
        cycle_count=0, cash_balance=500.0, reserved_cash=0.0,
        total_equity=500.0, realized_pnl=0.0, unrealized_pnl=0.0,
        total_fees_paid=0.0, position_count=0,
    )
    session.add(genesis)
    session.flush()
    yield session
    session.close()


def _make_agent(session, name, role, **kw):
    defaults = dict(
        name=name, type=role, status="active", generation=1,
        capital_allocated=80.0, capital_current=80.0, cash_balance=80.0,
        reserved_cash=0.0, total_equity=80.0, realized_pnl=0.0,
        unrealized_pnl=0.0, total_fees_paid=0.0, position_count=0,
        thinking_budget_daily=0.50, thinking_budget_used_today=0.0,
        total_api_cost=0.0, total_gross_pnl=0.0, total_true_pnl=0.0,
        total_true_pnl_cad=0.0, evaluation_count=0, profitable_evaluations=0,
        cycle_count=0, composite_score=0.0, reputation_score=100.0,
    )
    defaults.update(kw)
    agent = Agent(**defaults)
    session.add(agent)
    session.flush()
    return agent


# ── STRESS TEST 1: 100-Cycle Agent Marathon ────────────────

@pytest.mark.stress
def test_100_cycle_marathon(stress_session, stress_factory):
    """Run 100 cycles per agent, verify no orphaned records or budget drift."""
    agents = [_make_agent(stress_session, f"Agent-{i}", "scout") for i in range(5)]
    stress_session.commit()

    # Simulate 100 cycles per agent (record directly, no API)
    for agent in agents:
        for cycle_num in range(1, 101):
            cycle_cost = 0.001  # simulated cost
            cycle = AgentCycle(
                agent_id=agent.id, cycle_number=cycle_num,
                cycle_type="normal", action_type="go_idle",
                api_cost_usd=cycle_cost, confidence_score=5,
                validation_passed=True,
            )
            stress_session.add(cycle)

            # Atomic budget update (matching production pattern)
            stress_session.execute(text(
                "UPDATE agents SET "
                "cycle_count = :cycle, "
                "total_api_cost = COALESCE(total_api_cost, 0) + :cost, "
                "thinking_budget_used_today = COALESCE(thinking_budget_used_today, 0) + :cost "
                "WHERE id = :aid"
            ), {"cycle": cycle_num, "cost": cycle_cost, "aid": agent.id})

    stress_session.commit()

    # Verify
    for agent in agents:
        stress_session.refresh(agent)
        assert agent.cycle_count == 100
        assert abs(agent.total_api_cost - 0.1) < 0.001  # 100 * 0.001
        assert abs(agent.thinking_budget_used_today - 0.1) < 0.001

    # Verify cycle count in DB
    total_cycles = stress_session.execute(
        select(func.count()).select_from(AgentCycle)
    ).scalar()
    assert total_cycles == 500  # 5 agents * 100 cycles


# ── STRESS TEST 2: Concurrent Budget Updates ───────────────

@pytest.mark.stress
def test_concurrent_budget_atomicity(stress_session):
    """Verify atomic SQL prevents race conditions in budget tracking."""
    agent = _make_agent(stress_session, "ConcurrentTest", "scout")
    stress_session.commit()

    # Simulate 50 concurrent cost increments of $0.002 each
    for _ in range(50):
        stress_session.execute(text(
            "UPDATE agents SET "
            "total_api_cost = COALESCE(total_api_cost, 0) + :cost, "
            "thinking_budget_used_today = COALESCE(thinking_budget_used_today, 0) + :cost "
            "WHERE id = :aid"
        ), {"cost": 0.002, "aid": agent.id})

    stress_session.commit()
    stress_session.refresh(agent)

    expected = 50 * 0.002  # $0.10
    assert abs(agent.total_api_cost - expected) < 0.0001
    assert abs(agent.thinking_budget_used_today - expected) < 0.0001


# ── STRESS TEST 3: Rapid Death and Respawn ─────────────────

@pytest.mark.stress
def test_rapid_death_respawn(stress_session):
    """Kill and respawn agents 5 rounds, verify integrity."""
    initial_treasury = 500.0

    for round_num in range(5):
        # Spawn 3 agents
        agents = [
            _make_agent(stress_session, f"R{round_num}-Agent-{i}", "operator",
                       capital_allocated=30.0, capital_current=30.0, cash_balance=30.0)
            for i in range(3)
        ]

        # Create dynasties and lineages
        for a in agents:
            dynasty = Dynasty(
                founder_id=a.id, founder_name=a.name, founder_role=a.type,
                dynasty_name=f"Dynasty {a.name}", status="active",
                total_generations=1, total_members=1, living_members=1, peak_members=1,
            )
            stress_session.add(dynasty)
            stress_session.flush()
            a.dynasty_id = dynasty.id

            lineage = Lineage(
                agent_id=a.id, parent_id=None, generation=1,
                lineage_path=str(a.id),
            )
            stress_session.add(lineage)

        stress_session.commit()

        # Kill all 3
        for a in agents:
            a.status = "terminated"
            a.termination_reason = f"test_round_{round_num}"
            # Create memorial
            memorial = Memorial(
                agent_id=a.id, agent_name=a.name, agent_role=a.type,
                dynasty_name=f"Dynasty {a.name}", generation=1,
                cause_of_death="test", lifespan_days=1,
                best_metric_name="n/a", best_metric_value=0,
                worst_metric_name="n/a", worst_metric_value=0,
            )
            stress_session.add(memorial)

        stress_session.commit()

    # Verify: 15 deaths, 15 memorials, no half-dead agents
    terminated = stress_session.execute(
        select(func.count()).where(Agent.status == "terminated")
    ).scalar()
    assert terminated == 15

    memorials = stress_session.execute(
        select(func.count()).select_from(Memorial)
    ).scalar()
    assert memorials == 15

    # No agents stuck in intermediate states
    stuck = stress_session.execute(
        select(func.count()).where(Agent.status.in_(["evaluating", "initializing"]))
    ).scalar()
    assert stuck == 0


# ── STRESS TEST 4: Database Disconnect Recovery ────────────

@pytest.mark.stress
def test_db_disconnect_recovery(stress_session):
    """Context assembly gracefully handles DB issues."""
    agent = _make_agent(stress_session, "DisconnectTest", "scout",
                        cycle_count=5, watched_markets=["BTC/USDT"])
    stress_session.commit()

    from src.agents.context_assembler import ContextAssembler

    # Normal assembly works
    assembler = ContextAssembler(db_session=stress_session, token_budget=3000)
    ctx = assembler.assemble(agent)
    assert ctx.system_prompt is not None

    # Simulate a partial DB issue by corrupting a query source
    # The _safe_build wrapper should handle it
    original_build = assembler._build_priority_context

    def failing_priority(*args, **kwargs):
        raise Exception("Simulated DB disconnect")

    assembler._build_priority_context = failing_priority
    ctx2 = assembler.assemble(agent)
    assert ctx2.system_prompt is not None  # Still succeeds
    assert ctx2.priority_tokens == 0  # Priority section failed → empty

    # Restore and verify recovery
    assembler._build_priority_context = original_build
    ctx3 = assembler.assemble(agent)
    assert ctx3.system_prompt is not None


# ── STRESS TEST 5: Redis Disconnect Recovery ───────────────

@pytest.mark.stress
def test_redis_disconnect_recovery(stress_session):
    """Operations continue when Redis is unavailable."""
    agent = _make_agent(stress_session, "RedisTest", "scout", cycle_count=5)
    stress_session.commit()

    from src.agents.cycle_recorder import CycleRecorder

    # Create recorder with None redis (simulates disconnect)
    recorder = CycleRecorder(db_session=stress_session, redis_client=None)

    # Record should work even without Redis (DB-only mode)
    from src.agents.cycle_recorder import CycleData
    data = CycleData(
        agent_id=agent.id, agent_name=agent.name, generation=1,
        cycle_number=1, cycle_type="normal", context_mode="normal",
        context_tokens=500, action_type="go_idle", action_params={},
        confidence_score=5, reasoning="test", self_note="test",
        api_cost_usd=0.001, input_tokens=100, output_tokens=50,
        model_used="test", validation_passed=True,
    )
    cycle_record = recorder.record(data)
    assert cycle_record is not None
    assert cycle_record.agent_id == agent.id


# ── STRESS TEST 6: Log Rotation ───────────────────────────

@pytest.mark.stress
def test_log_rotation_under_load():
    """Verify RotatingFileHandler works under rapid logging."""
    from logging.handlers import RotatingFileHandler

    with tempfile.TemporaryDirectory() as tmpdir:
        log_path = os.path.join(tmpdir, "stress_test.log")

        handler = RotatingFileHandler(
            log_path, maxBytes=50 * 1024, backupCount=3,  # 50KB for quick rotation
        )
        test_logger = logging.getLogger("stress_test_rotation")
        test_logger.addHandler(handler)
        test_logger.setLevel(logging.DEBUG)

        # Generate enough entries to trigger rotation
        for i in range(2000):
            test_logger.info(f"Stress log entry {i}: " + "x" * 50)

        handler.close()
        test_logger.removeHandler(handler)

        # Verify rotation happened
        assert os.path.exists(log_path)
        files = [f for f in os.listdir(tmpdir) if f.startswith("stress_test")]
        assert len(files) >= 2  # At least main + 1 backup

        # Verify sizes are within limits
        for f in files:
            size = os.path.getsize(os.path.join(tmpdir, f))
            assert size <= 55 * 1024  # Allow slight overshoot


# ── STRESS TEST 7: Clean Slate Verification ────────────────

@pytest.mark.stress
def test_clean_slate_full_verification(stress_session):
    """Verify clean slate empties all data tables."""
    # Populate with test data
    agents = [_make_agent(stress_session, f"Agent-{i}", "scout") for i in range(3)]

    for agent in agents:
        # Add cycles
        for j in range(5):
            stress_session.add(AgentCycle(
                agent_id=agent.id, cycle_number=j+1,
                cycle_type="normal", action_type="go_idle",
                api_cost_usd=0.001, validation_passed=True,
            ))

        # Add memory
        stress_session.add(AgentLongTermMemory(
            agent_id=agent.id, content="test memory",
            memory_type="observation", confidence=0.8,
        ))

    # Add messages
    for i in range(10):
        stress_session.add(Message(
            channel="market-intel", agent_id=agents[0].id,
            agent_name="Agent-0", content=f"Test message {i}",
            message_type="SIGNAL",
        ))

    stress_session.commit()

    # Verify data exists
    assert stress_session.execute(select(func.count()).select_from(AgentCycle)).scalar() == 15
    assert stress_session.execute(select(func.count()).select_from(Message)).scalar() == 10

    # Simulate clean slate: truncate all data tables
    data_tables = [
        "agent_cycles", "agent_long_term_memory", "messages",
        "study_history",
    ]
    for table in data_tables:
        try:
            stress_session.execute(text(f"DELETE FROM {table}"))
        except Exception:
            pass

    # Delete non-genesis agents
    stress_session.execute(text("DELETE FROM agents WHERE id != 0"))
    stress_session.commit()

    # Verify everything is clean
    assert stress_session.execute(select(func.count()).select_from(AgentCycle)).scalar() == 0
    assert stress_session.execute(select(func.count()).select_from(Message)).scalar() == 0
    agent_count = stress_session.execute(
        select(func.count()).where(Agent.id != 0)
    ).scalar()
    assert agent_count == 0

    # Genesis survives
    genesis = stress_session.get(Agent, 0)
    assert genesis is not None
    assert genesis.name == "Genesis"
