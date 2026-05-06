"""
The Wire — constants and the authoritative severity/event-type contract.

This module is the single source of truth for severity bands, event types, and
dedup windows. Other Wire modules MUST import from here rather than redefining.
"""

from __future__ import annotations

from typing import Final

# ---------------------------------------------------------------------------
# Severity scale (1-5). Codified contract between The Wire and the colony.
# ---------------------------------------------------------------------------

SEVERITY_TRIVIAL: Final[int] = 1
SEVERITY_NOTABLE: Final[int] = 2
SEVERITY_MATERIAL: Final[int] = 3
SEVERITY_HIGH_IMPACT: Final[int] = 4
SEVERITY_CRITICAL: Final[int] = 5

SEVERITY_LABELS: Final[dict[int, str]] = {
    1: "trivial",
    2: "notable",
    3: "material",
    4: "high_impact",
    5: "critical",
}

# Severity threshold for ticker push to Agora.
TICKER_PUBLISH_MIN_SEVERITY: Final[int] = SEVERITY_MATERIAL

# Severity 5 cannot be assigned by Haiku — only by deterministic rules.
HAIKU_MAX_SEVERITY: Final[int] = SEVERITY_HIGH_IMPACT

# ---------------------------------------------------------------------------
# Event types (closed enum)
# ---------------------------------------------------------------------------

EVENT_TYPES: Final[tuple[str, ...]] = (
    # crypto / venue
    "listing",
    "delisting",
    "hack",
    "exploit",
    "tvl_change",
    "funding_extreme",
    "whale_transfer",
    "exchange_outage",
    "withdrawal_halt",
    "chain_halt",
    # macro
    "macro_calendar",
    "macro_data",
    "regulatory",
    # fallback
    "other",
)

EVENT_TYPES_SET: Final[frozenset[str]] = frozenset(EVENT_TYPES)

# Event types that, at severity 5, halt Operator activity for the affected scope.
OPERATOR_HALT_EVENT_TYPES: Final[frozenset[str]] = frozenset(
    {"exchange_outage", "withdrawal_halt", "chain_halt"}
)

# ---------------------------------------------------------------------------
# Direction (closed enum)
# ---------------------------------------------------------------------------

DIRECTIONS: Final[tuple[str, ...]] = ("bullish", "bearish", "neutral")
DIRECTIONS_SET: Final[frozenset[str]] = frozenset(DIRECTIONS)

# ---------------------------------------------------------------------------
# Dedup
# ---------------------------------------------------------------------------

# Cross-source dedup window. Same canonical_hash within this window collapses.
DEDUP_WINDOW_HOURS: Final[int] = 24

# ---------------------------------------------------------------------------
# Agora event classes registered by The Wire
# ---------------------------------------------------------------------------

AGORA_EVENT_TICKER: Final[str] = "wire.ticker"
AGORA_EVENT_VOLUME_FLOOR_BREACH: Final[str] = "wire.volume_floor_breach"
AGORA_EVENT_DIVERSITY_BREACH: Final[str] = "wire.diversity_breach"
AGORA_EVENT_SOURCE_DISABLED: Final[str] = "wire.source_disabled"
AGORA_EVENT_HAIKU_SEVERITY_CAPPED: Final[str] = "wire.haiku_severity_capped"

# ---------------------------------------------------------------------------
# Health states
# ---------------------------------------------------------------------------

HEALTH_HEALTHY: Final[str] = "healthy"
HEALTH_DEGRADED: Final[str] = "degraded"
HEALTH_FAILING: Final[str] = "failing"
HEALTH_DISABLED: Final[str] = "disabled"
HEALTH_UNKNOWN: Final[str] = "unknown"

HEALTH_STATES: Final[frozenset[str]] = frozenset(
    {HEALTH_HEALTHY, HEALTH_DEGRADED, HEALTH_FAILING, HEALTH_DISABLED, HEALTH_UNKNOWN}
)

# Heartbeat thresholds (Tier 2 enforcement).
DEGRADED_INTERVAL_MULTIPLIER: Final[float] = 2.0
FAILING_CONSECUTIVE_FAILURES: Final[int] = 5
DISABLED_CONSECUTIVE_FAILURES: Final[int] = 20

# Volume floor: minimum events expected over rolling window.
VOLUME_FLOOR_WINDOW_HOURS: Final[int] = 6
VOLUME_FLOOR_MIN_EVENTS: Final[int] = 3

# Diversity check: no single source > X% of last 24h events.
DIVERSITY_WINDOW_HOURS: Final[int] = 24
DIVERSITY_MAX_SHARE: Final[float] = 0.70

# ---------------------------------------------------------------------------
# Digestion
# ---------------------------------------------------------------------------

DIGESTION_STATUS_PENDING: Final[str] = "pending"
DIGESTION_STATUS_DIGESTED: Final[str] = "digested"
DIGESTION_STATUS_REJECTED: Final[str] = "rejected"
DIGESTION_STATUS_DEAD_LETTER: Final[str] = "dead_letter"

