"""
Tests for Phase 9B Tier A: Parameter Registry Read Path proof of concept.

Tier A scope: read-pattern wiring (registry -> consumer). Tier A does
NOT prove the full production lifecycle path (propose -> debate -> vote
-> tally -> implement -> consume). The headline integration test
bypasses lifecycle.advance via direct apply_change() because
_validate_eval_weights auto-rejects non-weight evaluation.* SIPs.
Narrowing that validator is Tier B work
(see DEFERRED_ITEMS_TRACKER.md, Phase 9B Tier B section).

Validates that:
  - probation_grace_cycles is read from the parameter registry via get_param
  - the read site falls back to config when the registry has no row
  - a registry update via apply_change propagates to runtime behavior
  - the AST guard fires if the get_param call is reverted
  - Tier 3 parameter modifications are rejected at validation
  - integer-semantic parameter reads truncate fractional registry values
  - the seed migration is idempotent across re-runs

The lifecycle state machine itself is covered in test_sip_voting.py.
"""

__version__ = "0.2.0"

import ast
import asyncio
import importlib.util
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


@pytest.fixture(autouse=True)
def _restore_event_loop_after_test():
    """asyncio.run() (used implicitly by pytest-asyncio) closes the current
    loop. Other tests in the suite still use the deprecated
    `asyncio.get_event_loop()` pattern; restore a fresh loop after each
    test so suite ordering doesn't matter.

    Pattern copied from tests/test_eval_engine_async_bridge.py:25-35
    and tests/test_genesis_regime_review_consumption.py:71-83 (subsystems
    H and P fixes for the same event-loop pollution symptom).
    """
    yield
    try:
        asyncio.set_event_loop(asyncio.new_event_loop())
    except Exception:
        pass


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

    @pytest.mark.asyncio
    async def test_probation_grace_cycles_truncates_fractional_value(
        self, db_session, engine
    ):
        """Truncation is current behavior, intentional. int() applied to a
        Float-typed registry value rounds toward zero (4.7 -> 4, not 5).

        Domain enforcement to reject fractional values for integer-semantic
        parameters is Tier B work (see DEFERRED_ITEMS_TRACKER entry:
        'Validator does not enforce parameter domain').

        Fail-loud behavior on schema violations (e.g., registry value is a
        string instead of float) is intentional. int('4.7') raising
        ValueError is preferred over silent coercion because schema
        corruption SHOULD crash loud rather than silently produce wrong
        values. Trust the schema; crash on schema violations.
        """
        # Insert raw 4.7 — bypasses validator that would have rejected it
        # if domain enforcement existed.
        entry = ParameterRegistryEntry(
            parameter_key="evaluation.probation_grace_cycles",
            display_name="Probation Grace Cycles",
            description="Test fixture for truncation behavior.",
            category="evaluation",
            current_value=4.7,
            default_value=3.0,
            min_value=1.0,
            max_value=10.0,
            tier=1,
            unit="cycles",
        )
        db_session.add(entry)
        db_session.flush()

        agent = _seed_probation_agent(db_session)
        pkg, result = _make_probation_inputs(agent)

        await engine._execute_decision(
            db_session, result, pkg, regime="crab", alert_hours=0.0,
        )

        db_session.flush()
        assert agent.probation is True
        assert agent.probation_grace_cycles == 4, (
            "Expected truncation (int(4.7) == 4), not rounding. If this "
            "fails with 5, the consumer is using round() — re-evaluate "
            "the truncation contract documented in this test."
        )


# ── Integration test ──────────────────────────────────────

