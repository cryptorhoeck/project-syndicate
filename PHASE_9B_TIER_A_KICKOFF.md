# Phase 9B Tier A — Parameter Registry Read Path (Proof of Concept)

**Branch:** hotfix/phase-9b-tier-a-param-reader
**Off:** main (commit 63fdfa4)
**Estimated scope:** Single CC session with one Critic round

## Context

PHASE_9A_INTEGRATION_AUDIT.md (commit 63fdfa4) found that the Parameter Registry is wired on the write side (proposing, validating, voting, applying, logging) but completely dead on the read side. `get_param()` is defined in `src/governance/param_reader.py` and called nowhere. Plus the registry has never been seeded — the migration creates the tables but no parameters exist as rows.

So the actual current state of parameter-modifying SIPs:
1. Agent emits `propose_sip` with `target_parameter_key="evaluation.honesty_weight"` and a new value
2. `_handle_propose_sip` calls `parameter_registry.validate_proposed_change()`
3. The validator queries the (empty) registry for that key
4. Returns `{"valid": False, "reason": "Parameter does not exist"}`
5. Auto-rejected before debate even opens

Parameter SIPs can't be created, let alone implemented. The only SIPs that work today are general-proposal SIPs with `target_parameter_key=NULL`.

This Tier A build proves the full loop end-to-end with ONE parameter, ONE read site, and the regression-guard pattern. Tier B (full sweep across remaining config-read sites) is a follow-up kickoff after Tier A establishes the working pattern.

## Goal

End-to-end proof: an agent can propose a SIP that changes `evaluation.probation_grace_cycles` from 3 to 5, the SIP advances through debate and voting, the lifecycle implements it, and the *next* `_apply_probation` call reads the new value via `get_param()` instead of `config.probation_grace_cycles`.

## Why probation_grace_cycles

Picked as the proof-of-concept parameter because:
- Numeric int with a 1-10 range — exercises the registry's min/max validation pipeline (booleans skip that)
- Single read site at `evaluation_engine.py:807` inside `_apply_probation` — minimal surface area
- Tier 1 (permissive) — doesn't touch the Risk Desk safety layer
- Behaviorally meaningful: it controls how many cycles an underperforming agent gets before being killed. Plausible democratic parameter.
- Test observability is strong: 3 vs 5 produces visibly different runtime behavior in tests, not just a binary flip that could mask int/bool conversion bugs

**Important naming distinction:** `Agent.probation_grace_cycles` is a model column tracking the *current* grace count of a specific agent (decrements over time). The registry parameter `evaluation.probation_grace_cycles` is the *initial value* assigned when an agent enters probation. Same name, different concept. Tests and docstrings must be unambiguous about which is which.

## What gets built

### 1. New migration: seed parameters

`alembic/versions/<new_id>_phase_9b_seed_parameter_registry.py`

Inserts five parameters into `parameter_registry_entries` covering all three tiers. The five-parameter seed list is intentionally small — Tier A is proof of concept, not a full registry buildout. Tier B will expand the list.

Parameters to seed:

| parameter_key | category | tier | default | min | max | description |
|---|---|---|---|---|---|---|
| `evaluation.probation_grace_cycles` | evaluation | 1 | 3 | 1 | 10 | Cycles an underperforming agent gets before being killed. Initial value for new probationers. |
| `evaluation.first_eval_leniency` | evaluation | 1 | 1 | 0 | 1 | If true, new agents get leniency on first evaluation. Boolean stored as 0/1. |
| `colony.min_spawn_capital` | colony | 2 | 50 | 25 | 200 | Minimum capital required before Genesis can spawn a new agent. Tier 2 — structural. |
| `colony.max_agents` | colony | 2 | 8 | 3 | 20 | Hard cap on simultaneous active agents. Tier 2 — structural. |
| `colony.darwin_pressure_enabled` | colony | 3 | 1 | — | — | If true, natural selection / agent termination is active. **TIER 3 FORBIDDEN.** Disabling this breaks the experiment's core premise. Seeded so SIPs targeting it get explicitly rejected at validation, not silently accepted. |

The Tier 3 choice deserves explanation: `colony.darwin_pressure_enabled` is plausible (an agent might genuinely propose it as a survival move) but architecturally must never be SIP-modifiable, since disabling Darwinian selection invalidates the experiment. This is exactly the shape Tier 3 exists to prevent. Importantly, no existing system component reads this parameter — it's seeded purely as a rejection target. (This avoids the architectural smell of seeding a Warden parameter the Warden never consumes.)

