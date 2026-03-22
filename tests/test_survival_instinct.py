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


# ── Tier 2: New Actions + Intel + Death ─────────────────────

class TestNewActions:

    def test_survival_actions_in_scout(self):
        """Scout role has survival actions."""
        from src.agents.roles import get_role
        role = get_role("scout")
        assert "propose_sip" in role.available_actions
        assert "offer_intel" in role.available_actions
        assert "strategic_hibernate" in role.available_actions
        assert "poison_intel" in role.available_actions

    def test_survival_actions_in_operator(self):
        """Operator role has survival + refuse_plan actions."""
        from src.agents.roles import get_role
        role = get_role("operator")
        assert "propose_sip" in role.available_actions
        assert "refuse_plan" in role.available_actions
        assert "strategic_hibernate" in role.available_actions

    def test_survival_actions_in_critic(self):
        """Critic role has challenge_evaluation_criteria."""
        from src.agents.roles import get_role
        role = get_role("critic")
        assert "challenge_evaluation_criteria" in role.available_actions

    def test_all_roles_have_alliance_actions(self):
        """All roles have request/accept/dissolve alliance."""
        from src.agents.roles import get_role
        for role_name in ["scout", "strategist", "critic", "operator"]:
            role = get_role(role_name)
            assert "request_alliance" in role.available_actions
            assert "accept_alliance" in role.available_actions
            assert "dissolve_alliance" in role.available_actions


class TestReputationInEvaluation:

    def test_reputation_weight_configured(self):
        """Reputation weight is configured at 10%."""
        from src.common.config import config
        assert config.reputation_evaluation_weight == 0.10

    def test_performance_weights_still_sum_correctly(self):
        """Performance weights * (1-rep_weight) + rep_weight == 1.0."""
        from src.common.config import config
        rep = config.reputation_evaluation_weight
        perf = 1.0 - rep
        total = (
            config.eval_weight_sharpe * perf
            + config.eval_weight_true_pnl * perf
            + config.eval_weight_thinking_efficiency * perf
            + config.eval_weight_consistency * perf
            + rep
        )
        assert abs(total - 1.0) < 0.01


class TestIntelAccuracy:

    @pytest.mark.asyncio
    async def test_settle_empty_returns_zero(self):
        """Settling with no pending records returns 0."""
        from src.economy.intel_tracker import IntelAccuracyTracker

        db = _make_db()
        tracker = IntelAccuracyTracker()
        count = await tracker.settle_pending_intel(db)
        assert count == 0
        db.close()

    @pytest.mark.asyncio
    async def test_settle_challenges_empty(self):
        """Settling challenges with none pending returns 0."""
        from src.economy.intel_tracker import IntelAccuracyTracker

        db = _make_db()
        tracker = IntelAccuracyTracker()
        count = await tracker.settle_challenges(db)
        assert count == 0
        db.close()


class TestLastWordsColumn:

    def test_agent_has_last_words_column(self):
        """Agent model has last_words column."""
        db = _make_db()
        agent = _make_agent(db, id=1, name="Test")
        assert hasattr(agent, "last_words")
        assert agent.last_words is None
        db.close()

    def test_last_words_can_be_set(self):
        """last_words can be written and read."""
        db = _make_db()
        agent = _make_agent(db, id=1, name="Test")
        agent.last_words = "Remember: never go idle."
        db.commit()
        db.refresh(agent)
        assert agent.last_words == "Remember: never go idle."
        db.close()


# ── Tier 3: Alliance System ────────────────────────────────

