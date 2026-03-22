"""
Project Syndicate — Genome Mutation Engine

Produces offspring genomes by mutating parent genomes.
"""

__version__ = "0.1.0"

import random

from src.common.config import config
from src.genome.genome_schema import (
    GENOME_BOUNDS, create_random_genome, clamp_genome, flatten_genome, unflatten_genome,
)

REPRODUCTION_MUTATION = {
    "rate": 0.15,
    "strength": 0.10,
    "structural_rate": 0.05,
}

WARMSTART_MUTATION = {
    "rate": 0.40,
    "strength": 0.20,
    "structural_rate": 0.15,
}

DIVERSITY_MUTATION = {
    "rate": 0.30,
    "strength": 0.20,
    "structural_rate": 0.10,
}


def mutate_genome(parent_genome: dict, mutation_config: dict | None = None) -> tuple[dict, list[str]]:
    """Create a mutated copy of parent genome.

    Returns (new_genome, list_of_mutations_applied).
    """
    cfg = mutation_config or REPRODUCTION_MUTATION
    rate = cfg.get("rate", config.genome_mutation_rate)
    strength = cfg.get("strength", config.genome_mutation_strength)

    flat = flatten_genome(parent_genome)
    mutations = []
    new_flat = []

    for path, value in flat:
        if isinstance(value, (int, float)) and random.random() < rate:
            bounds = GENOME_BOUNDS.get(path)
            if bounds:
                low, high = bounds
                delta = random.gauss(0, strength)
                new_value = value * (1 + delta)
                if isinstance(low, int) and isinstance(high, int):
                    new_value = max(low, min(high, int(round(new_value))))
                else:
                    new_value = max(low, min(high, round(new_value, 4)))
                mutations.append(f"{path}: {value} → {new_value}")
                new_flat.append((path, new_value))
            else:
                new_flat.append((path, value))
        else:
            new_flat.append((path, value))

    return unflatten_genome(new_flat), mutations


def create_warmstart_genome(role: str, best_genome: dict | None = None) -> dict:
    """Create a genome for a new Gen 1 agent.

    If best_genome exists: mutate with heavy mutations.
    If no best_genome: create fully random.
    """
    if best_genome:
        genome, _ = mutate_genome(best_genome, WARMSTART_MUTATION)
        return clamp_genome(genome)
    return create_random_genome(role)
