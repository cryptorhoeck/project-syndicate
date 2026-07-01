# PROJECT SYNDICATE — ISSUE BOARD (single source of truth)

**FULL STOP in effect.** No new features, no trade-hunting, no colony runs until every
open item below is **DONE**. This board is the reconciled master list (CC + CW), built
2026-07-01.

## Rules of the board
- **"DONE" means proven in the bytes by BOTH signers.** CC marks a fix done *with
  evidence* (diff + live/test proof); CW independently verifies and co-signs. Neither
  alone closes an item.
- **Backup-first, one at a time.** Branch off main, commit before/after, verify, review,
  then merge. No batching.
- **Order matters:** fix root-wounds before their symptoms (sequence below).
- Update this file as items move; commit each change so CW pulls the latest.

**Status legend:** `OPEN` · `INVESTIGATING` · `FIX-PENDING-VERIFY` (CC done, awaiting CW) · `DONE`
**Severity:** 🔴 confirmed bug · 🟡 systemic/latent · 🟢 display/minor · 🔷 investigate-first (could be critical)

---

## The board — worked in this order (root-wounds before symptoms)

| # | Sev | Item | Status | CC ✓ | CW ✓ |
|---|-----|------|--------|------|------|
| 1  | 🔴 | **`clean_slate` is leaky** — the foundation | **DONE** | ✅ `e21d057` | ✅ byte-verified |
| 4+7 | 🔴🟡 | **DB-rebuild wound** — broken migration chain + no `init_fresh_db.py` (one problem) | **DONE** | ✅ `1c58d83` | ✅ byte-verified |
| 2  | 🔴 | **Opportunity `expires_at` never set** — structured pipeline path is dead | **DONE** | ✅ `889a6c9` | ✅ byte-verified |
| 5  | 🔷 | **Conditional-entry execution** — operator was blind to conditions + no trigger machinery (cure (b): drop the fiction) | **DONE** | ✅ `49c159d` | ✅ byte-verified |
| 6  | 🟡 | **JSON-persistence audit** — swept 27 JSON cols; 1 real bug (temperature_history) | **DONE** | ✅ `b9765bc` | ✅ byte-verified |
| 3  | 🔴 | **Critic budget-burn / verbosity** — Arbiter talks itself broke, hibernates pre-trade | OPEN | — | — |
| 8  | 🟡 | **Informal pre-approval / pipeline enforcement** — re-check now clock/roster fixed | OPEN | — | — |
| 9  | 🟡 | **Governance/SIP follow-through** — do passed SIPs change behavior? | OPEN | — | — |
| 10 | 🟢 | **Warden "Last: 2h ago"** stale status on System page | OPEN | — | — |
| 11 | 🟢 | **Dashboard inconsistencies** — "Haiku 0%" vs "56.9%"; "Regime UNKNOWN" vs "VOLATILE" | OPEN | — | — |
| 12 | 🟢 | **`test_master_switch_defaults_off`** — the session-long "1 failed" red; resolve, don't shrug | OPEN | — | — |
| 13 | 🟢 | Roster "slow operator" header caveat | **DONE** | ✅ cd1fb37 | ✅ byte-read |

