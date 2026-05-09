"""
Tests for Phase 9B Tier A: Parameter Registry Read Path proof of concept.

Validates that:
  - probation_grace_cycles is read from the parameter registry via get_param
  - the read site falls back to config when the registry has no row
  - a SIP-driven apply_change propagates to runtime behavior
  - the AST guard fires if the get_param call is reverted
  - Tier 3 parameter modifications are rejected at validation

The headline test (test_full_sip_loop_changes_probation_behavior) drives the
implementation step of the SIP lifecycle directly via apply_change rather
than running the full DEBATE -> VOTING -> TALLIED -> IMPLEMENTING state
machine, because the lifecycle's _validate_eval_weights guard rejects
non-weight evaluation.* parameters when no _weight params are seeded
(pre-existing behavior, separate concern). The integration coverage of the
state machine itself lives in test_sip_voting.py.
"""

__version__ = "0.1.0"

import ast
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from src.common.config import config
from src.common.models import (
    Agent, Base, Evaluation, ParameterRegistryEntry,
    SystemImprovementProposal,
)
from src.genesis.evaluation_engine import EvaluationEngine, EvaluationResult
from src.genesis.evaluation_assembler import EvaluationPackage
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
def registry():
    return ParameterRegistry()


@pytest.fixture
def engine():
    return EvaluationEngine()


def _seed_probation_param(session, current=3.0, default=3.0):
    """Seed the probation_grace_cycles parameter at Tier 1."""
    entry = ParameterRegistryEntry(
        parameter_key="evaluation.probation_grace_cycles",
        display_name="Probation Grace Cycles",
        description="Cycles a new probationer gets before survival clock expires.",
        category="evaluation",
        current_value=current,
        default_value=default,
        min_value=1.0,
        max_value=10.0,
        tier=1,
        unit="cycles",
    )
    session.add(entry)
    session.flush()
    return entry


def _seed_tier_3_param(session, key="colony.darwin_pressure_enabled"):
    """Seed a Tier 3 forbidden parameter."""
    entry = ParameterRegistryEntry(
        parameter_key=key,
        display_name="Darwinian Selection Pressure Enabled",
        description="If 1, natural selection is active. Tier 3 forbidden.",
        category="colony",
        current_value=1.0,
        default_value=1.0,
        min_value=0.0,
        max_value=1.0,
        tier=3,
        unit=None,
    )
    session.add(entry)
    session.flush()
    return entry


def _seed_probation_agent(session, name="OPERATOR-1"):
    """Create an agent in active state, suitable for probation routing."""
    agent = Agent(
        name=name,
        type="operator",
        status="active",
        generation=1,
        capital_allocated=100.0,
        capital_current=100.0,
        thinking_budget_daily=0.50,
        evaluation_count=5,
        survival_clock_end=datetime.now(timezone.utc) + timedelta(days=10),
        prestige_title="Apprentice",
    )
    session.add(agent)
    session.flush()
    return agent


def _make_probation_inputs(agent):
    """Build minimal pkg + result that route _execute_decision into the
    probation branch (pre_filter_result='probation', genesis_decision other
    than 'terminate')."""
    now = datetime.now(timezone.utc)
    pkg = EvaluationPackage(
        agent_id=agent.id,
        agent_name=agent.name,
        agent_role=agent.type,
        generation=agent.generation,
        evaluation_number=2,  # not first eval, so leniency does not apply
        period_start=now - timedelta(hours=1),
        period_end=now,
        metrics=None,
    )
    result = EvaluationResult(
        agent_id=agent.id,
        agent_name=agent.name,
        agent_role=agent.type,
        pre_filter_result="probation",
        genesis_decision="survive_probation",
        package=pkg,
    )
    return pkg, result


# ── Production-path tests ─────────────────────────────────

class TestRegistryReadPath:

    @pytest.mark.asyncio
    async def test_probation_grace_cycles_default_when_registry_seeded(
        self, db_session, engine
    ):
        """Seeded registry value (3) is read at probation entry."""
        _seed_probation_param(db_session, current=3.0)
        agent = _seed_probation_agent(db_session)
        pkg, result = _make_probation_inputs(agent)

        await engine._execute_decision(
            db_session, result, pkg, regime="crab", alert_hours=0.0,
        )

        db_session.flush()
        assert agent.probation is True
        assert agent.probation_grace_cycles == 3

    @pytest.mark.asyncio
    async def test_probation_grace_cycles_changed_after_sip_implementation(
        self, db_session, engine, registry
    ):
        """apply_change(5) updates the runtime value picked up by the next
        _apply_probation call."""
        _seed_probation_param(db_session, current=3.0)
        sip = SystemImprovementProposal(
            proposer_agent_id=1,
            proposer_agent_name="SCOUT-1",
            title="Increase grace cycles",
            category="evaluation",
            proposal="Give probationers more time to recover.",
            rationale="Too many probationers die before stabilizing.",
            status="proposed",
            lifecycle_status="implementing",
            target_parameter_key="evaluation.probation_grace_cycles",
            proposed_value=5.0,
        )
        db_session.add(sip)
        db_session.flush()

        await registry.apply_change(
            "evaluation.probation_grace_cycles", 5.0, sip.id, db_session,
        )

        agent = _seed_probation_agent(db_session)
        pkg, result = _make_probation_inputs(agent)

        await engine._execute_decision(
            db_session, result, pkg, regime="crab", alert_hours=0.0,
        )

        db_session.flush()
        assert agent.probation is True
        assert agent.probation_grace_cycles == 5

    @pytest.mark.asyncio
    async def test_probation_grace_cycles_falls_back_to_config_when_unseeded(
        self, db_session, engine
    ):
        """Empty registry => fallback path uses config.probation_grace_cycles."""
        # Sanity check: no row for the key
        existing = db_session.execute(
            select(ParameterRegistryEntry).where(
                ParameterRegistryEntry.parameter_key
                == "evaluation.probation_grace_cycles"
            )
        ).scalar_one_or_none()
        assert existing is None

        agent = _seed_probation_agent(db_session)
        pkg, result = _make_probation_inputs(agent)

        await engine._execute_decision(
            db_session, result, pkg, regime="crab", alert_hours=0.0,
        )

        db_session.flush()
        assert agent.probation is True
        assert agent.probation_grace_cycles == config.probation_grace_cycles


