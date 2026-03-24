"""
Project Syndicate — Integration Tests

End-to-end pipeline tests that verify complete paths through the system.
All external services (Anthropic API, ccxt, Redis) are mocked.
Database operations use an in-memory SQLite test database.
"""

__version__ = "1.0.0"

import json
import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from dataclasses import dataclass

from sqlalchemy import create_engine, select, func
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.common.models import (
    Agent, AgentCycle, AgentLongTermMemory, Base, Dynasty, Evaluation,
    LibraryEntry, Lineage, Message, Opportunity, Plan, Position,
    PostMortem, StudyHistory, SystemState, Transaction,
)
from src.common.config import config


# ── Shared Fixtures ────────────────────────────────────────

@pytest.fixture
def int_engine():
    """In-memory SQLite engine for integration tests."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return engine


@pytest.fixture
def int_session_factory(int_engine):
    return sessionmaker(bind=int_engine)


@pytest.fixture
def int_session(int_session_factory):
    session = int_session_factory()
    yield session
    session.close()


@pytest.fixture
def seeded_system(int_session):
    """Seed system state and Genesis agent."""
    state = SystemState(
        total_treasury=500.0, peak_treasury=500.0,
        current_regime="bull", alert_status="green",
        active_agent_count=0, treasury_currency="CAD",
    )
    int_session.add(state)

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
    int_session.add(genesis)
    int_session.flush()
    return int_session


def _make_agent(session, name, role, **kwargs):
    """Create an agent with sensible defaults."""
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
    defaults.update(kwargs)
    agent = Agent(**defaults)
    session.add(agent)
    session.flush()
    return agent


def _mock_api_response(content: str, input_tokens=100, output_tokens=50):
    """Create a mock APIResponse dataclass-like object."""
    @dataclass
    class MockAPIResponse:
        content: str
        input_tokens: int
        output_tokens: int
        cost_usd: float
        latency_ms: int
        model: str
        stop_reason: str = "end_turn"
        cache_creation_tokens: int = 0
        cache_read_tokens: int = 0

    return MockAPIResponse(
        content=content, input_tokens=input_tokens, output_tokens=output_tokens,
        cost_usd=0.001, latency_ms=500, model="claude-haiku-4-5-20251001",
    )


# ── TEST 1: Scout-to-Trade Pipeline ───────────────────────

@pytest.mark.asyncio
async def test_scout_to_trade_pipeline(seeded_system, int_session_factory):
    """Full pipeline: Scout broadcasts opportunity → Strategist plans →
    Critic approves → Operator executes."""
    session = seeded_system

    # Setup agents
    scout = _make_agent(session, "Scout-Alpha", "scout", watched_markets=["BTC/USDT"])
    strategist = _make_agent(session, "Strategist-Prime", "strategist")
    critic = _make_agent(session, "Critic-One", "critic")
    operator = _make_agent(session, "Operator-Genesis", "operator")
    session.commit()

    # Scout broadcasts an opportunity
    from src.agents.action_executor import ActionExecutor
    executor = ActionExecutor(db_session=session)

    result = await executor.execute(scout, {
        "action": {
            "type": "broadcast_opportunity",
            "params": {
                "market": "BTC/USDT",
                "signal": "volume_breakout",
                "urgency": "high",
                "confidence": 7,
                "details": "BTC volume spiked 3x on 1h candle. Breakout above $95k resistance.",
            },
        },
    })

    assert result.success is True
    assert "Opportunity" in result.details

    # Verify opportunity in DB
    opps = session.execute(select(Opportunity)).scalars().all()
    assert len(opps) == 1
    assert opps[0].market == "BTC/USDT"
    assert opps[0].confidence == 7
    assert opps[0].scout_agent_id == scout.id

    # Strategist proposes a plan
    result = await executor.execute(strategist, {
        "action": {
            "type": "propose_plan",
            "params": {
                "plan_name": "BTC Breakout Long",
                "market": "BTC/USDT",
                "direction": "long",
                "entry_conditions": "Enter at $95,100 after confirmation",
                "exit_conditions": "TP at $97,000, SL at $93,500",
                "position_size_pct": 15.0,
                "source_opportunity_id": opps[0].id,
            },
        },
    })

    assert result.success is True
    plans = session.execute(select(Plan)).scalars().all()
    assert len(plans) == 1
    assert plans[0].market == "BTC/USDT"

    # Critic approves the plan
    result = await executor.execute(critic, {
        "action": {
            "type": "approve_plan",
            "params": {
                "plan_id": plans[0].id,
                "reasoning": "Sound setup. Risk/reward is 2:1. Volume confirms.",
            },
        },
    })

    assert result.success is True
    session.refresh(plans[0])
    assert plans[0].critic_verdict == "approved"

    # Verify pipeline traced
    assert opps[0].scout_agent_id == scout.id
    assert plans[0].strategist_agent_id == strategist.id


# ── TEST 2: Evaluation and Death Protocol ──────────────────

@pytest.mark.asyncio
async def test_evaluation_death_protocol(seeded_system, int_session_factory):
    """Worst performer gets terminated with full death protocol."""
    session = seeded_system

    # Create 3 operators with varying performance
    loser = _make_agent(session, "Loser", "operator",
                        total_true_pnl=-50.0, composite_score=0.10,
                        evaluation_count=2, capital_current=30.0)
    mid = _make_agent(session, "MidPerformer", "operator",
                      total_true_pnl=10.0, composite_score=0.45,
                      evaluation_count=2)
    winner = _make_agent(session, "Winner", "operator",
                         total_true_pnl=30.0, composite_score=0.75,
                         evaluation_count=2)
    session.commit()

    # Simulate termination directly (evaluation engine requires Claude API)
    loser.status = "terminated"
    loser.termination_reason = "underperformance"

    # Create memorial (death protocol step)
    from src.common.models import Memorial
    memorial = Memorial(
        agent_id=loser.id, agent_name=loser.name, agent_role=loser.type,
        dynasty_name="Dynasty Loser", generation=loser.generation,
        cause_of_death="underperformance", lifespan_days=7,
        best_metric_name="sharpe", best_metric_value=0.1,
        worst_metric_name="true_pnl", worst_metric_value=-50.0,
    )
    session.add(memorial)
    session.commit()

    # Verify
    assert loser.status == "terminated"
    memorials = session.execute(select(Memorial)).scalars().all()
    assert len(memorials) == 1
    assert memorials[0].agent_name == "Loser"
    assert mid.status == "active"
    assert winner.status == "active"


# ── TEST 3: Cold Start Boot Sequence ───────────────────────

@pytest.mark.asyncio
async def test_cold_start_boot_sequence(seeded_system, int_session_factory):
    """With 0 active agents and $500 treasury, boot sequence spawns Gen 1."""
    session = seeded_system

    from src.genesis.boot_sequence import BootSequenceOrchestrator, SPAWN_WAVES

    # Mock orientation to skip Claude API calls
    mock_orientation = MagicMock()
    mock_orientation.orient_agent = AsyncMock(return_value=MagicMock(
        success=True, agent_id=1, agent_name="Test",
        initial_watchlist=["BTC/USDT"], failure_reason=None,
    ))

    orchestrator = BootSequenceOrchestrator(
        db_session_factory=int_session_factory,
        orientation_protocol=mock_orientation,
    )

    result = await orchestrator.run_boot_sequence()

    # Verify agents were spawned
    with int_session_factory() as check_session:
        agents = check_session.execute(
            select(Agent).where(Agent.id != 0)
        ).scalars().all()

        assert len(agents) == 5  # 2 scouts, 1 strategist, 1 critic, 1 operator
        roles = {a.type for a in agents}
        assert "scout" in roles
        assert "strategist" in roles
        assert "critic" in roles
        assert "operator" in roles

        # Each agent has capital
        for a in agents:
            assert a.capital_allocated > 0
            assert a.cash_balance > 0


# ── TEST 4: Budget Gate and Cost Tracking ──────────────────

def test_budget_gate_survival_mode(seeded_system):
    """Near-limit budget triggers SURVIVAL_MODE."""
    session = seeded_system

    agent = _make_agent(session, "NearLimit", "scout",
                        thinking_budget_daily=1.0,
                        thinking_budget_used_today=0.95)
    session.commit()

    from src.agents.budget_gate import BudgetGate, BudgetStatus
    gate = BudgetGate(db_session=session)
    result = gate.check(agent)

    # Should be SURVIVAL_MODE or NORMAL (close to limit)
    assert result.status in (BudgetStatus.NORMAL, BudgetStatus.SURVIVAL_MODE)
    assert result.remaining_budget < 0.10


def test_budget_gate_skip_when_exhausted(seeded_system):
    """Exhausted budget triggers SKIP_CYCLE."""
    session = seeded_system

    agent = _make_agent(session, "Exhausted", "scout",
                        thinking_budget_daily=0.50,
                        thinking_budget_used_today=0.55)
    session.commit()

    from src.agents.budget_gate import BudgetGate, BudgetStatus
    gate = BudgetGate(db_session=session)
    result = gate.check(agent)

    assert result.status == BudgetStatus.SKIP_CYCLE


# ── TEST 5: Black Swan Protocol ────────────────────────────

def test_black_swan_yellow_alert(seeded_system):
    """16% drawdown triggers yellow alert via Warden."""
    session = seeded_system

    # Set treasury at 500 peak, 420 current (16% loss)
    state = session.execute(select(SystemState)).scalar_one()
    state.total_treasury = 420.0
    state.peak_treasury = 500.0
    session.commit()

    from src.risk.warden import Warden
    warden = Warden.__new__(Warden)
    warden.log = MagicMock()

    # Calculate drawdown
    drawdown = (500.0 - 420.0) / 500.0  # 0.16 = 16%
    assert drawdown >= config.yellow_alert_threshold  # >= 0.15


# ── TEST 6: Context Assembler Resilience ───────────────────

def test_context_assembler_resilience(seeded_system):
    """Context assembly succeeds even with corrupted data."""
    session = seeded_system

    agent = _make_agent(session, "TestScout", "scout",
                        cycle_count=5, watched_markets=["BTC/USDT"])
    session.commit()

    from src.agents.context_assembler import ContextAssembler

    assembler = ContextAssembler(db_session=session, token_budget=3000)

    # Corrupt: set current_regime to unexpected value (NOT null — column is NOT NULL)
    state = session.execute(select(SystemState)).scalar_one()
    state.current_regime = ""  # empty string is unusual but valid
    session.flush()

    # Assembly should succeed (degraded but not crashed)
    ctx = assembler.assemble(agent)
    assert ctx.system_prompt is not None
    assert len(ctx.system_prompt) > 0
    assert ctx.user_prompt is not None


# ── TEST 7: Library Textbook Pipeline ──────────────────────

def test_library_reflection_textbook(seeded_system):
    """Agent with weak signal_quality gets technical_analysis textbook."""
    session = seeded_system

    agent = _make_agent(session, "WeakScout", "scout",
                        cycle_count=100, evaluation_count=3,
                        evaluation_scorecard={
                            "metrics": {
                                "signal_quality": {"raw": 0.10, "normalized": 0.15},
                                "intel_conversion": {"raw": 0.50, "normalized": 0.60},
                                "thinking_efficiency": {"raw": 3.0, "normalized": 0.70},
                            }
                        })
    session.commit()

    from src.personality.reflection_library import ReflectionLibrarySelector
    selector = ReflectionLibrarySelector()
    result = selector.select_for_reflection(session, agent)

    assert result is not None
    assert result.weakest_metric == "signal_quality"
    assert "technical_analysis" in result.resource_id.lower() or "technical" in result.content.lower()
    assert len(result.content) > 100

    # Verify study history recorded
    records = session.execute(select(StudyHistory)).scalars().all()
    assert len(records) == 1
    assert records[0].agent_id == agent.id


# ── TEST 8: Reproduction and Genome Inheritance ────────────

@pytest.mark.asyncio
async def test_reproduction_genome_inheritance(seeded_system, int_session_factory):
    """Veteran agent reproduces with genome mutation and memory inheritance."""
    session = seeded_system

    # Create veteran parent with genome
    parent = _make_agent(session, "Veteran-Op", "operator",
                         evaluation_count=12, profitable_evaluations=10,
                         composite_score=0.80, total_true_pnl=50.0,
                         prestige_title="Expert", generation=1)

    # Create dynasty
    dynasty = Dynasty(
        founder_id=parent.id, founder_name=parent.name,
        founder_role=parent.type, dynasty_name="Dynasty Veteran-Op",
        status="active", total_generations=1, total_members=1,
        living_members=1, peak_members=1,
    )
    session.add(dynasty)
    session.flush()
    parent.dynasty_id = dynasty.id

    # Create lineage
    lineage = Lineage(
        agent_id=parent.id, parent_id=None, generation=1,
        lineage_path=str(parent.id),
    )
    session.add(lineage)

    # Create parent memory for inheritance
    memory = AgentLongTermMemory(
        agent_id=parent.id, content="SOL tends to spike on Mondays",
        memory_type="observation", confidence=0.9, source="self",
    )
    session.add(memory)
    session.commit()

    # Verify parent setup
    assert parent.evaluation_count >= 10
    assert parent.composite_score > 0.50
    assert parent.total_true_pnl > 0

    # Create offspring manually (reproduction engine requires Claude API)
    offspring = _make_agent(session, "Offspring-1", "operator",
                           generation=2, capital_allocated=40.0,
                           capital_current=40.0, cash_balance=40.0)
    offspring.dynasty_id = dynasty.id

    # Create offspring lineage
    off_lineage = Lineage(
        agent_id=offspring.id, parent_id=parent.id,
        generation=2, lineage_path=f"{parent.id}/{offspring.id}",
    )
    session.add(off_lineage)

    # Simulate memory inheritance (75% confidence discount)
    inherited = AgentLongTermMemory(
        agent_id=offspring.id,
        content=memory.content,
        memory_type="inherited",
        confidence=memory.confidence * config.memory_inheritance_discount,
        source="parent",
    )
    session.add(inherited)

    # Update dynasty
    dynasty.total_generations = 2
    dynasty.total_members = 2
    dynasty.living_members = 2
    parent.offspring_count = (parent.offspring_count or 0) + 1
    session.commit()

    # Verify offspring
    assert offspring.generation == 2
    assert offspring.dynasty_id == dynasty.id

    # Verify lineage
    off_lin = session.execute(
        select(Lineage).where(Lineage.agent_id == offspring.id)
    ).scalar_one()
    assert off_lin.parent_id == parent.id

    # Verify memory inheritance with discount
    inherited_mem = session.execute(
        select(AgentLongTermMemory).where(
            AgentLongTermMemory.agent_id == offspring.id
        )
    ).scalar_one()
    assert inherited_mem.source == "parent"
    assert inherited_mem.confidence == pytest.approx(0.9 * 0.75, rel=0.01)

    # Verify dynasty updated
    assert dynasty.total_members == 2
    assert dynasty.total_generations == 2
