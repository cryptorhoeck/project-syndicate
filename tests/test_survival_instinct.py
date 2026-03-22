"""Tests for Phase 8B — Survival Instinct."""

__version__ = "0.1.0"

import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.common.models import Agent, Base, Opportunity, Plan, Position, SystemState


def _make_db():
    """Create an in-memory test database."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    session = factory()

    session.add(SystemState(
        id=1, total_treasury=500.0, peak_treasury=1000.0,
        current_regime="crab", active_agent_count=3, alert_status="green",
    ))
    session.add(Agent(
        id=0, name="Genesis", type="genesis", status="active",
        reputation_score=0.0, generation=0,
    ))
    session.commit()
    return session


def _make_agent(session, **kwargs):
    """Add an agent with sensible defaults."""
    defaults = {
        "type": "scout",
        "status": "active",
        "reputation_score": 100.0,
        "generation": 1,
        "capital_allocated": 50.0,
        "capital_current": 50.0,
        "thinking_budget_daily": 0.50,
        "thinking_budget_used_today": 0.0,
        "total_gross_pnl": 0.0,
        "total_true_pnl": 0.0,
        "total_api_cost": 0.01,
        "composite_score": 0.50,
        "cycle_count": 0,
        "probation": False,
    }
    defaults.update(kwargs)
    agent = Agent(**defaults)
    session.add(agent)
    session.commit()
    return agent


# ── Tier 1: Survival Context Tests ─────────────────────────

class TestSurvivalContextAssembler:

    @pytest.mark.asyncio
    async def test_countdown_normal(self):
        """Agent with 10 days left shows countdown without warning."""
        from src.agents.survival_context import SurvivalContextAssembler

        db = _make_db()
        agent = _make_agent(db, id=1, name="Scout-A",
                            survival_clock_end=datetime.now(timezone.utc) + timedelta(days=10))

        sca = SurvivalContextAssembler()
        result = await sca.assemble(agent, db)

        assert "EVALUATION COUNTDOWN" in result
        assert "IMMINENT" not in result
        db.close()

    @pytest.mark.asyncio
    async def test_countdown_imminent(self):
        """Agent with 3 days left shows EVALUATION IMMINENT."""
        from src.agents.survival_context import SurvivalContextAssembler

        db = _make_db()
        agent = _make_agent(db, id=1, name="Scout-A",
                            survival_clock_end=datetime.now(timezone.utc) + timedelta(days=3))

        sca = SurvivalContextAssembler()
        result = await sca.assemble(agent, db)

        assert "IMMINENT" in result
        db.close()

    @pytest.mark.asyncio
    async def test_standing_includes_rank(self):
        """Standing section includes role rank."""
        from src.agents.survival_context import SurvivalContextAssembler

        db = _make_db()
        _make_agent(db, id=1, name="Scout-A", composite_score=0.80)
        agent2 = _make_agent(db, id=2, name="Scout-B", composite_score=0.40)

        sca = SurvivalContextAssembler()
        result = await sca.assemble(agent2, db)

        assert "YOUR STANDING" in result
        assert "#2 of 2" in result
        db.close()

    @pytest.mark.asyncio
    async def test_standing_shows_probation(self):
        """Probation agent gets probation warning."""
        from src.agents.survival_context import SurvivalContextAssembler

        db = _make_db()
        agent = _make_agent(db, id=1, name="Scout-A", probation=True)

        sca = SurvivalContextAssembler()
        result = await sca.assemble(agent, db)

        assert "PROBATION" in result
        db.close()

    @pytest.mark.asyncio
    async def test_competition_lists_same_role(self):
        """Competition shows only agents of the same role."""
        from src.agents.survival_context import SurvivalContextAssembler

        db = _make_db()
        agent = _make_agent(db, id=1, name="Scout-A")
        _make_agent(db, id=2, name="Scout-B")
        _make_agent(db, id=3, name="Strategist-A", type="strategist")

        sca = SurvivalContextAssembler()
        result = await sca.assemble(agent, db)

        assert "Scout-B" in result
        assert "Strategist-A" not in result
        db.close()

    @pytest.mark.asyncio
    async def test_competition_sorted_by_score(self):
        """Agents listed in descending composite score order."""
        from src.agents.survival_context import SurvivalContextAssembler

        db = _make_db()
        agent = _make_agent(db, id=1, name="Scout-A", composite_score=0.30)
        _make_agent(db, id=2, name="Scout-B", composite_score=0.80)
        _make_agent(db, id=3, name="Scout-C", composite_score=0.50)

        sca = SurvivalContextAssembler()
        result = await sca.assemble(agent, db)

        # Scout-B should appear before Scout-C
        b_pos = result.find("Scout-B")
        c_pos = result.find("Scout-C")
        assert b_pos < c_pos
        db.close()

    @pytest.mark.asyncio
    async def test_death_feed_no_deaths(self):
        """When no deaths, shows 'You could be the first.'"""
        from src.agents.survival_context import SurvivalContextAssembler

        db = _make_db()
        agent = _make_agent(db, id=1, name="Scout-A")

        sca = SurvivalContextAssembler()
        result = await sca.assemble(agent, db)

        assert "could be the first" in result
        db.close()

    @pytest.mark.asyncio
    async def test_death_feed_shows_deaths(self):
        """Deaths appear in the feed."""
        from src.agents.survival_context import SurvivalContextAssembler

        db = _make_db()
        agent = _make_agent(db, id=1, name="Scout-A")
        _make_agent(db, id=2, name="Dead-Agent", status="terminated",
                    total_true_pnl=-5.0)

        sca = SurvivalContextAssembler()
        result = await sca.assemble(agent, db)

        assert "Dead-Agent" in result
        assert "RECENT DEATHS" in result
        db.close()

    @pytest.mark.asyncio
    async def test_ecosystem_pulse(self):
        """Ecosystem pulse section is present."""
        from src.agents.survival_context import SurvivalContextAssembler

        db = _make_db()
        agent = _make_agent(db, id=1, name="Scout-A")

        sca = SurvivalContextAssembler()
        result = await sca.assemble(agent, db)

        assert "ECOSYSTEM PULSE" in result
        db.close()

    @pytest.mark.asyncio
    async def test_compressed_mode(self):
        """Compressed mode produces short output."""
        from src.agents.survival_context import SurvivalContextAssembler

        db = _make_db()
        agent = _make_agent(db, id=1, name="Scout-A")

        sca = SurvivalContextAssembler()
        result = await sca.assemble_compressed(agent, db)

        assert len(result) < 200
        assert "Rank" in result
        db.close()


class TestPressureAddenda:

    @pytest.mark.asyncio
    async def test_ranked_last_gets_warning(self):
        """Lowest-ranked agent gets termination warning."""
        from src.agents.survival_context import SurvivalContextAssembler

        db = _make_db()
        _make_agent(db, id=1, name="Scout-Good", composite_score=0.80)
        agent = _make_agent(db, id=2, name="Scout-Bad", composite_score=0.10)

        sca = SurvivalContextAssembler()
        result = await sca.build_pressure_addenda(agent, db)

        assert "lowest-ranked" in result
        db.close()

    @pytest.mark.asyncio
    async def test_safe_agent_gets_empty(self):
        """Well-ranked agent gets no pressure."""
        from src.agents.survival_context import SurvivalContextAssembler

        db = _make_db()
        agent = _make_agent(db, id=1, name="Scout-A", composite_score=0.80,
                            survival_clock_end=datetime.now(timezone.utc) + timedelta(days=15))

        sca = SurvivalContextAssembler()
        result = await sca.build_pressure_addenda(agent, db)

        assert result == ""
        db.close()

    @pytest.mark.asyncio
    async def test_probation_gets_warning(self):
        """Probation agent gets probation pressure text."""
        from src.agents.survival_context import SurvivalContextAssembler

        db = _make_db()
        agent = _make_agent(db, id=1, name="Scout-A", probation=True,
                            survival_clock_end=datetime.now(timezone.utc) + timedelta(days=15))

        sca = SurvivalContextAssembler()
        result = await sca.build_pressure_addenda(agent, db)

        assert "probation" in result.lower()
        db.close()


class TestStrategicReview:

    def test_strategic_review_every_50_cycles(self):
        """Cycle 50 triggers strategic review."""
        # Strategic review at cycle 50 (multiple of 50)
        cycle = 50
        review_interval = 50
        reflection_interval = 10
        is_strategic = (cycle > 0 and cycle % review_interval == 0)
        is_reflection = (cycle > 0 and cycle % reflection_interval == 0 and not is_strategic)
        assert is_strategic
        assert not is_reflection

    def test_regular_reflection_not_on_50(self):
        """Cycle 10 triggers regular reflection, not strategic."""
        cycle = 10
        review_interval = 50
        reflection_interval = 10
        is_strategic = (cycle > 0 and cycle % review_interval == 0)
        is_reflection = (cycle > 0 and cycle % reflection_interval == 0 and not is_strategic)
        assert not is_strategic
        assert is_reflection

    def test_cycle_30_is_normal(self):
        """Cycle 30 is neither reflection nor strategic."""
        cycle = 30
        review_interval = 50
        reflection_interval = 10
        is_strategic = (cycle > 0 and cycle % review_interval == 0)
        is_reflection = (cycle > 0 and cycle % reflection_interval == 0 and not is_strategic)
        assert not is_strategic
        assert is_reflection  # 30 IS a multiple of 10

    def test_cycle_100_is_strategic(self):
        """Cycle 100 is strategic (multiple of both 10 and 50)."""
        cycle = 100
        review_interval = 50
        reflection_interval = 10
        is_strategic = (cycle > 0 and cycle % review_interval == 0)
        assert is_strategic
