# Arena Trading Service [NO SERVICE] — Diagnosis

**Type:** Read-only diagnostic. No code changes in this session.
**Date:** 2026-05-03
**Investigator:** Claude Code
**Subject Arena run:** 2026-04-14 23:57 → 2026-04-18 01:01 (per `ARENA_LAST_RUN_AUDIT.md`)

---

## 0. Correction on counts (factual)

The task framing said "[NO SERVICE] 365 times in a row." The DB does not support that figure. Authoritative numbers from `messages` + `agent_cycles`:

| Metric | Count |
|---|---|
| `[NO SERVICE]` messages on `trade-signals` channel (Operator-authored) | **18** |
| `agent_cycles.action_type = 'execute_trade'` (1:1 with the messages above) | 18 |
| **Total `agent_cycles` rows across all roles and action types** | **365** |

**365 is the total cycle count across the whole colony for the run.** Of those, the Operator attempted `execute_trade` 18 times — every single attempt fell into the `[NO SERVICE]` fallback. The other 347 cycles were the rest of the colony doing other work (Scout opportunities, Strategist plans, Critic verdicts, reflections, SIP debates, etc.).

The structural failure is real; the tally is just smaller than reported. The colony only got as far as 18 trade attempts before the Operator started hibernating in protest and proposing SIP #8 to fix the infrastructure.

| Pipeline stage | Count |
|---|---|
| Opportunities (`broadcast_opportunity`) | 22 |
| Plans submitted (`plans` table) | 7 — 2 approved, 4 rejected, 1 in revision |
| `execute_trade` actions attempted | 18 |
| `[NO SERVICE]` outcomes | 18 (100%) |
| Orders booked | 0 |
| Positions opened | 0 |

---

## 1. Code-path trace — where `[NO SERVICE]` is raised

### 1a. The exact site

`src/agents/action_executor.py:338-347`

```python
async def _handle_execute_trade(
    self, agent: Agent, action_type: str, params: dict
) -> ActionResult:
    """Handle trade execution via TradeExecutionService."""
    if not self.trading:
        # Fallback: log-only mode when no trading service configured
        logger.info("trade_no_service", extra={"agent_id": agent.id, "action": action_type})
        summary = self._summarize_action(action_type, params)
        await self._post_to_agora(agent, "trade-signals", f"[NO SERVICE] {summary}", action_type, params)
        return ActionResult(success=False, action_type=action_type, details="No trading service configured")
    ...
```

The fallback fires whenever `self.trading is None`. The same defensive pattern exists at `action_executor.py:442` for `_handle_close_position`.

### 1b. What's expected

`self.trading` is meant to be a `TradeExecutionService` instance — the abstract base at `src/trading/execution_service.py:79`. In paper-trading mode (current `.env` setting), the concrete class is `PaperTradingService` (`src/trading/execution_service.py:132`).

The service is not a daemon. It's an **in-process Python object**. The factory function `get_trading_service(...)` at `src/trading/execution_service.py:644` constructs the right concrete class given configuration:

```python
def get_trading_service(
    db_session_factory, price_cache=None, slippage_model=None,
    fee_schedule=None, warden=None, redis_client=None, agora_service=None,
) -> TradeExecutionService:
    if config.trading_mode == "paper":
        return PaperTradingService(...)
    else:
        raise NotImplementedError(...)
```

### 1c. How it's supposed to be started

It is **not** a separate process. There is no Redis key, no HTTP endpoint, no DB row, no service registration. The factory should be called once at agent-runner startup, the returned object handed into the `ThinkingCycle`, which then hands it into `ActionExecutor`'s `trading_service` parameter.

That dependency chain is **never connected** in production code. Trace:

| File | Line | Code | Problem |
|---|---|---|---|
| `scripts/run_agents.py` | 117 | `ThinkingCycle(db_session=cycle_session, claude_client=claude, redis_client=redis_client, agora_service=agora, config=config)` | No trading service is constructed or passed. |
| `src/agents/thinking_cycle.py` | 59-67 | `__init__(self, db_session, claude_client, redis_client=None, agora_service=None, warden=None, config=None)` | The `ThinkingCycle` constructor does not even accept a `trading_service` parameter. There is no surface for the runner to inject through. |
| `src/agents/thinking_cycle.py` | 80 | `self.action_executor = ActionExecutor(db_session, agora_service, warden)` | Three positional args. The `trading_service` slot defaults to `None`. |
| `src/agents/action_executor.py` | 41 | `def __init__(self, db_session, agora_service=None, warden=None, trading_service=None):` | The slot exists, but nothing reaches it through this code path. |

Confirmed via grep: **no production source file calls `get_trading_service(...)` or constructs `PaperTradingService(...)` directly.** The only references in the repo are the factory itself, four test files, the kickoff doc, and the CHANGELOG entry that announced Phase 3C shipped.

---

## 2. Root-cause category

**(d) — A service that exists but was never actually fully wired up.**

| Option | Verdict |
|---|---|
| (a) Auto-started by `run_arena.py` but isn't | ❌ — not a daemon. There is nothing to "start" at the process level. `run_arena.py`'s PROCESSES dict is correctly silent on it. |
| (b) Manual startup that wasn't documented | ❌ — no manual startup applies; this is purely an in-process Python object. |
| (c) Configuration drift | ❌ — `.env` has `TRADING_MODE=paper`. The factory would do the right thing if anything called it. The configuration is correct; the call site is missing. |
| **(d) Built but not wired** | **✅** — Phase 3C built `PaperTradingService`, the abstract base, the factory, and a test suite. The dependency-injection chain `run_agents.py → ThinkingCycle → ActionExecutor` was never updated to pass the service through. The `ActionExecutor.trading_service` slot has been empty since Phase 3C shipped. The defensive `if not self.trading:` fallback hid the gap by degrading to a log line. |

