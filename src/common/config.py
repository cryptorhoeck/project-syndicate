"""
Project Syndicate — Central Configuration

All system configuration in one place, loaded from .env with sensible defaults.
Uses pydantic-settings for validation and type coercion.
"""

__version__ = "1.5.0"

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

    # Phase 3C: Paper Trading
    trading_mode: str = "paper"  # "paper" or "live"
    default_exchange: str = "kraken"
    price_cache_ticker_ttl: int = 10
    price_cache_orderbook_ttl: int = 10
    stale_price_threshold: int = 60
    position_monitor_interval: int = 10
    limit_order_monitor_interval: int = 10
    sanity_check_interval: int = 300
    equity_snapshot_interval: int = 300
    default_limit_order_expiry_hours: int = 24
    min_slippage_pct: float = 0.0001  # 0.01%
    slippage_noise_range: float = 0.2  # +/-20%
    book_depth_penalty_pct: float = 0.005  # 0.5%
    concentration_warning_threshold: float = 0.40  # 40%

    # Phase 3D — Evaluation cycle
    first_eval_leniency: bool = True
    probation_grace_cycles: int = 3
    post_mortem_publish_delay_hours: int = 6
    portfolio_concentration_hard_limit: float = 0.50
    portfolio_concentration_warning: float = 0.35
    top_performer_budget_increase: float = 0.50
    second_performer_budget_increase: float = 0.25
    probation_budget_decrease: float = 0.25

    # Normalization ranges (Phase 3D)
    norm_operator_sharpe_range: list = [-1.0, 3.0]
    norm_operator_pnl_range: list = [-20.0, 30.0]
    norm_operator_efficiency_range: list = [0.0, 5.0]
    norm_scout_conversion_range: list = [0.0, 0.50]
    norm_scout_profitability_range: list = [-5.0, 10.0]
    norm_strategist_approval_range: list = [0.0, 0.80]
    norm_critic_rejection_range: list = [-1.0, 1.0]
    norm_critic_throughput_range: list = [0.0, 3.0]

    # Attribution shares (Phase 3D)
    attribution_scout_share: float = 0.25
    attribution_strategist_share: float = 0.25
    attribution_critic_share: float = 0.50

    # Critic rubber-stamp detection (Phase 3D)
    critic_rubber_stamp_threshold: float = 0.90
    critic_rubber_stamp_penalty: float = 0.50

    # Phase 3E — Personality Through Experience
    # Temperature evolution
    temperature_drift_amount: float = 0.05
    temperature_signal_threshold: float = 0.2
    temperature_bounds_scout: list = [0.3, 0.9]
    temperature_bounds_strategist: list = [0.2, 0.7]
    temperature_bounds_critic: list = [0.1, 0.4]
    temperature_bounds_operator: list = [0.1, 0.4]

    # Relationship memory
    trust_decay_factor: float = 0.95
    trust_prior: float = 0.5
    trust_min_interactions_to_show: int = 2

    # Reflection library
    reflection_library_cooldown: int = 5  # reflections between same resource

    # Divergence tracking
    divergence_low_threshold: float = 0.15
    divergence_min_comparable_metrics: int = 3

    # Behavioral profile thresholds
    profile_min_positions: int = 10
    profile_min_cycles: int = 20
    profile_min_cycle_days: int = 3
    profile_min_actions: int = 15
    profile_min_pipeline_outcomes: int = 5
    profile_min_evaluations: int = 2
    profile_min_losses: int = 3

    # Identity section
    identity_new_threshold: int = 30
    identity_established_threshold: int = 100

    # Personality drift alarm
    personality_drift_tier_threshold: int = 2

    # Phase 3F — Reproduction & Dynasties
    reproduction_cooldown_evals: int = 3
    reproduction_min_prestige: str = "Veteran"
    dynasty_concentration_hard_limit: float = 0.40
    dynasty_concentration_warning: float = 0.25
    memory_inheritance_discount: float = 0.75
    memory_age_decay_factor: float = 0.95
    memory_age_decay_start_days: int = 30
    memory_confidence_floor: float = 0.10
    trust_inheritance_factor: float = 0.50
    temperature_mutation_range: float = 0.03
    max_reproductions_per_cycle: int = 1
    offspring_survival_clock_days: int = 14

    # Phase 3.5: Cost Optimization
    # Model routing
    model_default: str = "claude-haiku-4-5-20251001"
    model_sonnet: str = "claude-sonnet-4-20250514"
    model_routing_enabled: bool = True

    # Pricing (per million tokens)
    haiku_input_price: float = 1.00
    haiku_output_price: float = 5.00
    sonnet_input_price: float = 3.00
    sonnet_output_price: float = 15.00

    # Prompt caching
    prompt_caching_enabled: bool = True

    # Adaptive frequency
    adaptive_frequency_enabled: bool = True
    min_cycle_interval_seconds: int = 30

    # Context diet
    haiku_context_budget_multiplier: float = 0.70
    agora_message_truncate_after_cycles: int = 5
    agora_message_truncate_length: int = 100

    # Batch processing
    batch_enabled: bool = False
    batch_poll_interval_seconds: int = 30
    batch_timeout_seconds: int = 3600

    # Scout Pipeline (anti-starvation)
    scout_min_confidence_threshold: int = 5  # min confidence to trigger Strategist interrupt
    scout_discovery_phase_cycles: int = 50  # cycles before Scout exits discovery phase
    scout_max_consecutive_idle: int = 3  # idle streak before pressure injection

    # Phase 8B: Survival Instinct
    strategic_review_cycle_interval: int = 50
    pressure_eval_imminent_days: int = 5
    pressure_eval_critical_days: int = 3
    death_feed_lookback_days: int = 7
    sip_max_per_evaluation_period: int = 1
    sip_thinking_tax_multiplier: float = 2.0
    intel_settlement_window_hours: int = 48
    intel_price_change_threshold_pct: float = 0.5
    alliance_trust_bonus: float = 0.1
    reputation_evaluation_weight: float = 0.10
    death_last_words_enabled: bool = True
    death_last_words_model: str = "claude-haiku-4-5-20251001"

    # Phase 8C: Code Sandbox
    sandbox_timeout_seconds: int = 5
    sandbox_memory_limit_mb: int = 50
    sandbox_output_limit_bytes: int = 10240
    sandbox_max_script_length: int = 5000
    sandbox_base_cost_usd: float = 0.001
    sandbox_time_rate_usd_per_ms: float = 0.0001
    sandbox_max_tools_per_cycle: int = 3
    sandbox_max_pre_compute_tools: int = 3

    # Phase 8C: Strategy Genome
    genome_mutation_rate: float = 0.15
    genome_mutation_strength: float = 0.10
    genome_structural_mutation_rate: float = 0.05
    genome_warmstart_mutation_rate: float = 0.40
    genome_warmstart_mutation_strength: float = 0.20
    genome_max_modifications_per_eval: int = 2
    genome_fitness_age_bonus: float = 0.1
    genome_diversity_low_threshold: float = 0.3
    tool_inheritance_stat_discount: float = 0.50
    tool_outcome_correlation_lookback_cycles: int = 3

    # Currency & Accounting
    home_currency: str = "CAD"
    starting_treasury: float = 500.0  # in CAD
    currency_cache_ttl_seconds: int = 300  # 5-minute Redis cache
    usd_cad_fallback_rate: float = 1.38  # used if Kraken API unavailable
    usdt_cad_fallback_rate: float = 1.38  # used if Kraken API unavailable
    usdt_cad_manual_override: float = 0.0  # >0 forces this rate (testing)
    usd_cad_manual_override: float = 0.0  # >0 forces this rate (testing)

    # Logging
    log_level: str = "INFO"

    # Sandbox cost cap
    daily_sandbox_cap_usd: float = 0.50

    # Phase 9A: SIP Voting & Colony Maturity
    sip_voting_enabled: bool = True
    sip_default_debate_hours: int = 8
    sip_default_voting_hours: int = 8

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }

    def validate_critical(self) -> list[str]:
        """Check critical fields. Returns list of errors (empty = OK)."""
        errors = []
        if not self.anthropic_api_key or len(self.anthropic_api_key) < 20:
            errors.append("ANTHROPIC_API_KEY is missing or too short")
        if not self.database_url:
            errors.append("DATABASE_URL is missing")
        if self.trading_mode not in ("paper", "live"):
            errors.append(f"TRADING_MODE must be 'paper' or 'live', got '{self.trading_mode}'")
        if self.trading_mode == "live" and not self.exchange_api_key:
            errors.append("EXCHANGE_API_KEY required for live trading")
        return errors


# Singleton instance — import this everywhere
config = SyndicateConfig()
