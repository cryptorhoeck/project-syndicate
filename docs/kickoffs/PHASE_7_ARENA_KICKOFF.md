## PROJECT SYNDICATE — PHASE 7: THE ARENA
## Copy everything below the line and paste it into Claude Code
## ================================================================

I'm continuing Project Syndicate. Read the CLAUDE.md file first, then CURRENT_STATUS.md and CHANGELOG.md to confirm Phase 3F is complete.

This is Phase 7 — The Arena. We are skipping Phases 4, 5, and 6 intentionally. The core system is complete (Phases 0-3F). The Arena is a live validation run: 5 agents paper-trading against real market data for 21 days. Phases 4-6 happen AFTER we validate the core system works.

**This is NOT a build phase. This is a launch phase.** We're lighting the fuse on everything that's been built. The kickoff has three stages:
- Stage 1: Pre-Flight (systems integration check)
- Stage 2: Launch Preparation (wire everything together, create the run script)
- Stage 3: Ignition (start the system and verify first cycles)

Work through each step in order. Use CMD commands only, never PowerShell. Always activate the .venv before any Python work.

**IMPORTANT:** Before modifying ANY existing file, create a timestamped backup in backups/. Before starting, run `python scripts/backup.py` to snapshot the current state.

---

## STAGE 1 — PRE-FLIGHT CHECKS

These checks verify every subsystem is operational BEFORE we try to run them together.

### CHECK 1 — Environment & Dependencies

```
Verify:
- .venv activates
- All dependencies importable: pip install -r requirements.txt
- Python 3.12+
- .env has values for: ANTHROPIC_API_KEY, EXCHANGE_API_KEY, EXCHANGE_API_SECRET
- .env is in .gitignore
```

### CHECK 2 — Database

```
Verify:
- PostgreSQL is running and accessible
- syndicate database exists with all tables from Phases 0-3F
- Run: alembic current (should show latest migration as head)
- Run: alembic upgrade head (ensure no pending migrations)
- Count tables: SELECT count(*) FROM information_schema.tables WHERE table_schema='public';
  (should be 20+ tables)
- system_state table has a row (created by Genesis on first init)
  If not: that's fine — Genesis will create it on first boot
```

### CHECK 3 — Redis/Memurai

```
Verify:
- redis-cli ping returns PONG
- No stale data from previous test runs:
  Run: redis-cli FLUSHDB
  (This clears all Redis data — safe because we haven't run the system yet)
```

### CHECK 4 — Exchange Connection

```
Verify:
- Kraken API responds:
  python -c "import ccxt; k = ccxt.kraken(); print(k.fetch_ticker('BTC/USDT')['last'])"
  (Should print a BTC price)
- Fetch multiple symbols (the ones agents will watch):
  python -c "
  import ccxt
  k = ccxt.kraken()
  for sym in ['BTC/USDT', 'ETH/USDT', 'SOL/USDT', 'XRP/USDT', 'ADA/USDT']:
      try:
          t = k.fetch_ticker(sym)
          print(f'{sym}: ${t[\"last\"]:,.2f}')
      except Exception as e:
          print(f'{sym}: FAILED - {e}')
  "
  (All 5 should return prices. Some symbols may use different notation on Kraken — 
   if any fail, check Kraken's symbol format and update the Gen 1 watchlists accordingly)
```

**CRITICAL: If any Kraken symbols fail, we need to fix the Gen 1 watchlists BEFORE launch.** Kraken may use different pair names than our config (e.g., "BTC/USD" instead of "BTC/USDT", or "XBT/USDT" instead of "BTC/USDT"). Check the actual available symbols and update:
- `gen1_scout_alpha_watchlist` in config
- `gen1_scout_beta_watchlist` in config
- The boot sequence Gen 1 definitions

To check available Kraken USDT pairs:
```python
import ccxt
k = ccxt.kraken()
k.load_markets()
usdt_pairs = [s for s in k.symbols if '/USDT' in s]
print(sorted(usdt_pairs))
```

### CHECK 5 — Anthropic API

