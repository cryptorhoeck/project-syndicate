# Governance Read Pattern — Parameter Registry Consumer Guide

**Status:** Phase 9B Tier A baseline (one parameter migrated; Tier B will sweep the rest)
**Last updated:** 2026-05-09

This is the canonical guide for reading SIP-modifiable parameters from the
parameter registry. The registry is the runtime source of truth for any
parameter that agents can vote to change. System components MUST read from
the registry — not directly from `config` — for those parameters, or the
SIPs that vote on them will pass without affecting behavior.

The audit that surfaced this requirement is in `PHASE_9A_INTEGRATION_AUDIT.md`.
The Tier A proof of concept is in `PHASE_9B_TIER_A_KICKOFF.md`.

## When to use `get_param` vs direct `config`

Use `get_param`:
- The parameter is seeded in the parameter registry (see
  `alembic/versions/phase_9b_tier_a_seed_parameter_registry.py` and any
  later seed migrations)
- The parameter appears as a `target_parameter_key` in any current or
  proposed SIP
- The parameter is intended to be tunable by the colony

Use direct `config`:
- The parameter is not in the registry (configuration secrets, infrastructure
  settings, owner-level switches)
- The parameter is part of the Risk Desk (`src/risk/`) — Warden, Accountant,
  Dead Man's Switch — these are immutable by SIP
- The parameter has no operational meaning to agents

When in doubt, check whether the key is seeded in the registry. If it is,
use `get_param`.

## Fallback semantics

`get_param` is designed to be safe in pre-seed and degraded states:

```python
from src.governance.param_reader import get_param

value = await get_param("evaluation.probation_grace_cycles", session,
                       fallback=config.probation_grace_cycles)
```

Resolution order:
1. **Registry hit** — if a row exists for the key, return its `current_value`
2. **Fallback** — if the row is missing (KeyError) or any other exception
   bubbles, return the `fallback` argument
3. **Re-raise** — if no fallback was provided and the read fails, the
   exception propagates

The fallback is the load-bearing safety net for deployment. Fresh databases,
forgotten migrations, or partially-rolled-back states will all fall through
to `config` and continue running. The cost of using the wrong value is small
relative to the cost of crashing.

ALWAYS pass `fallback`. Never call `get_param` without one in production code.

## The hoist-up-one-level pattern

`get_param` is `async`. Many existing read sites are inside synchronous
helpers (e.g. `_apply_probation`, `_apply_survival`). You cannot `await`
from a `def` function without restructuring.

The cheapest fix: hoist the read into the nearest async caller and pass
the value down as a keyword-only argument. Example from
`evaluation_engine.py`:

**Before**

```python
# _apply_probation (sync) — direct config read
def _apply_probation(self, session, agent, result, evaluation):
    agent.probation_grace_cycles = config.probation_grace_cycles
    ...
```

**After**

```python
# _execute_decision (async) — read once, pass kwarg down
async def _execute_decision(self, session, result, pkg, regime, alert_hours):
    ...
    elif final == "probation":
        grace_cycles_default = int(await get_param(
            "evaluation.probation_grace_cycles",
            session,
            fallback=config.probation_grace_cycles,
        ))
        self._apply_probation(
            session, agent, result, evaluation,
            grace_cycles_default=grace_cycles_default,
        )
    ...

# _apply_probation (sync) — accepts kwarg
def _apply_probation(self, session, agent, result, evaluation,
                    *, grace_cycles_default: int):
    agent.probation_grace_cycles = grace_cycles_default
    ...
```

Why hoist instead of converting the sync helper to async? Because async
propagation is viral — flipping `_apply_probation` to async forces every
caller (and every test) to also become async. Threading a kwarg is a local
change. Take the local change every time.

The kwarg should be **keyword-only** (`*,` separator) to make new call sites
explicit and to prevent positional drift if the function signature grows.

## Schema constraint: registry is Float-only

`ParameterRegistryEntry.current_value` and `default_value` are SQLAlchemy
`Float` columns (`src/common/models.py:1685-1688`).

- Booleans store as `0.0` / `1.0`. Cast at the read site: `bool(int(value))`
- Strings cannot be stored. Parameters like `accounting.home_currency` cannot
  use the registry today. (Tier B may add a typed registry; until then,
  string parameters stay in `config`.)

The `validate_proposed_change` method does not enforce a "domain" — a
boolean-shaped parameter (`min=0, max=1`) will accept fractional proposals
like `0.5`. This is a known limitation; Tier B should add per-parameter
validators or a `domain` discriminator.

## Adding a new SIP-modifiable parameter

Two steps. Both must land in the same commit, or the registry diverges from
the read sites.

**Step 1 — Seed the registry.** Add a row to a new alembic migration
following the pattern in `alembic/versions/phase_9b_tier_a_seed_parameter_registry.py`:

```python
SEED_ROWS = [
    (
        "category.parameter_name",      # parameter_key
        "Display Name",                 # display_name
        "Description.",                 # description
        "category",                     # category
        3.0,                            # default_value
        1.0,                            # min_value
        10.0,                           # max_value
        1,                              # tier (1, 2, or 3)
        "unit",                         # unit (or None)
    ),
]
```

**Step 2 — Migrate the read site(s).** Find every `config.parameter_name`
read in `src/`. For each, apply the hoist-up-one-level pattern (or the
direct-await pattern if the site is already async).

Add an AST regression guard in tests asserting that the call is present —
this catches future refactors that silently re-hardcode the value. Pattern
in `tests/test_phase_9b_param_reader_loop.py::test_get_param_actually_called_in_evaluation_engine`.

## Tier conventions

The `tier` column drives validation behavior in `parameter_registry.validate_proposed_change`:

| Tier | Name | Pass threshold | Notes |
|------|------|----------------|-------|
| 1 | Permissive | 60% (varies by maturity) | Most operational tunables. Default tier. |
| 2 | Structural | 75% supermajority | Architecture-level: spawn capital, max agents, eval timing |
| 3 | Forbidden | Unmodifiable | Validator returns `valid=False` with "Tier 3 (Forbidden)" reason |

Tier 3 rows exist as **rejection targets** — they document what the colony
must never vote to change. No system component should read Tier 3 parameter
values from the registry, since they are guaranteed to equal the default
(no SIP can ever modify them). Read Tier 3 invariants from `config` directly.

When in doubt about which tier to assign, the conservative choice is the
higher tier. Tier B sweeps may downgrade parameters as operational
experience accumulates.

## What's NOT in the registry today

- Risk Desk parameters (`src/risk/`) — Warden thresholds, Accountant rates,
  DMS heartbeat. Read from `config` only.
- String-valued parameters (Float-only schema constraint).
- Per-agent configuration — anything keyed by agent ID belongs on the Agent
  row, not the registry.
- Phase rollout flags — feature gates belong in `config` so they can be
  flipped instantly without governance overhead.
