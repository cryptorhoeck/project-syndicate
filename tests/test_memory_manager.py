"""Tests for the Memory Manager module."""

__version__ = "0.7.0"

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.common.models import Agent, AgentLongTermMemory, AgentReflection, Base
from src.agents.memory_manager import MemoryManager


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
    session.add(Agent(id=1, name="Scout-Alpha", type="scout", status="active", generation=1))
    session.add(Agent(id=2, name="Scout-Beta", type="scout", status="active", generation=2))
    session.commit()
    yield session
    session.close()


class TestLongTermMemory:
    def test_add_memory(self, db_session):
        mm = MemoryManager(db_session)
        mem = mm.add_memory(
            agent_id=1, memory_type="lesson",
            content="SOL volatile during Asian session",
            confidence=0.7,
        )
        assert mem.id is not None
        assert mem.content == "SOL volatile during Asian session"
        assert mem.confidence == 0.7
        assert mem.is_active

    def test_get_active_memories(self, db_session):
        mm = MemoryManager(db_session)
        mm.add_memory(1, "lesson", "Lesson A", confidence=0.9)
        mm.add_memory(1, "pattern", "Pattern B", confidence=0.5)
        mm.add_memory(1, "lesson", "Lesson C (inactive)", confidence=0.3)

        # Deactivate the third
        mems = mm.get_active_memories(1)
        assert len(mems) == 3

        mm.deactivate_memory(mems[2].id)
        active = mm.get_active_memories(1)
        assert len(active) == 2

    def test_promote_memory(self, db_session):
        mm = MemoryManager(db_session)
        mem = mm.add_memory(1, "lesson", "Good lesson", confidence=0.5)
        mm.promote_memory(mem.id)

        db_session.refresh(mem)
        assert mem.times_confirmed == 1
        assert mem.confidence == pytest.approx(0.6, abs=0.01)

    def test_demote_memory(self, db_session):
        mm = MemoryManager(db_session)
        mem = mm.add_memory(1, "lesson", "Bad lesson", confidence=0.3)
        mm.demote_memory(mem.id)

        db_session.refresh(mem)
        assert mem.times_contradicted == 1
        assert mem.confidence == pytest.approx(0.15, abs=0.01)

    def test_demote_to_zero_deactivates(self, db_session):
        mm = MemoryManager(db_session)
        mem = mm.add_memory(1, "lesson", "Wrong lesson", confidence=0.1)
        mm.demote_memory(mem.id)

        db_session.refresh(mem)
        assert not mem.is_active


class TestReflectionProcessing:
    def test_process_reflection(self, db_session):
        mm = MemoryManager(db_session)
        reflection = {
            "what_worked": "Timing improved",
            "what_failed": "False breakout calls",
            "pattern_detected": "Better with higher timeframe",
            "lesson": "Wait for 4h close above resistance",
            "confidence_trend": "improving",
            "confidence_reason": "Hit rate up",
            "strategy_note": "Focus on fewer signals",
            "memory_promotion": [],
            "memory_demotion": [],
        }

        ref = mm.process_reflection(1, 10, reflection)
        assert ref.id is not None
        assert ref.lesson == "Wait for 4h close above resistance"
        assert ref.confidence_trend == "improving"

        # Check that lesson was stored as long-term memory
        memories = mm.get_active_memories(1)
        lessons = [m for m in memories if m.memory_type == "lesson"]
        assert any("4h close above resistance" in m.content for m in lessons)

    def test_reflection_promotes_memory(self, db_session):
        mm = MemoryManager(db_session)
        # Add a memory first
        mm.add_memory(1, "lesson", "SOL volatile during Asian session", confidence=0.5)

        reflection = {
            "what_worked": "Asian session awareness",
            "what_failed": "",
            "pattern_detected": "",
            "lesson": "Continue monitoring Asian session",
            "confidence_trend": "stable",
            "memory_promotion": ["SOL volatile"],
            "memory_demotion": [],
        }

        mm.process_reflection(1, 10, reflection)

        memories = mm.get_active_memories(1)
        sol_mem = next(m for m in memories if "SOL volatile" in m.content)
        assert sol_mem.times_confirmed == 1

    def test_reflection_demotes_memory(self, db_session):
        mm = MemoryManager(db_session)
        mm.add_memory(1, "lesson", "Low volume means safe", confidence=0.5)

        reflection = {
            "what_worked": "",
            "what_failed": "Low volume assumption",
            "pattern_detected": "",
            "lesson": "Volume alone is unreliable",
            "confidence_trend": "declining",
            "memory_promotion": [],
            "memory_demotion": ["Low volume"],
        }

        mm.process_reflection(1, 10, reflection)

        memories = mm.get_active_memories(1)
        lv_mem = next((m for m in memories if "Low volume means safe" in m.content), None)
        if lv_mem:
            assert lv_mem.times_contradicted == 1


class TestMemoryInheritance:
    def test_inherit_memories(self, db_session):
        mm = MemoryManager(db_session)

        # Parent has memories
        mm.add_memory(1, "lesson", "SOL works well in trending markets", confidence=0.8)
        mm.add_memory(1, "pattern", "Volume precedes breakout", confidence=0.6)

        count = mm.inherit_memories(parent_id=1, offspring_id=2)
        assert count >= 2

        offspring_memories = mm.get_active_memories(2)
        assert len(offspring_memories) >= 2

        # Check confidence is reduced
        for mem in offspring_memories:
            assert mem.source == "parent"
            assert mem.confidence < 0.8  # reduced by 20%

    def test_grandparent_inheritance(self, db_session):
        mm = MemoryManager(db_session)

        # Parent has a memory inherited from their parent (grandparent → parent → offspring)
        mm.add_memory(1, "lesson", "Grandparent wisdom", confidence=0.7, source="parent")

        count = mm.inherit_memories(parent_id=1, offspring_id=2)
        assert count >= 1

        offspring_memories = mm.get_active_memories(2)
        gp_mems = [m for m in offspring_memories if m.source == "grandparent"]
        assert len(gp_mems) >= 1
        assert gp_mems[0].confidence < 0.7  # further reduced