```
Verify:
- API key works and has credits:
  python -c "
  import anthropic
  import os
  client = anthropic.Anthropic(api_key=os.getenv('ANTHROPIC_API_KEY'))
  response = client.messages.create(
      model='claude-sonnet-4-20250514',
      max_tokens=50,
      messages=[{'role': 'user', 'content': 'Say hello in exactly 5 words.'}]
  )
  print(response.content[0].text)
  print(f'Tokens used: {response.usage.input_tokens} in, {response.usage.output_tokens} out')
  "
  (Should print a 5-word greeting and token counts)
```

### CHECK 6 — All Tests Pass

```
python -m pytest tests/ -v --tb=short
(ALL tests must pass. Fix any failures before proceeding.)
```

### CHECK 7 — Process Runners Exist

```
Verify these scripts exist and are syntactically valid:
- scripts/run_genesis.py
- scripts/run_warden.py
- scripts/run_all.py (or equivalent multi-process runner)

Check if there's a script for the trading monitors:
- scripts/run_trading.py (position monitor + limit order monitor)

If run_trading.py doesn't exist, it needs to be created in Stage 2.
```

### CHECK 8 — Dashboard Works

```
Verify the FastAPI dashboard starts:
- Start it manually (whatever the current start command is)
- Open browser to http://localhost:8000 (or whatever port)
- Confirm the dashboard loads with the dark theme
- Verify API endpoints respond: 
  http://localhost:8000/api/agents
  http://localhost:8000/api/agora/messages
- Stop the dashboard server
```

### CHECK 9 — Clean Slate

```
The database should be clean for a fresh Arena run.
If there's any leftover test data from development:

- Truncate agent-related tables (but NOT schema tables or config):
  WARNING: This deletes all agent data. Only do this for a fresh Arena start.
  
  python -c "
  from src.common.config import get_config
  from sqlalchemy import create_engine, text
  
  engine = create_engine(get_config().database_url)
  with engine.connect() as conn:
      # Order matters due to foreign keys
      tables = [
          'memorials', 'divergence_scores', 'study_history', 
          'behavioral_profiles', 'agent_relationships',
          'rejection_tracking', 'post_mortems', 'evaluations',
          'agent_equity_snapshots', 'orders', 'positions',
          'boot_sequence_log', 'lineage', 'dynasties',
          'agent_long_term_memory', 'agent_reflections', 'agent_cycles',
          'plans', 'opportunities', 'inherited_positions',
          'messages', 'daily_reports', 'market_regimes',
          'agents',
      ]
      for table in tables:
          try:
              conn.execute(text(f'TRUNCATE TABLE {table} CASCADE'))
              print(f'Truncated: {table}')
          except Exception as e:
              print(f'Skip {table}: {e}')
      
      # Reset system_state to clean
      conn.execute(text(\"\"\"
          UPDATE system_state SET 
              peak_treasury = 500.0,
              current_treasury = 500.0,
              alert_status = 'GREEN',
              active_agents = 0
          WHERE id = 1
      \"\"\"))
      conn.commit()
      print('System state reset to $500 treasury, GREEN alert, 0 agents')
  "
  
  Also flush Redis:
  redis-cli FLUSHDB
```

**Report the results of all 9 checks before proceeding to Stage 2.**

---

## STAGE 2 — LAUNCH PREPARATION

### STEP 1 — Fix Any Symbol Issues From CHECK 4

If any Kraken symbols failed or use different notation, update:
- The Gen 1 watchlist configs in SyndicateConfig
- The boot sequence Gen 1 agent definitions
- Any hardcoded symbol references in market data assembler

Verify all watchlist symbols are fetchable from Kraken after fixes.

### STEP 2 — Create/Verify the Arena Run Script

Create `scripts/run_arena.py` — the single script that starts EVERYTHING needed for The Arena:

