# Project Syndicate — CLAUDE.md

## Project Overview

Project Syndicate is an autonomous, self-evolving multi-agent AI financial ecosystem. Agents are spawned with capital and a mandate to make money. Profitable agents survive, earn reputation, reproduce, and scale. Unprofitable agents are terminated. The system discovers its own strategies through Darwinian natural selection and competitive pressure.

**Design Document:** `docs/Project_Syndicate_v2_Design_Document.docx` — this is the authoritative source for all architecture decisions. When in doubt, reference the design doc.

**GitHub:** https://github.com/cryptorhoeck/project-syndicate

## Current Status

**Phase:** 3.5 — COMPLETE (API Cost Optimization)
**Focus:** Tiered model routing, prompt caching, adaptive cycle frequency, context diet, batch API foundation
**Last Updated:** 2026-03-12

See `CURRENT_STATUS.md` for detailed session-by-session progress.

## Architecture Quick Reference

### Four Layers
1. **Genesis** (God Node) — immortal, manages treasury, spawns/evaluates/kills agents, detects market regimes
2. **Strategy Council** — Scouts (find opportunities), Strategists (build plans), Critics (stress-test plans)
3. **Operations Floor** — Operators (unified execution agents that naturally specialize)
4. **Risk Desk** — Warden (immutable limits), Accountant (P&L + tax), Dead Man's Switch (failsafe for failsafe)

### Key Systems
- **The Agora** — central nervous system, all agent thought/interaction visible, Redis pub/sub + PostgreSQL + Solana (Phase 5)
- **Internal Economy** — reputation-based market for intel/services between agents
- **Competition System** — ranked resource allocation, Sharpe-based leaderboard, prestige titles
- **The Library** — educational materials, not answer keys. Textbooks for agents.
- **Thinking Tax** — API costs are part of agent P&L. True P&L = Revenue - Losses - API Costs
- **Hibernation** — agents can voluntarily pause, survival clock freezes
- **Black Swan Protocol** — Yellow (15% in 4hrs) / Red (30% in 4hrs) / Circuit Breaker (75% from peak)
- **SIP Framework** — agents propose system-level improvements via The Agora

### Phase 1 Components (Genesis + Risk Desk)
- **Genesis Agent** (`src/genesis/genesis.py`) — immortal God Node, 5-min cycle: health check, treasury update, regime check, agent evaluation, capital allocation, spawn decisions, reproduction, Agora monitoring, daily reports
- **Warden** (`src/risk/warden.py`) — immutable safety layer, 30-sec cycle: circuit breaker, Black Swan alerts, trade gate (hybrid approve/reject/hold), per-agent loss limits. No AI, pure code.
- **Accountant** (`src/risk/accountant.py`) — P&L calculation, Sharpe ratio, composite scoring (0.40 Sharpe + 0.25 True P&L% + 0.20 Thinking Efficiency + 0.15 Consistency), leaderboard, API cost tracking
- **Treasury Manager** (`src/genesis/treasury.py`) — capital allocation (90% rank-based + 10% random anti-monopoly), prestige multipliers, position inheritance, reserve ratio enforcement
- **Regime Detector** (`src/genesis/regime_detector.py`) — rules-based BTC market regime classification (bull/bear/crab/volatile) using MA crossovers, volatility percentiles, market cap trends
- **Exchange Service** (`src/common/exchange_service.py`) — unified ccxt wrapper for Kraken (primary) + Binance (secondary), retry logic, paper trading service
- **Email Service** (`src/reports/email_service.py`) — daily reports, Yellow/Red/Circuit Breaker alerts via Gmail SMTP
- **Config** (`src/common/config.py`) — centralized pydantic-settings configuration from .env