DIGESTION_STATES: Final[frozenset[str]] = frozenset(
    {
        DIGESTION_STATUS_PENDING,
        DIGESTION_STATUS_DIGESTED,
        DIGESTION_STATUS_REJECTED,
        DIGESTION_STATUS_DEAD_LETTER,
    }
)

DIGEST_MAX_PARSE_RETRIES: Final[int] = 1  # one retry, then dead-letter
DIGEST_SUMMARY_MAX_CHARS: Final[int] = 200

# ---------------------------------------------------------------------------
# Archive query token costs (Tier 3)
# ---------------------------------------------------------------------------

ARCHIVE_QUERY_BASE_TOKENS: Final[int] = 50
ARCHIVE_QUERY_PER_RESULT_TOKENS: Final[int] = 10
ARCHIVE_QUERY_LOOKBACK_PENALTY_TOKENS: Final[int] = 20
ARCHIVE_QUERY_LOOKBACK_PENALTY_THRESHOLD_HOURS: Final[int] = 24

# Subsystems F + G constants (hotfix 2026-05-04, Critic iteration 2
# Finding 4: derivation comments). Centralized here so the
# Strategist/Critic Archive integration uses the same numbers
# everywhere — context_assembler, thinking_cycle, and tests.

PRE_FETCH_SLICE_SIZE: Final[int] = 5
# Matches Scout's recent_signals slice size for consistency across roles.
# The Scout recent_signals block established this convention. Tunable if
# Strategist/Critic operational data shows different appropriate value.

PRE_FETCH_SEVERITY_FLOOR: Final[int] = 3
# Matches Scout's recent_signals severity filter. Severity 1-2 are noise
# tier; severity 3+ are events worth showing the agent without it being
# asked.

PRE_FETCH_LOOKBACK_HOURS: Final[int] = 24
# Matches Scout's recent_signals lookback. Daily window captures
# relevant events without accumulating stale context.

CRITIC_FREE_QUERIES_PER_CRITIQUE: Final[int] = 3
# Matches K=3 across async-bridge users (subsystem H regime review
# threshold, subsystem P eval engine escalation threshold). Tunable if
# Critic operational data shows different appropriate value. The
# mechanism is "free quota per critique cycle"; the value can move
# without changing the mechanism.

MAX_ATTEMPTS_ARCHIVE_QUERY: Final[int] = 3
# Per-row consume-side retry cap for archive_query_results (subsystem
# F+G fix, Critic iteration 2 Finding 1). Threshold matches K=3 across
# async-bridge users (regime review fix H, eval engine fix P). Tunable
# if operational experience shows different appropriate value. The
# contract is consecutive-failure-only; threshold value can move
# without changing the contract.

ARCHIVE_PREFETCH_ESCALATION_THRESHOLD: Final[int] = 3
# Consecutive-prefetch-failure cap on ContextAssembler before
# escalating to CRITICAL log + Agora system-alert (subsystem F+G fix,
# Critic iteration 2 Finding 3 — Library reflection bug shape). Same
# K=3 derivation as the row-level cap above.

MAX_PENDING_ARCHIVE_RESULTS_PER_CYCLE: Final[int] = 10
# Bounds the number of pending archive_query_results consumed in a
# single ContextAssembler.assemble call. Larger values risk
# overwhelming the agent's priority context budget; smaller values
# risk old results getting silently aged out if a Strategist issues
# many queries faster than they're consumed. 10 chosen by inspection —
# matches the `.limit(10)` used elsewhere in ContextAssembler for
# similar context-bounding purposes (e.g. lines 871, 907, 1288).
# Tunable if operational experience shows different appropriate value.

# ---------------------------------------------------------------------------
# Source names (canonical)
# ---------------------------------------------------------------------------

SOURCE_KRAKEN_ANNOUNCEMENTS: Final[str] = "kraken_announcements"
SOURCE_CRYPTOPANIC: Final[str] = "cryptopanic"
SOURCE_DEFILLAMA: Final[str] = "defillama"
SOURCE_ETHERSCAN_TRANSFERS: Final[str] = "etherscan_transfers"
SOURCE_FUNDING_RATES: Final[str] = "funding_rates"
SOURCE_FRED: Final[str] = "fred"
SOURCE_TRADING_ECONOMICS: Final[str] = "trading_economics"
SOURCE_FEAR_GREED: Final[str] = "fear_greed"

TIER1_SOURCE_NAMES: Final[tuple[str, ...]] = (
    SOURCE_KRAKEN_ANNOUNCEMENTS,
    SOURCE_CRYPTOPANIC,
    SOURCE_DEFILLAMA,
)

TIER2_SOURCE_NAMES: Final[tuple[str, ...]] = (
    SOURCE_ETHERSCAN_TRANSFERS,
    SOURCE_FUNDING_RATES,
    SOURCE_FRED,
    SOURCE_TRADING_ECONOMICS,
    SOURCE_FEAR_GREED,
)