```python
"""
Project Syndicate — The Arena
Starts all system processes for the paper trading validation run.

Processes managed:
1. Genesis (5-minute cycle — the god node)
2. Warden (30-second cycle — risk enforcement)  
3. Position Monitor (10-second cycle — stop/TP triggers)
4. Limit Order Monitor (10-second cycle — pending order fills)
5. Sanity Checker (5-minute cycle — reconciliation)
6. Dead Man's Switch (heartbeat monitoring)
7. FastAPI Dashboard (web interface)
8. Maintenance Tasks (opportunity/plan expiry, post-mortem publication)

Usage: python scripts/run_arena.py
Stop: Ctrl+C (graceful shutdown of all processes)
"""

# This script should:
# 1. Verify all pre-flight checks pass (DB, Redis, API keys)
# 2. Start each process as a subprocess or async task
# 3. Monitor all processes — restart any that die
# 4. Handle Ctrl+C gracefully — shut down all processes in order
# 5. Log startup/shutdown events
# 6. Print a startup banner showing what's running

# STARTUP BANNER (print to console):
# ╔══════════════════════════════════════════════╗
# ║     PROJECT SYNDICATE — THE ARENA           ║
# ║     Paper Trading Validation Run             ║
# ╠══════════════════════════════════════════════╣
# ║  Treasury:    $500.00                        ║
# ║  Mode:        PAPER TRADING                  ║
# ║  Agents:      0 (boot sequence pending)      ║
# ║  Market:      BTC at $XX,XXX                 ║
# ║  Dashboard:   http://localhost:8000           ║
# ╠══════════════════════════════════════════════╣
# ║  Processes:                                  ║
# ║    ✓ Genesis         (5 min cycle)           ║
# ║    ✓ Warden          (30 sec cycle)          ║
# ║    ✓ Position Monitor (10 sec cycle)         ║
# ║    ✓ Limit Order Mon  (10 sec cycle)         ║
# ║    ✓ Sanity Checker  (5 min cycle)           ║
# ║    ✓ Dead Man Switch (heartbeat)             ║
# ║    ✓ Dashboard       (port 8000)             ║
# ║    ✓ Maintenance     (periodic)              ║
# ╠══════════════════════════════════════════════╣
# ║  Press Ctrl+C to shutdown gracefully         ║
# ╚══════════════════════════════════════════════╝

# PROCESS MONITORING:
# - Check each subprocess every 10 seconds
# - If any process dies unexpectedly, log the error and restart it
# - If Genesis dies, wait 30 seconds before restart (let it recover cleanly)
# - If Warden dies, restart IMMEDIATELY (safety critical)
# - Log all process starts/stops/restarts

# GRACEFUL SHUTDOWN ORDER (on Ctrl+C):
# 1. Stop Genesis (no new cycles)
# 2. Stop all agent cycle scheduling
# 3. Wait for any in-flight API calls to complete (5 second timeout)
# 4. Stop trading monitors (position monitor, limit order monitor)
# 5. Stop Warden (safety layer stays up until trading stops)
# 6. Stop maintenance tasks
# 7. Stop dashboard
# 8. Stop Dead Man's Switch (last thing to stop)
# 9. Log shutdown complete
```

### STEP 3 — Verify Boot Sequence Integration

The boot sequence is triggered automatically when Genesis detects 0 active agents. Verify this flow works end-to-end:

1. Read `src/genesis/genesis.py` — confirm the main cycle checks for `active_agent_count == 0` and triggers `cold_start_boot_sequence()`
2. Read `src/genesis/boot_sequence.py` — confirm it creates the 5 Gen 1 agents with correct config
3. Read `src/agents/orientation.py` — confirm it runs orientation with Library textbook summaries
4. Confirm the Library textbook summaries exist in `data/library/summaries/` (3 files from Phase 3E fix)
5. Confirm the full textbooks exist in `data/library/textbooks/` (8 files)

If the boot sequence isn't wired into the Genesis main cycle, wire it in now.

### STEP 4 — Verify TRADING_MODE Is Paper

```python
# Confirm in config:
assert get_config().trading_mode == "paper"
# This should already be the default, but verify explicitly.
# We are NOT touching real money.
```

### STEP 5 — Create Arena Monitoring Checklist

