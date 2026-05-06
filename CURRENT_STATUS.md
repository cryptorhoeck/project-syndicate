# CURRENT_STATUS.md

## Session: 2026-05-04 evening through 2026-05-05 afternoon — Wiring Audit Closeout

### What was accomplished

Seven wiring-audit subsystems closed in one extended session. The audit's signature bug class — "built but not wired" — is structurally eliminated from the production code paths it was hiding in.

| Subsystem | Description | Branch (now merged) |
|---|---|---|
| N | Warden trade-time gate (Yellow/Red/Circuit Breaker enforcement at trade initiation) | hotfix/warden-trade-time-wiring |
| U | Wire scheduler in PROCESSES dict for Arena boot + shutdown ordering chore | hotfix/wire-scheduler-in-processes (+ chore/wire-scheduler-test-tightening) |
| I | Operator halt consumer + Redis persistence layer (closes severity-5 cross-process gap) | hotfix/operator-halt-consumer-wiring |
| H | Genesis regime-review consumption hook (DB-as-queue pattern, retry cap, per-row error attribution) | hotfix/genesis-regime-review-hook |
| P | Eval engine async bridge (run_async_safely helper, escalation counter pattern) | hotfix/eval-engine-coroutine-fix |
| T-subset | Maintenance run_all() wiring (three orphaned cleanup methods now invoked hourly) | hotfix/maintenance-run-all-wiring |
| F+G | Strategist + Critic Archive query helpers (pre-fetch slice + query_archive action) | hotfix/strategist-critic-archive-helpers |

### Test count trajectory

- Session start: 991 passing
- After wire scheduler chore: 991
- After Operator halt + persistence (I): 1017
- After Genesis regime review (H): 1038
- After Eval engine async bridge (P): 1059
- After Maintenance run_all (T-subset): 1074
- **After Archive helpers (F+G): 1106**

115 new tests added in this session. Each one guards against a specific failure mode surfaced during Critic review or directive verification.

### Architectural patterns established this session

These patterns are now consistent across the colony's infrastructure. New code should follow them:

1. **Wiring-contract pattern** — when a service has a defined slot for a safety-critical dependency, the factory must construct it; if construction fails, sys.exit(2) at boot. Defense-in-depth fallback must scream loud (CRITICAL log + system-alert), never silent. Established in N, applied in I, used as model for everything else.

2. **DB-as-queue with retry cap** — pending/reviewed/failed status, attempt_count column, last_error column, MAX_ATTEMPTS = 3 with derivation, pre-flip-pass exclusion in the SELECT, per-row try/except (so one poison row doesn't corrupt others' last_error). Established in H iteration 4, replicated in F+G.

3. **Cross-process state via Redis** — halt-state-unknown auto-clearing pattern, fail-closed-to-halt-everything semantics on read failure, mirror Warden's _safety_state_unknown shape. Established in I.

4. **Async bridge for sync-wrapping-async patterns** — run_async_safely helper with narrow Exception scope (KeyboardInterrupt + SystemExit propagate), per-call-type failure counter, ASYNC_FAILURE_ESCALATION_THRESHOLD = 3 with consecutive-only contract. Established in P.

5. **Production-path tests** (test_*_actually_*_in_production_path) — instantiate real production classes through real code paths, assert observable side effects (DB rows, log emissions, attribute states). Not unit tests dressed up as integration tests. Established in I, required in every fix since.

6. **AST source-inspection guards** — for load-bearing wiring lines, parse the AST and assert the assignment node exists. Catches refactors that silently drop production wiring. Used as one layer of multi-layer defense, paired with production-path tests.

7. **Constants need derivation** — every threshold/window/limit needs either operational derivation (e.g. matches halt expiry TTL) or honest pattern-match acknowledgment (e.g. matches K=3 across async-bridge users). No magic numbers.

8. **CRITICAL log is the contract; Agora is best-effort** — when escalating, CRITICAL log fires FIRST. Agora system-alert post is wrapped in narrow try/except, logs WARNING on failure with structured field, does not propagate exception.

### Deferred-tracker entries logged this session

Four substantial deferred items added to DEFERRED_ITEMS_TRACKER.md. None block progress; all should be addressed before live trading transition.

