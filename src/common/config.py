"""
Project Syndicate — Central Configuration

All system configuration in one place, loaded from .env with sensible defaults.
Uses pydantic-settings for validation and type coercion.
"""

__version__ = "0.8.0"

from pydantic_settings import BaseSettings


class SyndicateConfig(BaseSettings):
    """Central configuration for the entire Syndicate system."""

    # Database
    database_url: str = "postgresql://postgres@localhost:5432/syndicate"
    pg_dump_path: str = "pg_dump"

    # Redis
    redis_url: str = "redis://localhost:6379"

    # Exchange (Primary — Kraken)
    exchange_api_key: str = ""
    exchange_api_secret: str = ""

    # Exchange (Secondary — Binance)
    exchange_secondary_api_key: str = ""
    exchange_secondary_api_secret: str = ""

    # Anthropic
    anthropic_api_key: str = ""

    # Risk thresholds
    circuit_breaker_threshold: float = 0.75   # 75% loss from peak = full shutdown
    yellow_alert_threshold: float = 0.15      # 15% loss in 4 hours
    red_alert_threshold: float = 0.30         # 30% loss in 4 hours
    per_agent_max_position_pct: float = 0.25  # No single position > 25% of agent capital
    per_agent_max_loss_pct: float = 0.50      # Agent loses 50% = instant kill
    trade_gate_threshold: float = 0.05        # Trades > 5% of agent capital need review

    # Genesis
    genesis_cycle_interval_seconds: int = 300  # 5 minutes
    warden_cycle_interval_seconds: int = 30
    max_agents: int = 20
    default_survival_clock_days: int = 14
    min_survival_clock_days: int = 3
    max_survival_clock_days: int = 60
    min_spawn_capital: float = 20.0
    treasury_reserve_ratio: float = 0.20
    random_allocation_pct: float = 0.10

    # Evaluation weights
    eval_weight_sharpe: float = 0.40
    eval_weight_true_pnl: float = 0.25
    eval_weight_thinking_efficiency: float = 0.20
    eval_weight_consistency: float = 0.15

    # Prestige thresholds (evaluations survived)
    prestige_proven_threshold: int = 3
    prestige_veteran_threshold: int = 10
    prestige_proven_multiplier: float = 1.10
    prestige_veteran_multiplier: float = 1.20
    prestige_elite_multiplier: float = 1.30
    prestige_legendary_multiplier: float = 1.50

    # Thinking budgets (daily caps in USD)
    genesis_daily_thinking_budget: float = 2.00
    new_agent_daily_thinking_budget: float = 0.50

    # Email / SMTP
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    alert_email_to: str = ""
    alert_email_from: str = ""

    # Phase 3A: Thinking Cycle
    scout_cycle_interval: int = 300
    strategist_cycle_interval: int = 900
    operator_active_cycle_interval: int = 60
    operator_idle_cycle_interval: int = 900
    interrupt_cooldown_seconds: int = 60
    max_retries_per_cycle: int = 1
    retry_tax_multiplier: float = 2.0
    reflection_every_n_cycles: int = 10
    short_term_memory_size: int = 50
    context_token_budget_normal: int = 3000
    context_token_budget_survival: int = 1500

    # API Temperature defaults (per role)
    scout_temperature: float = 0.7
    strategist_temperature: float = 0.5
    critic_temperature: float = 0.2
    operator_temperature: float = 0.2

    # Phase 3B: Boot Sequence
    gen1_survival_clock_days: int = 21
    opportunity_ttl_hours: int = 6
    health_check_day: int = 10
    orientation_token_budget_multiplier: float = 1.5

    # Logging
    log_level: str = "INFO"

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }


# Singleton instance — import this everywhere
config = SyndicateConfig()