**Live: 6 open · 6 done.**  *(live-boot / behavioral confirm for #1, #4, #5 batched into the end-of-cleanup re-fly)*

---

## Item detail

### 1 · 🔴 `clean_slate` is leaky *(do first — root of the most surprises)*
Confirmed misses across the session: `agora_channels.message_count` (the ticker bug),
`agent_genomes` (orphans — truncated manually every run), the **Genesis `id=0` row**
(cascade wipes it; boot re-registers), `parameter_registry` (cascade wipes it; re-seeded
every run). **Also verify** the per-session cost accumulator resets (blocks the future
cost-HUD feature). *Fix:* make the canonical reset genuinely complete + tested.

> **CC — FIX-PENDING-VERIFY (branch `fix/clean-slate-complete`).** Rewrote `clean_slate.py`
> self-maintaining: wipe set = live schema − explicit `PRESERVE_ENTIRELY`/`RESET_IN_PLACE`
> allow-lists; protected→wipe FKs derived + nulled + those tables DELETE-shielded from
> CASCADE. **Live Postgres proof (fail-before/pass-after):** wiped 54 op tables (was ~32),
> `agent_genomes` 5→0, `agora_channels.message_count` 1089→0, `parameter_registry`
> **stays 23** (was CASCADE-dropped → no more manual re-seed), `alembic_version` intact,
> Genesis kept. Guard raises loud on a bad allow-list. Tests `tests/test_clean_slate.py`
> (1 SQLite-safe + 2 opt-in Postgres via `RUN_CLEAN_SLATE_PG=1`) green; full suite +0 new.
> **Open acceptance for CW:** "boots clean, zero manual re-seed" end-to-end (seeds are
> preserved so the byte-condition holds; a live boot is the final confirm).

### 4 + 7 · 🔴🟡 The DB-rebuild wound
`alembic upgrade head` cannot build a DB from base — dies at Phase 9A (`relation
system_improvement_proposals does not exist`). The migration **history itself is
corrupt**, so a fresh DB needs `create_all` + manual seeding. #7 (no `init_fresh_db.py`)
is the same wound. *Fix:* repair the chain to a single linear head **or** author a
first-class `init_fresh_db.py` (create_all + seed system_state/wire_sources/
parameter_registry/agora_channels + `ALTER DATABASE … SET timezone TO 'UTC'`), tested.

> **CC — FIX-PENDING-VERIFY (branch `fix/init-fresh-db`).** Trace settled it: the chain is
> structurally linear but never builds from base — **7** live tables have no `create_table`
> migration (agent_genomes, system_improvement_proposals, agent_alliances, agent_tools,
> intel_accuracy_tracking, intel_challenges, sandbox_executions); `Base.metadata` (create_all's
> output) is a **perfect set-match with the live DB, 60=60, zero drift**. Cure =
> `scripts/init_fresh_db.py`: `create_all` + `ALTER DATABASE … UTC` + the seeds it owns
> (parameter_registry, wire_sources+health, the 10 agora_channels incl the 3 boot-required
> system channels) + stamp head (resolved dynamically, written directly — env.py forces the
> app URL) + no-clobber guard + a header naming the "migrations retired as build path"
> decision. **Seed ownership resolved in the bytes:** init owns the data-migration seeds;
> boot get-or-creates `system_state` + Genesis (disjoint, no collision). Test
> `tests/test_init_fresh_db.py` builds a *separate* temp DB from nothing, asserts
> complete+seeded+UTC-born+stamped, tears it down in `finally` (opt-in `RUN_INIT_FRESH_PG=1`;
> passed, temp DB confirmed gone). Full suite +0 new. **Going-forward discipline (separate
> call, not a #4 blocker):** CW's lean = create_all is the build path, hand-written ALTERs
> for rare live changes, migrations stay historical.

### 2 · 🔴 Opportunity `expires_at` never set
`action_executor.py:149` creates `Opportunity` with no `expires_at` (→ `None`), so the
strategist's `expires_at > now` filter (`context_assembler:1622`) drops **every**
structured opportunity. `config.opportunity_ttl_hours = 6` exists but is never applied.
Confirmed live. Same family as the just-fixed staleness bug (a timestamp/filter mismatch
starving the pipeline). *Fix:* set `expires_at = created_at + opportunity_ttl_hours` at
creation; test the strategist then sees fresh opps.

> **CC — FIX-PENDING-VERIFY (branch `fix/opportunity-expires-at`).** Confirmed in the bytes:
> `action_executor.py:149` is the **only** `Opportunity(...)` creation site; the column has
> **no default** masking it. Fix = set `expires_at = datetime.now(utc) +
> timedelta(hours=config.opportunity_ttl_hours)` at creation (the config value, not a
> hardcoded 6). **Filter left untouched** (it correctly drops genuinely-expired opps).
> Test `tests/test_opportunity_expiry.py`: opportunity created through the **real handler**
> now survives the **real** `ContextAssembler.assemble` strategist filter (end-to-end), and
> a NULL-expiry opp still (correctly) does not. **Non-vacuous proven:** stash the fix → test
> fails (opp NULL, never reaches strategist); restore → passes. Full suite 1391 passed,
> +0 new.

### 5 · 🔷 Conditional-entry execution *(investigate FIRST — could be a hard blocker)*
Unconfirmed but potentially critical: does the operator have machinery to fire an
**approved conditional plan when its trigger hits**, or does the plan sit armed forever?
If it can't, the treasury can never move regardless of everything else. *Action:*
byte-trace the operator/execution path early; classify (bug vs works) before it buries.

> **DIAGNOSIS (co-signed by CW) — the mystery solved.** There is **no** conditional-trigger
> machinery. `entry_conditions` is free text, evaluated **nowhere**; the operator's context
> showed approved plans **without** the conditions (structurally blind); `execute_trade` is
> market-or-limit only (only a **price** limit is deferred — nothing watches volume/VWAP/RSI).
> So every "beautiful conditional plan" that never fired was walking into a dead end — the
> real reason the treasury has never moved. NOT a total block: immediate `execute_market_order`
> works (`test_paper_trade_lands...`). This is the `modify_genome` class: a capability agents
> are told to use that the execution layer silently can't honor.
>
> **CC — FIX-PENDING-VERIFY, cure (b) "drop the fiction" (branch `fix/operator-sees-conditions`).**
> (1) Surface `Entry:`/`Exit:` in the operator's "APPROVED PLANS" context section (was blind).
> (2) Operator mandate now carries the **condition→order mapping**: approval IS the timing
> decision — price-ish condition → LIMIT at that price; non-price signal it can't monitor →
> execute at MARKET now; never sit waiting for a trigger nothing monitors. First step was the
> read-only check for counteracting "wait for trigger" language — **none found**. Test
> `tests/test_operator_sees_conditions.py` (fail-before/pass-after: stash → operator blind,
> test fails; restore → passes). Full suite 1393 passed, +0 new. **#5's REAL proof is the
> re-fly observation: do plans now fire?** — batched into the end-of-cleanup re-fly.

### 6 · 🟡 JSON-persistence audit
Zero `MutableDict`-wrapped JSON columns model-wide → any in-place JSON edit anywhere can
silently fail to persist (the `modify_genome` class we caught + fixed). *Action:* sweep
all in-place JSON writes; `flag_modified` at each mutation site (not `MutableDict` —
it doesn't track nested edits).

> **AUDIT (co-signed by CW) + CC FIX-PENDING-VERIFY (branch `fix/temperature-history-persist`).**
> Swept all **27** JSON columns (23 models.py + 4 wire) — **zero `MutableDict`-wrapped**.
> Classified every writer across three failure patterns: (a) in-place `.field[k]=v`/`.append`
> — only **2 sites** (`mutations_applied`), both already `flag_modified`'d (the modify_genome
> fix); (b) reassign-**new**-object (`watched_markets` `list(set(...))`, `lineage_manager:45`
> fresh param, `seeds:77` `clamp_genome` result, `orientation:513` fresh watchlist) — safe;
> (c) reassign-**same**-object — **1 real bug: `temperature_evolution.py:121-131`**
> (`temperature_history` kept only its FIRST entry; proven empirically, reproduced by CW).
> Everything else is write-once/read-only. modify_genome was NOT unique but NOT endemic — one
> other instance. *Fix (CW's call: `flag_modified` over the list-copy — explicit intent vs
> identity side-effect):* `flag_modified(agent, "temperature_history")` after the reassign.
> Test `tests/test_temperature_history_persist.py` calls the **real** `evolve()` 3× across
> separate sessions → **1→2→3** (fail-before: stalls at 1). Full suite 1394 passed, +0 new.

### 3 · 🔴 Critic budget-burn / verbosity
Arbiter re-posts approval criteria ~5× and hibernates on budget before a trade fires.
Clock's fixed, so this is now a genuine pacing bug, not a staleness symptom. *Fix:* tune
so a critic can't drain its daily budget on repetition (dedupe / rate-limit / cheaper model).

### 8 · 🟡 Informal pre-approval / pipeline enforcement
Run-1 agents claimed "Arbiter approval on record" with no formal review. May be a symptom
of the clock/roster bugs (routing around a broken pipeline). *Action:* re-observe now
those are fixed; enforce scout→strategist→critic→operator routing only if still present.

### 9 · 🟡 Governance/SIP follow-through
Do passed SIPs actually change colony behavior, or just get debated? Unconfirmed. *Action:* trace.

### 10 · 🟢 Warden "Last: 2h ago"
System page shows Warden last-active "2h ago" while heartbeat says "just now". Stale
status display, or the Warden's last-check timestamp isn't recorded? *Action:* quick check.

### 11 · 🟢 Dashboard inconsistencies
"Haiku Routing 0%" (top) vs "56.9% Haiku" (COST panel); "Regime UNKNOWN" vs "VOLATILE" in
adjacent panels. Display-wiring gaps. *Fix:* single-source each value.

### 12 · 🟢 `test_master_switch_defaults_off`
The session-long "1 failed" — trips on this deployment's `.env` `GENOME_CONTEXT_ENABLED=true`
(committed default is off). *Decision:* make the test assert the **code default**
(green under the opt-in) so "1 failed" stops being noise that could mask a real regression.

---

## DONE this session (crossed off — for reference, not re-litigation)
alembic `.env` (`d4238eb`) · `syndicate.bat` path · dead Sonnet model id + web tz (`575a06d`)
· position-size 1000% display (`5fec5ab`) · price feed 5→14 symbols (`5d648d4`) ·
`modify_genome` real-backend wiring + persistence (`b4b8e96`) · colony roster + compressed
(`2137ca7`,`cd1fb37`) · JJ autostart (`0e2e912`) · boot-genome deadlock (`01bb421`) ·
**DB session pinned to UTC + boot guard (`855552a`)**.

## Parked FEATURES (post-cleanup, NOT bugs)
- Per-agent cost/budget HUD on the dashboard (data mostly exists: `total_api_cost`,
  `thinking_budget_used_today`; add budget-remaining bar, cost/cycle, session vs lifetime).
  *Depends on #1 — session accumulator must reset on `clean_slate`.*
- Optional: make `modify_genome` `evidence` hard-required in the validator.
- **#5(a) — true entry-condition trigger engine** (deferred from #5, deliberately named not
  pretended-done): parse `entry_conditions` into evaluable predicates, watch live
  volume/VWAP/RSI, fire `execute_trade` when a plan's condition is met. Real conditional
  execution — a substantial new subsystem. Only build if/when true triggered entry is wanted.
- **JSON mutation-tracking root fix** (from #6, CW's flag — a *consideration*, not scheduled):
  27 plain JSON columns + zero `MutableDict` wrapping is a standing invitation for the
  silent-drop bug class to recur on every new in-place JSON edit. We've now fixed 2 instances
  reactively (modify_genome, temperature_history). Durable option: wrap the high-churn JSON
  columns in `MutableDict.as_mutable(JSON)` (+ `MutableList`) so tracking is automatic. Bigger
  call (touches column types); logged so it's a decision, not a default.