class TestFullSIPLoop:

    @pytest.mark.asyncio
    async def test_full_sip_loop_changes_probation_behavior(
        self, db_session, engine, registry
    ):
        """Tier A integration test: PARTIAL end-to-end. Demonstrates the
        read-pattern path (registry update -> consumer read), NOT the full
        production lifecycle path.

        Production lifecycle (propose -> debate -> vote -> tally ->
        _validate_eval_weights -> apply_change) is bypassed via direct
        apply_change() call. This is because _validate_eval_weights
        auto-rejects any evaluation.* SIP that doesn't have sibling
        _weight rows summing to 1.0 — and probation_grace_cycles isn't
        a weight.

        Tier B will narrow the validator scope so non-weight evaluation.*
        parameters can advance through the full lifecycle. Until then,
        this test proves: when apply_change() runs, the next consumer
        read sees the new value.
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


# ── Migration idempotency ─────────────────────────────────

class TestMigrationIdempotency:
    """The seed migration must be safe to re-run.

    A partial rollback + reapply is a realistic operational scenario.
    Without an upsert, the second run crashes on the unique constraint
    on parameter_key.
    """

    def _load_migration_module(self):
        """Load the seed migration as a Python module via importlib.

        alembic/versions/ files aren't a normal Python package; importlib
        with a file path is the canonical way to import them in tests.
        """
        migration_path = (
            Path(__file__).resolve().parent.parent / "alembic" / "versions"
            / "phase_9b_tier_a_seed_parameter_registry.py"
        )
        spec = importlib.util.spec_from_file_location(
            "phase_9b_seed_migration", migration_path,
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_seed_migration_is_idempotent(self, db_engine):
        """Running _emit_seed_inserts twice yields exactly 5 rows, no error.

        This test exercises _emit_seed_inserts via db_engine.connect()
        rather than the production path which uses op.get_bind() from
        inside alembic's upgrade() context. These are equivalent for
        idempotency purposes because:
          - Both return a Connection bound to the same engine
          - Both honor the same dialect (sqlite or postgresql)
          - The INSERT OR IGNORE / ON CONFLICT DO NOTHING clauses operate
            at the SQL dialect level, not at the connection-management
            level

        The production path is verified manually via 'alembic upgrade
        head' run twice on the dev Postgres DB; see CHANGELOG.md Phase
        9B Tier A manual verification.
        """
        module = self._load_migration_module()

        with db_engine.connect() as conn:
            module._emit_seed_inserts(conn)
            module._emit_seed_inserts(conn)
            conn.commit()

        with Session(db_engine) as session:
            seed_keys = [row[0] for row in module.SEED_ROWS]
            rows = session.execute(
                select(ParameterRegistryEntry).where(
                    ParameterRegistryEntry.parameter_key.in_(seed_keys)
                )
            ).scalars().all()

            assert len(rows) == 5, (
                f"Expected 5 seed rows after double-run, got {len(rows)}. "
                f"Either the upsert is missing (rows == 10 if INSERT ran "
                f"twice) or the second run crashed on the unique constraint."
            )
            actual_keys = {r.parameter_key for r in rows}
            assert actual_keys == set(seed_keys), (
                f"Seed key mismatch. Expected {set(seed_keys)}, "
                f"got {actual_keys}."
            )

    def test_seed_migration_aborts_on_partial_seed(self, db_engine):
        """RuntimeError fires when post-insert count is less than expected.

        Simulates a silent partial seed by wrapping the bind so one INSERT
        is dropped. The post-insert SELECT COUNT(*) returns 4, the
        verification block in _emit_seed_inserts raises RuntimeError.

        Same fail-loud pattern as the dialect RuntimeError: refuse to
        succeed in an unknown state rather than letting future SIPs hit
        validation errors for missing parameters at runtime.
        """
        module = self._load_migration_module()

        class _LossyBind:
            """Wraps a real connection, silently drops the 3rd INSERT."""

            def __init__(self, inner):
                self._inner = inner
                self._insert_count = 0

            @property
            def dialect(self):
                return self._inner.dialect

            def execute(self, *args, **kwargs):
                sql_obj = args[0] if args else None
                sql_text = ""
                if hasattr(sql_obj, "text"):
                    sql_text = sql_obj.text or ""
                elif sql_obj is not None:
                    sql_text = str(sql_obj)
                is_insert = "INSERT" in sql_text.upper()
                if is_insert:
                    self._insert_count += 1
                    if self._insert_count == 3:
                        # Silently drop — simulate a silent partial seed.
                        return None
                return self._inner.execute(*args, **kwargs)

        with db_engine.connect() as conn:
            lossy = _LossyBind(conn)
            with pytest.raises(RuntimeError, match=r"only 4/5"):
                module._emit_seed_inserts(lossy)
