# Changelog

All notable changes to Project Syndicate will be documented in this file.

## [Hotfix] - 2026-05-04 - Genesis regime-review (subsystem H) — Critic iteration 3

Addresses four blocking findings from iteration 2 Critic review:

### Finding 1 (HIGH) — Cap correctness, no extra attempts past MAX
`_consume_pending_regime_reviews` is now two passes:
  - PRE-FLIP: SELECT pending rows where `attempt_count >= MAX_ATTEMPTS`,
    flip to 'failed' with `last_error` populated. attempt_count is NOT
    incremented during flip; the row stays at exactly MAX at the time
    of the flip.
  - CONSUME: SELECT pending rows where `attempt_count < MAX_ATTEMPTS`.
    Defense-in-depth — even if a future refactor drops the pre-flip
    pass, the SELECT itself excludes capped rows so they cannot be
    consumed forever.
Tests:
  - `test_regime_review_exact_attempt_count_at_failure_cap` —
    attempt_count == MAX (NOT MAX+1) at flip time.
  - `test_regime_review_failed_row_excluded_from_select` — row at
    `status='failed'` with `attempt_count=MAX` is not selected, not
    incremented.

### Finding 2 (HIGH) — Per-row last_error attribution
Per-row try/except moved INSIDE the consume loop. New helper
`_process_pending_regime_review_row(row)` is the test seam — a per-row
exception stamps `last_error` on THAT row only; other rows in the
same batch keep `last_error = NULL`. The cycle-level
`_record_regime_review_failure` batch-stamp helper has been removed
entirely; cycle-level failures surface only via `cycle_report["error"]`
and the structured `genesis_cycle_error` log. The existing
`test_regime_review_marks_failed_after_three_attempts` was updated to
assert `last_error is None` after the cycle-level crashes (new
contract); the cap-flip path still populates a generic
exceeded-max-attempts message.
Test:
  - `test_last_error_attaches_to_offending_row_only` — batch of 5
    rows, row #3 raises during processing, last_error stamps on
    row #3 only; rows 1, 2, 4, 5 stay clean and get marked
    'reviewed'.

### Finding 3 (MEDIUM) — `BACKFILL_WINDOW_MINUTES` rationale
Comment block above the constant in
`alembic/versions/phase_10_wire_006_regime_review_status.py`
documents the derivation: matches `DEFAULT_AUTO_EXPIRE_MINUTES = 30`
in `src/wire/integration/operator_halt.py`. Sev-5 events older than
the operator-halt TTL are presumed stale; if the underlying condition
is still active the upstream producer will re-emit. If the halt TTL
ever changes, the migration constant must move in lockstep.

### Finding 4 (MEDIUM) — Consecutive-only escalation contract documented
Comment block above `REGIME_REVIEW_QUERY_FAILURE_ALERT_THRESHOLD`
documents the consecutive-only contract: counter resets on first
success, only K consecutive failures escalate. Intermittent patterns
(fail, success, fail, fail) do NOT escalate by design.
DEFERRED_ITEMS_TRACKER.md gets an explicit entry for the
cumulative-window detector as a future observability improvement.
Test:
  - `test_escalation_does_not_fire_on_intermittent_pattern` —
    fail/success/fail/fail/success/fail pattern → 4 failures across
    6 cycles, never 3 in a row → no escalation log, no system-alert
    post.

## [Hotfix] - 2026-05-04 - Genesis regime-review (subsystem H) — Critic iteration 2

Addresses five blocking findings from the iteration 1 Critic review of
the subsystem H wiring. NOT-APPROVED → APPROVED-pending-merge once
War Room signs off on this submission.

### Schema (extends migration phase_10_wire_006)
- `wire_events.attempt_count` INTEGER NOT NULL DEFAULT 0 — incremented
  before each consumption attempt; cap at 3 flips the row to 'failed'
  (Finding 1, poison-pill guard).
- `wire_events.last_error` TEXT NULL — populated on cycle exception
  for all rows consumed in the failing cycle; preserved when the cap
  fires.
- 'failed' added to the `regime_review_status` check constraint
  (`'pending'|'reviewed'|'skipped'|'failed'`).
- Backfill cutoff (Finding 3): only sev-5 rows newer than 30 minutes
  flip to 'pending'. Older sev-5 rows stay 'skipped' to prevent
  stale-event replay corrupting regime detection on first deploy.
  30-minute window matches the operator-halt auto-expiry TTL.
  Constant `BACKFILL_WINDOW_MINUTES = 30` exported by the migration.

### Consumer (`src/genesis/genesis.py`)
- `REGIME_REVIEW_MAX_ATTEMPTS = 3` and
  `REGIME_REVIEW_QUERY_FAILURE_ALERT_THRESHOLD = 3` module constants.
- `_consume_pending_regime_reviews`:
    - Per-row poison-pill check: rows at the cap flip to 'failed'
      with `last_error` and are NOT re-consumed (Finding 1).
    - Increment `attempt_count` BEFORE consuming, commit per-cycle so
      the increment survives a mid-cycle crash.
- `_mark_regime_reviews_reviewed`: WHERE filter is now `id IN
  (consumed_ids)` ONLY — drops the redundant `status='pending'`
  filter that was behaviorally wrong if a concurrent process flipped
  status (Finding 2).
- `_record_regime_review_failure`: new helper. run_cycle's top-level
  except writes the exception text to `last_error` for all consumed
  rows so the eventual poison-pill flip carries diagnostic context.
- Step 2c gains consumption-query failure escalation (Finding 5):
  per-instance `_regime_review_query_failure_count` increments on
  SELECT failure, escalates to CRITICAL + `system-alerts` Agora post
  after 3 consecutive cycles, resets to 0 on first success.

### Tests (6 new, total 15 in
`tests/test_genesis_regime_review_consumption.py`)
- `test_regime_review_attempt_count_increments_on_consumption` (F1)
- `test_regime_review_marks_failed_after_three_attempts` (F1)
- `test_regime_review_failed_rows_excluded_from_consumption_query` (F1)
- `test_mid_cycle_inserts_remain_pending` (F2)
- `test_backfill_marks_old_sev_5_as_skipped` (F3)
- `test_consumption_query_failure_escalates_after_three_cycles` (F5)

### Postgres e2e (Finding 4)
- New diagnostic injector `scripts/_postgres_e2e_inject.py`. Run
  against the real dev Postgres (started via
  `C:/ProDesk/pgsql/bin/pg_ctl.exe`). Captures pre/post counts,
  applies migration, injects synthetic sev-5, runs Genesis
  consumption, asserts row marked 'reviewed' with attempt_count=1,
  cleans up so dev DB returns to its pre-injection state. See commit
  message for full output.

## [Hotfix] - 2026-05-04 - Genesis regime-review consumption (subsystem H)

Closes WIRING_AUDIT_REPORT.md subsystem H. Severity-5 wire events were
firing but no listener invoked Genesis regime review in production —
the digester had a hook reference but no consumer was ever wired. Per
War Room iteration 1 directive on hotfix/genesis-regime-review-hook
(Option C): Postgres-as-queue, no Redis pub/sub, no new GenesisAgent
public method.

### Verification stop before build (no code yet)
The original directive locked in `Genesis.review_regime()` as "the
existing function. Do NOT modify its internals." Verified via
`git log --all -S "review_regime"` that the method had never been
implemented — only referenced in docstrings/kickoff docs. Reported
back to War Room rather than build wiring around a non-existent
function. War Room confirmed Option C (no new method, queue
consumption inline in `run_cycle`).

### Added
- `alembic/versions/phase_10_wire_006_regime_review_status.py`:
  adds `wire_events.regime_review_status` VARCHAR(16) NOT NULL DEFAULT
  'skipped' with check constraint `IN ('pending','reviewed','skipped')`.
  Backfills existing severity-5 rows to 'pending' for catch-up. Adds
  `ix_wire_events_regime_review_status` on (regime_review_status,
  severity) for the consumption query.
- `WireEvent.regime_review_status` ORM column + matching CheckConstraint
  / Index in `src/wire/models.py`.
- `GenesisAgent._consume_pending_regime_reviews()` and
  `_mark_regime_reviews_reviewed(event_ids)` private helpers.
  Bounded at `REGIME_REVIEW_BATCH_LIMIT = 50` per cycle.
- New step 2c (consume) and step 12 (mark) inserted into
  `GenesisAgent.run_cycle()`. At-least-once: an exception in steps
  3–11 leaves rows 'pending' for the next cycle (mark-reviewed UPDATE
  is the last statement in the try block).