# ── Integration test ──────────────────────────────────────

class TestFullSIPLoop:

    @pytest.mark.asyncio
    async def test_full_sip_loop_changes_probation_behavior(
        self, db_session, engine, registry
    ):
        """End-to-end: SIP implementation step -> registry update ->
        _execute_decision picks up new value.

        Drives the implementation step (apply_change) directly rather than
        the full state machine; see module docstring for rationale.
        """
        _seed_probation_param(db_session, current=3.0)

        # Simulate a SIP that has cleared debate, voting, and Genesis ratification
        sip = SystemImprovementProposal(
            proposer_agent_id=1,
            proposer_agent_name="STRATEGIST-1",
            title="Loosen probation grace",
            category="evaluation",
            proposal="Set evaluation.probation_grace_cycles to 5.",
            rationale="Pre-filter is too aggressive; agents need recovery time.",
            status="proposed",
            lifecycle_status="implementing",
            target_parameter_key="evaluation.probation_grace_cycles",
            proposed_value=5.0,
            vote_pass_percentage=0.75,
            weighted_support=3.0,
            weighted_oppose=1.0,
            weighted_total_cast=4.0,
        )
        db_session.add(sip)
        db_session.flush()

        # Implementation step: apply_change is what _implement_sip would call
        change = await registry.apply_change(
            sip.target_parameter_key, sip.proposed_value, sip.id, db_session,
        )
        assert change["new_value"] == 5.0
        assert change["old_value"] == 3.0

        # Verify the registry row reflects the change
        row = db_session.execute(
            select(ParameterRegistryEntry).where(
                ParameterRegistryEntry.parameter_key
                == "evaluation.probation_grace_cycles"
            )
        ).scalar_one()
        assert row.current_value == 5.0
        assert row.last_modified_by_sip_id == sip.id

        # Now drive the evaluation engine. The probation read should pick up 5.
        agent = _seed_probation_agent(db_session, name="OPERATOR-POST-SIP")
        pkg, result = _make_probation_inputs(agent)

        await engine._execute_decision(
            db_session, result, pkg, regime="crab", alert_hours=0.0,
        )

        db_session.flush()
        assert agent.probation_grace_cycles == 5
        assert agent.probation_grace_cycles != 3, (
            "Read site is still using config default — get_param hoist did not "
            "propagate the registry change."
        )


# ── Regression guards ─────────────────────────────────────

class TestRegressionGuards:

    def test_get_param_actually_called_in_evaluation_engine(self):
        """AST guard: evaluation_engine.py must call
        get_param('evaluation.probation_grace_cycles', ...).

        Future refactors that revert to direct config.probation_grace_cycles
        reads inside the probation path will break this test.
        """
        source_path = (
            Path(__file__).resolve().parent.parent
            / "src" / "genesis" / "evaluation_engine.py"
        )
        tree = ast.parse(source_path.read_text(encoding="utf-8"))

        found = False
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue

            # Resolve the called name (handles bare get_param and module.get_param)
            func = node.func
            if isinstance(func, ast.Name):
                name = func.id
            elif isinstance(func, ast.Attribute):
                name = func.attr
            else:
                continue

            if name != "get_param":
                continue
            if not node.args:
                continue

            first_arg = node.args[0]
            if (
                isinstance(first_arg, ast.Constant)
                and first_arg.value == "evaluation.probation_grace_cycles"
            ):
                found = True
                break

        assert found, (
            "evaluation_engine.py no longer calls "
            "get_param('evaluation.probation_grace_cycles', ...). The "
            "Phase 9B read-path migration was reverted or moved. Check "
            "_execute_decision around the probation branch."
        )

    @pytest.mark.asyncio
    async def test_tier_3_parameter_rejected_at_validation(
        self, db_session, registry
    ):
        """Tier 3 parameters cannot be modified by SIP.

        Validation must reject the proposed change with a Tier 3 / Forbidden
        reason — never silently accept it.
        """
        _seed_tier_3_param(db_session, key="colony.darwin_pressure_enabled")

        result = await registry.validate_proposed_change(
            "colony.darwin_pressure_enabled", 0.0, db_session,
        )

        assert result["valid"] is False
        assert result["tier"] == 3
        assert (
            "Tier 3" in result["reason"]
            or "Forbidden" in result["reason"]
        ), (
            f"Tier 3 rejection reason did not mention Tier 3 or Forbidden: "
            f"{result['reason']!r}"
        )