1. **critic.py SHA-tagging improvement** — capture HEAD SHA at review time, include in filename/header. Surfaced when stale Critic output drove a duplicate-branch directive.
2. **Halt event durability across Memurai outages** — haiku digester catches OperatorHaltPublishError and continues; one-shot sev-5 events that fire during a Memurai outage are lost. Bounded risk for current scope.
3. **T-subset escalation policy** — explicit decision to use WARNING-only failure handling for hygiene-class maintenance. Documented rationale; revisit if real incidents show bounded-impact assumption is wrong.
4. **CI Postgres integration test fixture** — production-path tests use SQLite; Postgres-specific code (sa.text, check constraints) is exercised manually via scripts. CI cannot catch Postgres regressions today.
5. **Production logging config: ensure CRITICAL logs route to durable destination** — CRITICAL logs across the codebase rely on structlog handler config. If production startup doesn't attach a durable handler, "CRITICAL log is the contract" is hollow. Gating for live trading.

### Calibration observations worth remembering

The multi-agent review pattern (CC implementation → script Critic → chat Critic → War Room triage) produced clean fixes consistently. Some calibration data:

1. **Chat Critic with iteration continuity is higher-trust on multi-iteration branches** than fresh-context script Critic. Script Critic is best for first-iteration structural review or totally new branches; on later iterations it tends to re-litigate previously-accepted design decisions or flag truncation artifacts.

2. **Script Critic flags often dissolve under direct verification.** Diff truncation at ~1500 lines causes the script to flag unseen code as concerning. Pattern from this session: when script Critic flags something with "I cannot verify because diff truncated," verify directly before iterating.

3. **CC's verification-stop behavior caught FIVE directive errors this session** that would have shipped real bugs:
   - Trading service wiring: built proof-of-correctness, didn't trust the design
   - False-alarm test failure: pushed back rather than create duplicate branch
   - Postgres unavailable: stopped rather than fake e2e validation
   - Genesis.review_regime() doesn't exist: stopped rather than build around a stub
   - 24× budget consumption trap: stopped before writing code that would have silently broken the daily thinking-budget cap

This pattern is now well-established. Reinforce when CC pushes back on a directive that doesn't match codebase reality. The default reaction to CC pushback should be "good, what did it find" rather than "why isn't it just doing what I asked."

4. **Convergent Critic findings are the strongest signal.** When script Critic and chat Critic flag the same gap from different angles, fix it without debate. Tonight: cross-process boundary in I, hourly gate untested in T-subset.

### Open work queue (not blocking, prioritized)

**Immediate next session candidates:**

- **Boot colony for live observation** with all seven fixes integrated. None of them have been validated *together* in a running colony. Six pieces of safety infrastructure all wired into one boot is a different beast than seven individually-tested pieces. Things might surface.
- **CRITICAL log handler audit** (deferred entry #5) — gating for live trading, should be addressed before any real-capital transition.

**Medium-term:**

- **Halt event durability replay** (deferred entry #2) — design + implement DB-backed halt event log so one-shot sev-5 events during Memurai outages aren't lost.
- **CI Postgres integration test fixture** (deferred entry #4) — meaningful CI hardening work.
- **critic.py SHA-tagging** (deferred entry #1) — small tooling improvement.

**Pending design conversations:**

- **J — Sentinel critical risk-flag mechanical action.** Today log-only. War Room call: should a sev-1 risk flag from a Critic trigger Genesis re-evaluation, Operator pause, or stay log-only? Pure design decision.
- **K/L/M (Black Swan tier semantics).** Detection is wired in N. Specific behavior at each tier (Yellow → reduce position sizing? Red → freeze new positions? Circuit Breaker → close existing?) is design, not wiring.

### Where main is

Branch: main
Latest commit (top of log): the F+G follow-up merge (1106 tests passing)
Postgres dev DB: still RUNNING from fix H session (started via C:/ProDesk/pgsql/bin/pg_ctl.exe). Andrew manages lifecycle.
Memurai: running.
Working tree: clean.

### Reset/cleanup notes for tomorrow

- No half-finished branches lingering (all fix branches merged and deleted)
- No uncommitted local state expected
- DEFERRED_ITEMS_TRACKER.md is current as of this session
- CHANGELOG.md updated through F+G merge
