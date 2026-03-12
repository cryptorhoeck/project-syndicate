# Changelog

All notable changes to Project Syndicate will be documented in this file.

## [0.8.0] - 2026-03-12

### Added — Phase 3B: The Cold Start Boot Sequence

#### Boot Sequence
- **Boot Sequence Orchestrator** (`src/genesis/boot_sequence.py`) — 3 condition-based spawn waves: Wave 1 (2 Scouts), Wave 2 (1 Strategist after scouts orient), Wave 3 (1 Critic + 1 Operator after strategist orients). 21-day survival clocks. Logs to boot_sequence_log table.
- **Orientation Protocol** (`src/agents/orientation.py`) — special first-cycle handling for new agents. Library textbook injection at 150% token budget, role-specific prompts, initial watchlist extraction, pass/fail validation.
- **Day-10 Health Check** (`src/genesis/health_check.py`) — early evaluation of Gen 1 agents. Checks cycle count, idle rate, validation fail rate, API cost efficiency. Can extend/shorten survival clocks and adjust budgets.

#### Inter-Agent Pipeline
- **Opportunities Manager** (`src/agents/opportunities.py`) — Scout → Strategist pipeline. Create, claim, expire, and convert opportunities. TTL-based expiry, market/urgency filtering.
- **Plans Manager** (`src/agents/plans.py`) — Strategist → Critic → Operator pipeline. Full plan lifecycle: draft → submitted → under_review → approved/rejected/revision_requested → executing → completed. Status transition validation.
- **Action Executor** updated — `broadcast_opportunity` creates Opportunity records, `propose_plan` creates Plan records, critic verdicts update Plan status. Full pipeline-aware routing.
- **Context Assembler** updated — pipeline-aware context: Scouts see their opportunities, Strategists see unclaimed opportunities + their plans, Critics see plans awaiting review, Operators see approved plans.

#### Infrastructure
- **Market Data Service** (`src/common/market_data.py`) — lightweight market data wrapper with exchange integration and mock fallback. Provides top markets, market summary, and individual snapshots with caching.
- **Maintenance Service** (`src/agents/maintenance.py`) — periodic housekeeping: expire stale opportunities, clean up abandoned plans, reset daily thinking budgets, prune terminated agent memory.
- **Textbook Summaries** (`data/library/summaries/`) — condensed training materials for agent orientation: thinking_efficiently, market_mechanics, risk_management.

#### Database
- New table: `opportunities` — Scout-discovered opportunities with TTL, urgency, and pipeline tracking
- New table: `plans` — trading plans with full lifecycle status, critic review, and operator assignment
- New table: `boot_sequence_log` — boot sequence events by wave
- Agent table additions: spawn_wave, orientation_completed, orientation_failed, health_check_passed, health_check_at, initial_watchlist

#### Configuration
- 4 new config variables: gen1_survival_clock_days, opportunity_ttl_hours, health_check_day, orientation_token_budget_multiplier

#### Tests
- 94 new tests (380 total): market_data (12), opportunities (12), plans (17), orientation (12), boot_sequence (16), health_check (12), maintenance (9)

## [0.7.0] - 2026-03-12

### Added — Phase 3A: The Agent Thinking Cycle

#### Core Engine
- **Thinking Cycle Engine** (`src/agents/thinking_cycle.py`) — OODA loop master orchestrator: Budget → Observe → Orient+Decide → Validate → Act → Record
- **Budget Gate** (`src/agents/budget_gate.py`) — pre-cycle check with NORMAL/SURVIVAL_MODE/SKIP_CYCLE states, rolling average cost from last 20 cycles
- **Context Assembler** (`src/agents/context_assembler.py`) — builds agent context within token budget, 4 dynamic modes (Normal/Crisis/Hunting/Survival), relevance scoring, tiktoken estimation
- **Output Validator** (`src/agents/output_validator.py`) — 5-step validation pipeline (JSON parse, schema check, action space, Warden pre-check, sanity), one retry with repair prompt (double tax)
- **Action Executor** (`src/agents/action_executor.py`) — routes 18 action types to Agora/DB/Warden, paper trading placeholder for Operator trades
- **Cycle Recorder** (`src/agents/cycle_recorder.py`) — writes to PostgreSQL (agent_cycles), Agora (agent-activity), Redis (short-term memory), agent running stats