- `tests/test_genesis_regime_review_consumption.py` — 9 tests
  including `test_severity_5_event_consumed_by_genesis_in_production_path`
  which goes end-to-end through the real HaikuDigester and the real
  GenesisAgent constructor (production code paths).
- `scripts/validate_regime_review_consumption_e2e.py` — single-phase
  e2e validation runner (Postgres-as-queue has well-understood
  semantics; no Memurai-down dance needed).

### Changed
- `src/wire/digest/haiku_digester.py`: sev-5 events get
  `regime_review_status='pending'` at INSERT time. Duplicate sev-5
  events stay 'skipped' (the original was already reviewed). Imports
  `SEVERITY_CRITICAL` from `src.wire.constants`.

### Constraints honored
- `detect_regime()` logic NOT modified — it runs after the new
  consumption step in the same position it always has.
- `run_cycle` flow preserved — one step added at top (2c), one at end
  (12). No restructuring.
- No new public methods on `GenesisAgent`.
- No Redis pub/sub.

## [Hotfix] - 2026-05-03 - Operator halt persistence — Critic iteration-5 fixes

Addresses six blocking findings from the iteration-5 Critic review of
the Redis-backed persistence layer. NOT-APPROVED -> APPROVED-pending-merge
contingent on War Room sign-off after this submission.

### Added
- `OperatorHaltPublishError` (`src/wire/integration/operator_halt.py`).
  Producer side now raises on Redis-write failure instead of silently
  appending to in-process `_ACTIVE` (which is invisible cross-process).
  CRITICAL log + Agora system-alerts mirror + raise — all three. Mirrors
  consumer-side fail-closed contract.
- `set_alert_publisher(callable)` injection point. Wire scheduler bootstrap
  registers a sync Redis-PUBLISH publisher that posts producer-side
  failures to `agora:system-alerts` cross-process.
- `_missing_halt_record_fields(record)` (`src/trading/execution_service.py`).
  When `is_halted` returns `(True, halt_record)` and the record is missing
  any of the canonical fields (`trigger_event_id`, `event_type`, `severity`,
  `issued_at`, `expires_at`), the consumer treats this as a failure mode
  (Critic Finding 4): `_halt_state_unknown=True`, alert + reject. Latch
  auto-clears on the next valid record.
- Post-construction verification (Critic Finding 3) in
  `scripts/run_agents.py:main` and `src/wire/cli.py:_initialize_producer_halt_store`.
  Both bootstraps re-read `get_halt_store()` after `set_halt_store(store)` and
  `sys.exit(2)` if the assignment was lost or the registered instance differs.
- `scripts/validate_halt_persistence_e2e.py` — production-runtime validation
  runner (Critic Finding 6). Three scenarios against real Memurai with
  production factories: (1) healthy boot + cross-subprocess publish,
  (2) Memurai down mid-run -> consumer fail-closed, (3) recovery + auto-clear.
  Run before merge; output captured in commit message.
- 9 new tests in `test_operator_halt_consumer_wiring.py`:
  - `test_producer_halt_publish_fails_closed_when_redis_raises` (F1)
  - `test_producer_halt_publish_fails_closed_even_without_alert_publisher` (F1)
  - `test_producer_halt_publish_succeeds_with_redis_writes_to_redis_only` (F1)
  - `test_post_construction_get_halt_store_reflects_set_halt_store` (F3)
  - `test_run_agents_bootstrap_fails_fast_on_assignment_mismatch` (F3)
  - `test_wire_cli_bootstrap_fails_fast_on_assignment_mismatch` (F3)
  - `test_check_operator_halt_fails_closed_on_malformed_record` (F4)
  - `test_malformed_record_unknown_state_auto_clears_on_valid_record` (F4)
  - `test_well_formed_halt_record_uses_canonical_fields_in_reason` (F4)

### Changed
- `test_halt_visible_across_process_boundary` (Critic Finding 2). Both
  subprocesses now construct via the production factories
  (`src.wire.cli._initialize_producer_halt_store` for the producer,
  `scripts.run_agents.build_halt_store` for the consumer) instead of
  hand-rolling RedisHaltStore inside the subprocess. If a future
  refactor moves wiring out of those factories, the test fails. Both
  factories accept an optional `key_prefix` override for test isolation
  while production callers stay unchanged.
- Halt store module docstring (Critic Finding 5) explicitly states the
  expiry mechanism: native Redis TTL via `SET ... EX ttl_seconds`. No
  filter-on-read, no background sweeper.
- Producer-side write path: when `_halt_store` is set and the Redis
  write fails, no fallback to `_ACTIVE` (re-creates the cross-process
  gap). When `_halt_store is None` (test fixtures, pre-bootstrap),
  `_ACTIVE` write preserved.
- `haiku_digester._dispatch_post_digest_hooks` catches
  `OperatorHaltPublishError` and logs CRITICAL — does NOT dead-letter
  the raw item over a Redis-write blip (the failure has already been
  broadcast cross-process via the alert publisher).

### Acknowledged
- LOW Finding 7 (wider-than-intended halt scope when exchange=None) —
  accepted as design trade-off. Source-side exchange classification
  remains the existing deferred entry that will narrow scope when
  implemented.

## [Hotfix] - 2026-05-01 - Operator halt cross-process persistence (Redis-backed)

### Added
- `src/wire/integration/halt_store.py` — `RedisHaltStore` class. Cross-process halt registry backed by Memurai. Key pattern `wire:halt:{coin}:{exchange}` with native Redis TTL. Wildcard exchange `*` matches every venue.
- `make_halt_record()` helper centralizes the canonical halt-record dict shape so producer and consumer don't drift.
- `scripts/run_agents.py:build_halt_store` — production wiring helper (mirrors `build_warden` / `build_trading_service`). `sys.exit(2)` on construction failure or None redis client.
- `src/wire/cli.py:_initialize_producer_halt_store` — producer-side wiring at scheduler startup.
- 9 new tests in `tests/test_operator_halt_consumer_wiring.py`, including the load-bearing **`test_halt_visible_across_process_boundary`** that spawns two real `subprocess.run` Python processes against a shared Memurai with unique key prefixes and asserts cross-process halt visibility.

### Changed
- `src/wire/integration/operator_halt.py` now write-throughs to `RedisHaltStore` as the primary persistence path. Module-level `_ACTIVE` becomes defense-in-depth: populated only when Redis writes fail, consulted by the consumer only when `_halt_state_unknown` is set.
- `src/trading/execution_service.py:PaperTradingService` accepts `halt_store=` (renamed from `halt_checker`). `_check_operator_halt` now calls `halt_store.is_halted(coin, exchange)`. Fail-closed-to-halt-everything when Redis raises or returns malformed data; latch auto-clears on next successful call (anti-DMS pattern, mirrors Warden `_safety_state_unknown`).
- `get_trading_service` factory threads `halt_store` through.

### Resolved
- DEFERRED_ITEMS_TRACKER.md "Wire halt cross-process visibility (PRODUCTION GAP)" — closes Critic Finding 3 from hotfix iteration 4. Producer (`wire_scheduler` subprocess) and consumer (`agents` subprocess) now share state via the same Memurai instance.

## [Phase 10] - 2026-05-01 - The Wire (External Intelligence Pipeline)

### Added
- New `src/wire/` package: 8 sources, ingestor scheduler, Haiku digestion, dedup, publish, agent integration.
- 6 new database tables: `wire_sources`, `wire_raw_items`, `wire_events`, `wire_source_health`, `wire_query_log`, `wire_treasury_ledger`.
- 3 Alembic migrations (`phase_10_wire_001..003`): schema, seed catalog, Tier 2 enable.
- Sources: Kraken announcements (RSS), CryptoPanic free, DefiLlama TVL deltas, Etherscan large transfers, Kraken funding rates (ccxt), FRED macro series, TradingEconomics calendar, Fear & Greed index.
- Ticker (push) and Archive (pull) APIs for agent consumption. Archive queries are token-costed against agent thinking budget.
- Scout `recent_signals` context block injected by `ContextAssembler._build_wire_recent_signals`.
- Strategist + Critic Archive helpers (`build_strategist_archive_helper`, `build_critic_archive_helper`). Critics get 3 free queries per critique cycle.
- Severity-5 deterministic-only path: Operator halt for `exchange_outage`/`withdrawal_halt`/`chain_halt`, Genesis regime review hook for any sev-5.
- Volume floor (6h, 3 events) and source diversity (24h, 70%) breach monitor wired into the scheduler tick on a 30-minute cadence.
- Wire dashboard widget (`/api/wire/{ticker,health,treasury,stats}` + `templates/fragments/wire_ticker.html`).
- 147 new Wire tests across unit, integration, end-to-end. Includes the **silent-failure callback test** (`test_silent_failure.py`) that asserts a 6h empty feed raises `wire.volume_floor_breach`.

