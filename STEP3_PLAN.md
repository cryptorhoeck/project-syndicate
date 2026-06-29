# STEP 3 PLAN — JJ Genome Seed + Genome→Prompt Wiring

**Status:** APPROVED IN PRINCIPLE (decisions D1–D4 below). Build is gated: 3a (inert)
proceeds; 3b (the colony-wide flip) ships default-OFF and is reviewed before any flip.
**Companion to:** `projects/JJ Gorilla/jj-bot/WEAVE_PROPOSAL.md`. **Date:** 2026-06-28.

## The central realization

Step 3 is two pieces with very different blast radius:

- **Part A — wire genome → prompt (colony-wide).** Activates the *latent*
  `genome_to_context_string` so each agent's genome numbers reach its prompt. Changes
  how **every** agent reasons. This is the real subject of Step 3.
- **Part B — seed a JJ genome (scoped).** One Scout starts with JJ's RSI/momentum/
  volume thresholds. Inert without Part A.

## The coupling finding (decides how much ceremony Part A needs)

**What reads the genome today?** Verified by grep across `src/`:
- The genome's **trading** params (`signal_generation.*`, `risk_management.*`,
  `market_selection.*`, `plan_construction.*`) are read by **no** machine/strategy/
  selection/trading path. They appear only in `genome_schema.py` (bounds) and as an
  example string in `roles.py`'s dead `modify_genome` action.
- The **only** genome field reaching behavior is `behavioral.communication_expressiveness`
  (Agora message verbosity, `context_assembler.py:286-299`).
- `modify_genome` is dead (routes to a no-op broadcast), so genome only moves by
  reproduction+mutation — whose fitness is unrelated to these unread values.

**Conclusion:** the prompt is the genome's **first** behavioral channel for trading, and
current trading-genome values are **unselected drift**. A global Part A flip would inject
that drift into every prompt at once → risk of a colony-wide behavioral lurch. Hence the
conservative gating below.

## Safety story (verified — all green)

1. **Paper-only.** `trading_mode="paper"`; live raises `NotImplementedError`
   (`execution_service.py:1167`). Worst case of anything here = bad *paper* trades.
2. **Warden-gated, fail-closed, genome-blind.** Every trade (any agent) →
   `_handle_execute_trade` → `execute_market_order` → `warden.evaluate_trade`, which
   rejects if not "approved" and rejects if Warden is absent. It judges trade
   size/capital/alert state — never genome/type/name. No genome bypass.
3. **Capital is rank/prestige-driven** (`treasury.py`), independent of genome; execution
   size still hard-capped by `PER_AGENT_MAX_POSITION_PCT`.
4. **Params clamped to bounds** at every machine path; a hand-authored seed is explicitly
   run through `clamp_genome` + `validate_genome` before persist.

**Key distinction:** Part A's risk is **behavioral** (colony reasoning shift), not a
safety-gate weakening. The Warden wall is untouched.

## Decisions (D1–D4)

- **D1 — Part A gating.** Master kill-switch `config.genome_context_enabled` (default
  **OFF**) AND per-agent `AgentGenome.context_enabled` (default False). Genome block shows
  only if both true. First flip = global ON + `context_enabled` for the **JJ Scout only**;
  capture baseline (P&L, survival rate, behavioral diversity) before flipping; widen to a
  post-flip cohort later. Live control group, not a blind global toggle.
- **D2 — Seed helper.** `seed_agent_genome(...)` clamps+validates before persist; the test
  feeds an out-of-range seed and proves it's clamped (guard tested, not happy path).
- **D3 — One JJ Scout first.** Then maybe a dynasty.
- **D4 — `modify_genome` out of scope.** Flagged as dead; fix separately.

## Sub-step sequence (each testable; Part A reversible + default-off)

- **3a — JJ genome data + seed helper (INERT; this commit).** `src/genome/seeds.py`:
  JJ Scout genome values + `seed_agent_genome` (clamp+validate+persist). Tests incl. the
  out-of-range clamp guard. No prompt wiring, no spawn, no behavior change.