Create `docs/arena_monitoring.md` — a checklist for the owner to use during the 21-day run:

```markdown
# Arena Monitoring Checklist

## Daily Check-In (5 minutes)

### Dashboard Quick Look
- [ ] Dashboard loads at http://localhost:8000
- [ ] All 5 agents showing as active (or expected state)
- [ ] No system alerts (alert status = GREEN)
- [ ] Treasury balance makes sense (started at $500)

### Agora Activity
- [ ] Messages flowing in agent-activity channel
- [ ] Scouts are broadcasting opportunities (or going idle with reasoning)
- [ ] Pipeline is moving (opportunities → plans → reviews)

### Financial Health
- [ ] Total API cost for last 24h < $5 (budget: $2.50/day agents + Genesis overhead)
- [ ] If any trades executed: P&L displayed, fees tracked
- [ ] No negative cash balances on any agent

### Process Health
- [ ] All processes running (check run_arena.py console output)
- [ ] No error spam in logs
- [ ] Dead Man's Switch hasn't triggered

### Red Flags (Investigate Immediately)
- All agents going idle every cycle (pipeline frozen)
- API costs spiking above $10/day (runaway thinking)
- Warden alerts (Yellow/Red)
- Any process repeatedly crashing and restarting
- Dashboard showing stale data (not updating)

## Day 10 — Health Check
- [ ] Genesis Day-10 health check runs for all Gen 1 agents
- [ ] All agents passed (or Genesis flagged issues)
- [ ] Review any flagged agents — are they actually broken or just cautious?

## Day 21 — First Evaluation
- [ ] Evaluations trigger for all 5 agents
- [ ] Review results: who survived, who's on probation, who died
- [ ] Post-mortems generated for any dead agents
- [ ] Capital reallocation happened
- [ ] If any role gap: emergency spawn triggered
- [ ] Review daily report email (if SMTP configured)

## Success Criteria
After 21 days, the Arena is a success if:
- [ ] At least 2 of 5 agents survived first evaluation
- [ ] The pipeline produced at least 1 executed trade
- [ ] No system crashes requiring manual restart
- [ ] Total API cost stayed under $75
- [ ] Daily reports were generated (in DB, even if email not configured)
- [ ] The dashboard showed real data flowing throughout
```

### STEP 6 — Create Arena Log

Create `docs/arena_log.md` — a running log for recording observations:

```markdown
# Arena Log — Project Syndicate

## Launch Date: [TO BE FILLED]
## Treasury: $500 (paper)
## Mode: Paper Trading

---

### Day 1 — Launch
- Time started: 
- Boot sequence completed: 
- All 5 agents spawned: Y/N
- First Scout opportunity broadcast: 
- First Strategist plan: 
- First Critic review: 
- First Operator trade: 
- API cost Day 1: $
- Notes:

### Day 2
- Agents active: /5
- Pipeline activity: 
- Trades executed: 
- API cost: $
- Issues:
- Notes:

(Copy this template for each day)
```

### STEP 7 — Final Test Suite Run

```
python -m pytest tests/ -v --tb=short
(Must be 601+ tests, 0 failures)
```

### STEP 8 — Git Commit Pre-Launch State

```
git add .
git commit -m "Phase 7: Arena preparation — run script, monitoring checklist, clean slate"
git push origin main
```

**Report the results of all Stage 2 steps before proceeding to Stage 3.**

---

## STAGE 3 — IGNITION

**This is it. We're starting the system.**

### STEP 1 — Open Two CMD Windows

```
Window 1: The Arena (system processes)
    cd /d "E:\project syndicate"
    .venv\Scripts\activate

Window 2: Monitoring (manual checks)  
    cd /d "E:\project syndicate"
    .venv\Scripts\activate
```

### STEP 2 — Start The Arena

In Window 1:
```
python scripts\run_arena.py
```

Watch the startup banner. Confirm all processes start. You should see:
- Genesis initializing
- Warden starting its 30-second loop
- Trading monitors starting their 10-second loops
- Dashboard available at http://localhost:8000
- Dead Man's Switch active

