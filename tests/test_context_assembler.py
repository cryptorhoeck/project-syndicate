"""Tests for the Context Assembler module."""

__version__ = "0.7.0"

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.common.models import Agent, AgentLongTermMemory, Base, Message, SystemState
from src.agents.budget_gate import BudgetStatus
from src.agents.context_assembler import ContextAssembler, ContextMode


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

    # Seed system state
    session.add(SystemState(
        id=1, total_treasury=500.0, peak_treasury=1000.0,
        current_regime="crab", active_agent_count=3, alert_status="green",
    ))

    # Seed a scout agent
    session.add(Agent(
        id=1, name="Scout-Alpha", type="scout", status="active",
        capital_allocated=50.0, capital_current=52.0,
        reputation_score=120.0, generation=1,
        thinking_budget_daily=0.50, thinking_budget_used_today=0.10,
        total_gross_pnl=5.0, total_true_pnl=3.0, total_api_cost=0.05,
    ))

    # Seed a strategist in crisis
    session.add(Agent(
        id=2, name="Strategist-Beta", type="strategist", status="active",
        capital_allocated=100.0, capital_current=80.0,
        reputation_score=90.0, generation=2,
        thinking_budget_daily=0.50, thinking_budget_used_today=0.10,
        total_gross_pnl=-15.0, total_true_pnl=-18.0, total_api_cost=0.03,
    ))
    session.commit()
    yield session
    session.close()


class TestModeDetection:
    def test_normal_mode(self, db_session):
        agent = db_session.query(Agent).get(1)
        # Scout defaults to HUNTING
        assembler = ContextAssembler(db_session)
        mode = assembler.determine_mode(agent, BudgetStatus.NORMAL)
        assert mode == ContextMode.HUNTING

    def test_crisis_mode(self, db_session):
        agent = db_session.query(Agent).get(2)
        assembler = ContextAssembler(db_session)
        mode = assembler.determine_mode(agent, BudgetStatus.NORMAL)
        assert mode == ContextMode.CRISIS

    def test_survival_mode(self, db_session):
        agent = db_session.query(Agent).get(1)
        assembler = ContextAssembler(db_session)
        mode = assembler.determine_mode(agent, BudgetStatus.SURVIVAL_MODE)
        assert mode == ContextMode.SURVIVAL

    def test_normal_mode_for_non_scout(self, db_session):
        # Add an operator in good standing
        db_session.add(Agent(
            id=3, name="Operator-Gamma", type="operator", status="active",
            capital_allocated=50.0, capital_current=55.0,
            reputation_score=100.0, generation=1,
            thinking_budget_daily=0.50, thinking_budget_used_today=0.10,
            total_gross_pnl=5.0, total_true_pnl=3.0,
        ))
        db_session.commit()

        agent = db_session.query(Agent).get(3)
        assembler = ContextAssembler(db_session)
        mode = assembler.determine_mode(agent, BudgetStatus.NORMAL)
        assert mode == ContextMode.NORMAL


class TestAssembly:
    def test_assemble_returns_context(self, db_session):
        agent = db_session.query(Agent).get(1)
        assembler = ContextAssembler(db_session, token_budget=3000)
        context = assembler.assemble(agent)

        assert isinstance(context.system_prompt, str)
        assert isinstance(context.user_prompt, str)
        assert context.total_tokens > 0
        assert context.mode == ContextMode.HUNTING

    def test_assemble_includes_agent_identity(self, db_session):
        agent = db_session.query(Agent).get(1)
        assembler = ContextAssembler(db_session)
        context = assembler.assemble(agent)

        assert "Scout-Alpha" in context.system_prompt
        assert "scout" in context.system_prompt
        assert "Scout-Alpha" in context.user_prompt

    def test_assemble_includes_actions(self, db_session):
        agent = db_session.query(Agent).get(1)
        assembler = ContextAssembler(db_session)
        context = assembler.assemble(agent)

        assert "broadcast_opportunity" in context.system_prompt
        assert "go_idle" in context.system_prompt

    def test_assemble_reflection(self, db_session):
        agent = db_session.query(Agent).get(1)
        assembler = ContextAssembler(db_session)
        context = assembler.assemble(agent, cycle_type="reflection")

        assert "REFLECTION" in context.system_prompt
        assert "what_worked" in context.system_prompt

    def test_assemble_survival_halves_budget(self, db_session):
        agent = db_session.query(Agent).get(1)
        assembler = ContextAssembler(db_session, token_budget=3000)

        normal = assembler.assemble(agent, budget_status=BudgetStatus.NORMAL)
        survival = assembler.assemble(agent, budget_status=BudgetStatus.SURVIVAL_MODE)

        assert "SURVIVAL MODE" in survival.system_prompt

    def test_mandatory_context_always_present(self, db_session):
        agent = db_session.query(Agent).get(1)
        assembler = ContextAssembler(db_session)
        context = assembler.assemble(agent)

        assert "IDENTITY" in context.user_prompt
        assert "CURRENT STATE" in context.user_prompt
        assert "SYSTEM STATE" in context.user_prompt


class TestMemoryContext:
    def test_memory_included_when_present(self, db_session):
        db_session.add(AgentLongTermMemory(
            agent_id=1, memory_type="lesson",
            content="SOL volatile during Asian session",
            confidence=0.8, source="self",
        ))
        db_session.commit()

        agent = db_session.query(Agent).get(1)
        assembler = ContextAssembler(db_session)
        context = assembler.assemble(agent)

        assert "SOL volatile" in context.user_prompt

    def test_no_memory_shows_placeholder(self, db_session):
        agent = db_session.query(Agent).get(1)
        assembler = ContextAssembler(db_session)
        context = assembler.assemble(agent)

        assert "No memories yet" in context.user_prompt
