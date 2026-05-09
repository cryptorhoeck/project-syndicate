# Phase 9A Integration Audit

**Date:** 2026-05-08
**Audited by:** War Room (with Andrew)
**Scope:** Phase 9A — SIP Voting + Colony Governance, full integration state
**Status:** Audit complete. One critical wiring gap found, two minor concerns deferred for verification.

---

## TL;DR

Phase 9A is **substantially built and mostly integrated** — but its primary mechanism is broken in a structurally identical way to the Library reflection bug. Parameter-modifying SIPs pass, log, and announce changes that nothing actually consumes. The colony has a working democratic vote on parameters that no system component reads. This is theatrical governance, not real governance.

The fix is small in scope (a `get_param` consumer pattern across the codebase) but high in coverage (every SIP-modifiable parameter site needs to be migrated). Tier 1 (substrate) and Tier 2 (lifecycle, agent surface) are clean. The gap is in the *post-implementation read path* — the missing half of the SIP system.

---

## What's built and integrated

### Tier 1 — Substrate (BUILT, INTEGRATED)

**`src/governance/maturity_tracker.py`**
- `ColonyMaturityTracker` class with `MaturityStage` enum (NASCENT, DEVELOPING, ESTABLISHED, MATURE) and `MaturityConfig` dataclass
- Each stage has its own `voting_period_hours`, `pass_threshold`, `structural_threshold`, `require_cosponsor`
- Methods: `get_config`, `compute_stage`, `update`, `get_debate_end_time`, `get_voting_end_time`
- **Integration confirmed:** `genesis.py:447` constructs the tracker, `genesis.py:448` calls `tracker.update()` each Genesis cycle. Stage transitions DO fire and DO post to Agora when crossings occur.

**`src/governance/parameter_registry.py`**
- `ParameterRegistry` class with full CRUD-plus-validation surface
- Six async methods: `get_value`, `get_parameter`, `get_all_parameters`, `validate_proposed_change`, `apply_change`, `get_drift_summary`
- **Integration confirmed:** Constructed in three places — `action_executor.py:790` for proposal validation, `genesis.py:454` for SIPLifecycleManager dependency, `param_reader.py:16` as the global instance for the (uncalled) reader

### Tier 2 — Lifecycle + Agent Surface (BUILT, INTEGRATED)

**`src/governance/sip_lifecycle.py`**
- `SIPLifecycleManager` class with state machine: `proposed` → `debate` → `voting` → `tallied` → `implemented`
- Public: `advance_all_sips`, `initiate_sip`
- Internal: `_advance_to_voting`, `_tally_votes`, `_implement_sip`, `_validate_eval_weights`, `_post_agora`
- **Integration confirmed:** `genesis.py:456` calls `await sip_lifecycle.advance_all_sips(gov_session)` each cycle. Lifecycle DOES advance.

**`src/governance/vote_weights.py`**
- Single function `get_vote_weight(prestige_title)`
- Imported and used at `action_executor.py:830` inside `_handle_vote_on_sip`
- *Not yet verified:* whether `_tally_votes` in sip_lifecycle.py weights votes by prestige or just counts them. (See deferred verification below.)

**Agent action surface in `src/agents/roles.py` and `src/agents/action_executor.py`**
- `propose_sip`, `vote_on_sip`, `debate_sip`, `cosponsor_sip` defined in roles, dispatched in action_executor
- `_handle_propose_sip` at `action_executor.py:753` validates against parameter registry, creates SIP row, calls `lifecycle.initiate_sip()` to kick off the lifecycle
- All four action types are reachable by agents via the normal output validator → action executor pipeline

### Tier 3 — Integration (PARTIALLY BUILT)

**Genesis maturity-aware behavior**
- `genesis.py:1276` imports `MATURITY_CONFIGS, MaturityStage` — Genesis is reading maturity config somewhere in its cycle
- *Not yet fully traced* whether maturity adapts Genesis posture (intervention authority, rapid evaluation, etc.) per the original kickoff doc, or only adapts SIP-related thresholds
- Acceptable to defer until live observation surfaces concrete behavior