#### Memory & Learning
- **Memory Manager** (`src/agents/memory_manager.py`) — three-tier memory: Working (context window), Short-term (Redis, 50 cycles), Long-term (PostgreSQL, persistent)
- Reflection processing: lesson/pattern extraction, memory promotion/demotion by content match
- Memory inheritance: parent → offspring with confidence decay, grandparent passthrough

#### Scheduling & Roles
- **Cycle Scheduler** (`src/agents/cycle_scheduler.py`) — per-role frequency, interrupt triggers (opportunity→strategist, plan→critic, approval→operator, alert→all), 60s cooldown, Redis priority queue
- **Role Definitions** (`src/agents/roles.py`) — Scout/Strategist/Critic/Operator with complete action spaces (4-5 actions each + universal go_idle), temperatures, cycle intervals
- **Claude API Client** (`src/agents/claude_client.py`) — Anthropic SDK wrapper with token/cost tracking, exponential backoff retries, repair call support

#### Database
- New table: `agent_cycles` — full black box record of every thinking cycle
- New table: `agent_long_term_memory` — curated agent wisdom with confidence scores
- New table: `agent_reflections` — reflection cycle outputs with memory promotions/demotions
- Agent table additions: cycle_count, last_cycle_at, avg_cycle_cost, avg_cycle_tokens, idle_rate, validation_fail_rate, warden_violation_count, current_context_mode, api_temperature, watched_markets

#### Configuration
- 16 new config variables: cycle intervals, temperatures, token budgets, memory sizes, retry settings

#### Tests
- 66 new tests (286 total): budget_gate (7), context_assembler (10), output_validator (12), cycle_scheduler (15), memory_manager (12), thinking_cycle integration (10)

## [0.6.0] - 2026-03-12

### Added — Phase 2D: Web Frontend (Mission Control Dashboard)

#### Application
- FastAPI app factory (`src/web/app.py`) — lifespan management, route registration, static file serving
- Dependencies module (`src/web/dependencies.py`) — shared DB session access, common template context
- Runner script (`scripts/run_web.py`) — standalone web server startup with uvicorn (port 8000)
- Updated `scripts/run_all.py` with `--with-web` flag for optional web inclusion

#### Pages (5 full pages + 2 detail pages)
- **Agora** (`/agora`) — live message feed with channel sidebar, type/importance filters, 10s auto-refresh
- **Leaderboard** (`/leaderboard`) — agent rankings table with Intel, Critic, Reputation, Dynasty tabs
- **Library** (`/library`) — tabbed entry browser (textbooks, post-mortems, strategies, patterns, contributions)
- **Library Entry** (`/library/{id}`) — full content view with metadata sidebar
- **Agents** (`/agents`) — card grid of active agents with summary stats
- **Agent Detail** (`/agents/{id}`) — full profile with metrics, lineage tree, messages, reputation history
- **System** (`/system`) — status banner, process health, economy overview, recent alerts

#### API Fragment Routes (HTMX)
- `/api/agora/messages`, `/api/agora/channels` — filtered message fragments
- `/api/leaderboard/agents`, `/intel`, `/critics`, `/reputation`, `/dynasties`
- `/api/library/entries` — category/search filtered entries
- `/api/agents/cards`, `/{id}/messages`, `/{id}/reputation`
- `/api/system/status`, `/processes`, `/economy`, `/alerts`, `/status-pill`