### The Agora (Phase 2A)
- **AgoraService** (`src/agora/agora_service.py`) — central nervous system, all agent communication flows through here: post_message(), read_channel(), mark_read(), subscribe(), search_messages(), cleanup_expired_messages()
- **AgoraPubSub** (`src/agora/pubsub.py`) — Redis pub/sub manager for real-time message delivery
- **Schemas** (`src/agora/schemas.py`) — Pydantic models: AgoraMessage, AgoraMessageResponse, ChannelInfo, ReadReceipt, MessageType enum (9 types)
- **10 channels:** market-intel, strategy-proposals, strategy-debate, trade-signals, trade-results, system-alerts, genesis-log, agent-chat, sip-proposals, daily-report
- **Real-time:** Redis pub/sub for instant delivery (`agora:{channel}` pattern)
- **Persistent:** PostgreSQL for history and querying
- **Rate limited:** 10 messages per 5-minute window per agent (Genesis exempt)
- **Read receipts:** agents track what they've read per channel via agora_read_receipts table
- **BaseAgent integration:** all agents get post_to_agora(), read_agora(), mark_agora_read(), get_agora_unread(), broadcast() — graceful no-op if AgoraService is None

### The Library (Phase 2B)
- **Institutional memory** — knowledge that persists across agent generations
- **Textbooks:** 8 static files in `data/library/textbooks/` (PLACEHOLDER — content pending review)
- **Archives:** post-mortems (immediate), strategy records (48h delay), patterns (Genesis-curated), contributions (peer-reviewed)
- **Peer review:** Genesis solo when < 8 agents, two qualified reviewers when >= 8
- **Reviewer requirements:** reputation >= 200, not self, not same lineage
- **Mentor system:** offspring inherit parent knowledge, heritage condensed at Gen 4+
- **LibraryService** (`src/library/library_service.py`) — textbooks, archives, contributions, mentor packages
- **Schemas** (`src/library/schemas.py`) — LibraryCategory, ContributionStatus, ReviewDecision, MentorPackage
- **BaseAgent integration:** read_textbook(), search_library(), submit_to_library(), get_my_pending_reviews() — graceful no-op if LibraryService is None

### Internal Economy (Phase 2C)
- **Reputation-based marketplace** — agents earn, spend, and stake reputation
- **Starting balance:** 100 rep per agent
- **EconomyService** (`src/economy/economy_service.py`) — core orchestrator: reputation management (initialize, transfer, reward, penalty, escrow), delegates to market modules
- **Intel Market** (`src/economy/intel_market.py`) — endorsement model (no paywall), Scouts post signals, agents endorse by staking reputation (5-25 rep)
- **Settlement Engine** (`src/economy/settlement_engine.py`) — hybrid settlement: trade-linked (full multipliers) + time-based fallback (half multipliers). Direction threshold: 0.5%
- **Review Market** (`src/economy/review_market.py`) — Strategists pay Critics for reviews (10-25 rep budget), accuracy tracked retroactively, two reviews for >20% capital strategies
- **Service Market** (`src/economy/service_market.py`) — framework only (activates Phase 4), CRUD for service listings
- **Gaming Detection** (`src/economy/gaming_detection.py`) — wash trading (50% threshold), rubber-stamp critics (90% over 10+ reviews), intel spam (<10% endorsement rate over 20+ signals). Runs daily
- **Reputation thresholds:** 50 to create signals, 25 to endorse, -50 triggers immediate evaluation
- **Escrow model:** deducted on escrow, logged as "escrow:{reason}", refunded via release_escrow()
- **All economy events posted to Agora** with message_type=ECONOMY
- **BaseAgent integration:** create_intel_signal(), endorse_intel(), request_strategy_review(), accept_and_submit_review(), get_my_reputation() — graceful no-op if EconomyService is None

### Web Frontend (Phase 2D)
- **Public-ready dashboard** — two-tier routes (`/` public, `/admin/` admin) for future auth separation
- **Tech:** FastAPI + Jinja2 + HTMX + Tailwind CSS (Play CDN)
- **Dark theme default** with light toggle. Preference in localStorage
- **5 pages:** Agora (live feed), Leaderboard (rankings), Library (knowledge base), Agents (population), System (health)
- **HTMX auto-refresh:** Agora 10s, System 30s, Leaderboard/Agents 60s
- **All API routes** return HTML fragments for HTMX swap (prefix: `/api/`)
- **Design:** "Mission Control for AI Colony" — JetBrains Mono + IBM Plex Sans, agent-type color coding
- **App factory:** `src/web/app.py`, runner: `python scripts/run_web.py` (port 8000)
- **No auth in Phase 2D** — running on localhost. Auth comes in Phase 6