Migration must include `op.bulk_insert()` or equivalent populating rows with `current_value = default_value` at seed time.

`down_revision` must point to the current alembic head — verify with `alembic heads` before writing.

Down migration: delete the five seed rows by parameter_key. Do NOT drop the table.

### 2. Migrate the read site to use get_param

The single call site is `evaluation_engine.py:807`:

```python
agent.probation_grace_cycles = config.probation_grace_cycles
```

Inside `_apply_probation()`, which is sync (`def`, not `async def`).

`get_param()` is async, so calling it from sync code requires architectural choice. The two options are: make `_apply_probation` async (and propagate up the call chain), or hoist the read into the nearest async caller and pass the value down as a kwarg.

**Use the hoist-up-one-level pattern.** Read the parameter once in the async caller, pass it down as `grace_cycles_default: int` kwarg. This pattern will recur across Tier B (most config reads in Genesis, Accountant, etc. live inside sync helpers called from async cycle drivers), so getting it right here matters.

**Caller analysis:** `_apply_probation` is called from `evaluate_batch` (async) and possibly other async paths. CC must trace the actual call sites and identify the correct hoist point. The pattern:

**Before:**
```python
# In _apply_probation (sync):
agent.probation_grace_cycles = config.probation_grace_cycles
```

**After:**
```python
# In evaluate_batch or whichever async caller drives _apply_probation:
from src.governance.param_reader import get_param

grace_cycles_default = int(await get_param(
    "evaluation.probation_grace_cycles",
    db_session,
    fallback=config.probation_grace_cycles,
))

# ... pass to _apply_probation as kwarg:
self._apply_probation(agent, grace_cycles_default=grace_cycles_default)

# In _apply_probation (sync, with new kwarg):
def _apply_probation(self, agent: Agent, *, grace_cycles_default: int) -> None:
    agent.probation_grace_cycles = grace_cycles_default
```

Function signature changes propagate to any tests calling `_apply_probation` directly. Update those test call sites too.

The fallback semantics matter: if the registry has no row for the key (fresh DB, registry not seeded), `get_param` falls back to `config.probation_grace_cycles`. The system continues to function exactly as today. Migration only changes behavior when the registry contains a row.

**Related call sites for `first_eval_leniency` (lines 411, 585, 591) are NOT migrated in Tier A.** Those touch both sync (`_pre_filter`) and async (`_execute_decision`) functions and would benefit from a more architectural pass. Defer to Tier B. Seeding `evaluation.first_eval_leniency` in this migration is intentional — it gives Tier B a ready target without blocking Tier A on the more complex hoist.

### 3. Validate boolean handling in the registry

The seed list includes `evaluation.first_eval_leniency` as a 0/1 boolean. Even though Tier A doesn't migrate its read site, the registry must accept it correctly. Read `parameter_registry.validate_proposed_change()` and confirm that boolean (0/1) parameters with min=0/max=1 ranges work through the validation pipeline. If there are int-vs-float assumptions that break this, fix them in this same commit. If the validator handles it cleanly, no code change needed — just confirm in the report.

### 4. Tests

`tests/test_phase_9b_param_reader_loop.py` (new file) with the following:

**Production-path tests (load-bearing):**

- `test_probation_grace_cycles_default_when_registry_seeded` — seed registry with default value (3), invoke evaluation engine through its production path with a probation-eligible agent, assert the agent's `probation_grace_cycles` field is set to 3

- `test_probation_grace_cycles_changed_after_sip_implementation` — seed registry with default (3), simulate a SIP that changes the value to 5 via `parameter_registry.apply_change()`, then invoke evaluation engine with a fresh probation-eligible agent, assert the agent's `probation_grace_cycles` field is set to 5

- `test_probation_grace_cycles_falls_back_to_config_when_unseeded` — empty registry, invoke evaluation engine with probation-eligible agent, assert it reads `config.probation_grace_cycles` via the fallback path. Proves fresh systems continue working before seeding.

**Integration test (end-to-end):**

- `test_full_sip_loop_changes_probation_behavior` — agent proposes SIP targeting `evaluation.probation_grace_cycles` value 5 → lifecycle advances through debate, voting (with at least one vote to pass), tallied, implemented → assert registry row updated to 5 → invoke evaluation engine on a probation-eligible agent → assert agent's `probation_grace_cycles` field is 5, not 3. This is the headline test proving the system works end-to-end.

**Regression guards:**