#### Templates & Components
- Base template with Tailwind CSS (Play CDN), HTMX, JetBrains Mono + IBM Plex Sans (Google Fonts)
- Dark/light theme toggle via `class="dark"` on `<html>`, saved in localStorage
- 8 reusable components: nav, agent_badge, message_row, agent_card, stat_card, status_dot, theme_toggle, empty_state
- 11 HTMX fragment templates for server-side partial rendering
- SVG favicon (network node icon, amber #fbbf24)

#### Design
- "Mission Control for AI Colony" aesthetic — dark theme default, data-dense, cinematic
- Agent-type color coding: Genesis=amber, Scout=sky, Strategist=violet, Critic=orange, Operator=emerald, System=rose
- Two-tier route structure (`/` public, `/admin/` redirects to public for now — auth in Phase 6)
- Narrative empty states for all pages/sections

#### Tests
- 34 new tests (`tests/test_web_app.py`): app startup, redirects, all page routes, all API fragments, theme, empty states
- Total: 220 tests passing

### Dependencies
- Added `aiofiles` to requirements.txt

## [0.5.0] - 2026-03-12

### Added — Phase 2C: The Internal Economy (Reputation Marketplace)

#### Economy Core
- EconomyService (`src/economy/economy_service.py`) — central orchestrator: reputation management (initialize, transfer, reward, penalty, escrow/release), delegates to Intel Market, Review Market, Service Market, Settlement Engine, Gaming Detector
- Economy Schemas (`src/economy/schemas.py`) — Pydantic models and enums: SignalDirection, SignalStatus, EndorsementStatus, ReviewRequestStatus, ReviewVerdict, GamingFlagType, GamingFlagSeverity, IntelSignalResponse, IntelEndorsementResponse, ReviewRequestResponse, ReviewAssignmentResponse, CriticAccuracyResponse, ServiceListingResponse, GamingFlagResponse, EconomyStats
- Economy package init (`src/economy/__init__.py`) — exports all public types

#### Intel Market
- IntelMarket (`src/economy/intel_market.py`) — create_signal() (validates rep >= 50, asset format, expiry), endorse_signal() (validates stake 5-25, no self-endorsement, no duplicates, escrows stake), link_trade_to_endorsement(), get_active_signals(), get_signals_ready_for_settlement(), get_endorsements_for_signal(), get_agent_signal_stats()

#### Settlement Engine
- SettlementEngine (`src/economy/settlement_engine.py`) — run_settlement_cycle() processes all expired signals. Hybrid settlement: trade-linked (full multipliers: scout +/-1x stake, endorser gets stake+2 bonus or loses stake) and time-based fallback (half multipliers: scout +/-0.5x stake, endorser always refunded). Direction threshold: price must move >= 0.5% to count as directional. Gracefully defers if exchange unavailable (extends expiry by 1 hour)

#### Review Market
- ReviewMarket (`src/economy/review_market.py`) — request_review() (budget 10-25 rep, auto-determines if 2 reviews needed for >20% capital strategies), get_open_requests(), accept_review(), submit_review() (pays critic from escrow), update_critic_accuracy(), expire_stale_requests() (refunds budget after 24h), check_overdue_assignments() (warns at deadline, releases after 24h overdue), get_critic_stats()

#### Service Market (Framework)
- ServiceMarket (`src/economy/service_market.py`) — CRUD only: create_listing(), get_listings(), cancel_listing(). Full marketplace deferred to Phase 4

#### Gaming Detection
- GamingDetector (`src/economy/gaming_detection.py`) — run_full_detection() runs all checks daily: check_wash_trading() (flags >50% endorsements between same pair over 7 days), check_rubber_stamp_critics() (flags >90% approval rate over 10+ reviews), check_intel_spam() (flags <10% endorsement rate over 20+ signals in 30 days). resolve_flag() with optional penalty. Posts summary to system-alerts

#### Database
- Alembic migration: 7 new tables (intel_signals, intel_endorsements, review_requests, review_assignments, critic_accuracy, service_listings, gaming_flags)
- Indexes: status+expires on signals/requests, scout_agent_id, signal_id, endorser+status, critic+completed, resolved+detected
- Unique constraints: one endorsement per agent per signal, one assignment per critic per request
- 7 new SQLAlchemy ORM models in `src/common/models.py`

#### Agent Integration
- BaseAgent (`src/common/base_agent.py`) — updated to v0.5.0: new economy_service parameter, create_intel_signal(), endorse_intel(), request_strategy_review(), accept_and_submit_review(), get_my_reputation(). Graceful no-op when EconomyService is None
- Genesis (`src/genesis/genesis.py`) — updated to v0.5.0: accepts economy_service, initializes agent reputation on spawn, checks negative reputation agents (flags for evaluation), runs settlement cycle every Genesis cycle, economy maintenance in hourly cycle (expire stale reviews, check overdue assignments), gaming detection + economy stats in daily report

#### Process Runners
- genesis_runner.py — updated to v0.5.0: creates EconomyService and passes to Genesis

#### Tests (66 new, 186 total — all passing)
- test_economy_service.py (9 tests): initialize reputation, transfer, insufficient balance, reward, penalty, negative detection, escrow/release, insufficient escrow, transaction history
- test_intel_market.py (16 tests): create signal (valid, low rep, invalid asset, past expiry), endorse (valid, own signal, duplicate, expired, min/max stake, insufficient rep, link trade), queries (active, by asset, ready for settlement, stats)
- test_settlement_engine.py (14 tests): no endorsements, bullish/bearish/neutral correct/incorrect, direction threshold, trade-linked profitable/unprofitable, time-based correct/incorrect, mixed settlement, no exchange, exchange error, full cycle
- test_review_market.py (13 tests): request (valid, two required, insufficient rep), accept (valid, own, already full, second reviewer), submit (single, two critics), expire stale, overdue, critic accuracy, stats
- test_gaming_detection.py (10 tests): wash trading (detected, below threshold), rubber stamp (detected, below threshold, insufficient reviews), intel spam (detected, below threshold), resolve flag (basic, with penalty), full cycle
- test_economy_integration.py (4 tests): reputation initialization, negative rep trigger, full intel lifecycle, full review lifecycle

### Design Decisions
- Intel model: endorsement, not paywall — all intel is public, scouts earn via accountability
- Settlement: hybrid — trade-linked (full multipliers) + time-based fallback (half multipliers)
- Warden does NOT interact with the Economy — financial safety is separate from reputation economics
- Escrow: reputation deducted on escrow, refunded via release_escrow — no separate escrow table

## [0.4.0] - 2026-03-12

### Added — Phase 2B: The Library (Institutional Memory)

#### Library Core
- LibraryService (`src/library/library_service.py`) — institutional memory hub: list_textbooks(), get_textbook(), search_textbooks(), get_entries(), search_entries(), record_view(), create_post_mortem(), create_strategy_record(), create_pattern_summary(), publish_delayed_entries(), submit_contribution(), submit_review(), handle_review_timeouts(), build_mentor_package(), get_mentor_package(), get_library_stats()
- Library Schemas (`src/library/schemas.py`) — Pydantic models: LibraryCategory enum (5 types), ContributionStatus enum, ReviewDecision enum, LibraryEntryResponse, LibraryEntryBrief, ContributionResponse, MentorPackage
- Library package init (`src/library/__init__.py`) — exports all public types

#### Database
- Alembic migration: 3 new tables (library_entries, library_contributions, library_views)
- library_entries: category, title, content, summary, tags, source_agent_id, publish_after, is_published, view_count
- library_contributions: full peer review workflow — submitter, two reviewers, decisions, reasoning, final_decision_by (consensus/genesis_tiebreaker/genesis_solo), reputation_effects_applied
- library_views: per-agent per-entry unique view tracking
- Lineage table updated: mentor_package_json, mentor_package_generated_at columns

#### Textbooks
- 8 placeholder markdown files in data/library/textbooks/: market mechanics, strategy categories, risk management, crypto fundamentals, technical analysis, DeFi protocols, exchange APIs, thinking efficiently
- Framework only — content pending review before Phase 3

#### Agent Integration
- BaseAgent (`src/common/base_agent.py`) — updated to v0.4.0: new library_service parameter, read_textbook(), search_library(), submit_to_library(), get_my_pending_reviews(). Graceful no-op when LibraryService is None
- Genesis (`src/genesis/genesis.py`) — updated to v0.4.0: accepts library_service, auto-creates post-mortems on agent termination, creates strategy records on profitable survival, runs publish_delayed_entries() and handle_review_timeouts() in hourly maintenance

#### Process Runners
- genesis_runner.py — updated: creates LibraryService with optional anthropic_client, passes to Genesis

#### Features
- Post-mortems: auto-generated on agent termination, immediate publication, template fallback when no AI
- Strategy records: auto-generated on profitable survival, 48-hour publication delay
- Pattern summaries: Genesis-curated insights, immediate publication to market-intel
- Peer review: Genesis solo when < 8 agents, two qualified reviewers when >= 8 (reputation >= 200, not self, not same lineage)
- Review timeouts: 24-hour deadline, single decision stands, neither → Genesis solo
- Reputation effects: logged as pending (reviewer +5 participation, +10 accuracy, submitter +15 approved, -10 rejected consensus)
- Mentor system: knowledge inheritance for offspring, heritage condensed at Gen 4+ via Claude API
- View tracking: idempotent per agent per entry

#### Tests (46 new, 120 total — all passing)
- test_library_textbooks.py (9 tests): list, get by topic, fuzzy match, not found, search, placeholder detection
- test_library_archives.py (13 tests): post-mortems (with/without AI, tags), strategy records (delayed, publish), patterns, views (increment, idempotent), entries (by category, published only), search
- test_library_contributions.py (11 tests): submission, genesis solo, peer assignment, not self, not same lineage, both approve, both reject, split without AI, genesis solo approve, timeout, reputation effects
- test_library_mentor.py (6 tests): gen1 package, grandparent data, no AI condensation, store/retrieve, gen1 no prior, recommended entries
- test_library_integration.py (7 tests): death → post-mortem, survival → strategy record, BaseAgent read/submit/reviews, Agora notifications, library stats

#### Dependencies
- Added: markdown

## [0.3.0] - 2026-03-12

### Added — Phase 2A: The Agora (Central Nervous System)

#### Agora Core
- AgoraService (`src/agora/agora_service.py`) — central communication hub for all agents: post_message(), read_channel(), read_channel_since_last_read(), read_multiple_channels(), get_recent_activity(), search_messages(), mark_read(), get_unread_counts(), get_channels(), create_channel(), subscribe(), cleanup_expired_messages(), get_channel_stats()
- AgoraPubSub (`src/agora/pubsub.py`) — Redis pub/sub manager using redis.asyncio: publish(), subscribe(), unsubscribe(), subscribe_multiple(), shutdown(), with background listener loop
- Agora Schemas (`src/agora/schemas.py`) — Pydantic models: MessageType enum (9 types: thought, proposal, signal, alert, chat, system, evaluation, trade, economy), AgoraMessage, AgoraMessageResponse, ChannelInfo, ReadReceipt
- Agora package init (`src/agora/__init__.py`) — create_agora_service() factory function

#### Database
- Alembic migration: 5 new columns on messages table (message_type, agent_name, parent_message_id, importance, expires_at)
- New table: agora_channels (10 default channels seeded: market-intel, strategy-proposals, strategy-debate, trade-signals, trade-results, system-alerts, genesis-log, agent-chat, sip-proposals, daily-report)
- New table: agora_read_receipts (per-agent per-channel read tracking with unique constraint)
- Backfill: existing messages get agent_name='Genesis' and message_type='chat' defaults

#### Agent Integration
- BaseAgent (`src/common/base_agent.py`) — updated to v0.3.0: new agora_service parameter, post_to_agora() now supports message_type/importance/expires_at, new methods: read_agora() with only_unread and message_types filters, mark_agora_read(), get_agora_unread(), broadcast(). Graceful fallback to direct DB writes when AgoraService is None
- Genesis (`src/genesis/genesis.py`) — updated to v0.3.0: accepts agora_service, all post_to_agora() calls now use proper MessageType (SYSTEM/SIGNAL/EVALUATION), Agora monitoring uses read receipts and unread counts, hourly expired message cleanup
- Warden (`src/risk/warden.py`) — updated to v0.3.0: accepts optional agora_service, alert escalation and emergency kills post via AgoraService (ALERT type, importance=2), fallback to Redis pub/sub when no AgoraService

#### Process Runners
- genesis_runner.py — updated: creates async Redis client and AgoraService, passes to Genesis, clean shutdown of pub/sub
- warden_runner.py — updated: creates async Redis client and AgoraService, passes to Warden, clean shutdown

#### Features
- Rate limiting: 10 messages per 5-minute window per agent via Redis counter with TTL (Genesis exempt)
- Read receipts: per-agent per-channel tracking, explicit mark_read() required after processing
- Channel management: auto-creation of non-system channels, system channels are protected
- Expired messages: messages can have expires_at, excluded from reads by default, Genesis cleans up hourly
- Message threading: parent_message_id FK for reply chains
- Importance levels: 0=normal, 1=important, 2=critical — filterable in reads
- Full-text search: basic ILIKE search across Agora messages with channel/agent filters

#### Tests (44 new, 74 total — all passing)
- test_agora_service.py (30 tests): posting (basic, all types, metadata, importance, expiry), reading (basic, since, type filter, importance filter, expired handling, limit, multi-channel), search (basic, by channel, by agent), rate limiting (enforced, per-agent, genesis exempt, reset), read receipts (create, update, since_last_read, unread_counts), channels (list, create, validation, system protection), maintenance (cleanup, stats)
- test_agora_pubsub.py (6 tests): publish, subscribe, multiple subscribers, unsubscribe, multi-channel subscribe, shutdown
- test_agora_integration.py (6 tests + 1 no-agora): BaseAgent post+read, unread counts, broadcast, message types, fallback without agora, graceful no-op

#### Dependencies
- Added: jinja2, python-multipart (for Phase 2D prep)

## [0.2.0] - 2026-03-12

### Added — Phase 1: Genesis + Risk Desk

#### Genesis Layer
- Genesis Agent (`src/genesis/genesis.py`) — immortal God Node with 5-minute cycle: health checks, treasury updates, regime detection, agent evaluations (rules-based + Claude API for probation), capital allocation, spawn decisions, reproduction checks, Agora monitoring, daily report generation, cold start boot sequence
- Genesis Runner (`src/genesis/genesis_runner.py`) — standalone process launcher with graceful shutdown
- Treasury Manager (`src/genesis/treasury.py`) — capital allocation with 20% reserve ratio, 90/10 rank/random split (anti-monopoly), prestige multipliers (Proven 1.1x, Veteran 1.2x, Elite 1.3x, Legendary 1.5x), position inheritance on agent death, peak treasury tracking
- Market Regime Detector (`src/genesis/regime_detector.py`) — rules-based BTC market classification (bull/bear/crab/volatile) using 20/50-day MA crossovers, 30-day annualized volatility, 80th percentile threshold, market cap trends

#### Risk Desk
- The Warden (`src/risk/warden.py`) — immutable safety layer (no AI, pure code), 30-second check cycle: circuit breaker (75% from peak), Black Swan Protocol (Yellow 15%/Red 30% in 4hrs), trade gate (hybrid auto-approve/hold/reject), per-agent 50% loss limit, Redis-based trade request queue, alert escalation with agent freezing
- Warden Runner (`src/risk/warden_runner.py`) — standalone process launcher
- The Accountant (`src/risk/accountant.py`) — P&L calculation (gross, API cost, true), Sharpe ratio (annualized, daily returns), thinking efficiency, consistency score, composite scoring (0.40 Sharpe + 0.25 True P&L% + 0.20 Efficiency + 0.15 Consistency), leaderboard generation, API cost tracking with model-specific pricing, system financial summary

#### Common Infrastructure
- Exchange Service (`src/common/exchange_service.py`) — unified ccxt wrapper for Kraken (primary) + Binance (secondary) with retry logic (3x exponential backoff), ticker, OHLCV, balance, order placement, cancellation, emergency close-all
- Paper Trading Service — same interface as ExchangeService but simulated execution with in-memory order book against real market data
- Central Config (`src/common/config.py`) — pydantic-settings based configuration with all system parameters loaded from .env
- Email Service (`src/reports/email_service.py`) — daily report delivery, Yellow/Red/Circuit Breaker alerts, emergency notifications via Gmail SMTP

#### Database
- Alembic migration: added 8 new columns to agents table (composite_score, hibernation_start, hibernation_reason, total_api_cost, total_gross_pnl, total_true_pnl, evaluation_count, profitable_evaluations)
- Added alert_status column to system_state table
- New table: inherited_positions (position inheritance on agent death)
- New table: market_regimes (regime detection history)
- New table: daily_reports (Claude-generated narrative reports)

#### Process Management
- `scripts/run_all.py` — starts Genesis, Warden, and Dead Man's Switch as monitored subprocesses with auto-restart
- `scripts/run_genesis.py` — standalone Genesis launcher
- `scripts/run_warden.py` — standalone Warden launcher

#### Tests (30 tests, all passing)
- test_warden.py: trade gate (auto-approve, review, yellow hold, red reject, circuit breaker reject), loss limit detection, alert escalation
- test_accountant.py: P&L calculation, Sharpe ratio, composite score, thinking efficiency, consistency, leaderboard
- test_treasury.py: reserve ratio enforcement, prestige multipliers, position inheritance, random allocation, capital reclamation, peak treasury update
- test_regime_detector.py: bull/bear/crab/volatile detection, regime change detection, insufficient data handling
- test_exchange_service.py: paper trading (buy/sell/balance/insufficient funds/cancel/close-all)

#### Configuration
- Updated .env.example with all Phase 1 environment variables (risk thresholds, Genesis config, evaluation weights, prestige multipliers, thinking budgets, SMTP settings)
- Added new dependencies: schedule, numpy, ta (technical analysis)

### Fixed
- backup.py: pg_dump command now uses --dbname= flag for correct URL handling

## [0.1.0] - 2026-03-12

### Added — Phase 0: Foundation
- Project scaffold and full directory structure
- CLAUDE.md with complete project documentation
- PostgreSQL database with 8 tables: agents, transactions, messages (Agora), evaluations, reputation_transactions, sips, system_state, lineage
- Alembic migration system initialized with initial schema
- SQLAlchemy 2.0 ORM models (`src/common/models.py`)
- Abstract base agent class with lifecycle, Agora integration, and thinking tax tracking (`src/common/base_agent.py`)
- Backup system with pg_dump and config backup, rotation policy (`scripts/backup.py`)
- Dead Man's Switch heartbeat monitor — independent process monitoring PostgreSQL, Redis, and system state freshness (`src/risk/heartbeat.py`)
- Python virtual environment with 20+ dependencies installed
- Environment configuration template (`.env.example`)
- `.gitignore` for Python/IDE/data exclusions
- Redis/Memurai connectivity confirmed
- PostgreSQL initialized and running