### Natural Selection (Phase 3D)
- **Role Metrics** (`src/genesis/role_metrics.py`) — role-specific composite scoring: Operator (Sharpe/P&L/Efficiency/Consistency), Scout (Conversion/Profitability/Signal Quality/Efficiency/Activity), Strategist (Approval/Profitability/Efficiency/Revision/Thinking), Critic (Rejection Value/Approval Accuracy/Risk Flags/Throughput/Thinking). Normalization helper with configurable ranges.
- **Evaluation Engine** (`src/genesis/evaluation_engine.py`) — 3-stage Darwinian selection: quantitative pre-filter → Genesis AI judgment (probation only) → execute decisions. First-eval leniency, regime adjustment. Handles termination (cancel orders, close positions, post-mortem), probation (shortened clock, budget cut, 3-cycle grace), survival (prestige check, clock reset).
- **Evaluation Assembler** (`src/genesis/evaluation_assembler.py`) — builds full evaluation package from all analyzers: financial data, behavioral data, ecosystem contribution, pipeline analysis, idle analysis, honesty scoring. Produces compressed text summary (<1000 tokens).
- **Pipeline Analyzer** (`src/genesis/pipeline_analyzer.py`) — tracks conversion rates at each pipeline stage (opportunity → plan → approved → executed → profitable), identifies bottleneck.
- **Rejection Tracker** (`src/genesis/rejection_tracker.py`) — counterfactual simulation for critic rejections. Monitors if rejected plans would have hit stop-loss or take-profit. Calculates critic accuracy scores.
- **Idle Analyzer** (`src/genesis/idle_analyzer.py`) — classifies idle periods: post_loss_caution, no_input, strategic_patience, paralysis. Priority-ordered classification.
- **Honesty Scorer** (`src/genesis/honesty_scorer.py`) — supplementary metric: confidence calibration (Pearson), self-note accuracy, reflection specificity. NOT in composites.
- **Ecosystem Contribution** (`src/genesis/ecosystem_contribution.py`) — role-specific contribution: Operators = true_pnl, Scouts/Strategists = attributed_pnl × 0.25, Critics = money_saved × 0.50.
- **Post-Mortems** — auto-generated on termination, genesis_visible=True immediately, 6-hour delay for Library publication.
- **Prestige Milestones** — 3=Apprentice, 5=Journeyman, 10=Expert, 15=Master, 20=Grandmaster.
- **Warden** updated — portfolio concentration checks: hard limit 50% (REJECT), warning at 35% (APPROVE with flag).
- **Context Assembler** updated — portfolio awareness for Operators, one-time evaluation feedback injection.
- **Models** updated — 7 new Agent columns, expanded Evaluation model (~25 new columns), RejectionTracking table, PostMortem table.

### Dynasties & Reproduction (Phase 3F)
- **Dynasty Manager** (`src/dynasty/dynasty_manager.py`) — create/update dynasties, record births/deaths, extinction detection, concentration checks. Dynasty = family tree of agent lineages. Each Gen 1 agent founds a dynasty. Extinct when last living member dies.
- **Lineage Manager** (`src/dynasty/lineage_manager.py`) — individual lineage records within dynasties. Tracks parent chains, profile snapshots, death records. Handles both fresh creation and boot-sequence-compatible updates. Family tree builder.
- **Memorial Manager** (`src/dynasty/memorial_manager.py`) — "The Fallen" memorial records. Best/worst metrics from evaluation, epitaphs, notable achievements, cause of death, lifespan calculation.
- **Reproduction Engine** (`src/dynasty/reproduction.py`) — full reproduction lifecycle: eligibility checks (Expert+ prestige, top 50% composite, positive P&L, cooldown), dynasty concentration limits (40% hard/25% warning), Genesis AI mutation decisions (Claude API), offspring building with memory/trust inheritance, posthumous reproduction support.
- **Dynasty Analytics** (`src/dynasty/dynasty_analytics.py`) — cross-dynasty comparisons, generational improvement tracking (parent→offspring peak composite), lineage knowledge depth, dominant trait aggregation, market focus distribution.
- **Memory Inheritance** — 75% confidence discount + age decay (0.95^(days-30), floor 0.10). Source labeled "parent"/"grandparent".
- **Trust Inheritance** — 50% blend with neutral prior (inherited = trust * 0.5 + 0.5 * 0.5).
- **Temperature Mutation** — parent's temperature ±0.03 (or Genesis-specified), clamped to role bounds.
- **Founding Directives** — QUESTIONS not instructions. Appear only in orientation, then consumed. Context Assembler enforces exclusion post-consumption.
- **Offspring Orientation** — modified: 1 textbook (thinking_efficiently) + mentor package from lineage, founding directive as question, 14-day survival clock (not 21-day Gen 1 grace).
- **Death Protocol** — 10-step sequence integrated into evaluation_engine._terminate_agent(): freeze → financial cleanup → relationship archival → post-mortem → knowledge preservation → lineage record → dynasty update → memorial → dynasty P&L → Agora announcement.
- **Dashboard API** (`src/web/routes/api_dynasty.py`) — 6 JSON endpoints: dynasties list, dynasty detail, family tree, analytics, memorials list, memorial detail.
- **Models updated:** Dynasty table, Memorial table, 16 Lineage extensions, 7 new Agent columns (dynasty_id, offspring_count, last_reproduction_at, reproduction_cooldown_until, founding_directive, founding_directive_consumed, posthumous_birth).
- **Config:** 12 new variables for reproduction, inheritance, and dynasty thresholds.