**Web dashboard governance widget**
- `src/web/routes/api_governance.py` exists and is registered in `app.py:75`
- Dashboard has SIP-related queries (debates, etc.)
- *Not audited:* visual quality, completeness, what data flows to which widget. Defer until live observation.

---

## Critical finding: the Parameter Registry is write-only

### What we expected

The whole point of the parameter registry is to make system parameters tunable through democratic SIPs. Per the original Phase 9A design:

1. Agent proposes "change `evaluation.honesty_weight` from 0.3 to 0.5" via `propose_sip`
2. Lifecycle advances through debate → voting → tallied → implemented
3. `_implement_sip` calls `parameter_registry.apply_change()` which updates the `parameter_registry_entries` row
4. **The next time the evaluation engine evaluates an agent, it reads the new weight from the registry** via `get_param("evaluation.honesty_weight", db_session, fallback=config.honesty_weight)`

That last step — the consume-side read — is what makes the SIP have *operational* effect, not just *accounting* effect.

### What we found

`get_param()` is **defined in `src/governance/param_reader.py` and invoked nowhere in the entire production codebase.** Verified two ways:

```
findstr /s /m "param_reader" src\*.py
→ ZERO HITS (no production module imports param_reader)

findstr /s /n /c:"get_param(" src\*.py
→ ONE HIT: param_reader.py:19 (the function definition itself)
```

No system component calls `get_param()`. Which means:

- A SIP that changes `evaluation.honesty_weight` updates the registry table
- The change_log records it
- The Agora announces it
- The dashboard shows it
- **The evaluation engine continues using `config.honesty_weight` from `.env` / config defaults, ignoring the change forever**

### Why this is the same shape as last night's bugs

We spent yesterday closing wiring gaps where one half of an integration was built but the consumer half was missing or broken. The Library reflection bug, the DMS dead heartbeat, the Operator halt cross-process gap — all the same shape: producer wired, consumer missing/broken, no observable error, system runs in degraded mode that nobody sees.

This is structurally identical. The "producer" (SIP lifecycle) is fine. The "consumer" (every system component that reads SIP-modifiable parameters) doesn't exist.

The good news: this is the *kind* of bug we now have a strong pattern for. The fix is conceptually clear (migrate read sites to `get_param`); the work is in coverage (find every site, migrate it, prove the migration with tests).

### Severity

**HIGH for system semantics, LOW for current operations.**

- HIGH because parameter-modifying SIPs are *the* mechanism that makes governance operationally real. Without it, the colony has a vote system that records but doesn't change behavior.
- LOW because no SIP has actually been proposed against a tunable parameter yet. The bug is dormant until Phase 9A starts being used.

But waiting until the colony has been running for weeks before discovering its democracy is theater would be embarrassing.

---

## Two minor verifications deferred

### Vote weight tallying

`vote_weights.get_vote_weight()` is imported in `_handle_vote_on_sip` (action_executor:830), so when an agent votes, their weight is recorded with the vote. But we haven't verified whether `_tally_votes` in sip_lifecycle.py *uses* those weights when computing whether a SIP passes its threshold. If not, votes are weighted on insertion but tallied raw — partial fix that defeats the prestige-weighting design.

**Action:** quick read of `sip_lifecycle._tally_votes` to confirm it sums weights, not raw vote counts. 5-minute verification, not blocking the audit.

### Test coverage

There's exactly one test file (`tests/test_sip_voting.py`, ~29KB) covering Phase 9A. No tests for `maturity_tracker`, `parameter_registry`, or the lifecycle's advancement loop. Last night's wiring audit added 115 tests; Phase 9A added zero in that round.