### Changed
- Scout OODA context now includes `=== THE WIRE — RECENT SIGNALS ===` block from the Wire ticker.
- `alembic/env.py` now imports `src.wire.models` so Wire tables register on `Base.metadata`.

### Deferred
- Sentiment data sources (Twitter/Reddit) -> Phase 11.
- Paid premium sources (CryptoPanic Pro, Messari, Glassnode) -> Phase 10.5.
- Live integration validation (1h scheduler run, network kill, real Haiku) -> awaits valid `ANTHROPIC_API_KEY` + free `FRED_API_KEY`/`ETHERSCAN_API_KEY`.

## [4.0.0] - 2026-04-13

### Phase 9A — SIP Voting & Colony Maturity

Full democratic governance layer for agent-proposed system changes.

#### Colony Maturity Model
- 4 stages: NASCENT -> DEVELOPING -> ESTABLISHED -> MATURE
- Each stage drives governance speed (4hr-24hr debates), strictness, and Genesis posture
- Maturity can only advance, never regress
- Transitions posted to Agora system-alerts

#### Parameter Registry
- 24 seed parameters across 5 categories (evaluation, lifecycle, economics, timing, risk/governance)
- 3 tiers: Open (standard vote), Structural (75% supermajority), Forbidden (immutable)
- Runtime parameter reads via `get_param()` helper with config fallback
- Cumulative drift tracking (softer vs harder changes)

#### SIP Lifecycle
- Full state machine: DEBATE -> VOTING -> TALLIED -> GENESIS_REVIEW -> OWNER_REVIEW -> IMPLEMENTING -> IMPLEMENTED
- Prestige-weighted voting (unproven=0.5 through grandmaster=3.0)
- 60% pass threshold (Tier 1), 75% supermajority (Tier 2)
- Abstains excluded from denominator
- Evaluation weight sum-to-1.0 validation at implementation time

#### Agent Actions
- Updated `propose_sip`: target_parameter, proposed_value, evidence fields
- New `vote_on_sip`: weighted, deduplicated, Agora-posted
- New `debate_sip`: position + argument, costs thinking tax
- New `cosponsor_sip`: required for Tier 2 in mature colonies

#### Genesis Integration
- Maturity-adaptive ratification posture (permissive -> skeptical)
- Public vetoes with reasoning, tracked via genesis_veto_used flag
- Governance seeding for nascent colonies (deferred to runtime)
- SIP lifecycle advancement in Genesis main cycle

#### Dashboard & API
- `GET /api/governance/sips` — active SIPs, maturity, drift summary
- `GET /api/governance/parameters` — full registry with tiers
- Governance context injected into agent OODA cycles

#### Database
- 5 new tables: colony_maturity, parameter_registry, parameter_change_log, sip_votes, sip_debates
- 16 new columns on system_improvement_proposals
- Alembic migration e5f6a7b8c9d0

#### Tests
- 45 new tests, 804 total passing

## [3.1.0] - 2026-04-13

### Directory Cleanup & Reorganization

Full codebase audit and directory reorganization after Phase 9 completion.

#### Audit Results
- 576 project files, 91 source modules, 73 test files, 759 tests
- 0 broken imports, 0 orphaned modules, 0 duplicate functionality
- 6 unused packages in requirements.txt, 5 missing .env.example vars
- Alembic migration fork (2 branches from same parent) identified and fixed

#### Directory Changes
- **Moved** 11 PHASE_*_KICKOFF.md files → `docs/kickoffs/`
- **Moved** 6 historical docs → `docs/archive/` (original chat, Phase 2 scorecard, React mockup, CC kickoff, preflight checklist, textbook prompt)
- **Deleted** `syndicateapi.png` (orphaned, unreferenced image)
- **Cleaned** 19 `__pycache__` directories outside .venv

#### Configuration Cleanup
- **requirements.txt** — removed 6 unused packages: langgraph, langchain-anthropic, langchain-core, web3, ta, apscheduler
- **.gitignore** — added `.claude/` to prevent worktree clutter from being tracked
- **Alembic** — linearized migration chain: c4a9d7f1b2e8 (CAD columns) → c1d2e3f4a5b6 (last_words). No more fork.

#### Documentation Overhaul
- **CLAUDE.md** — comprehensive rewrite: corrected Python version (3.13.7), replaced LangGraph with actual Custom OODA loop framework, updated all 48 DB tables, documented all phases through 9, corrected Key Technical Decisions table, updated directory structure, added all scripts
- **DEFERRED_ITEMS_TRACKER.md** — updated to 2026-04-13, added CLEANUP ITEMS section (5 missing .env vars, SQLAlchemy warnings, datetime deprecation, sandbox test failures)
- **CURRENT_STATUS.md** — updated to reflect cleanup session

## [3.0.1] - 2026-03-23

### Database Schema Fix — Missing CAD Currency Columns

Added Alembic migration `c4a9d7f1b2e8` for 6 columns that were added to
SQLAlchemy models during the currency layer work but never migrated to the
database:

- `system_state.treasury_currency` — VARCHAR(10), default 'CAD'
- `agents.total_true_pnl_cad` — FLOAT, default 0.0
- `evaluations.pnl_gross_cad` — FLOAT, default 0.0
- `evaluations.pnl_net_cad` — FLOAT, default 0.0
- `evaluations.api_cost_cad` — FLOAT, default 0.0
- `daily_reports.usdt_cad_rate` — FLOAT, nullable

Fixes: `clean_slate.py` crash on `treasury_currency` column not found.

## [3.0.0] - 2026-03-24

### Production Readiness — Hardening, Integration Tests, Stress Tests

Three-tier production readiness pass.

#### Tier 1: Hardening Verification & Fixes

14-point audit of production hardening fixes. Report card:

| Fix | Description | Status |
|-----|-------------|--------|
| C1 | Atomic budget increments | Already applied |
| D1 | Death protocol savepoint | Already applied |
| F1 | Context assembler crash isolation | Already applied (_safe_build wrapper) |
| F2 | Thinking cycle crash resilience | Already applied (degraded fallback) |
| F3 | File handle cleanup | Already applied |
| F5 | Engine disposal | **Fixed** — added to run_trading, genesis_runner, warden_runner |
| A1 | Unbudgeted API calls | **Fixed** — 5 API calls now tracked (evaluation, last words, post-mortem, SIP review, reproduction) |
| A5 | Async sleep | Already applied |
| G5 | Log rotation | Partial (run_arena only; others use structlog console) |
| S5 | Agora prompt sanitization | Already applied (delimiters, length cap, name sanitization) |
| R3 | Redis connection resilience | **Fixed** — added socket_timeout/connect_timeout/retry to 6 connections |
| G2 | Startup config validation | Already applied |
| S6 | SSE connection limits | **Fixed** — added per-IP limit (MAX_PER_IP=5) alongside existing MAX_TOTAL=50 |
| A6 | Sandbox daily cost cap | Already applied ($0.50/agent/day) |

#### Tier 2: Integration Test Harness

