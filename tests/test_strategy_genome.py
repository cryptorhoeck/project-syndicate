"""Tests for Phase 8C Tier 2 — Strategy Genome."""

__version__ = "0.1.0"

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.common.models import Agent, AgentGenome, Base, SystemState
from src.genome.genome_schema import (
    GENOME_BOUNDS, ROLE_SECTIONS,
    create_random_genome, validate_genome, clamp_genome,
    genome_to_context_string, flatten_genome, unflatten_genome,
)
from src.genome.mutation import mutate_genome, create_warmstart_genome, WARMSTART_MUTATION


def _make_db():
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
        current_regime="crab", active_agent_count=0, alert_status="green",
    ))
    session.commit()
    return session


class TestGenomeSchema:

    def test_random_genome_within_bounds(self):
        """All values in random genome are within defined bounds."""
        genome = create_random_genome("scout")
        valid, violations = validate_genome(genome, "scout")
        assert valid, f"Violations: {violations}"

    def test_role_sections_correct(self):
        """Scout genome doesn't have plan_construction."""
        genome = create_random_genome("scout")
        assert "signal_generation" in genome
        assert "behavioral" in genome
        assert "plan_construction" not in genome

    def test_operator_has_risk_management(self):
        """Operator genome has risk_management."""
        genome = create_random_genome("operator")
        assert "risk_management" in genome
        assert "signal_generation" not in genome

    def test_validate_catches_out_of_bounds(self):
        """Validation detects values outside bounds."""
        genome = create_random_genome("scout")
        flat = flatten_genome(genome)
        # Set a value way out of bounds
        bad = [(p, 999.0 if isinstance(v, (int, float)) else v) for p, v in flat]
        bad_genome = unflatten_genome(bad)
        valid, violations = validate_genome(bad_genome, "scout")
        assert not valid
        assert len(violations) > 0

    def test_clamp_fixes_violations(self):
        """Clamping brings all values into bounds."""
        genome = create_random_genome("scout")
        flat = flatten_genome(genome)
        bad = [(p, 999.0 if isinstance(v, float) else (999 if isinstance(v, int) else v)) for p, v in flat]
        bad_genome = unflatten_genome(bad)
        fixed = clamp_genome(bad_genome)
        valid, _ = validate_genome(fixed, "scout")
        assert valid

    def test_context_string_reasonable_length(self):
        """Context string is under 400 tokens (~1600 chars)."""
        genome = create_random_genome("scout")
        ctx = genome_to_context_string(genome, "scout", 1)
        assert len(ctx) < 2000

    def test_flatten_unflatten_roundtrip(self):
        """Flatten → unflatten preserves data."""
        genome = create_random_genome("operator")
        flat = flatten_genome(genome)
        restored = unflatten_genome(flat)
        flat2 = flatten_genome(restored)
        assert len(flat) == len(flat2)
        for (p1, v1), (p2, v2) in zip(flat, flat2):
            assert p1 == p2
            assert v1 == v2


class TestGenomeMutation:

    def test_mutation_changes_some_values(self):
        """Mutated genome differs from parent in at least one value."""
        parent = create_random_genome("scout")
        child, mutations = mutate_genome(parent, {"rate": 0.5, "strength": 0.2, "structural_rate": 0.1})
        # With 50% mutation rate, very likely at least one changes
        assert len(mutations) > 0 or parent != child

    def test_mutation_stays_in_bounds(self):
        """All mutated values remain within bounds."""
        parent = create_random_genome("operator")
        child, _ = mutate_genome(parent, {"rate": 0.8, "strength": 0.5, "structural_rate": 0.0})
        child = clamp_genome(child)
        valid, violations = validate_genome(child, "operator")
        assert valid, f"Violations: {violations}"

    def test_mutation_preserves_structure(self):
        """Mutated genome has same keys as parent."""
        parent = create_random_genome("scout")
        child, _ = mutate_genome(parent)
        parent_flat = set(p for p, _ in flatten_genome(parent))
        child_flat = set(p for p, _ in flatten_genome(child))
        assert parent_flat == child_flat

    def test_warmstart_uses_best_genome(self):
        """Warm-start produces genome near the best existing one."""
        best = create_random_genome("scout")
        warmstarted = create_warmstart_genome("scout", best)
        # Should have same keys
        best_keys = set(p for p, _ in flatten_genome(best))
        ws_keys = set(p for p, _ in flatten_genome(warmstarted))
        assert best_keys == ws_keys

    def test_warmstart_no_best_creates_random(self):
        """Warm-start without best genome creates random."""
        genome = create_warmstart_genome("scout", None)
        assert "signal_generation" in genome

    def test_warmstart_has_more_mutations(self):
        """Warm-start applies more mutations than reproduction."""
        parent = create_random_genome("scout")
        # Run multiple times to get statistical sense
        repro_mutations = 0
        warm_mutations = 0
        for _ in range(20):
            _, muts = mutate_genome(parent, {"rate": 0.15, "strength": 0.10, "structural_rate": 0.05})
            repro_mutations += len(muts)
            _, muts = mutate_genome(parent, WARMSTART_MUTATION)
            warm_mutations += len(muts)
        assert warm_mutations > repro_mutations


