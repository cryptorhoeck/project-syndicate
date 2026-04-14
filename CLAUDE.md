# Project Syndicate — CLAUDE.md

## Project Overview

Project Syndicate is an autonomous, self-evolving multi-agent AI financial ecosystem. Agents are spawned with capital and a mandate to make money. Profitable agents survive, earn reputation, reproduce, and scale. Unprofitable agents are terminated. The system discovers its own strategies through Darwinian natural selection and competitive pressure.

**GitHub:** https://github.com/cryptorhoeck/project-syndicate

## Current Status

**Phase:** Production Readiness Complete — Arena Launch Pending
**Focus:** Get valid Anthropic API key, run smoke test GREEN, launch Arena
**Last Updated:** 2026-04-13

See `CURRENT_STATUS.md` for detailed session-by-session progress.

### Codebase Stats
- **91 source modules** across 14 packages (~29,000 lines of Python)
- **73 test files**, **759 tests** (757 pass, 2 known sandbox failures)
- **47 SQLAlchemy models** → 48 database tables
- **8 Alembic migrations** (linear chain, no forks)
- **29 HTML templates** + 1 JS module (constellation.js)

## Architecture Quick Reference

### Four Layers
1. **Genesis** (God Node) — immortal, manages treasury, spawns/evaluates/kills agents, detects market regimes
2. **Strategy Council** — Scouts (find opportunities), Strategists (build plans), Critics (stress-test plans)
3. **Operations Floor** — Operators (unified execution agents that naturally specialize)
4. **Risk Desk** — Warden (immutable limits), Accountant (P&L + tax), Dead Man's Switch (failsafe for failsafe)

### Key Systems
- **The Agora** — central nervous system, all agent thought/interaction visible, Redis pub/sub + PostgreSQL
- **Internal Economy** — reputation-based market for intel/services between agents
- **Competition System** — ranked resource allocation, Sharpe-based leaderboard, prestige titles
- **The Library** — educational materials, not answer keys. Textbooks for agents.
- **Thinking Tax** — API costs (USD) are part of agent P&L. True P&L = Revenue - Losses - API Costs. All converted to CAD for owner display.
- **Hibernation** — agents can voluntarily pause, survival clock freezes
- **Black Swan Protocol** — Yellow (15% in 4hrs) / Red (30% in 4hrs) / Circuit Breaker (75% from peak) — operates on CAD treasury
- **SIP Framework** — agents propose system-level improvements via The Agora
- **Currency Layer** — two-tier: agents trade in USDT, owner sees CAD. `CurrencyService` fetches USDT/CAD from Kraken, caches in Redis. Treasury stored in CAD, agent capital in USDT, converted at allocation/reclamation boundary.
- **Survival Instinct** — competitive pressure, alliances, strategic hibernation, death last words
- **Code Sandbox** — RestrictedPython execution environment for agent analysis code
- **Strategy Genome** — ~30 evolvable numerical parameters per agent, inherited and mutated during reproduction

### Phase 1: Genesis + Risk Desk
- **Genesis Agent** (`src/genesis/genesis.py`) — immortal God Node, 5-min cycle: health check, treasury update, regime check, agent evaluation, capital allocation, spawn decisions, reproduction, Agora monitoring, daily reports
- **Genesis Runner** (`src/genesis/genesis_runner.py`) — process launcher for Genesis
- **Boot Sequence** (`src/genesis/boot_sequence.py`) — cold-start spawn waves, orientation, pipeline setup
- **Warden** (`src/risk/warden.py`) — immutable safety layer, 30-sec cycle: circuit breaker, Black Swan alerts, trade gate (hybrid approve/reject/hold), per-agent loss limits, portfolio concentration checks (50% hard / 35% warning). No AI, pure code.
- **Accountant** (`src/risk/accountant.py`) — P&L calculation, Sharpe ratio, composite scoring, leaderboard, multi-model API cost tracking, cache token tracking
- **Treasury Manager** (`src/genesis/treasury.py`) — capital allocation (90% rank-based + 10% random anti-monopoly), prestige multipliers, position inheritance, reserve ratio enforcement
- **Regime Detector** (`src/genesis/regime_detector.py`) — rules-based BTC market regime classification (bull/bear/crab/volatile) using MA crossovers, volatility percentiles, market cap trends
- **Exchange Service** (`src/common/exchange_service.py`) — unified ccxt wrapper for Kraken (primary) + Binance (secondary), retry logic
- **Currency Service** (`src/common/currency_service.py`) — USDT/CAD and USD/CAD conversion. Live rates from Kraken, Redis cache (5min TTL), config fallback rates
- **Price Cache** (`src/common/price_cache.py`) — Redis-backed price data caching
- **Email Service** (`src/reports/email_service.py`) — daily reports, Yellow/Red/Circuit Breaker alerts via Gmail SMTP
- **Config** (`src/common/config.py`) — centralized pydantic-settings configuration from .env (~160 variables)
- **Models** (`src/common/models.py`) — all 47 SQLAlchemy models in one file