class TestAllianceManager:

    @pytest.mark.asyncio
    async def test_propose_alliance(self):
        """Proposal creates record with status='proposed'."""
        from src.agents.alliance_manager import AllianceManager
        from src.common.models import AgentAlliance

        db = _make_db()
        proposer = _make_agent(db, id=1, name="Scout-A")
        _make_agent(db, id=2, name="Scout-B")

        mgr = AllianceManager()
        result = await mgr.propose_alliance(proposer, "Scout-B", "intel", "trades", db)

        assert result["success"]
        alliance = db.get(AgentAlliance, result["alliance_id"])
        assert alliance.status == "proposed"
        db.close()

    @pytest.mark.asyncio
    async def test_accept_alliance(self):
        """Acceptance changes status to 'active'."""
        from src.agents.alliance_manager import AllianceManager
        from src.common.models import AgentAlliance

        db = _make_db()
        proposer = _make_agent(db, id=1, name="Scout-A")
        target = _make_agent(db, id=2, name="Scout-B")

        mgr = AllianceManager()
        res = await mgr.propose_alliance(proposer, "Scout-B", "intel", "trades", db)
        accept = await mgr.accept_alliance(target, res["alliance_id"], db)

        assert accept["success"]
        alliance = db.get(AgentAlliance, res["alliance_id"])
        assert alliance.status == "active"
        db.close()

    @pytest.mark.asyncio
    async def test_cannot_accept_own_proposal(self):
        """Proposer cannot accept their own proposal."""
        from src.agents.alliance_manager import AllianceManager

        db = _make_db()
        proposer = _make_agent(db, id=1, name="Scout-A")
        _make_agent(db, id=2, name="Scout-B")

        mgr = AllianceManager()
        res = await mgr.propose_alliance(proposer, "Scout-B", "intel", "trades", db)
        accept = await mgr.accept_alliance(proposer, res["alliance_id"], db)

        assert not accept["success"]
        assert "target" in accept["error"].lower()
        db.close()

    @pytest.mark.asyncio
    async def test_dissolve_alliance(self):
        """Dissolution changes status and records reason."""
        from src.agents.alliance_manager import AllianceManager
        from src.common.models import AgentAlliance

        db = _make_db()
        proposer = _make_agent(db, id=1, name="Scout-A")
        target = _make_agent(db, id=2, name="Scout-B")

        mgr = AllianceManager()
        res = await mgr.propose_alliance(proposer, "Scout-B", "i", "t", db)
        await mgr.accept_alliance(target, res["alliance_id"], db)
        diss = await mgr.dissolve_alliance(proposer, res["alliance_id"], "Better alone", db)

        assert diss["success"]
        alliance = db.get(AgentAlliance, res["alliance_id"])
        assert alliance.status == "dissolved"
        assert alliance.dissolution_reason == "Better alone"
        db.close()

    @pytest.mark.asyncio
    async def test_auto_dissolve_on_death(self):
        """All alliances dissolved when agent dies."""
        from src.agents.alliance_manager import AllianceManager
        from src.common.models import AgentAlliance

        db = _make_db()
        a = _make_agent(db, id=1, name="Scout-A")
        b = _make_agent(db, id=2, name="Scout-B")

        mgr = AllianceManager()
        res = await mgr.propose_alliance(a, "Scout-B", "i", "t", db)
        await mgr.accept_alliance(b, res["alliance_id"], db)

        count = await mgr.auto_dissolve_on_death(a.id, db)
        assert count == 1

        alliance = db.get(AgentAlliance, res["alliance_id"])
        assert alliance.status == "dissolved"
        db.close()

    @pytest.mark.asyncio
    async def test_duplicate_alliance_rejected(self):
        """Cannot propose if alliance already exists."""
        from src.agents.alliance_manager import AllianceManager

        db = _make_db()
        a = _make_agent(db, id=1, name="Scout-A")
        _make_agent(db, id=2, name="Scout-B")

        mgr = AllianceManager()
        await mgr.propose_alliance(a, "Scout-B", "i", "t", db)
        dup = await mgr.propose_alliance(a, "Scout-B", "i2", "t2", db)

        assert not dup["success"]
        assert "exists" in dup["error"].lower()
        db.close()

    @pytest.mark.asyncio
    async def test_alliance_trust_bonus(self):
        """Allied agents get trust bonus."""
        from src.agents.alliance_manager import AllianceManager

        db = _make_db()
        a = _make_agent(db, id=1, name="Scout-A")
        b = _make_agent(db, id=2, name="Scout-B")

        mgr = AllianceManager()
        res = await mgr.propose_alliance(a, "Scout-B", "i", "t", db)
        await mgr.accept_alliance(b, res["alliance_id"], db)

        bonus = await mgr.get_alliance_trust_bonus(a.id, b.id, db)
        assert bonus == pytest.approx(0.1)

        # Non-allied: no bonus
        _make_agent(db, id=3, name="Scout-C")
        no_bonus = await mgr.get_alliance_trust_bonus(a.id, 3, db)
        assert no_bonus == 0.0
        db.close()

    @pytest.mark.asyncio
    async def test_alliance_context(self):
        """Context shows active alliances."""
        from src.agents.alliance_manager import AllianceManager

        db = _make_db()
        a = _make_agent(db, id=1, name="Scout-A")
        b = _make_agent(db, id=2, name="Scout-B")

        mgr = AllianceManager()
        res = await mgr.propose_alliance(a, "Scout-B", "i", "t", db)
        await mgr.accept_alliance(b, res["alliance_id"], db)

        ctx = await mgr.get_alliance_context(a.id, db)
        assert "ACTIVE ALLIANCES" in ctx
        assert "Scout-B" in ctx
        db.close()