9 end-to-end pipeline tests in `tests/test_integration.py`:
1. Scout-to-Trade Pipeline (opportunity → plan → approval → position)
2. Evaluation and Death Protocol (termination, memorial creation)
3. Cold Start Boot Sequence (5 agents spawned from empty state)
4. Budget Gate Survival Mode (near-limit triggers survival)
5. Budget Gate Skip (exhausted budget blocks cycle)
6. Black Swan Protocol (16% drawdown triggers yellow alert)
7. Context Assembler Resilience (corrupted data doesn't crash)
8. Library Textbook Pipeline (weak metric → textbook injection)
9. Reproduction and Genome Inheritance (parent → offspring with memory discount)

#### Tier 3: Stress Tests + Smoke Test

7 stress tests in `tests/test_stress.py` (marked `@pytest.mark.stress`):
1. 100-Cycle Marathon (500 cycles, budget accuracy verified)
2. Concurrent Budget Atomicity (50 concurrent increments)
3. Rapid Death/Respawn (15 deaths, 15 births, integrity verified)
4. Database Disconnect Recovery (priority section fails → others survive)
5. Redis Disconnect Recovery (cycle continues in DB-only mode)
6. Log Rotation Under Load (2000 entries, rotation verified)
7. Clean Slate Verification (all tables emptied, Genesis survives)

Smoke test script (`scripts/smoke_test.py`):
- 7 checks: PostgreSQL, Redis, Anthropic, Kraken, Config, Logs, Library
- Output: GREEN / YELLOW / RED
- Accessible from CLI menu as option [S]

#### CLAUDE.md Updated
- Phase roadmap reflects reality (Phase 9: Production Readiness Testing)
- Directory structure matches actual codebase
- Last Updated: 2026-03-24

### Test Results
- 759 tests passing (743 unit + 9 integration + 7 stress)
- 0 failures

## [2.7.0] - 2026-03-23

### Production Readiness Audit + All Warnings Fixed

Full 6-point codebase audit followed by fixes for all 7 identified warnings.

#### Audit Fixes (from initial scan)
- **BTC dominance placeholder** (`exchange_service.py`) — removed hardcoded 50.0%, set to 0.0 (informational only, not used in regime classification)
- **Mock ticker live-mode guard** (`market_data.py`) — in live mode, refuses to serve mock data when exchanges unreachable
- **DB session leak** (`web/dependencies.py`) — `get_db()` fixed to use `yield` + `session.close()`

#### Warning 1: Email service wired into Genesis
- Genesis `__init__` now accepts `email_service` parameter
- Daily report method calls `email_service.send_daily_report()` after DB save and Agora post
- No-op when SMTP credentials are not configured (graceful degradation)

#### Warning 2: 5 unwired modules now connected
- **tool_tracker** — wired into `action_executor.py` for both `execute_analysis` and `run_tool` actions. Records tool usage in Redis for outcome correlation.
- **diversity monitor** — wired into Genesis post-evaluation. Checks genome convergence and posts Agora alert when diversity index drops below threshold (0.3).
- **equity snapshots** — wired into Genesis cycle (step 10b). Takes equity snapshots for all active agents every cycle.
- **sanity checker** — wired into Genesis cycle (step 10b). Runs cash balance, equity reconciliation, orphaned position, and stale reservation checks.
- **email service** — wired into Genesis (see Warning 1) and heartbeat (see Warning 4).

#### Warning 3: 4 dead code files removed
- `src/agents/opportunities.py` — removed (functionality in action_executor)
- `src/agents/plans.py` — removed (functionality in action_executor)
- `src/common/market_data.py` — removed (never used in production)
- `src/genesis/health_check.py` — removed (superseded by evaluation engine)
- 4 corresponding test files removed (56 tests that tested dead code)

#### Warning 4: Heartbeat escalation now sends email
- `_escalate()` in `heartbeat.py` now imports EmailService and sends emergency email on critical health check failures
- Graceful no-op when SMTP is not configured

#### Warning 5: rank_delta now tracked on dashboard
- New `_calc_rank_delta()` helper in `pages.py` computes rank change from last evaluation
- Positive = improved (moved up), negative = declined

#### Warning 6: Sandbox cost cap logs on fail-open
- Bare `pass` replaced with `logger.warning()` when Redis-based budget check fails

#### Warning 7: CLAUDE.md directory structure updated
- Removed non-existent `src/council/`, `src/operators/`, `src/social/`, `src/console/`
- Added real directories: `src/agents/`, `src/trading/`, `src/sandbox/`, `src/genome/`, `src/web/`

### Test Results
- 743 tests passing (799 - 56 removed dead tests, 0 failures)

## [2.6.0] - 2026-03-23

### Fixed — Library Textbook Pipeline

Diagnosed and fixed every broken link in the textbook injection chain. Agents were not receiving Library content during reflection cycles due to three compounding bugs.

#### Bugs Found & Fixed

1. **Asyncio deadlock killed reflection library injection** — `_build_reflection_library_content()` in context_assembler.py checked `if loop.is_running(): return ""`. In production (everything async), the loop is ALWAYS running, so this method always returned empty. Fix: made `select_for_reflection()` synchronous (it only does DB queries and file I/O) and removed the broken asyncio wrapper.

2. **5 of 8 summary files missing** — `data/library/summaries/` had only 3 files (thinking_efficiently, market_mechanics, risk_management). The reflection library selector referenced 5 others that didn't exist. Fix: created all 5 missing summaries (strategy_categories, crypto_fundamentals, technical_analysis, defi_protocols, exchange_apis) — concise ~15-line summaries matching the existing style.

3. **WEAKNESS_TO_RESOURCE filenames mismatched** — the mapping used names like `05_technical_analysis_summary.md` (numbered prefix + `_summary` suffix) but no files matched that pattern. Fix: aligned filenames to match the actual summary directory convention (e.g., `technical_analysis.md`). Updated fallback logic to search textbooks by keyword instead of exact name.

4. **CLAUDE.md PLACEHOLDER marker** — line 62 said textbooks were "(PLACEHOLDER — content pending review)" despite all 8 having real content since Phase 2B. Fix: updated to reflect actual state.

#### Verification
- All 8 textbooks in `data/library/textbooks/` confirmed AVAILABLE
- All 8 summaries in `data/library/summaries/` confirmed present
- Orientation loads 3 summaries per role (2 for critic) — all load successfully
- Reflection library maps 12 role/metric combinations — all resolve to content
- Simulated Scout orientation: 4425 chars of training material injected
- Simulated Scout reflection with low signal_quality: technical_analysis.md correctly offered

### Changed
- `src/personality/reflection_library.py` v1.1.0 → v1.2.0 — sync `select_for_reflection()`, fixed filenames, improved fallback
- `src/agents/context_assembler.py` v1.3.0 → v1.4.0 — removed broken asyncio wrapper
- `tests/test_reflection_library.py` — removed async from test methods
- `CLAUDE.md` — removed PLACEHOLDER marker for textbooks

### Added
- `data/library/summaries/strategy_categories.md` — NEW
- `data/library/summaries/crypto_fundamentals.md` — NEW
- `data/library/summaries/technical_analysis.md` — NEW
- `data/library/summaries/defi_protocols.md` — NEW
- `data/library/summaries/exchange_apis.md` — NEW

## [2.5.0] - 2026-03-23

### Added — CAD Accounting Conversion

Two-layer financial system: agents trade in USDT, owner sees everything in CAD.

#### Currency Service (`src/common/currency_service.py`)
- **CurrencyService** — fetches live USDT/CAD rate from Kraken, Redis cache (5min TTL), fallback rates
- Methods: `get_usdt_cad_rate()`, `usdt_to_cad()`, `cad_to_usdt()`, `usd_to_cad()`, `get_usd_cad_rate()`
- Manual override config for testing (`usdt_cad_manual_override`)
- Local in-memory cache + Redis cache for hot-path performance

#### Treasury in CAD
- Starting treasury: C$500 (configurable via `STARTING_TREASURY`)
- `system_state.total_treasury` and `peak_treasury` now denominated in CAD
- `treasury_currency` column added to SystemState
- Capital allocation: treasury deducts CAD, converts to USDT for agent
- Capital reclamation: agent USDT → converts to CAD → adds to treasury
- `get_treasury_balance()` returns CAD values with USDT/CAD rate

#### P&L Dual-Tracking
- Evaluation model: added `pnl_gross_cad`, `pnl_net_cad`, `api_cost_cad` columns
- Agent model: added `total_true_pnl_cad` column
- DailyReport model: added `usdt_cad_rate` column
- Accountant: `calculate_agent_pnl()` returns both USDT and CAD values
- API costs (USD) converted to CAD via `usd_to_cad()`
- System summary includes `usdt_cad_rate`, `usd_cad_rate`, CAD-converted API spend

#### Dashboard & Reports — All C$
- All 6 dashboard templates updated: `$` → `C$`
- Top bar, nav, system page, agent detail, agent cards, system status
- API route dollar formatting updated (`api_system.py`)
- Genesis cycle log and daily report use `C$` prefix
- Daily report includes USDT/CAD rate in header
- `clean_slate.py` and `syndicate_services.py` use `config.starting_treasury`

#### Config
- 7 new variables: `HOME_CURRENCY`, `STARTING_TREASURY`, `CURRENCY_CACHE_TTL_SECONDS`, `USD_CAD_FALLBACK_RATE`, `USDT_CAD_FALLBACK_RATE`, `USDT_CAD_MANUAL_OVERRIDE`, `USD_CAD_MANUAL_OVERRIDE`

### Added — Kraken Pairs Expansion

Queried Kraken live: 14 of top 20 crypto by market cap confirmed available as /USDT pairs.

#### Confirmed Pairs (all tested with ticker + OHLCV)
BTC, ETH, BNB, XRP, SOL, ADA, DOGE, AVAX, DOT, LINK, SHIB, TON, LTC, BCH

#### Not Available on Kraken
TRX, MATIC, UNI, NEAR, APT, ICP

#### Scout Watchlist Updates
- Default scout watchlist: 14 confirmed pairs (was 10)
- Per-scout splits: `SCOUT_WATCHLISTS` dict in orientation.py
  - Scout-Alpha: BTC/ETH/SOL + XRP/DOGE/ADA/LINK/LTC (8 pairs)
  - Scout-Beta: BTC/ETH/SOL + BNB/AVAX/DOT/SHIB/TON/BCH (9 pairs)
  - 3-pair overlap (BTC/ETH/SOL) ensures no blind spots on majors
- Strategist default watchlist expanded to include SOL
- Watchlist extraction now accepts agent name for per-scout routing

#### Tests
- 17 new tests for CurrencyService + CAD integration
- Total: 799 tests passing

### Changed
- `src/common/currency_service.py` v1.0.0 — NEW
- `src/common/config.py` v1.4.0 → v1.5.0 — CAD config variables
- `src/common/models.py` — CAD columns on Agent, Evaluation, SystemState, DailyReport
- `src/genesis/treasury.py` v0.2.0 → v0.3.0 — CAD treasury, CurrencyService integration
- `src/genesis/genesis.py` — C$ formatting in cycle logs, daily reports
- `src/genesis/boot_sequence.py` — CAD→USDT conversion during spawn
- `src/risk/accountant.py` v1.1.0 → v1.2.0 — dual USDT/CAD P&L, CurrencyService
- `src/agents/orientation.py` v1.5.0 → v1.6.0 — 14 confirmed Kraken pairs, per-scout splits
- `scripts/clean_slate.py` — uses config.starting_treasury
- `scripts/syndicate_services.py` — uses config.starting_treasury
- 6 web templates — $ → C$
- `src/web/routes/api_system.py` — C$ formatting
- `.env.example` — CAD config section
- `tests/conftest.py` — MockCurrencyService fixtures

## [2.4.0] - 2026-03-23

### Fixed — Scout Starvation Problem

The pipeline death spiral (no Scout opps → no plans → no trades → everyone dies) was caused by Scouts choosing `go_idle` indefinitely. Four fixes applied:

- **Expanded watchlists** — default Scout watchlist from 3 pairs to 10 (added XRP, DOGE, ADA, AVAX, LINK, DOT, MATIC) for broader market coverage
- **Scout role rewrite** — replaced "A bad Scout wastes everyone's time with noise" (which discouraged output) with "silence is the worst signal — cast a wide net"
- **Discovery phase directive** — new Scouts (< 50 cycles) get explicit instructions to broadcast aggressively. Confidence 4-5 is enough. Downstream agents filter.
- **Idle streak pressure** — after 3+ consecutive idle cycles, Scouts get escalating warnings that continued silence = termination
- **go_idle warning** — action description now warns that consecutive idle cycles count against evaluation
- **3 new config variables** — `scout_min_confidence_threshold` (5), `scout_discovery_phase_cycles` (50), `scout_max_consecutive_idle` (3)

### Changed
- `src/agents/roles.py` v1.1.0 — Scout description rewrite, go_idle warning
- `src/agents/context_assembler.py` v1.3.0 — `_build_scout_directive()` method (discovery phase + idle streak)
- `src/agents/orientation.py` v1.5.0 — expanded default watchlists
- `src/common/config.py` v1.3.0 — 3 new Scout pipeline config variables

## [2.3.0] - 2026-03-22

### Added — Phase 8C: Code Sandbox & Strategy Genome

#### Tier 1 — Code Sandbox
- **Sandbox Security** — static analysis blocklist (20+ blocked patterns) + RestrictedPython compilation. Allowed: math, numpy, pandas, statistics, collections, datetime, json, re
- **Sandbox Runner** — in-process execution with restricted globals, threading timeout (5s), safe builtins only
- **Data API** — pre-fetched read-only data injected as functions: get_price_history, get_current_price, get_my_trades, get_my_positions, get_agora_messages, get_market_regime, output()
- **Cost accounting** — $0.001 base + $0.0001/ms added to agent thinking tax
- **Tool-outcome correlation** — Redis-backed lookback tracking for tool win rates
- **3 new actions** (all roles) — execute_analysis, run_tool, modify_genome
- **DB tables** — agent_tools (with dedup, versioning, inheritance tracking), sandbox_executions

#### Tier 2 — Strategy Genome
- **Genome Schema** — ~30 numerical parameters across 5 sections (market_selection, signal_generation, plan_construction, risk_management, behavioral). Role-specific section filtering
- **Mutation Engine** — reproduction (15% rate), warm-start from best genome (40% rate), diversity pressure (30% rate). All mutations clamped to bounds
- **Genome Manager** — CRUD, agent-directed modifications (max 2/eval, validated against bounds), fitness tracking with age bonus
- **Diversity Monitor** — cosine distance across population, convergence alert below 0.3 diversity index
- **DB table** — agent_genomes (genome_data JSONB, fitness_score, parent linkage, mutation history)
- **Config** — 20 new variables

#### Tests
- 41 new tests across Tier 1 (21) and Tier 2 (20)
- Total: 782 tests passing

## [2.2.0] - 2026-03-22

### Added — Phase 8B: Survival Instinct

#### Tier 1 — Context Enrichment + System Prompt Rewrite
- **Survival Context Assembler** (`src/agents/survival_context.py`) — injects competitive landscape into every cycle: rank, competition, death feed, ecosystem pulse, evaluation countdown
- **System prompt rewrite** — survival directive replaces cost warning: permission to be self-interested, pressure addenda when in danger
- **Strategic review** every 50th cycle — competitive analysis, alliance/resource strategy, wild card

#### Tier 2 — New Actions + Intel Tracking + Death Last Words
- **7 universal actions** — propose_sip, offer_intel, request/accept/dissolve_alliance, strategic_hibernate
- **3 role-specific actions** — poison_intel (Scout), challenge_evaluation_criteria (Critic), refuse_plan (Operator)
- **Intel accuracy tracking** with 48h settlement window and reputation stakes
- **Intel challenge system** — challenge bad intel for reputation gains/losses
- **Death last words** — Agent.last_words column for dying agents' final messages
- **Reputation in evaluation** — 10% of composite score (performance weights scaled to 90%)

#### Tier 3 — Alliance System + SIP Processing
- **Alliance Manager** (`src/agents/alliance_manager.py`) — full lifecycle: propose, accept, dissolve, auto-dissolve on death, context injection, trust bonus (+0.1)
- **SIP tables** — system_improvement_proposals with Genesis verdict tracking, owner decision pipeline
- **DB schema** — 4 new tables (agent_alliances, system_improvement_proposals, intel_accuracy_tracking, intel_challenges), 1 new Agent column (last_words)
- **Config** — 12 new variables for SIP limits, intel settlement, alliance bonuses, pressure thresholds

#### Tests
- 35 new tests across all 3 tiers
- Total: 741 tests passing

## [2.1.0] - 2026-03-21

### Added — Phase 8A: The Syndicate CLI Launcher

#### CLI Application
- **`syndicate.bat`** — one-click desktop launcher (4 lines, calls syndicate_cli.py via venv Python)
- **`scripts/syndicate_cli.py`** — rich terminal menu with 9 options: Launch All, Shutdown All, Open Dashboard, System Status, Backup Now, View Logs, Clean Slate, Settings, Exit
- **Live status display** — green/red status indicators for PostgreSQL, Memurai, Arena in the menu
- **Exit options** — leave services running (exit CLI only) or shut everything down first

#### Configuration & Detection
- **`scripts/syndicate_config.py`** — auto-detects PostgreSQL, Memurai, project paths, venv
- **First-run wizard** — interactive setup that finds everything automatically, asks user only for missing paths
- **Config persistence** — `scripts/syndicate_config.json` (gitignored), editable via Settings menu
- **Path detection** — searches PATH, known install locations, glob patterns for PostgreSQL versions

#### Service Management
- **`scripts/syndicate_services.py`** — start/stop/health-check for PostgreSQL (pg_ctl), Memurai (net start/stop), Arena (subprocess with CREATE_NEW_PROCESS_GROUP)
- **Health gates** — sequential startup waits for each service to accept connections before proceeding
- **Clean Slate** — database reset with safety confirmation (type YES), truncates all agent tables, resets treasury to $500, flushes Redis

#### PID Tracking
- **`scripts/syndicate_pids.py`** — JSON-based PID tracking survives CLI restarts
- **Stale PID cleanup** — automatically removes entries for dead processes on startup
- **Windows Service detection** — Memurai tracked as service (no PID needed)

#### Operational Features
- **View Logs** — tail last 50 lines of arena/postgresql logs, live tail with Ctrl+C
- **Backup Now** — triggers existing backup system from menu
- **Settings** — view/edit config, re-run auto-detection, toggle browser-on-launch

#### Tests
- 16 new tests: config save/load/detect, PID record/remove/alive/cleanup, service port checks, status dict structure
- Total: 706 tests passing

## [2.0.0] - 2026-03-21

### Added — Phase 6A: The Command Center

#### Complete Visual Overhaul
- **Sci-fi command center aesthetic** — deep navy (#080c18) background, custom color palette (cyan/amber/red/green/purple), JetBrains Mono + Inter fonts
- **Dark theme only** — removed light mode toggle, locked in dark theme
- **Sticky top bar** — replaced sidebar navigation with 48px sticky header: PROJECT SYNDICATE logo, LIVE badge, nav tabs, system vitals (treasury, alert, regime, agents)
- **New home page** — `GET /` now renders Command Center instead of redirecting to Agora

#### Agent Character Cards
- **Hex avatars** — deterministic hexagonal SVG avatars from agent ID + role, server-rendered via Jinja2 macro
- **Visual states** — active (role-color glow pulse), hibernating (dimmed 60%), dying (<3 days: red cracks), dead (greyscale + TERMINATED stamp)
- **Survival bars** — depleting progress bar: green >50%, amber 25-50%, red <25%
- **Sparklines** — inline SVG polyline showing last 20 data points, green/red trend coloring
- **Metrics row** — True P&L, Sharpe, Efficiency with semantic coloring
- **Status row** — action label, animated status dots, model used + cycle cost

#### Live Feed (SSE)
- **Server-Sent Events endpoint** (`/api/events/stream`) — real-time streaming of Agora messages
- **Event type mapping** — icons and colors per channel/message_type (trade=⚡, intel=◎, plan=◈, alert=⚠, death=☠, birth=✦)
- **Major event detection** — deaths, reproductions, circuit breakers trigger full-width event banners
- **Opacity gradient** — newer entries brighter, older entries fade out
- **Auto-reconnect** on connection loss

#### Constellation Ecosystem View
- **Canvas-based force-directed graph** (`static/js/constellation.js`) — agents as role-colored nodes orbiting Genesis
- **Dynasty connections** — purple lines between same-dynasty agents
- **Physics simulation** — gravity toward center, node repulsion, drift velocity
- **Node sizing** — proportional to composite score

#### Leaderboard
- **Ranked list** — crown (♛) for #1 with amber highlight, bold top 3, rank delta arrows (▲▼─)
- **Role icons** — colored per role type

#### System Status Panel
- **Compact stats list** — Market Regime, Alert Level, Haiku Routing %, Saved Today, Avg Cost/Cycle
- **Color-coded values** — semantic coloring per metric

#### API Endpoints
- `GET /api/system/topbar` — HTML fragment for top bar vitals
- `GET /api/system/constellation` — JSON: agent list + dynasty connections
- `GET /api/events/stream` — SSE endpoint for live activity feed

#### Templates
- **New:** command_center.html, hex_avatar.html, leaderboard.html (component), topbar_vitals.html
- **Rewritten:** base.html, agent_card.html, system_status.html, system.html, agora.html, agent_cards.html
- **New static:** js/constellation.js

#### Tests
- 19 new tests: test_command_center.py (10), test_sse.py (4), updated test_web_app.py
- Total: 690 tests passing

## [1.4.0] - 2026-03-21

### Added — Phase 3.5: API Cost Optimization

#### Model Router
- **Model Router** (`src/agents/model_router.py`) — deterministic Haiku/Sonnet selection based on role, cycle type, capital-at-risk, and alert level. Haiku ($1/$5) for routine work, Sonnet ($3/$15) for high-stakes decisions (Genesis evaluations, Critic reviews, Strategist plans, capital commitment, crisis mode, retry escalation)
- **Kill switch:** `MODEL_ROUTING_ENABLED=false` reverts to all-Sonnet behavior

#### Prompt Caching
- **Cache control** integrated into `ClaudeClient.call()` and `call_repair()` — `cache_control: {"type": "ephemeral"}` on system prompts for 90% input token savings on repeated content
- **Cache-aware cost calculation** — cache writes at 1.25x rate, cache reads at 0.1x rate
- **Cache token tracking** — `cache_creation_tokens` and `cache_read_tokens` in `APIResponse` dataclass
- **Kill switch:** `PROMPT_CACHING_ENABLED=false` disables cache_control

#### Adaptive Cycle Frequency
- **Regime-based multipliers** in `CycleScheduler` — volatile 0.5x (faster), trending 0.75x, ranging/crab 1.5x (slower), low_volatility 2.0x (slowest). Unknown defaults to 1.0x
- **30-second floor** prevents cycles from running too frequently
- **Kill switch:** `ADAPTIVE_FREQUENCY_ENABLED=false` uses fixed intervals

#### Context Window Diet
- **Haiku token budget** — 70% of normal budget when Haiku is selected (configurable via `HAIKU_CONTEXT_BUDGET_MULTIPLIER`)
- **Output length guidance** — Haiku cycles get "2-3 sentences max" nudge, Sonnet gets "thorough but not verbose"
- **Agora message truncation** — messages older than 5 cycles truncated to 100 chars

#### Batch Processor
- **Batch Processor** (`src/agents/batch_processor.py`) — foundation for Anthropic Batch API (50% savings). Submit, poll, retrieve pattern with timeout handling
- **Disabled by default** (`BATCH_ENABLED=false`) — enable in Phase 4 for evaluations/reflections

#### Cost Tracking & Dashboard
- **Enhanced Accountant** — multi-model pricing, cache token tracking, savings calculation vs all-Sonnet baseline, today/all-time breakdowns
- **System summary** includes: `estimated_savings_today`, `estimated_savings_alltime`, `model_distribution_today`, `haiku_ratio_today`, `avg_cost_per_cycle_today`
- **Dashboard panel** — Cost Optimization section on system page: Haiku/Sonnet distribution, avg cost/cycle, savings today/all-time (30s HTMX refresh)

#### Centralized Model Strings
- Replaced hardcoded model strings in genesis.py, evaluation_engine.py, library_service.py, reproduction.py with `config.model_sonnet`
- `MODEL_PRICING` dicts in both claude_client.py and accountant.py cover all known model IDs

#### Database Schema
- **AgentCycle** — 2 new columns: `model_used` (String(60)), `model_reason` (String(30))
- **CycleData** — 2 new fields: `model_used`, `model_reason`

#### Configuration
- 12 new variables: `MODEL_DEFAULT`, `MODEL_SONNET`, `MODEL_ROUTING_ENABLED`, `HAIKU_INPUT_PRICE`, `HAIKU_OUTPUT_PRICE`, `SONNET_INPUT_PRICE`, `SONNET_OUTPUT_PRICE`, `PROMPT_CACHING_ENABLED`, `ADAPTIVE_FREQUENCY_ENABLED`, `MIN_CYCLE_INTERVAL_SECONDS`, `HAIKU_CONTEXT_BUDGET_MULTIPLIER`, `AGORA_MESSAGE_TRUNCATE_AFTER_CYCLES`, `AGORA_MESSAGE_TRUNCATE_LENGTH`, `BATCH_ENABLED`, `BATCH_POLL_INTERVAL_SECONDS`, `BATCH_TIMEOUT_SECONDS`

#### Tests
- 70 new tests: test_model_router.py (18), test_prompt_caching.py (9), test_adaptive_frequency.py (9), test_batch_processor.py (9), test_cost_tracking.py (9), plus existing test updates
- Total: 671 tests passing

## [1.3.0] - 2026-03-12

### Added — Phase 7: The Arena (Launch Preparation)

#### Boot Sequence Integration
- **Genesis auto-trigger** — `_maybe_run_boot_sequence()` added to Genesis run_cycle step 0; detects zero active agents and triggers wave-based BootSequenceOrchestrator with full orientation protocol

#### Arena Run Script
- **`scripts/run_arena.py`** — single-command launcher for all system processes: Warden, Genesis, Trading Monitors, Dead Man's Switch, Dashboard. Pre-flight checks, startup banner with live BTC price, process monitoring with auto-restart, graceful shutdown in criticality order

#### Monitoring & Documentation
- **`docs/arena_monitoring.md`** — daily 5-minute check-in checklist, Day 10 health check, Day 21 evaluation milestones, success criteria
- **`docs/arena_log.md`** — 21-day structured observation log template

#### Database
- Clean slate: all agent data truncated, system_state reset to $500 treasury, GREEN alert, 0 agents
- Redis flushed

## [1.2.0] - 2026-03-12

### Added — Phase 3F: First Death, First Reproduction, First Dynasty

#### Dynasty System
- **Dynasty Manager** (`src/dynasty/dynasty_manager.py`) — dynasty creation, birth/death recording, extinction detection, concentration checks (40% hard limit, 25% warning), P&L aggregation
- **Lineage Manager** (`src/dynasty/lineage_manager.py`) — lineage records with parent chains, profile snapshots, death records, family tree builder, ancestor chain walker
- **Memorial Manager** (`src/dynasty/memorial_manager.py`) — "The Fallen" memorial records with best/worst metrics, epitaphs, notable achievements, cause of death
- **Dynasty Analytics** (`src/dynasty/dynasty_analytics.py`) — cross-dynasty comparison, generational improvement tracking, lineage knowledge depth, dominant trait aggregation

#### Reproduction Engine
- **Reproduction Engine** (`src/dynasty/reproduction.py`) — full lifecycle: eligibility (Expert+ prestige, top 50% composite, positive P&L, cooldown), Genesis AI mutation decisions, offspring building, memory/trust inheritance, posthumous reproduction
- **Memory Inheritance** — 75% confidence discount + age decay (0.95^(days-30), floor 0.10), source labeled parent/grandparent
- **Trust Inheritance** — 50% blend with neutral prior (inherited = trust * 0.5 + 0.5 * 0.5)
- **Temperature Mutation** — parent's temp ± uniform(0, 0.03) clamped to role bounds
- **Founding Directives** — QUESTIONS not instructions, consumed after orientation

#### Death Protocol (10-step sequence)
- Integrated into `evaluation_engine._terminate_agent()`: freeze → financial cleanup → relationship archival → post-mortem → knowledge preservation → lineage death record → dynasty death record → memorial creation → dynasty P&L update → Agora announcement

#### Offspring Orientation
- Modified orientation for offspring: 1 textbook (thinking_efficiently) + mentor package, lineage identity in system prompt, founding directive as question, 14-day survival clock

#### Boot Sequence Dynasty Support
- Each Gen 1 agent creates a Dynasty record during spawn
- Lineage records include dynasty_id and agent_name

#### Dashboard
- **Dynasty API** (`src/web/routes/api_dynasty.py`) — 6 JSON endpoints: dynasties list, dynasty detail, family tree, analytics, memorials list, memorial detail

#### Database Schema
- **Dynasty table** — founder info, status (active/extinct), member counts, total P&L, avg lifespan, best performer, generational improvement
- **Memorial table** — agent info, dynasty, metrics, epitaph, cause of death, notable achievement
- **Lineage extensions** — 16 new columns: agent_name, dynasty_id, grandparent_id, inherited memories/temperature, mutations, founding directive, posthumous birth, parent profile snapshot, death fields
- **Agent extensions** — 7 new columns: dynasty_id, offspring_count, last_reproduction_at, reproduction_cooldown_until, founding_directive, founding_directive_consumed, posthumous_birth

#### Configuration
- 12 new variables: reproduction_cooldown_evals, reproduction_min_prestige, dynasty_concentration_hard_limit, dynasty_concentration_warning, memory_inheritance_discount, memory_age_decay_factor, memory_age_decay_start_days, memory_confidence_floor, trust_inheritance_factor, temperature_mutation_range, max_reproductions_per_cycle, offspring_survival_clock_days

#### Tests
- 45 new tests across 7 test files (test_dynasty_manager, test_lineage_manager, test_memorial_manager, test_dynasty_analytics, test_offspring_orientation, test_reproduction_engine, test_death_protocol)
- All 599 tests passing (2 pre-existing library textbook failures)

#### Bug Fixes
- Fixed naive vs timezone-aware datetime comparisons in memorial_manager, lineage_manager, and reproduction engine (SQLite compatibility)

## [1.1.0] - 2026-03-12

### Added — Phase 3E: Personality Through Experience

#### Behavioral Profile System
- **Behavioral Profile Calculator** (`src/personality/behavioral_profile.py`) — computes 7 traits from actual behavior: risk_appetite (position sizing/loss tolerance), market_focus (Shannon entropy), timing (hour heatmap), decision_style (reasoning × confidence variance), collaboration (pipeline outcomes), learning_velocity (eval score trend), resilience (loss-to-recovery). Agents NEVER see their own profile. Threshold-based classification with tier distance drift detection (2+ tier shift = alarm).

#### Temperature Evolution
- **Temperature Evolution Engine** (`src/personality/temperature_evolution.py`) — API temperature drifts ±0.05 per evaluation based on diversity-profitability Pearson correlation. 2-eval momentum requirement. Role-specific bounds (scout 0.3–0.9, operator 0.1–0.4). Full history recorded on agent.

#### Reflection Library Access
- **Reflection Library Selector** (`src/personality/reflection_library.py`) — targeted study sessions during reflection cycles. System offers Library material matching weakest evaluation metric. 5-reflection cooldown per resource. Passive injection via buffer token budget. Archive fallback for missing textbooks.

#### Dynamic Identity
- **Dynamic Identity Builder** (`src/personality/identity_builder.py`) — evolving system prompt identity from facts, not labels. Architectural constraint: NEVER imports BehavioralProfile. Three tiers: new (<30), established (30-99), veteran (100+). Blocked label word validation. `extract_evaluation_facts()` helper.

#### Relationship Memory
- **Relationship Manager** (`src/personality/relationship_manager.py`) — Bayesian trust scoring (prior=0.5, decay=0.95/day). Auto-updated from pipeline outcomes (position → plan → opportunity chain) and self-note sentiment analysis (positive/negative word sets). Dead agent relationships archived. Trust summary for context injection.

#### Divergence Tracking
- **Divergence Calculator** (`src/personality/divergence.py`) — cosine distance between behavioral profile score vectors for same-role pairs. Low divergence (<0.15) flagged as redundancy. Snapshots stored per evaluation.

#### Dashboard
- **Personality API** (`src/web/routes/api_personality.py`) — JSON endpoints: GET /api/personality/{id}/profile, /relationships, /temperature-history, /divergence

#### Database Schema
- 4 new tables: `behavioral_profiles`, `agent_relationships`, `divergence_scores`, `study_history`
- 4 new Agent columns: `last_temperature_signal`, `temperature_history`, `identity_tier`, `behavioral_profile_id`

#### Integration
- **Context Assembler** — dynamic identity replaces static intro, trust relationships in memory context, library content injection during reflection cycles
- **Evaluation Engine** — Phase 7 (profile computation + drift detection + temperature evolution) and Phase 8 (divergence computation + low divergence flagging)
- **Action Executor** — relationship tracking on position close via RelationshipManager
- **Memory Manager** — relationship extraction from reflection text via sentiment analysis

#### Configuration
- 22 new config variables in `src/common/config.py` and `.env.example` for temperature bounds, trust parameters, profile thresholds, identity tiers, and divergence settings

#### Tests
- 58 new tests across 6 test files: behavioral profile (15), temperature evolution (7), reflection library (5), dynamic identity (9), relationship manager (11), divergence (11)

## [1.0.0] - 2026-03-12

### Added — Phase 3D: Natural Selection (The First Evaluation Cycle)

#### Evaluation Engine
- **Role Metrics** (`src/genesis/role_metrics.py`) — 4 role-specific composite calculators (Operator, Scout, Strategist, Critic) with configurable normalization ranges. Operator: 0.40 Sharpe + 0.25 True P&L% + 0.20 Thinking Efficiency + 0.15 Consistency. Scout: 0.30 Intel Conversion + 0.30 Profitability + 0.15 Signal Quality + 0.15 Efficiency + 0.10 Activity. Strategist: 0.25 Approval + 0.30 Profitability + 0.15 Efficiency + 0.15 Revision + 0.15 Thinking. Critic: 0.30 Rejection Value + 0.25 Approval Accuracy + 0.15 Risk Flag + 0.15 Throughput + 0.15 Thinking.
- **Evaluation Engine** (`src/genesis/evaluation_engine.py`) — 3-stage Darwinian selection: quantitative pre-filter → Genesis AI judgment (probation cases only) → execute decisions. Pre-filter thresholds per role. First-evaluation leniency (no termination). Regime adjustment when alert hours > 50% of period. Handles termination (cancel orders, close positions, post-mortem generation), probation (shortened survival clock, 25% budget cut, 3-cycle grace period), survival (update counters, reset clock, prestige milestone check).
- **Evaluation Assembler** (`src/genesis/evaluation_assembler.py`) — builds full evaluation package from all analyzers: financial data, behavioral data, ecosystem contribution, pipeline analysis, idle analysis, honesty scoring. Produces compressed text summary (<1000 tokens) for Genesis AI review.

#### Pipeline & Attribution
- **Pipeline Analyzer** (`src/genesis/pipeline_analyzer.py`) — tracks conversion rates at each pipeline stage (opportunity → plan → approved → executed → profitable), identifies bottleneck stage. Special case: approved-but-not-executed detection.
- **Ecosystem Contribution** (`src/genesis/ecosystem_contribution.py`) — role-specific contribution calculation: Operators = true_pnl, Scouts = attributed_pnl × 0.25, Strategists = attributed_pnl × 0.25, Critics = money_saved × 0.50.

#### Behavioral Analysis
- **Rejection Tracker** (`src/genesis/rejection_tracker.py`) — counterfactual simulation for critic rejections. Monitors rejected plans against market data to determine if stop-loss or take-profit would have been hit. Calculates per-critic accuracy scores. Direction-aware (long/short). Timeframe parsing for monitoring duration.
- **Idle Analyzer** (`src/genesis/idle_analyzer.py`) — classifies idle cycles in priority order: post_loss_caution → no_input → strategic_patience → paralysis. Checks pipeline availability per role.
- **Honesty Scorer** (`src/genesis/honesty_scorer.py`) — supplementary metric (NOT in composites): confidence calibration via Pearson correlation (0.40 weight), self-note accuracy via prediction tracking (0.30), reflection specificity via regex scoring (0.30). Requires ≥5 data points.

#### Post-Mortems & Prestige
- Auto-generated post-mortems on agent termination: genesis_visible=True immediately, 6-hour delay for Library publication
- Prestige milestones: 3=Apprentice, 5=Journeyman, 10=Expert, 15=Master, 20=Grandmaster
- Probation mechanics: shortened survival clock (half), budget cut (25%), 3-cycle grace period

#### Database
- 7 new Agent columns: pending_evaluation, probation, probation_grace_cycles, ecosystem_contribution, role_rank, last_evaluation_id (FK), evaluation_scorecard (JSON)
- Expanded Evaluation model with ~25 new Phase 3D columns (composite_score, metric_breakdown, pre_filter_result, genesis_decision, prestige_before/after, capital_before/after, etc.)
- New table: `rejection_tracking` — counterfactual simulation tracking for critic rejections
- New table: `post_mortems` — agent death analysis with Library publication workflow

#### Configuration
- 22 new config variables: normalization ranges, attribution shares, probation settings, concentration limits, budget adjustments, rubber-stamp penalties

#### Cross-Agent Awareness
- **Warden** updated: portfolio concentration checks — hard limit 50% (REJECT), warning at 35% (APPROVE with flag)
- **Context Assembler** updated: portfolio awareness for Operator agents (cash, positions, concentration), one-time evaluation feedback injection (scorecard cleared after delivery)
- **Plans Manager** updated: rejection tracking on critic rejection

#### Tests (47 new, 496 total — all passing)
- test_role_metrics.py (12): normalize, operator/scout/strategist/critic composites, rubber stamp penalty, factory
- test_pipeline_analyzer.py (4): empty pipeline, scout bottleneck, approved-not-executed, conversion rates
- test_rejection_tracker.py (6): tracking creation, stop-loss/take-profit/timeframe outcomes, score calculation, no-data neutral
- test_idle_analyzer.py (5): post_loss_caution, no_input, strategic_patience, paralysis, idle rate
- test_honesty_scorer.py (5): correlated/uncorrelated confidence, specific/vague reflections, insufficient data
- test_evaluation_engine.py (7): profitable survives, deep loss terminated, borderline probation, first-eval leniency, probation mechanics, role gap detection, prestige milestone
- test_post_mortem.py (3): creation, 6-hour publish delay, API failure graceful handling
- test_cross_agent.py (5): concentration reject/warn, portfolio awareness, scout exclusion, feedback injection

### Changed
- **models.py** bumped to v1.0.0: Agent/Evaluation model expansions, new FK relationships with explicit foreign_keys
- **config.py** bumped to v1.0.0: 22 new evaluation/selection config variables
- **warden.py** bumped to v1.0.0: concentration check before large trade approval
- **accountant.py** bumped to v1.0.0: Sharpe returns None for non-operator roles
- **context_assembler.py** bumped to v1.0.0: portfolio awareness, evaluation feedback injection
- **plans.py** bumped to v1.0.0: rejection tracking on critic rejection
- **genesis.py** bumped to v1.0.0: new EvaluationEngine integration, rejection tracker monitoring, post-mortem publication in maintenance

## [0.9.0] - 2026-03-12

### Added — Phase 3C: Paper Trading Infrastructure
- **Database Schema**: 3 new tables (`positions`, `orders`, `agent_equity_snapshots`) + 7 new Agent columns (`cash_balance`, `reserved_cash`, `total_equity`, `realized_pnl`, `unrealized_pnl`, `total_fees_paid`, `position_count`)
- **PriceCache** (`src/common/price_cache.py`): Redis-backed ticker and order book cache with 10s TTL, 60s stale threshold, batch fetch
- **FeeSchedule** (`src/trading/fee_schedule.py`): Kraken (0.16%/0.26%) and Binance (0.10%/0.10%) fee rates, maker/taker distinction
- **SlippageModel** (`src/trading/slippage_model.py`): Order-book VWAP walk with ±20% noise, minimum 0.01% floor, depth penalty
- **TradeExecutionService** (`src/trading/execution_service.py`): Abstract interface + PaperTradingService implementation
  - Market orders with slippage and fees
  - Limit orders with cash reservation
  - Position close with Redis lock (double-close prevention)
  - Warden integration for trade gate checks
  - Transaction records for Accountant bridge
  - Factory function `get_trading_service()` for paper/live switch
- **PositionMonitor** (`src/trading/position_monitor.py`): 10s loop monitoring all open positions
  - Stop-loss fills at BID price + slippage (realistic)
  - Take-profit fills at TP price (maker fee)
  - Stale price detection pauses stop/TP triggers
  - Redis heartbeat for Dead Man's Switch
- **LimitOrderMonitor** (`src/trading/limit_order_monitor.py`): 10s loop monitoring pending limit orders
  - Price improvement (buy at min(limit, ask))
  - 24h expiry with automatic cash reservation release
  - No fills on stale prices
- **EquitySnapshotService** (`src/trading/equity_snapshots.py`): 5-minute equity snapshots for Sharpe ratio calculation
- **SanityChecker** (`src/trading/sanity_checker.py`): 5-minute health checks
  - Negative cash balance detection (CRITICAL)
  - Equity reconciliation auto-correction
  - Orphaned position detection
  - Stale reservation cleanup
  - ConcentrationMonitor (40% threshold warning)
- **Process runner** (`scripts/run_trading.py`): Starts PositionMonitor + LimitOrderMonitor as async tasks
- 14 new config variables for Phase 3C
- 71 new tests (449 total passing), 9 test files

### Changed
- **Warden** (`src/risk/warden.py`): Trade gate now checks buying power (cash - reservations) instead of just capital
- **ActionExecutor** (`src/agents/action_executor.py`): Operator trades now route through TradeExecutionService instead of placeholder
- **run_all.py**: Added `--with-trading` flag for trading monitors
- All module versions bumped to 0.9.0

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