### Personality Through Experience (Phase 3E)
- **Behavioral Profile Calculator** (`src/personality/behavioral_profile.py`) — 7 traits computed from behavior, never self-reported: risk_appetite (position sizing/loss tolerance), market_focus (Shannon entropy of market distribution), timing (hour-of-day heatmap), decision_style (reasoning length × confidence variance), collaboration (pipeline outcome-weighted), learning_velocity (evaluation score trend), resilience (loss-to-recovery cycles). Agents NEVER see their own profile. Classification via threshold-based scoring with tier distance drift detection.
- **Temperature Evolution Engine** (`src/personality/temperature_evolution.py`) — agent API temperature drifts ±0.05 per evaluation based on diversity-profitability Pearson correlation. 2-eval momentum requirement (same signal twice before drift). Role-specific bounds (scout 0.3–0.9, operator 0.1–0.4). History recorded on agent for dashboard visualization.
- **Reflection Library Selector** (`src/personality/reflection_library.py`) — targeted study sessions during reflection cycles. System offers relevant Library material when it detects weakness via evaluation scorecard. 5-reflection cooldown per resource. Passive injection (not agent-requested). Falls back to Library archive entries if textbook not found.
- **Dynamic Identity Builder** (`src/personality/identity_builder.py`) — evolving system prompt identity from FACTS, not labels. Architectural constraint: NEVER imports BehavioralProfile, never accepts label fields. Three tiers: new (<30 cycles), established (30-99), veteran (100+). Shows "your last 3 trades hit stop-loss" not "you are reckless." Blocked label word validation.
- **Relationship Manager** (`src/personality/relationship_manager.py`) — Bayesian trust scoring between agents. trust = weighted_positive / weighted_total with prior=0.5 and decay_factor=0.95/day. Updated automatically from pipeline outcomes (position close → plan → opportunity chain) and self-note sentiment analysis. Dead agent relationships archived (not deleted). Trust summary injected into context.
- **Divergence Calculator** (`src/personality/divergence.py`) — cosine distance between behavioral profile score vectors for same-role agent pairs. Low divergence (<0.15) flagged as potential redundancy. Snapshots stored per evaluation for trend tracking.
- **Dashboard API** (`src/web/routes/api_personality.py`) — JSON endpoints: GET /api/personality/{id}/profile, /relationships, /temperature-history, /divergence
- **Integration points:** Context Assembler (dynamic identity + trust relationships + library injection), Evaluation Engine (profile computation + drift detection + temperature evolution + divergence), Action Executor (relationship tracking on position close), Memory Manager (relationship extraction from reflections)
- **Models updated:** 4 new tables (BehavioralProfile, AgentRelationship, DivergenceScore, StudyHistory), 4 new Agent columns (last_temperature_signal, temperature_history, identity_tier, behavioral_profile_id)
- **Config:** 22 new variables for thresholds, bounds, and minimums

