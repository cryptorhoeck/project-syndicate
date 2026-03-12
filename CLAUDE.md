# Project Syndicate — CLAUDE.md

## Project Overview

Project Syndicate is an autonomous, self-evolving multi-agent AI financial ecosystem. Agents are spawned with capital and a mandate to make money. Profitable agents survive, earn reputation, reproduce, and scale. Unprofitable agents are terminated. The system discovers its own strategies through Darwinian natural selection and competitive pressure.

**Design Document:** `docs/Project_Syndicate_v2_Design_Document.docx` — this is the authoritative source for all architecture decisions. When in doubt, reference the design doc.

**GitHub:** https://github.com/cryptorhoeck/project-syndicate

## Current Status

**Phase:** 0 — Foundation
**Focus:** Core infrastructure, database schema, message bus, base agent class, backup system, disaster recovery, Dead Man's Switch
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
| 0 | Foundation (infra, DB, Redis, base agent, backup, recovery) | **ACTIVE** |
| 1 | Genesis + Risk Desk | Pending |
| 2 | The Agora + Library + Internal Economy | Pending |
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
