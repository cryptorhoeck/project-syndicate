# Changelog

All notable changes to Project Syndicate will be documented in this file.

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