- **3b — Gated genome→prompt wiring (the flip; reviewed closely).** Add
  `config.genome_context_enabled` (default OFF) + additive `AgentGenome.context_enabled`
  column (additive migration, tested down). Wire `genome_to_context_string` into
  `_build_system_prompt`, gated on both. Tests: off→no block, on+enabled→block, None-safe.
- **3c — Spawn one JJ-seeded Scout** with `context_enabled=True`; capture baseline; flip;
  observe.
- **3d (later) —** JJ dynasty; wire `modify_genome` properly.

## JJ Scout genome (3a values, mapped from jj-bot thresholds)

`signal_generation`: rsi_oversold 30, rsi_overbought 70 (JJ RSI bands); volume_spike_threshold
2.0 (JJ 2x); momentum_threshold_pct 0.5 (JJ's native 0.3% is below the genome floor of 0.5);
contrarian_bias 0.3 (VWAP mean-reversion lean). Plus reasonable `market_selection` (volume
focus, crab-regime weight high for mean-reversion) and `behavioral` (tool_execution_frequency
0.6 — JJ leans on its own analysis). All within `GENOME_BOUNDS`.

## Step 3c — the deliberate flip (prepared; switch NOT thrown)

3c provides the mechanics + the pre-flip checks; the actual master flip is an operational
step taken when the colony runs, after this review.

### (1) What the JJ Scout will actually see (the new prompt surface)

The genome block renders **trading sections only** — the behavioral knobs
(sip_propensity, communication_expressiveness, alliance_willingness, …) are excluded; an
agent must not see its own meta-behavioral profile framed as trading instinct. Rendered:

```
YOUR STRATEGY GENOME (your evolved trading instincts — let these bias your decisions):
  market_selection.volatility_preference: 0.500
  market_selection.volume_threshold_multiplier: 2.000
  market_selection.max_concurrent_markets: 3
  market_selection.regime_weights.{bull,bear,crab,volatile}: 0.6/0.6/0.9/0.5
  signal_generation.min_confidence_to_broadcast: 5
  signal_generation.momentum_threshold_pct: 0.500
  signal_generation.volume_spike_threshold: 2.000
  signal_generation.rsi_oversold: 30
  signal_generation.rsi_overbought: 70
  signal_generation.contrarian_bias: 0.300
```

### (2) Cohort-of-one stays a cohort of one (tested invariant)

`context_enabled` is a separate column, NOT in `genome_data`. `genome_manager.create_genome`
builds offspring genomes without setting it → offspring default to `context_enabled=False`.
So the JJ Scout's lineage inherits its *genome values* (JJ-flavored) but **not** the
enabled flag — offspring stay dark. Locked by
`test_offspring_do_not_inherit_context_enabled`. Enabling the master switch never silently
grows the experiment.

### (3) Revert criterion — the trip-wire (decided BEFORE the flip)

Capture a baseline (the window: the JJ Scout's first ~3 evaluation cycles, or ~3 days),
then flip and observe. **Flip the master switch back OFF and investigate if ANY:**
- **Mechanical:** the JJ Scout's OODA output-validation failure rate is materially above
  peers, or prompt-assembly exceptions reference the genome block.
- **Behavioral:** the JJ Scout produces degenerate/incoherent actions (qualitative, from
  the Agora) — e.g. spamming, ignoring the colony, nonsensical trades.
- **Safety:** any Warden circuit-breaker / Black-Swan event coincident with the flip
  (precautionary — flip off, then determine causation).
- **Colony (matters when widening past the cohort-of-one):** behavioral diversity collapses
  below `config.genome_diversity_low_threshold` (0.3) attributable to the change.

**NOT a trip-wire:** a clean death-by-selection of the JJ Scout — that's a valid Darwinian
outcome (the seeded genome simply wasn't fit), bounded to one agent's paper capital.

Exact P&L thresholds calibrate against the captured baseline (the Arena has not yet run, so
there is no historical baseline to set hard numbers against today).

### 3c mechanics (bytes, inert until the master flip)
- `seed_agent_genome(agent_id, jj_scout_genome(), "scout", db)` — clamp+validate+persist.
- `enable_genome_context(agent_id, db)` — set the per-agent flag (inert while master OFF).
- Operational flip (when ready): set `GENOME_CONTEXT_ENABLED=true`, observe against the
  trip-wire, revert instantly by unsetting it.