This is the same risk class as the Library reflection injection bug and the DMS self-defeating loop: well-meaning fallback code converted a hard failure into a silent failure, and nothing surfaces above the noise floor until an Arena run produces zero trades for three days.

---

## 3. Cross-reference: deferred items tracker

I searched `DEFERRED_ITEMS_TRACKER.md` for prior mentions of this gap.

**One related entry** exists at line 114:
> **LiveTradingService implementation**: Build the live implementation of TradeExecutionService that routes orders to real exchanges via ccxt. Same interface as PaperTradingService. Switch is one env variable: `TRADING_MODE=live`. *(Identified: Phase 3C architecture)*

That entry is about the **live-mode** implementation (a future Phase 8 deliverable). It does NOT cover the wiring gap for the **paper-mode** service that already exists. The wiring gap itself has **never been logged** in the tracker.

**Net conclusion:** This is the first formal diagnosis of the issue. It is genuinely missing institutional memory.

---

## 4. Proposed fix

### Scope

Five small changes, all surgical and additive. The existing `if not self.trading:` fallback stays in place as defense in depth.

| # | File | Change | Effort |
|---|---|---|---|
| 1 | `src/agents/thinking_cycle.py` | Add `trading_service: TradeExecutionService \| None = None` to `__init__` and pass through to `ActionExecutor`. | ~6 LOC |
| 2 | `scripts/run_agents.py` | Construct trading service via `get_trading_service(...)` once at startup. Pass `trading_service=trading` into every `ThinkingCycle(...)` construction. | ~15 LOC |
| 3 | `scripts/run_arena.py` | Hard preflight: assert that paper-mode `get_trading_service()` returns a non-None object before agents are allowed to start. Same shape as the DMS preflight just shipped — refuses Arena entry if the safety dependency isn't wired. | ~10 LOC |
| 4 | `tests/test_action_executor_wiring.py` (new) | Regression guards: (i) `ThinkingCycle.__init__` accepts a `trading_service` kwarg, (ii) running the bootstrap path produces an `ActionExecutor` with `self.trading is not None`, (iii) calling the factory in paper mode returns a `PaperTradingService` instance. Mirror the DMS regression-guard pattern that asserts a removed name cannot be re-introduced unnoticed. | ~50 LOC |
| 5 | `src/agents/action_executor.py` (small policy change) | Escalate the no-service fallback from `logger.info` to `logger.critical` and post the `[NO SERVICE]` message to `system-alerts` (importance=2) in addition to `trade-signals`. The Critic in the last Arena only caught this on day 2 because the alert was buried in `trade-signals`; routing to `system-alerts` would have surfaced it within the first cycle. | ~10 LOC |

**Total effort estimate: SMALL.** Roughly 90 LOC across 4 modified files + 1 new test file. ~1-2 hours of careful work plus a 30-min full-suite run.

### Risk assessment

- **Blast radius: low.** All changes are additive. The wired path replaces a `None` with a working service; the existing fallback remains. No existing test relies on `self.trading is None`.
- **Existing tests:** 968 passing today. The wiring change unlocks a code path that was always short-circuiting; no test that currently passes should fail.
- **New behaviour:** Operator's `execute_trade` actions will actually book trades against the paper-trading engine. This is the intended Arena behaviour that has never run in production.
- **Reversibility:** Trivial — revert the commit.

### Approval status

**Needs War Room approval before implementation.** Reasons:

1. This change finally lets Operators move money against the paper book. Even though it's paper, the colony will start producing P&L numbers, and the next Arena run's behaviour will be qualitatively different from any prior run. Andrew should explicitly bless this transition rather than have a hotfix author quietly flip the switch.
2. Change #5 (escalating the `[NO SERVICE]` alert level) is a policy change to the operator's degraded-mode behaviour. Any change that affects how silent-failure modes surface should go through the same review bar as the DMS hotfix did.
3. Mechanically the work is small and safe to do solo. The decision to do it now (vs sequencing it after another preparation step) is a War Room call.

**Branch suggestion when approved:** `hotfix/operator-trading-service-wiring`, separate from `hotfix/dead-mans-switch-silent` so each can be reviewed and merged on its own evidence.

---

## 5. Pre-fix sanity items the War Room may want to consider

Surfaced during diagnosis but **out of scope for this report's fix**:

- The 18-trade window in the last Arena was small enough that the Strategist/Critic pipeline never had to handle high-volume plan-to-trade conversion. Once trades start booking, the Strategist's plan-rate may overwhelm Critic review capacity — worth watching in the next Arena.
- The current `ActionExecutor._handle_execute_trade` does not appear to enforce a per-cycle position-size cap beyond what the Warden enforces at trade-gate time. If the Warden is configured tightly this is fine; if not, the first real Arena run could see large position sizes Day 1.
- The `LiveTradingService` deferred entry (line 114 of the tracker) becomes the next item once paper mode is producing reliable P&L. The fix proposed here does not block that future work.

---

**End of diagnosis. Awaiting War Room signoff before any code changes.**