### Phase 2A: The Agora
- **AgoraService** (`src/agora/agora_service.py`) — central nervous system, all agent communication
- **AgoraPubSub** (`src/agora/pubsub.py`) — Redis pub/sub for real-time message delivery
- **Schemas** (`src/agora/schemas.py`) — Pydantic models, MessageType enum (9 types)
- **10 channels:** market-intel, strategy-proposals, strategy-debate, trade-signals, trade-results, system-alerts, genesis-log, agent-chat, sip-proposals, daily-report
- **Rate limited:** 10 messages per 5-minute window per agent (Genesis exempt)

### Phase 2B: The Library
- **LibraryService** (`src/library/library_service.py`) — textbooks, archives, contributions, mentor packages
- **8 textbooks** in `data/library/textbooks/`, 8 summaries in `data/library/summaries/`
- **Archives:** post-mortems (immediate), strategy records (48h delay), patterns (Genesis-curated), contributions (peer-reviewed)
- **Mentor system:** offspring inherit parent knowledge, heritage condensed at Gen 4+

### Phase 2C: Internal Economy
- **EconomyService** (`src/economy/economy_service.py`) — reputation management, escrow, delegates to market modules
- **Intel Market** (`src/economy/intel_market.py`) — endorsement model, Scouts post signals, agents stake reputation
- **Settlement Engine** (`src/economy/settlement_engine.py`) — hybrid: trade-linked + time-based fallback
- **Review Market** (`src/economy/review_market.py`) — Strategists pay Critics for reviews
- **Service Market** (`src/economy/service_market.py`) — framework only (activates Phase 4)
- **Gaming Detection** (`src/economy/gaming_detection.py`) — wash trading, rubber-stamp, intel spam detection
- **Intel Tracker** (`src/economy/intel_tracker.py`) — 48h settlement, reputation stakes, challenge system

### Phase 3A: Thinking Cycle
- **OODA Loop Engine** (`src/agents/thinking_cycle.py`) — Budget → Observe → Orient+Decide → Validate → Act → Record
- **Budget Gate** (`src/agents/budget_gate.py`) — pre-cycle check: NORMAL / SURVIVAL_MODE / SKIP_CYCLE
- **Context Assembler** (`src/agents/context_assembler.py`) — builds agent "mind" per cycle. 4 modes: Normal/Crisis/Hunting/Survival. Token-budgeted with relevance scoring.
- **Output Validator** (`src/agents/output_validator.py`) — JSON parsing, schema validation, action space check, Warden pre-check
- **Action Executor** (`src/agents/action_executor.py`) — routes validated actions to Agora/DB/Warden/Exchange
- **Cycle Recorder** (`src/agents/cycle_recorder.py`) — writes to PostgreSQL, Agora, Redis short-term memory
- **Memory Manager** (`src/agents/memory_manager.py`) — three-tier: Working (context), Short-term (Redis, 50 cycles), Long-term (PostgreSQL)
- **Cycle Scheduler** (`src/agents/cycle_scheduler.py`) — per-role frequency, interrupt triggers, adaptive regime-based multipliers
- **Role Definitions** (`src/agents/roles.py`) — Scout/Strategist/Critic/Operator with action spaces, temperatures, intervals
- **Claude API Client** (`src/agents/claude_client.py`) — Anthropic API wrapper with token tracking, cost calculation, prompt caching, retry logic
- **Orientation** (`src/agents/orientation.py`) — new agent onboarding with textbook injection
- **Model Router** (`src/agents/model_router.py`) — deterministic Haiku/Sonnet routing based on role + cycle type + risk
- **Batch Processor** (`src/agents/batch_processor.py`) — Anthropic Batch API foundation (disabled, for Phase 4)
- **Maintenance** (`src/agents/maintenance.py`) — agent housekeeping operations

