# Project Syndicate — CLAUDE.md

## Project Overview

Project Syndicate is an autonomous, self-evolving multi-agent AI financial ecosystem. Agents are spawned with capital and a mandate to make money. Profitable agents survive, earn reputation, reproduce, and scale. Unprofitable agents are terminated. The system discovers its own strategies through Darwinian natural selection and competitive pressure.

**Design Document:** `docs/Project_Syndicate_v2_Design_Document.docx` — this is the authoritative source for all architecture decisions. When in doubt, reference the design doc.

**GitHub:** https://github.com/cryptorhoeck/project-syndicate

## Current Status

**Phase:** 2 — COMPLETE (all 4 sub-phases: Agora, Library, Economy, Web Frontend)
**Focus:** Phase 2D Web Frontend complete. Next: Phase 3 (First Generation)
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

## Phase Roadmap

| Phase | Focus | Status |
|-------|-------|--------|
| 0 | Foundation (infra, DB, Redis, base agent, backup, recovery) | **COMPLETE** |
| 1 | Genesis + Risk Desk | **COMPLETE** |
| 2A | The Agora (central nervous system) | **COMPLETE** |
| 2B | The Library (knowledge layer) | **COMPLETE** |
| 2C | Internal Economy (reputation marketplace) | **COMPLETE** |
| 2D | Web Frontend (dashboard) | **COMPLETE** |
| 3 | First Generation (boot sequence, paper trading) | Pending |
| 4 | Natural Selection (evolution loop) | Pending |
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