### STEP 3 — Watch Genesis Detect Zero Agents

Genesis's first cycle (within 5 minutes of start) should:
1. Detect `active_agent_count == 0`
2. Trigger `cold_start_boot_sequence()`
3. Begin spawning Wave 1 (Scouts)

Watch the console output for boot sequence events. You should see:
```
[info] Boot sequence triggered — 0 active agents detected
[info] Wave 1: Spawning Scout-Alpha...
[info] Scout-Alpha orientation cycle starting...
[info] Scout-Alpha orientation complete
[info] Wave 1: Spawning Scout-Beta...
[info] Scout-Beta orientation cycle starting...
[info] Scout-Beta orientation complete
```

### STEP 4 — Verify First Agent Cycles

In Window 2, check the database:
```python
python -c "
from sqlalchemy import create_engine, text
from src.common.config import get_config
engine = create_engine(get_config().database_url)
with engine.connect() as conn:
    agents = conn.execute(text('SELECT id, name, role, status, cycle_count FROM agents ORDER BY id')).fetchall()
    for a in agents:
        print(f'  {a.name} ({a.role}) — status: {a.status}, cycles: {a.cycle_count}')
    
    cycles = conn.execute(text('SELECT agent_id, cycle_type, action_type, api_cost_usd FROM agent_cycles ORDER BY id DESC LIMIT 5')).fetchall()
    print(f'\nLast 5 cycles:')
    for c in cycles:
        print(f'  Agent {c.agent_id}: {c.cycle_type} — action: {c.action_type} — cost: ${c.api_cost_usd:.4f}')
"
```

You should see Scouts running orientation cycles, then normal cycles.

### STEP 5 — Verify Wave 2 Triggers

After Scouts complete 5 cycles each (or 30 minutes), Wave 2 should trigger:
```
[info] Wave 2 triggered: condition_met (both Scouts completed 5+ cycles)
[info] Spawning Strategist-Prime...
[info] Spawning Critic-One...
```

### STEP 6 — Verify Wave 3 Triggers

After a plan enters the pipeline (or 2-hour timeout), Wave 3 triggers:
```
[info] Wave 3 triggered: plan_submitted (or timeout)
[info] Spawning Operator-First...
```

### STEP 7 — Verify Dashboard Shows Live Data

Open http://localhost:8000 in your browser. You should see:
- 5 agents listed (once all waves complete)
- Agora messages flowing (agent-activity channel)
- Market regime displayed
- Treasury at $500

### STEP 8 — Verify First Hour Economics

After ~1 hour of runtime, check API costs:
```python
python -c "
from sqlalchemy import create_engine, text
from src.common.config import get_config
engine = create_engine(get_config().database_url)
with engine.connect() as conn:
    costs = conn.execute(text('''
        SELECT a.name, a.role, a.cycle_count, a.total_api_cost, 
               a.thinking_budget_used_today, a.thinking_budget_daily
        FROM agents a WHERE a.status = 'active' ORDER BY a.name
    ''')).fetchall()
    total = 0
    for c in costs:
        print(f'  {c.name} ({c.role}): {c.cycle_count} cycles, ${c.total_api_cost:.4f} spent, ${c.thinking_budget_daily - c.thinking_budget_used_today:.4f} budget remaining')
        total += c.total_api_cost
    print(f'\n  Total API cost so far: ${total:.4f}')
"
```

**Expected first-hour costs:** ~$0.10-0.25 total across all agents and Genesis. If costs are significantly higher, something is over-thinking.

### STEP 9 — Confirm The System Is Alive

If all of the above checks out:
- Agents are spawning and cycling ✓
- The Agora has messages flowing ✓
- The dashboard shows live data ✓
- API costs are reasonable ✓
- No crashes or error spam ✓

**The Arena is live. The Syndicate breathes.**

Post to the Agora manually (via Genesis or a script):
```
Channel: genesis-log
Message: "THE ARENA IS OPEN. Day 1. Treasury: $500. 5 agents active. 
Let the games begin."
```

---