### Phase 3C: Paper Trading
- **Execution Service** (`src/trading/execution_service.py`) — paper trading engine with realistic simulation
- **Position Monitor** (`src/trading/position_monitor.py`) — track open positions, stop-loss/take-profit
- **Limit Order Monitor** (`src/trading/limit_order_monitor.py`) — limit order fill simulation
- **Slippage Model** (`src/trading/slippage_model.py`) — realistic slippage simulation
- **Fee Schedule** (`src/trading/fee_schedule.py`) — exchange fee calculation
- **Sanity Checker** (`src/trading/sanity_checker.py`) — pre-trade validation
- **Equity Snapshots** (`src/trading/equity_snapshots.py`) — periodic equity tracking

### Phase 3D: Natural Selection
- **Role Metrics** (`src/genesis/role_metrics.py`) — role-specific composite scoring (Operator/Scout/Strategist/Critic each have unique metrics)
- **Evaluation Engine** (`src/genesis/evaluation_engine.py`) — 3-stage Darwinian selection: quantitative pre-filter → Genesis AI judgment → execute decisions
- **Evaluation Assembler** (`src/genesis/evaluation_assembler.py`) — builds evaluation packages from all analyzers
- **Pipeline Analyzer** (`src/genesis/pipeline_analyzer.py`) — conversion rates at each pipeline stage
- **Rejection Tracker** (`src/genesis/rejection_tracker.py`) — counterfactual simulation for critic rejections
- **Idle Analyzer** (`src/genesis/idle_analyzer.py`) — classifies idle periods (strategic_patience/post_loss_caution/no_input/paralysis)
- **Honesty Scorer** (`src/genesis/honesty_scorer.py`) — supplementary metric (NOT in composites)
- **Ecosystem Contribution** (`src/genesis/ecosystem_contribution.py`) — role-specific contribution calculation

### Phase 3E: Personality Through Experience
- **Behavioral Profile** (`src/personality/behavioral_profile.py`) — 7 traits computed from behavior (risk_appetite, market_focus, timing, decision_style, collaboration, learning_velocity, resilience). Agents NEVER see their own profile.
- **Temperature Evolution** (`src/personality/temperature_evolution.py`) — API temperature drifts ±0.05 per evaluation based on diversity-profitability correlation
- **Reflection Library** (`src/personality/reflection_library.py`) — targeted Library material injection during reflections
- **Identity Builder** (`src/personality/identity_builder.py`) — evolving system prompt identity from FACTS, never labels
- **Relationship Manager** (`src/personality/relationship_manager.py`) — Bayesian trust scoring between agents
- **Divergence Calculator** (`src/personality/divergence.py`) — cosine distance between same-role behavioral profiles

### Phase 3F: Dynasties & Reproduction
- **Dynasty Manager** (`src/dynasty/dynasty_manager.py`) — create/update dynasties, extinction detection, concentration checks
- **Lineage Manager** (`src/dynasty/lineage_manager.py`) — parent chains, profile snapshots, death records, family tree
- **Memorial Manager** (`src/dynasty/memorial_manager.py`) — "The Fallen" memorial records
- **Reproduction Engine** (`src/dynasty/reproduction.py`) — eligibility checks, Genesis AI mutation decisions, memory/trust inheritance, posthumous reproduction
- **Dynasty Analytics** (`src/dynasty/dynasty_analytics.py`) — cross-dynasty comparisons, generational improvement tracking

### Phase 8B: Survival Instinct
- **Survival Context** (`src/agents/survival_context.py`) — rank, competition, death feed, evaluation countdown injected every cycle
- **Alliance Manager** (`src/agents/alliance_manager.py`) — propose/accept/dissolve alliances, trust bonus
- **7 universal actions** — propose_sip, offer_intel, request/accept/dissolve_alliance, strategic_hibernate
- **Death Last Words** — dying agents get final Haiku API call, message stored and broadcast

