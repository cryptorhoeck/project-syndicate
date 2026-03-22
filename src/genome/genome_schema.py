"""
Project Syndicate — Strategy Genome Schema

Defines the structure, bounds, and defaults for agent genomes.
Role-specific sections are included/excluded based on agent role.
"""

__version__ = "0.1.0"

import random

GENOME_BOUNDS = {
    "market_selection.volatility_preference": (0.1, 0.9),
    "market_selection.volume_threshold_multiplier": (1.0, 5.0),
    "market_selection.max_concurrent_markets": (1, 5),
    "market_selection.regime_weights.bull": (0.1, 1.0),
    "market_selection.regime_weights.bear": (0.1, 1.0),
    "market_selection.regime_weights.crab": (0.1, 1.0),
    "market_selection.regime_weights.volatile": (0.1, 1.0),
    "signal_generation.min_confidence_to_broadcast": (3, 9),
    "signal_generation.momentum_threshold_pct": (0.5, 5.0),
    "signal_generation.volume_spike_threshold": (1.5, 5.0),
    "signal_generation.rsi_oversold": (15, 40),
    "signal_generation.rsi_overbought": (60, 85),
    "signal_generation.contrarian_bias": (-0.5, 0.5),
    "plan_construction.min_risk_reward_ratio": (1.0, 5.0),
    "plan_construction.preferred_timeframe_hours": (1, 168),
    "plan_construction.max_position_size_pct": (5.0, 25.0),
    "plan_construction.entry_patience_candles": (1, 10),
    "risk_management.stop_loss_pct": (1.0, 10.0),
    "risk_management.take_profit_pct": (2.0, 30.0),
    "risk_management.trailing_stop_activation_pct": (2.0, 15.0),
    "risk_management.trailing_stop_distance_pct": (0.5, 5.0),
    "risk_management.max_portfolio_heat_pct": (10.0, 50.0),
    "risk_management.loss_cooldown_cycles": (1, 15),
    "risk_management.max_drawdown_before_hibernate_pct": (5.0, 25.0),
    "behavioral.idle_tolerance_cycles": (2, 15),
    "behavioral.intel_sharing_generosity": (0.0, 1.0),
    "behavioral.alliance_willingness": (0.0, 1.0),
    "behavioral.sip_propensity": (0.0, 0.5),
    "behavioral.hibernate_threshold_budget_pct": (5.0, 30.0),
    "behavioral.tool_execution_frequency": (0.0, 1.0),
}

ROLE_SECTIONS = {
    "scout": ["market_selection", "signal_generation", "behavioral"],
    "strategist": ["market_selection", "plan_construction", "behavioral"],
    "critic": ["risk_management", "behavioral"],
    "operator": ["risk_management", "plan_construction", "behavioral"],
}


def _random_value(low, high):
    """Generate a random value within bounds, respecting type."""
    if isinstance(low, int) and isinstance(high, int):
        return random.randint(low, high)
    return round(random.uniform(low, high), 4)


def create_random_genome(role: str) -> dict:
    """Create a fully randomized genome for a Gen 1 agent."""
    sections = ROLE_SECTIONS.get(role, ["behavioral"])
    genome = {}

    for path, (low, high) in GENOME_BOUNDS.items():
        section = path.split(".")[0]
        if section not in sections:
            continue

        parts = path.split(".")
        d = genome
        for part in parts[:-1]:
            if part not in d:
                d[part] = {}
            d = d[part]
        d[parts[-1]] = _random_value(low, high)

    return genome


def validate_genome(genome: dict, role: str) -> tuple[bool, list[str]]:
    """Validate genome against bounds. Returns (valid, list_of_violations)."""
    violations = []
    flat = flatten_genome(genome)

    for path, value in flat:
        if path in GENOME_BOUNDS:
            low, high = GENOME_BOUNDS[path]
            if isinstance(value, (int, float)):
                if value < low or value > high:
                    violations.append(f"{path}: {value} outside [{low}, {high}]")

    return len(violations) == 0, violations


def clamp_genome(genome: dict) -> dict:
    """Clamp all values to their bounds."""
    flat = flatten_genome(genome)
    clamped = []
    for path, value in flat:
        if path in GENOME_BOUNDS and isinstance(value, (int, float)):
            low, high = GENOME_BOUNDS[path]
            if isinstance(low, int) and isinstance(high, int):
                value = max(low, min(high, int(value)))
            else:
                value = max(low, min(high, float(value)))
        clamped.append((path, value))
    return unflatten_genome(clamped)


def genome_to_context_string(genome: dict, role: str, generation: int) -> str:
    """Format genome for injection into agent context (~200 tokens)."""
    lines = []
    flat = flatten_genome(genome)
    for path, value in flat:
        if isinstance(value, float):
            lines.append(f"  {path}: {value:.3f}")
        else:
            lines.append(f"  {path}: {value}")
    return "\n".join(lines)


def flatten_genome(genome: dict, prefix: str = "") -> list[tuple[str, any]]:
    """Flatten nested genome dict into (dotted_path, value) tuples."""
    result = []
    for key, value in genome.items():
        full_path = f"{prefix}{key}" if not prefix else f"{prefix}.{key}"
        if isinstance(value, dict):
            result.extend(flatten_genome(value, full_path))
        else:
            result.append((full_path, value))
    return result


def unflatten_genome(flat: list[tuple[str, any]]) -> dict:
    """Reconstruct nested dict from flattened list."""
    result = {}
    for path, value in flat:
        parts = path.split(".")
        d = result
        for part in parts[:-1]:
            if part not in d:
                d[part] = {}
            d = d[part]
        d[parts[-1]] = value
    return result
