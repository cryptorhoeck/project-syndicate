# Current Status — Project Syndicate

## Last Updated: 2026-03-21

## Phase: 3.5 — API Cost Optimization (COMPLETE)

### Completed This Session (Phase 3.5 — API Cost Optimization)
- [x] Model Router — deterministic Haiku/Sonnet routing by role, cycle type, alert level
- [x] Prompt Caching — cache_control on system prompts, cache-aware cost calculation
- [x] Adaptive Cycle Frequency — regime-based multipliers with 30s floor
- [x] Context Window Diet — Haiku budget multiplier, output guidance, Agora truncation
- [x] Batch Processor — foundation for Batch API (disabled by default)
- [x] Cost Tracking — multi-model pricing, savings vs all-Sonnet baseline
- [x] Dashboard — Cost Optimization panel on system page
- [x] Centralized Model Strings — config.model_sonnet across all files
- [x] DB schema — model_used/model_reason on agent_cycles
- [x] Config — 12+ new variables with kill switches
- [x] Tests — 70 new tests, 671 total passing
- [x] Documentation — CLAUDE.md, CHANGELOG.md, .env.example updated

### Previously Completed (Phase 7 — Arena Launch Preparation)
- [x] Boot sequence wired into Genesis, Arena run script, monitoring checklist, clean slate

### Previously Completed (Phase 3F)
- [x] Death protocol, reproduction engine, dynasty system, lineage tracking, memorials

### Previously Completed (Phase 3E)
- [x] Behavioral Profile: 7 traits from behavior, temperature evolution, reflection library, dynamic identity, relationship memory, divergence tracking

### Previously Completed (Phase 3D)
- [x] Natural Selection: role-specific composites, 3-stage evaluation, rejection tracking, post-mortems, prestige milestones, probation, portfolio concentration

### Previously Completed (Phase 3C)
- [x] Paper Trading: PriceCache, FeeSchedule, SlippageModel, PaperTradingService, PositionMonitor, LimitOrderMonitor, EquitySnapshots, SanityChecker

### Previously Completed (Phase 3B)
- [x] Boot Sequence: spawn waves, orientation protocol, pipeline handoffs, health checks, maintenance

### Previously Completed (Phase 3A)
- [x] Thinking Cycle Engine: OODA loop, Budget Gate, Context Assembler, Output Validator, Action Executor, Cycle Recorder, Memory Manager, Cycle Scheduler, Role Definitions, Claude API Client

### Previously Completed (Phase 2D)
- [x] Web Frontend: FastAPI + Jinja2 + HTMX + Tailwind, 5 pages, HTMX auto-refresh

### Previously Completed (Phase 2C)
- [x] Internal Economy: reputation, intel market, settlement, review market, gaming detection

### Previously Completed (Phase 2B)
- [x] LibraryService: textbooks, archives, peer review, mentor system

### Previously Completed (Phase 2A)
- [x] AgoraService: central communication hub, Redis pub/sub, read receipts, rate limiting

### Previously Completed (Phase 1)
- [x] Genesis Agent, Warden, Accountant, Treasury, Regime Detector

### Previously Completed (Phase 0)
- [x] Full project scaffold, PostgreSQL, Redis, base agent, backup, heartbeat

### What's Next — The Arena (Phase 4/7)
- [ ] Get valid Anthropic API key
- [ ] Run `python scripts/run_arena.py` — start the system
- [ ] Watch Genesis auto-trigger boot sequence (5 agents, 3 waves)
- [ ] Verify model routing in cycle logs (Haiku vs Sonnet selection)
- [ ] Monitor cost savings in dashboard Cost Optimization panel
- [ ] Enable Batch API for reflections/evaluations after validation

### Kill Switches (Phase 3.5)
- `MODEL_ROUTING_ENABLED=false` → All cycles use Sonnet (old behavior)
- `PROMPT_CACHING_ENABLED=false` → No cache_control sent (old behavior)
- `ADAPTIVE_FREQUENCY_ENABLED=false` → Fixed intervals (old behavior)

### Important Notes
- **Genesis version bumped to 1.3.0** — boot sequence auto-trigger added
- **Arena launch command**: `python scripts/run_arena.py`
- **Expected savings**: 60-75% reduction in daily API spend
- **Haiku is the default model** — Sonnet only for high-stakes (evaluations, plans, reviews, trades, crises)
- **All optimization is transparent** — every cycle logs model_used and model_reason

### Known Issues
- PostgreSQL binaries not in system PATH (located at C:/ProDesk/pgsql/bin/)
- Memurai binaries not in system PATH (located at C:/Program Files/Memurai/)
- PostgreSQL server must be manually started after system reboot
- Exchange API keys configured (Kraken, paper trading only)
- **Anthropic API key invalid** — needs replacement before Arena launch
- SMTP not yet configured (email sends will be skipped)
- RuntimeWarning in tests from mock Redis pipeline coroutines (cosmetic only)
- DeprecationWarning from Starlette TemplateResponse parameter order (cosmetic only)
- LegacyAPIWarning from Query.get() usage in tests (cosmetic only)
- 2 pre-existing test_library_textbooks failures (unrelated to Phase 3.5)

### Environment Notes
- Python venv: E:\project syndicate\.venv
- PostgreSQL data: C:/ProDesk/pgsql/data
- PostgreSQL binaries: C:/ProDesk/pgsql/bin/
- Memurai: C:/Program Files/Memurai/
- Database: syndicate (PostgreSQL, user: postgres, localhost:5432)
- PG_DUMP_PATH configured in .env: C:/ProDesk/pgsql/bin/pg_dump
- Web frontend: http://localhost:8000 (via `python scripts/run_web.py`)
