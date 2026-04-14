"""
Tests for Phase 9A: SIP Voting & Colony Maturity.
"""

__version__ = "0.1.0"

import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from src.common.models import (
    Base, Agent, ColonyMaturity, ParameterRegistryEntry,
    ParameterChangeLog, SystemImprovementProposal,
    SIPVote, SIPDebate,
)
from src.governance.maturity_tracker import (
    ColonyMaturityTracker, MaturityStage, MATURITY_CONFIGS, MaturityConfig,
)
from src.governance.parameter_registry import ParameterRegistry


# ── Fixtures ──────────────────────────────────────────────

@pytest.fixture
def db_engine():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine


@pytest.fixture
def db_session(db_engine):
    with Session(db_engine) as session:
        yield session


@pytest.fixture
def tracker():
    return ColonyMaturityTracker()


@pytest.fixture
def registry():
    return ParameterRegistry()


def _seed_agent(session, name="SCOUT-1", status="active", generation=1, days_ago=0):
    """Helper to create a test agent."""
    created = datetime.now(timezone.utc) - timedelta(days=days_ago)
    agent = Agent(
        name=name,
        type="scout",
        status=status,
        generation=generation,
        capital_allocated=100.0,
        created_at=created,
    )
    session.add(agent)
    session.flush()
    return agent


def _seed_sip(session, agent_id=1, lifecycle_status="proposed", title="Test SIP"):
    """Helper to create a test SIP."""
    sip = SystemImprovementProposal(
        proposer_agent_id=agent_id,
        proposer_agent_name="SCOUT-1",
        title=title,
        category="evaluation",
        proposal="Test proposal",
        rationale="Test rationale",
        status="proposed",
        lifecycle_status=lifecycle_status,
    )
    session.add(sip)
    session.flush()
    return sip


def _seed_param(session, key="lifecycle.survival_clock_days", current=14.0,
                default=14.0, min_val=7.0, max_val=30.0, tier=1):
    """Helper to create a parameter registry entry."""
    entry = ParameterRegistryEntry(
        parameter_key=key,
        display_name=f"Test {key}",
        description=f"Test description for {key}",
        category=key.split(".")[0],
        current_value=current,
        default_value=default,
        min_value=min_val,
        max_value=max_val,
        tier=tier,
        unit="days",
    )
    session.add(entry)
    session.flush()
    return entry


# ── Colony Maturity Tests ────────────────────────────────

class TestColonyMaturity:

    def test_nascent_is_default(self, db_session, tracker):
        """Fresh colony starts at NASCENT stage."""
        config = tracker.get_config(db_session)
        assert config.stage == MaturityStage.NASCENT

    @pytest.mark.asyncio
    async def test_nascent_to_developing_requires_all_conditions(self, db_session, tracker):
        """Must meet age >= 8, gen >= 2, AND sips >= 1 to advance."""
        # Create agent old enough
        _seed_agent(db_session, "A1", generation=2, days_ago=10)
        # Create an implemented SIP
        sip = _seed_sip(db_session, lifecycle_status="implemented")
        db_session.flush()

        stage = await tracker.compute_stage(db_session)
        assert stage == MaturityStage.DEVELOPING

    @pytest.mark.asyncio
    async def test_partial_conditions_dont_advance(self, db_session, tracker):
        """Meeting only age but not generation stays NASCENT."""
        # Old enough but gen 1 and no SIPs
        _seed_agent(db_session, "A1", generation=1, days_ago=15)
        db_session.flush()

        stage = await tracker.compute_stage(db_session)
        assert stage == MaturityStage.NASCENT

    @pytest.mark.asyncio
    async def test_maturity_never_regresses(self, db_session, tracker):
        """Once DEVELOPING, stays DEVELOPING even if conditions no longer met."""
        # Set to DEVELOPING manually
        row = ColonyMaturity(stage="developing")
        db_session.add(row)
        db_session.flush()

        # No agents at all — conditions not met, but should not regress
        stage, did_transition = await tracker.update(db_session)
        assert stage == MaturityStage.DEVELOPING
        assert not did_transition

    def test_nascent_config_values(self):
        """NASCENT: 4hr debate, 4hr vote, 2 SIPs/eval, 1.5x tax."""
        c = MATURITY_CONFIGS[MaturityStage.NASCENT]
        assert c.debate_period_hours == 4
        assert c.voting_period_hours == 4
        assert c.sip_rate_limit_per_eval == 2
        assert c.sip_thinking_tax_multiplier == 1.5
        assert c.genesis_posture == "permissive"

    def test_mature_config_values(self):
        """MATURE: 24hr debate, 24hr vote, 1 SIP/eval, 2.5x tax, evidence required."""
        c = MATURITY_CONFIGS[MaturityStage.MATURE]
        assert c.debate_period_hours == 24
        assert c.voting_period_hours == 24
        assert c.sip_rate_limit_per_eval == 1
        assert c.sip_thinking_tax_multiplier == 2.5
        assert c.require_evidence is True
        assert c.require_cosponsor is True
        assert c.genesis_posture == "skeptical"

    @pytest.mark.asyncio
    async def test_stage_transition_posts_to_agora(self, db_session, tracker):
        """Stage transition creates Agora message in system-alerts."""
        _seed_agent(db_session, "A1", generation=2, days_ago=10)
        _seed_sip(db_session, lifecycle_status="implemented")
        db_session.flush()

        mock_agora = AsyncMock()
        stage, did_transition = await tracker.update(db_session, agora_service=mock_agora)
        assert stage == MaturityStage.DEVELOPING
        assert did_transition is True
        mock_agora.post_message.assert_called_once()
        call_kwargs = mock_agora.post_message.call_args
        assert "COLONY MATURITY" in call_kwargs.kwargs.get("content", call_kwargs[1].get("content", ""))

    def test_debate_end_time(self, db_session, tracker):
        """Debate end time is based on maturity config."""
        now = datetime.now(timezone.utc)
        end = tracker.get_debate_end_time(db_session, now)
        # NASCENT = 4 hours
        expected = now + timedelta(hours=4)
        assert abs((end - expected).total_seconds()) < 1

    def test_voting_end_time(self, db_session, tracker):
        """Voting end time is based on maturity config."""
        now = datetime.now(timezone.utc)
        end = tracker.get_voting_end_time(db_session, now)
        # NASCENT = 4 hours
        expected = now + timedelta(hours=4)
        assert abs((end - expected).total_seconds()) < 1