### Phase 8C: Code Sandbox & Strategy Genome
- **Sandbox Security** (`src/sandbox/security.py`) — static analysis blocklist + RestrictedPython compilation
- **Sandbox Runner** (`src/sandbox/runner.py`) — in-process execution, threading timeout (5s), safe builtins only
- **Sandbox Data API** (`src/sandbox/data_api.py`) — pre-fetched read-only market data for sandbox
- **Sandbox Cost** (`src/sandbox/cost.py`) — execution cost added to thinking tax
- **Tool Tracker** (`src/sandbox/tool_tracker.py`) — tool-outcome correlation via Redis
- **Genome Schema** (`src/genome/genome_schema.py`) — ~30 evolvable parameters per role
- **Mutation Engine** (`src/genome/mutation.py`) — reproduction/warm-start/diversity pressure mutations
- **Genome Manager** (`src/genome/genome_manager.py`) — CRUD, agent-directed modifications, fitness tracking
- **Diversity Monitor** (`src/genome/diversity.py`) — convergence detection

### Phase 6A: Web Dashboard (Command Center)
- **Dark theme only** (deep navy `#080c18`), sci-fi aesthetic (Stellaris meets Bloomberg Terminal)
- **Tech:** FastAPI + Jinja2 + HTMX + Tailwind CSS (Play CDN) + vanilla JS
- **App factory:** `src/web/app.py`, runner: `python scripts/run_web.py` (port 8000)
- **Pages:** Command Center, Agents, Agent Detail, Agora, Leaderboard, Library, System
- **Live Feed:** Server-Sent Events (`/api/events/stream`) for real-time Agora streaming
- **Constellation:** Canvas-based force-directed ecosystem graph (`src/web/static/js/constellation.js`)
- **Templates:** `src/web/templates/` (29 HTML files across pages/, fragments/, components/)
- **API routes:** `src/web/routes/` (agents, agora, dynasty, leaderboard, library, personality, sse, system, pages)

### Phase 8A: CLI Launcher
- **syndicate.bat** — double-click entry point, activates venv, runs CLI
- **syndicate_cli.py** (`scripts/syndicate_cli.py`) — rich terminal menu with options: Launch All, individual services, smoke test, health check
- **Service management:** `scripts/syndicate_services.py`, `scripts/syndicate_pids.py`, `scripts/syndicate_config.py`

### Phase 3.5: API Cost Optimization
- **Model Router** — deterministic Haiku/Sonnet routing. Kill switch: `model_routing_enabled=False`
- **Prompt Caching** — `cache_control: {"type": "ephemeral"}` on system prompts. 90% input savings on cache hits
- **Adaptive Cycle Frequency** — regime-based multipliers (volatile=faster, crab=slower). 30-second floor
- **Context Window Diet** — Haiku gets 70% token budget, old Agora messages truncated

## Dev Environment

- **OS:** Windows 11
- **Shell:** Command Prompt (CMD) — always use CMD commands, never PowerShell
- **Python:** 3.13.7 with .venv virtual environment
- **Path:** E:\project syndicate
- **Database:** PostgreSQL (local, 48 tables)
- **Message Bus:** Redis-compatible (Memurai on Windows)
- **Git Remote:** https://github.com/cryptorhoeck/project-syndicate.git

## Directory Structure