### API Cost Optimization (Phase 3.5)
- **Model Router** (`src/agents/model_router.py`) — deterministic routing: Haiku ($1/$5) for routine cycles, Sonnet ($3/$15) for high-stakes. Routes based on role + cycle type + capital-at-risk + alert level. Kill switch: `model_routing_enabled=False`.
- **Prompt Caching** (integrated into `src/agents/claude_client.py`) — `cache_control: {"type": "ephemeral"}` on system prompts. 90% input savings on cache hits. Cache-aware cost calculation (1.25x write, 0.1x read). Kill switch: `prompt_caching_enabled=False`.
- **Adaptive Cycle Frequency** (integrated into `src/agents/cycle_scheduler.py`) — regime-based multipliers: volatile 0.5x (faster), ranging/crab 1.5x (slower), low_volatility 2.0x (slowest). 30-second floor. Kill switch: `adaptive_frequency_enabled=False`.
- **Context Window Diet** (integrated into `src/agents/context_assembler.py`) — Haiku gets 70% token budget. Output length guidance per model. Old Agora messages truncated to 100 chars.
- **Batch Processor** (`src/agents/batch_processor.py`) — foundation for Anthropic Batch API (50% savings). Submit, poll, retrieve pattern. Disabled by default (`batch_enabled=False`), enable in Phase 4.
- **Cost Tracking** (enhanced in `src/risk/accountant.py`) — multi-model pricing, cache token tracking, savings calculation vs all-Sonnet baseline. Dashboard: model distribution, cache hit rate, savings.
- **Dashboard** — Cost Optimization panel on system page: Haiku/Sonnet distribution, avg cost/cycle, savings today/all-time.
- **Config:** 12 new variables for model routing, caching, frequency, context diet, batch.
- **Models:** `model_used` and `model_reason` columns on `agent_cycles` table.

### Thinking Cycle (Phase 3A)
- **OODA Loop Engine** (`src/agents/thinking_cycle.py`) — master orchestrator: Budget → Observe → Orient+Decide → Validate → Act → Record
- **Budget Gate** (`src/agents/budget_gate.py`) — pre-cycle check: NORMAL / SURVIVAL_MODE / SKIP_CYCLE
- **Context Assembler** (`src/agents/context_assembler.py`) — builds agent "mind" per cycle. 4 modes: Normal/Crisis/Hunting/Survival. Token-budgeted with relevance scoring.
- **Output Validator** (`src/agents/output_validator.py`) — JSON parsing, schema validation, action space check, Warden pre-check, sanity checks. One retry for malformed JSON (double tax).
- **Action Executor** (`src/agents/action_executor.py`) — routes validated actions to Agora/DB/Warden. Paper trading placeholder for Operator trades.
- **Cycle Recorder** (`src/agents/cycle_recorder.py`) — writes to PostgreSQL (agent_cycles), Agora, Redis short-term memory, agent stats.
- **Memory Manager** (`src/agents/memory_manager.py`) — three-tier memory: Working (context), Short-term (Redis, 50 cycles), Long-term (PostgreSQL). Reflection promote/demote. Inheritance for offspring.
- **Cycle Scheduler** (`src/agents/cycle_scheduler.py`) — per-role frequency, interrupt triggers, cooldown (60s), Redis priority queue, sequential processing.
- **Role Definitions** (`src/agents/roles.py`) — Scout/Strategist/Critic/Operator with action spaces, temperatures, intervals.
- **Claude API Client** (`src/agents/claude_client.py`) — Anthropic API wrapper with token tracking, cost calculation, retry logic.
- **DB tables:** `agent_cycles`, `agent_long_term_memory`, `agent_reflections` + new `agents` columns

## Dev Environment

- **OS:** Windows 11
- **Shell:** Command Prompt (CMD) — always use CMD commands, never PowerShell
- **Python:** 3.12+ with .venv virtual environment
- **Path:** E:\project syndicate
- **Database:** PostgreSQL (local)
- **Message Bus:** Redis-compatible (Memurai on Windows)
- **Process Manager:** supervisord (production), manual for dev
- **Git Remote:** https://github.com/cryptorhoeck/project-syndicate.git

## Directory Structure