- `test_get_param_actually_called_in_evaluation_engine` — AST guard inspecting `evaluation_engine.py` source for the `get_param("evaluation.probation_grace_cycles"` call. Future refactors that revert to direct `config` reads will break this test.

- `test_tier_3_parameter_rejected_at_validation` — agent proposes SIP targeting `colony.darwin_pressure_enabled`, assert validation rejects with reason mentioning Tier 3 / Forbidden. Prevents the seed list from accidentally allowing termination-disabling.

Six new tests total. Test count: 1106 (current) + 6 (new) = **1112 minimum**.

### 5. Documentation

Create `docs/governance_read_pattern.md` documenting:
- When to use `get_param` vs direct `config.X`
- The fallback semantics (registry → config default → fail loud)
- The hoist-up-one-level pattern for migrating sync call sites
- How to add a new SIP-modifiable parameter (seed migration + read-site migration)
- Tier conventions (1=permissive, 2=structural, 3=forbidden)

Add a one-line pointer in `CLAUDE.md` directing future sessions to `docs/governance_read_pattern.md`. Keeps CLAUDE.md focused on what fresh sessions need for orientation.

Update `CHANGELOG.md` with the Phase 9B Tier A entry covering the seed migration, read-site migration, hoist pattern, and tests.

## Constraints

- Migrate ONLY `evaluation.probation_grace_cycles` reads in this commit. Other config reads stay as-is. Tier B handles them.
- Do NOT modify `parameter_registry.py` or `sip_lifecycle.py` core logic. They're working as designed; this build is purely consumer-side wiring (with the one boolean-handling check above).
- Do NOT modify the Risk Desk layer in any way.
- The Tier 3 seed `colony.darwin_pressure_enabled` exists ONLY as a rejection target. No production code reads it. Do not add a consumer for it in this build.
- No new dependencies on packages outside what's already in the project.
- All tests except the integration end-to-end test use SQLite for the in-memory test fixture per the project's standard pattern. The integration test can use the dev Postgres if available (otherwise skip with a clear `pytest.skip`).

## Verification protocol

Before writing any code, verify:
1. `src/governance/param_reader.py:19` is `async def get_param(key, db_session, fallback=None)` — signature matches what the migration call sites will use
2. Current alembic head matches what main has (run `alembic heads`)
3. `parameter_registry.validate_proposed_change()` handles numeric int parameters with min/max ranges correctly — read the function and confirm
4. `evaluation_engine.py:807` still reads `config.probation_grace_cycles` and is still inside `_apply_probation` (which is still sync). Trace the actual async caller — likely `evaluate_batch`, but verify rather than assume.
5. Confirm whether other call sites of `_apply_probation` exist (test files, other engine paths). All callers need the new kwarg.

If any assumption breaks under verification, STOP and report back to War Room rather than working around it. Same protocol that caught five directive errors in last night's session.

## What's NOT in this build

These are deliberately deferred to keep Tier A scope tight:

- **`first_eval_leniency` migration** — seeded but not consumed. Deferred to Tier B due to mixed sync/async call sites.
- **Other parameter migrations** (prestige thresholds, budget multipliers, post-mortem timing, etc.) — Tier B kickoff after Tier A ships
- **Test coverage retrofit** for `maturity_tracker`, `parameter_registry`, `sip_lifecycle` — separate follow-up
- **Vote weight tallying verification** — separate 5-minute task per the audit
- **Genesis maturity-aware posture full trace** — defer until live observation
- **Dashboard governance widget verification** — defer until live observation

These are all logged in PHASE_9A_INTEGRATION_AUDIT.md and the deferred-tracker, not lost.

## Definition of done

1. Migration applied cleanly, registry contains five seed rows
2. Read site at `evaluation_engine.py:807` migrated to `get_param` via hoist-up-one-level pattern
3. All 6 tests pass
4. AST regression guard test passes
5. Tier 3 rejection test passes
6. Full test suite passes (1106 prior + 6 new = 1112 minimum)
7. CLAUDE.md, `docs/governance_read_pattern.md`, and CHANGELOG.md updated
8. CC submits to War Room + Critic for review BEFORE merge

**Manual smoke verification (Andrew's responsibility, after CC submits):** seed a Postgres dev DB with the new migration, propose a SIP via direct DB write, advance lifecycle manually via Python REPL, observe registry row update, observe next evaluation cycle reading new value. CC does NOT do this — REPL-based smoke tests don't replay cleanly. CC's responsibility ends with the test suite passing.