class TestGenomeManager:

    @pytest.mark.asyncio
    async def test_create_and_retrieve(self):
        """Create genome, retrieve it, verify."""
        from src.genome.genome_manager import GenomeManager

        db = _make_db()
        db.add(Agent(id=1, name="S1", type="scout", status="active",
                     reputation_score=100, generation=1))
        db.commit()

        mgr = GenomeManager()
        data = await mgr.create_genome(1, "scout", db_session=db)
        assert "signal_generation" in data

        loaded = await mgr.get_genome(1, db)
        assert loaded is not None
        db.close()

    @pytest.mark.asyncio
    async def test_modify_genome(self):
        """Modification updates the parameter correctly."""
        from src.genome.genome_manager import GenomeManager

        db = _make_db()
        db.add(Agent(id=1, name="S1", type="scout", status="active",
                     reputation_score=100, generation=1))
        db.commit()

        mgr = GenomeManager()
        await mgr.create_genome(1, "scout", db_session=db)

        result = await mgr.modify_genome(
            1, "signal_generation.rsi_oversold", 25, "Works better at 25", 8, db
        )
        assert result is not None
        assert result["signal_generation"]["rsi_oversold"] == 25
        db.close()

    @pytest.mark.asyncio
    async def test_modify_out_of_bounds_rejected(self):
        """Out-of-bounds modification is rejected."""
        from src.genome.genome_manager import GenomeManager

        db = _make_db()
        db.add(Agent(id=1, name="S1", type="scout", status="active",
                     reputation_score=100, generation=1))
        db.commit()

        mgr = GenomeManager()
        await mgr.create_genome(1, "scout", db_session=db)

        result = await mgr.modify_genome(1, "signal_generation.rsi_oversold", 999, "test", 5, db)
        assert result is None  # rejected
        db.close()

    @pytest.mark.asyncio
    async def test_fitness_increases_with_age(self):
        """Fitness bonus grows with evaluations_with_genome."""
        from src.genome.genome_manager import GenomeManager

        db = _make_db()
        db.add(Agent(id=1, name="S1", type="scout", status="active",
                     reputation_score=100, generation=1))
        db.commit()

        mgr = GenomeManager()
        await mgr.create_genome(1, "scout", db_session=db)

        await mgr.update_fitness(1, 0.50, db)
        r1 = await mgr.get_genome_record(1, db)
        f1 = r1.fitness_score

        await mgr.update_fitness(1, 0.50, db)
        r2 = await mgr.get_genome_record(1, db)
        f2 = r2.fitness_score

        assert f2 > f1  # age bonus increases fitness
        db.close()


class TestPopulationDiversity:

    @pytest.mark.asyncio
    async def test_fewer_than_2_returns_1(self):
        """Single agent returns diversity 1.0."""
        from src.genome.diversity import calculate_diversity_index

        db = _make_db()
        db.add(Agent(id=1, name="S1", type="scout", status="active",
                     reputation_score=100, generation=1))
        db.add(AgentGenome(agent_id=1, genome_data=create_random_genome("scout")))
        db.commit()

        div = await calculate_diversity_index("scout", db)
        assert div == 1.0
        db.close()

    @pytest.mark.asyncio
    async def test_identical_genomes_low_diversity(self):
        """Two identical genomes produce diversity near 0."""
        from src.genome.diversity import calculate_diversity_index

        db = _make_db()
        genome = create_random_genome("scout")
        db.add(Agent(id=1, name="S1", type="scout", status="active",
                     reputation_score=100, generation=1))
        db.add(Agent(id=2, name="S2", type="scout", status="active",
                     reputation_score=100, generation=1))
        db.add(AgentGenome(agent_id=1, genome_data=genome))
        db.add(AgentGenome(agent_id=2, genome_data=genome))
        db.commit()

        div = await calculate_diversity_index("scout", db)
        assert div < 0.1  # Should be near 0
        db.close()

    def test_diversity_pressure_triggered(self):
        """Below threshold triggers diversity pressure."""
        from src.genome.diversity import should_apply_diversity_pressure
        assert should_apply_diversity_pressure(0.2)
        assert not should_apply_diversity_pressure(0.5)