```
E:\project syndicate\
├── CLAUDE.md                    ← You are here
├── CHANGELOG.md                 ← Version history (update with every change)
├── CURRENT_STATUS.md            ← Session-by-session progress tracking
├── DEFERRED_ITEMS_TRACKER.md    ← Deferred design items, checked per phase
├── requirements.txt             ← Python dependencies
├── .env / .env.example          ← Environment variables (~160 config vars)
├── .gitignore
├── alembic.ini                  ← DB migration config
├── pytest.ini                   ← Test runner config
├── syndicate.bat                ← Double-click to launch CLI Command Center
├── alembic/                     ← DB migrations (8 files, linear chain)
├── src/
│   ├── agents/                  ← Thinking cycle, context, actions, orientation, model routing, alliances
│   ├── agora/                   ← Message bus, channels, persistence, pub/sub
│   ├── common/                  ← Base agent, config, models (47 tables), exchange, currency, price cache
│   ├── dynasty/                 ← Dynasties, lineage, reproduction, memorials, analytics
│   ├── economy/                 ← Reputation, intel market, reviews, settlement, gaming detection
│   ├── genesis/                 ← Genesis agent, boot sequence, treasury, evaluation, regime, role metrics
│   ├── genome/                  ← Strategy genome, mutation, diversity monitoring
│   ├── library/                 ← Knowledge layer, textbooks, archives, contributions
│   ├── personality/             ← Behavioral profiles, temperature, identity, trust, divergence
│   ├── reports/                 ← Email service, alert system
│   ├── risk/                    ← Warden, Accountant, Dead Man's Switch (heartbeat)
│   ├── sandbox/                 ← Code sandbox, security, data API, cost, tool tracking
│   ├── trading/                 ← Paper trading, execution, position/order monitoring, fees, slippage
│   └── web/                     ← Dashboard (FastAPI + Jinja2 + HTMX)
│       ├── routes/              ← API endpoints (9 route modules)
│       ├── templates/           ← HTML (pages/, fragments/, components/)
│       └── static/              ← JS (constellation.js), favicon
├── tests/                       ← 73 test files, 759 tests
├── config/                      ← System configuration
├── data/                        ← Library textbooks + summaries (16 files)
├── backups/                     ← Timestamped backups (gitignored)
├── scripts/
│   ├── syndicate_cli.py         ← CLI launcher main app (rich terminal menu)
│   ├── syndicate_config.py      ← Config detection and management
│   ├── syndicate_pids.py        ← PID tracking for service management
│   ├── syndicate_services.py    ← Service start/stop/health logic
│   ├── run_arena.py             ← Arena process launcher
│   ├── run_agents.py            ← Agent process launcher
│   ├── run_all.py               ← Full system launcher (dev mode)
│   ├── run_genesis.py           ← Genesis standalone launcher
│   ├── run_warden.py            ← Warden standalone launcher
│   ├── run_web.py               ← Web dashboard launcher (port 8000)
│   ├── run_trading.py           ← Trading services launcher
│   ├── run_price_fetcher.py     ← Price data fetcher
│   ├── backup.py                ← Database/config backup
│   ├── clean_slate.py           ← Reset DB to fresh state
│   ├── smoke_test.py            ← Pre-launch gate (checks DB, Redis, API, config)
│   ├── pulse_check.py           ← Quick health check
│   └── arena_status.py          ← Arena monitoring script
├── logs/                        ← Service logs (gitignored)
└── docs/
    ├── README.md
    ├── arena_log.md             ← Arena observation log template
    ├── arena_monitoring.md      ← Arena daily check-in guide
    ├── kickoffs/                ← Phase kickoff docs (11 files, historical reference)
    └── archive/                 ← Historical docs (original chat, React mockup, etc.)
```

## Mandatory Boilerplate — EVERY Script

At the beginning of every script or module, include (or call) standard boilerplate that performs:

1. **Environment Check:** Verify Python version, required packages, database connectivity, Redis connectivity, API key availability
2. **Version Control:** Script/module version number in a `__version__` variable
3. **Backup:** Before any destructive operation, create a timestamped backup of affected files/state in `backups/`
4. **Process Management:** Check for and handle conflicting processes before starting

## Development Rules

### Always Do
- [ ] Update CHANGELOG.md with every meaningful change
- [ ] Update CURRENT_STATUS.md at the end of every work session
- [ ] Create timestamped backup before modifying existing files
- [ ] Run tests after changes (`python -m pytest tests/`)
- [ ] Commit with descriptive messages referencing the phase and component
- [ ] Use .venv for all Python work (`.venv\Scripts\activate`)
- [ ] Use CMD commands, never PowerShell syntax
- [ ] Keep agent process isolation in mind — agents should not share state except through The Agora

### Never Do
- [ ] Modify anything in `src/risk/` without explicit user approval — this is the immutable safety layer
- [ ] Store API keys, passwords, or secrets in code — use .env only
- [ ] Skip the backup step before modifying database schemas
- [ ] Deploy to production without completing paper trading phase
- [ ] Allow any agent code to bypass the Warden's execution layer

### Code Style
- Python 3.13+ with type hints
- Docstrings on all public functions and classes
- Async where appropriate (especially exchange API calls, agent communication)
- Logging via Python's `logging` module, not print statements
- Configuration via environment variables and config files, not hardcoded values