# ── Parameter Registry Tests ────────────────────────────

class TestParameterRegistry:

    @pytest.mark.asyncio
    async def test_get_value_returns_current(self, db_session, registry):
        """get_value returns the current_value for a known parameter."""
        _seed_param(db_session, current=14.0)
        val = await registry.get_value("lifecycle.survival_clock_days", db_session)
        assert val == 14.0

    @pytest.mark.asyncio
    async def test_get_value_unknown_key_raises(self, db_session, registry):
        """get_value raises KeyError for unknown parameter_key."""
        with pytest.raises(KeyError):
            await registry.get_value("nonexistent.param", db_session)

    @pytest.mark.asyncio
    async def test_validate_within_range_passes(self, db_session, registry):
        """Proposed value within min/max is valid."""
        _seed_param(db_session, current=14.0, min_val=7.0, max_val=30.0)
        result = await registry.validate_proposed_change(
            "lifecycle.survival_clock_days", 10.0, db_session
        )
        assert result["valid"] is True

    @pytest.mark.asyncio
    async def test_validate_below_min_fails(self, db_session, registry):
        """Proposed value below min_value is invalid."""
        _seed_param(db_session, current=14.0, min_val=7.0, max_val=30.0)
        result = await registry.validate_proposed_change(
            "lifecycle.survival_clock_days", 5.0, db_session
        )
        assert result["valid"] is False
        assert "below minimum" in result["reason"]

    @pytest.mark.asyncio
    async def test_validate_above_max_fails(self, db_session, registry):
        """Proposed value above max_value is invalid."""
        _seed_param(db_session, current=14.0, min_val=7.0, max_val=30.0)
        result = await registry.validate_proposed_change(
            "lifecycle.survival_clock_days", 35.0, db_session
        )
        assert result["valid"] is False
        assert "above maximum" in result["reason"]

    @pytest.mark.asyncio
    async def test_validate_forbidden_tier_fails(self, db_session, registry):
        """Any proposed change to a Tier 3 parameter is invalid."""
        _seed_param(db_session, key="risk.circuit_breaker_threshold",
                    current=0.75, default=0.75, min_val=0.75, max_val=0.75, tier=3)
        result = await registry.validate_proposed_change(
            "risk.circuit_breaker_threshold", 0.80, db_session
        )
        assert result["valid"] is False
        assert "Forbidden" in result["reason"]

    @pytest.mark.asyncio
    async def test_validate_same_value_fails(self, db_session, registry):
        """Proposing the same value as current is invalid."""
        _seed_param(db_session, current=14.0)
        result = await registry.validate_proposed_change(
            "lifecycle.survival_clock_days", 14.0, db_session
        )
        assert result["valid"] is False
        assert "same as current" in result["reason"]

    @pytest.mark.asyncio
    async def test_apply_change_updates_value(self, db_session, registry):
        """apply_change updates current_value in registry."""
        _seed_param(db_session, current=14.0)
        sip = _seed_sip(db_session)

        await registry.apply_change(
            "lifecycle.survival_clock_days", 12.0, sip.id, db_session
        )

        val = await registry.get_value("lifecycle.survival_clock_days", db_session)
        assert val == 12.0

    @pytest.mark.asyncio
    async def test_apply_change_creates_log_entry(self, db_session, registry):
        """apply_change creates a parameter_change_log record."""
        _seed_param(db_session, current=14.0)
        sip = _seed_sip(db_session)

        await registry.apply_change(
            "lifecycle.survival_clock_days", 12.0, sip.id, db_session
        )

        logs = db_session.execute(
            select(ParameterChangeLog).where(
                ParameterChangeLog.parameter_key == "lifecycle.survival_clock_days"
            )
        ).scalars().all()
        assert len(logs) == 1
        assert logs[0].old_value == 14.0
        assert logs[0].new_value == 12.0

    @pytest.mark.asyncio
    async def test_drift_summary_counts_directions(self, db_session, registry):
        """get_drift_summary correctly counts softer vs harder changes."""
        _seed_param(db_session, current=14.0)
        sip = _seed_sip(db_session)

        # Two changes: one softer, one harder
        log1 = ParameterChangeLog(
            parameter_key="lifecycle.survival_clock_days",
            old_value=14.0, new_value=20.0,
            changed_by_sip_id=sip.id,
            changed_at=datetime.now(timezone.utc),
            drift_direction="softer",
        )
        log2 = ParameterChangeLog(
            parameter_key="lifecycle.survival_clock_days",
            old_value=20.0, new_value=12.0,
            changed_by_sip_id=sip.id,
            changed_at=datetime.now(timezone.utc),
            drift_direction="harder",
        )
        db_session.add_all([log1, log2])
        db_session.flush()

        summary = await registry.get_drift_summary(db_session)
        assert summary["total_changes"] == 2
        assert summary["softer_changes"] == 1
        assert summary["harder_changes"] == 1
        assert summary["drift_alert"] is False

    @pytest.mark.asyncio
    async def test_drift_alert_when_imbalanced(self, db_session, registry):
        """drift_alert is True when softer_changes exceeds harder by 3+."""
        sip = _seed_sip(db_session)
        now = datetime.now(timezone.utc)

        for i in range(4):
            db_session.add(ParameterChangeLog(
                parameter_key=f"test.param{i}",
                old_value=1.0, new_value=2.0,
                changed_by_sip_id=sip.id,
                changed_at=now,
                drift_direction="softer",
            ))
        db_session.flush()

        summary = await registry.get_drift_summary(db_session)
        assert summary["drift_alert"] is True

    @pytest.mark.asyncio
    async def test_get_all_parameters_filter_by_category(self, db_session, registry):
        """get_all_parameters filters by category."""
        _seed_param(db_session, key="lifecycle.test1", current=1.0)
        _seed_param(db_session, key="evaluation.test2", current=2.0)
        db_session.flush()

        results = await registry.get_all_parameters(db_session, category="lifecycle")
        assert len(results) == 1
        assert results[0]["parameter_key"] == "lifecycle.test1"

    @pytest.mark.asyncio
    async def test_get_all_parameters_filter_by_tier(self, db_session, registry):
        """get_all_parameters filters by tier."""
        _seed_param(db_session, key="lifecycle.t1", current=1.0, tier=1)
        _seed_param(db_session, key="risk.t3", current=2.0, tier=3)
        db_session.flush()

        results = await registry.get_all_parameters(db_session, tier=3)
        assert len(results) == 1
        assert results[0]["parameter_key"] == "risk.t3"


class TestSeedScript:

    def test_seed_script_is_idempotent(self, db_session):
        """Running seed script twice doesn't create duplicate entries."""
        from scripts.seed_parameter_registry import PARAMETERS

        # First run
        for p in PARAMETERS:
            db_session.add(ParameterRegistryEntry(**p))
        db_session.commit()

        count1 = len(db_session.execute(
            select(ParameterRegistryEntry)
        ).scalars().all())

        # Second run — skip existing
        for p in PARAMETERS:
            existing = db_session.execute(
                select(ParameterRegistryEntry).where(
                    ParameterRegistryEntry.parameter_key == p["parameter_key"]
                )
            ).scalar_one_or_none()
            if not existing:
                db_session.add(ParameterRegistryEntry(**p))
        db_session.commit()

        count2 = len(db_session.execute(
            select(ParameterRegistryEntry)
        ).scalars().all())

        assert count1 == count2
        assert count1 == len(PARAMETERS)