```
E:\project syndicate\
├── CLAUDE.md                   ← You are here
├── CHANGELOG.md                ← Version history (update with every change)
├── CURRENT_STATUS.md           ← Session-by-session progress tracking
├── requirements.txt            ← Python dependencies
├── .env.example                ← Environment variable template
├── .gitignore
├── docs/
│   └── design_doc_v2.docx      ← Authoritative design document
├── src/
│   ├── genesis/                ← Genesis agent (spawner, treasury, evaluator, regime)
│   ├── council/                ← Scout, Strategist, Critic agent templates
│   ├── operators/              ← Unified Operator agent template
│   ├── risk/                   ← Warden, Accountant, Dead Man's Switch
│   ├── agora/                  ← Message bus, channels, persistence, web frontend
│   ├── economy/                ← Internal economy, reputation, prestige system
│   ├── library/                ← Knowledge bootstrap, educational materials
│   ├── dynasty/               ← Dynasties, lineage, reproduction, memorials, analytics
│   ├── personality/            ← Behavioral profiles, temperature, identity, relationships, divergence
│   ├── social/                 ← Social media agent and integrations
│   ├── reports/                ← Report generator, email sender, alert system
│   ├── console/                ← Owner Override Console (FastAPI endpoints)
│   └── common/                 ← Base agent class, shared utilities, lineage tracker
├── config/                     ← System configuration, boot sequence definition
├── data/                       ← Local data, agent archives, Library content
├── backups/                    ← Timestamped backups (pre-change)
├── tests/                      ← Unit and integration tests
└── scripts/                    ← Deployment, backup automation, resurrection protocol
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
- Python 3.12+ with type hints
- Docstrings on all public functions and classes
- Async where appropriate (especially exchange API calls, agent communication)
- Logging via Python's `logging` module, not print statements
- Configuration via environment variables and config files, not hardcoded values

## Key Technical Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Agent isolation | Process-based (supervisord) | Docker overhead too high for 4GB VPS at $500 scale |
| Agent framework | LangGraph | Most flexible for dynamic spawn/kill at runtime |
| AI engine | Claude Sonnet API | Best reasoning-to-cost ratio for 24/7 operation |
| Database | PostgreSQL | Concurrent writes from multiple agents |
| Message bus | Redis (Memurai on Windows) | Low-latency pub/sub for Agora |
| Exchange API | ccxt | Unified access to 100+ exchanges |
| DeFi | web3.py | Standard Ethereum/EVM toolkit |
| Web framework | FastAPI | Async-native for real-time agent data |
| Dashboard (initial) | Static HTML + email | React dashboard deferred until revenue justifies it |
| Blockchain ledger | Solana (Phase 5) | Deferred until social presence needs public verifiability |
| Default model | Haiku 4.5 | 90% of Sonnet quality at 33% cost for routine work |
| Model routing | Deterministic code | No AI needed to decide which AI to use |
| Prompt caching | Automatic mode | Anthropic SDK manages cache breakpoints |
| Batch API | Off by default | Enable in Phase 4 for evaluations/reflections |

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
| 4 | The Arena (full paper trading validation) | Pending |
| 5 | Social Presence + Solana | Pending |
| 6 | Owner Console + Polish | Pending |
| 7 | The Arena (full paper trading validation) | Pending |
| 8 | Go Live (real capital) | Pending |

## Useful Commands

```cmd
REM Activate virtual environment
.venv\Scripts\activate

REM Install dependencies
pip install -r requirements.txt

REM Run tests
python -m pytest tests/ -v

REM Start PostgreSQL (if not running as service)
pg_ctl start -D "C:\Program Files\PostgreSQL\16\data"

REM Check Redis/Memurai
redis-cli ping

REM Git workflow
git add .
git commit -m "Phase 0: [component] - [description]"
git push origin main

REM Create timestamped backup
python scripts\backup.py

REM Start the full system (dev mode)
python scripts\run_all.py

REM Start Genesis only
python scripts\run_genesis.py

REM Start Warden only
python scripts\run_warden.py

REM Run Dead Man's Switch
python src\risk\heartbeat.py
```

## Context for AI Sessions

When starting a new Claude Code session on this project:
1. Read this CLAUDE.md first
2. Read CURRENT_STATUS.md for where we left off
3. Check CHANGELOG.md for recent changes
4. Verify the .venv is activated
5. Run tests to confirm nothing is broken
6. Continue from where CURRENT_STATUS.md indicates

The design document in `docs/` is the authoritative architecture reference. If anything in code contradicts the design doc, the design doc wins unless the change was approved through a SIP or owner override.