If the param_reader fix happens in a future session, that session must include test coverage for both the fix and the surrounding code that wasn't covered originally. Otherwise the same class of bug will silently regrow.

**Action:** included in the proposed Phase 9B work below, not blocking the audit.

---

## Action items

Sorted by priority. Each can become a kickoff directive or a deferred-tracker entry as appropriate.

### P0 — Phase 9B: Parameter Registry Read Path

**The big one.** Three sub-tasks:

1. **Identify all read sites.** Sweep the codebase for hardcoded reads of parameters that should be SIP-modifiable. Candidates include:
   - `evaluation_engine.py` reading evaluation weights from `config`
   - `genesis.py` reading SIP costs, voting thresholds, and other governance parameters from hardcoded values
   - `accountant.py` reading thinking-tax rates from config
   - Any other subsystem the kickoff doc identified as "tunable"

2. **Migrate to `get_param` pattern.** Each read site becomes:

   ```python
   from src.governance.param_reader import get_param
   value = await get_param("eval.honesty_weight", db_session, fallback=config.honesty_weight)
   ```

   Fallback semantics ensure that if the registry hasn't been seeded with that key yet, the system uses the config default and continues without breaking.

3. **Add the regression guards.** Following the patterns established in last night's wiring audit:
   - Production-path tests that prove a SIP-modified parameter actually changes runtime behavior end-to-end
   - AST guards against future refactors silently re-hardcoding the values
   - Integration test exercising the full proposed → debated → voted → implemented → consumed cycle

This is meaningful work — probably 2–3 hours of CC time with at least one Critic round. Should be its own Phase 9B kickoff doc when we're ready to ship it.

### P1 — Verify vote weight tallying

5-minute read of `sip_lifecycle._tally_votes`. If it sums weights, fine. If it counts raw votes, add it to the Phase 9B scope.

### P2 — Test coverage retrofit

Add coverage for `maturity_tracker`, `parameter_registry`, and the lifecycle advancement loop. Not just unit tests — production-path tests that exercise the integration. Roll into Phase 9B or a follow-up.

### P3 — Defer until live observation

- Genesis maturity-aware posture: full trace of how `MATURITY_CONFIGS` and `MaturityStage` are consumed in `genesis.py`. Defer until colony runs and we can see actual behavior.
- Dashboard governance widget completeness: check what the user sees, what's missing, what's broken. Defer until live observation.

---

## Calibration notes (worth holding for next session)

**Project memory was stale.** Last night's `CURRENT_STATUS.md` said "Phase 9A kickoff written but not yet executed." In reality Phase 9A was substantially built before this session. This is the kind of drift that will keep happening — the project moves faster than memory snapshots can keep up. The lesson: **always verify the actual codebase before designing new work.** The first 30 minutes of this session were saved by running `dir /s /b src\governance` instead of writing a "build Phase 9A from scratch" kickoff doc.

**CMD's `findstr` regex is fragile.** Multiple times this session, `\|` alternation and quoted patterns silently returned empty when the underlying data was non-empty. Pattern: when a search returns surprising emptiness, re-run with simpler patterns one concept at a time before drawing conclusions. The first round of "governance is orphaned" findings was a regex artifact; the verification commands surfaced the real picture.

**Same bug class, different layer.** The Library reflection bug, the wiring-audit subsystems, and the Phase 9A param_reader gap are all the same shape: producer wired, consumer missing, no observable error. The colony has a *strong tendency* to develop these gaps because each subsystem can be built and tested independently. Cross-subsystem integration is where bugs live. **Future kickoffs should explicitly require a "consumer-side audit" as part of the build, not as a follow-up.**

---

## State as of this audit

- **Branch:** main
- **Latest commit:** `d1521f7` (last night's CURRENT_STATUS.md handoff doc)
- **Test count:** 1106 passing
- **Working tree:** clean
- **Phase 9A code surface:** complete except for read-path consumers (this audit)

End of audit.