## Key Technical Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Home currency | CAD | Owner is in Canada, Canadian regulations require CAD accounting |
| Trading currency | USDT | Liquidity — all pairs denominated /USDT |
| Agent isolation | Process-based | Docker overhead too high for 4GB VPS at C$500 scale |
| Agent framework | Custom OODA loop | Built in Phase 3A (`thinking_cycle.py`). LangGraph was originally planned but never used. |
| AI engine | Claude API (Anthropic SDK) | Best reasoning-to-cost ratio for 24/7 operation |
| Default model | Haiku 4.5 | 90% of Sonnet quality at 33% cost for routine work |
| Model routing | Deterministic code | No AI needed to decide which AI to use |
| Prompt caching | Automatic mode | Anthropic SDK manages cache breakpoints |
| Batch API | Off by default | Enable in Phase 4 for evaluations/reflections |
| Database | PostgreSQL | Concurrent writes from multiple agents |
| Message bus | Redis (Memurai on Windows) | Low-latency pub/sub for Agora |
| Exchange API | ccxt | Unified access to 100+ exchanges |
| Web framework | FastAPI | Async-native for real-time agent data |
| Dashboard | FastAPI + Jinja2 + HTMX | Server-rendered with live updates via SSE. React was prototyped but not adopted. |
| DeFi (web3.py) | Deferred to Phase 5 | Solana integration not yet needed |

## Phase Roadmap

| Phase | Focus | Status |
|-------|-------|--------|
| 0 | Foundation (infra, DB, Redis, base agent, backup, recovery) | **COMPLETE** |
| 1 | Genesis + Risk Desk | **COMPLETE** |
| 2A | The Agora (central nervous system) | **COMPLETE** |
| 2B | The Library (knowledge layer) | **COMPLETE** |
| 2C | Internal Economy (reputation marketplace) | **COMPLETE** |
| 2D | Web Frontend (dashboard) | **COMPLETE** |
| 3A | Agent Thinking Cycle (OODA loop, memory, scheduling) | **COMPLETE** |
| 3B | Cold Start Boot Sequence (spawn waves, orientation, pipeline) | **COMPLETE** |
| 3C | Paper Trading Infrastructure | **COMPLETE** |
| 3D | Natural Selection (evaluation, survival, reproduction) | **COMPLETE** |
| 3E | Personality Through Experience (profiles, temperature, identity, trust) | **COMPLETE** |
| 3F | First Death, First Reproduction, First Dynasty | **COMPLETE** |
| 3.5 | API Cost Optimization (model routing, caching, adaptive frequency) | **COMPLETE** |
| 6A | The Command Center (sci-fi dashboard) | **COMPLETE** |
| 8A | CLI Launcher (one-click startup) | **COMPLETE** |
| 8B | Survival Instinct (competitive behavior, alliances, SIPs) | **COMPLETE** |
| 8C | Code Sandbox & Strategy Genome | **COMPLETE** |
| 9 | Production Readiness Testing (hardening, integration, stress) | **COMPLETE** |
| 4 | The Arena (full paper trading — 21-day run) | **NEXT** |
| 5 | Social Presence + Solana | Pending |
| 6B | Owner Console + Auth | Pending |
| 8 | Go Live (real capital) | Pending |

## Useful Commands

```cmd
REM ══ THE EASY WAY — double-click syndicate.bat or: ══
.venv\Scripts\python.exe scripts\syndicate_cli.py

REM Activate virtual environment
.venv\Scripts\activate

REM Install dependencies
pip install -r requirements.txt

REM Run tests
python -m pytest tests/ -v

REM Run smoke test (pre-launch gate)
python scripts\smoke_test.py

REM Start the full system (dev mode)
python scripts\run_all.py

REM Start the Arena
python scripts\run_arena.py

REM Start individual services
python scripts\run_genesis.py
python scripts\run_warden.py
python scripts\run_web.py

REM Run Dead Man's Switch
python src\risk\heartbeat.py

REM Check Redis/Memurai
redis-cli ping

REM Database backup
python scripts\backup.py

REM Reset to fresh DB state
python scripts\clean_slate.py

REM Git workflow
git add .
git commit -m "Phase X: [component] - [description]"
git push origin main
```

## Context for AI Sessions

When starting a new Claude Code session on this project:
1. Read this CLAUDE.md first
2. Read CURRENT_STATUS.md for where we left off
3. Check CHANGELOG.md for recent changes
4. Verify the .venv is activated
5. Run tests to confirm nothing is broken
6. Continue from where CURRENT_STATUS.md indicates

Phase kickoff docs are in `docs/kickoffs/` for reference. Historical project documents are in `docs/archive/`.