## WHAT TO DO WHEN THINGS BREAK

They will break. Here's the runbook:

### "All Agents Are Going Idle Every Cycle"

**Diagnosis:** The pipeline is frozen. Scouts aren't finding opportunities, or their confidence threshold is too high.
**Fix options:**
1. Check Scout cycle records — are they seeing market data? Look at their context_summary.
2. Check if market data is being assembled correctly (price cache working?)
3. Lower the opportunity confidence threshold for triggering Strategist interrupts (config: currently >= 7, try >= 5)
4. Check if Scouts are in SURVIVAL mode (low budget) — they might be too resource-constrained to broadcast

### "API Costs Are Spiking"

**Diagnosis:** Agents are producing verbose output or the context window is too large.
**Fix options:**
1. Check avg tokens per cycle: `SELECT agent_id, AVG(input_tokens), AVG(output_tokens) FROM agent_cycles GROUP BY agent_id`
2. If input tokens are huge: context assembler is overstuffing. Reduce token budgets in config.
3. If output tokens are huge: agents are being verbose. The thinking tax will naturally punish this, but you can reduce context_token_budget_normal in config for immediate relief.

### "A Process Keeps Crashing"

**Diagnosis:** Unhandled exception in one of the monitor loops or Genesis.
**Fix options:**
1. Check the error output — the run_arena.py script should log why the process died
2. Most loops have try/except (by design). If something is getting through, it's a new edge case.
3. Fix the bug, restart the affected process. The Arena run continues — you don't need to start over.

### "Warden Alert Triggered"

**Diagnosis:** Paper trading P&L hit the alert thresholds.
**Fix options:**
1. Yellow alert (15% drawdown in 4h): system halves position sizes automatically. Monitor but don't panic.
2. Red alert (30% drawdown in 4h): all trading stops for 24h. Check what caused the drawdown.
3. Circuit breaker (75% from peak): everything shuts down. This shouldn't happen in paper trading unless there's a bug in P&L calculation. Investigate.

### "The Dashboard Is Stale"

**Diagnosis:** FastAPI process died or HTMX auto-refresh isn't working.
**Fix options:**
1. Check if the FastAPI process is still running
2. Restart it manually if needed: `python -m uvicorn src.console.app:app --host 0.0.0.0 --port 8000`
3. The dashboard being down doesn't affect the trading system — agents keep running regardless

### "Want to Stop Everything Cleanly"

In the run_arena.py console window: press **Ctrl+C**

The script should gracefully shut down all processes in order. If it doesn't respond after 10 seconds, press Ctrl+C again (forced stop). Then verify no orphaned Python processes: `taskkill /F /IM python.exe` (nuclear option — kills ALL Python processes).

---

## DESIGN DECISIONS (Reference for Claude Code)

1. **The Arena is a launch phase, not a build phase.** Minimal new code. Focus is on integration, wiring, and verification.
2. **Single run script (`run_arena.py`)** starts everything. One command to launch, Ctrl+C to stop.
3. **Clean slate before launch.** All agent data truncated. Fresh $500 treasury. This is Day 1 of the Syndicate.
4. **TRADING_MODE=paper is verified explicitly.** No real money touches the exchange.
5. **Symbol validation against Kraken** before boot. Gen 1 watchlists must use Kraken's actual symbol names.
6. **Monitoring checklist for the owner.** Daily 5-minute check-in protocol. Day 10 and Day 21 milestones.
7. **Runbook for common failures.** Pre-written diagnostic and fix procedures for the most likely issues.
8. **Three-stage launch** (pre-flight → preparation → ignition) ensures nothing is missed.
9. **The Arena continues through bugs.** If one process crashes, fix and restart it. No need to reset the 21-day run unless there's a fundamental architectural failure.
10. **Success criteria defined upfront.** 2/5 agents survive, 1+ trades executed, <$75 API cost, no manual restarts needed, data flows on dashboard.

---

Before you start, confirm you've read CLAUDE.md and the current project state. Run through Stage 1 checks first and report results before moving to Stage 2. Ask me if anything is unclear.
