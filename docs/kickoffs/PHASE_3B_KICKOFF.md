## PROJECT SYNDICATE — PHASE 3B CLAUDE CODE KICKOFF PROMPT
## Copy everything below the line and paste it into Claude Code
## ================================================================

I'm continuing Project Syndicate. Read the CLAUDE.md file first, then CURRENT_STATUS.md and CHANGELOG.md to confirm Phase 3A is complete.

This is Phase 3B — The Cold Start Boot Sequence. Phase 3 is split into 6 sub-phases:
- 3A: The Agent Thinking Cycle ← COMPLETE
- **3B: The Cold Start Boot Sequence** ← YOU ARE HERE
- 3C: Paper Trading Infrastructure
- 3D: The First Evaluation Cycle
- 3E: Personality Through Experience
- 3F: First Death, First Reproduction, First Dynasty

Work through each step in order. Use CMD commands only, never PowerShell. Always activate the .venv before any Python work.

**IMPORTANT:** Before modifying ANY existing file, create a timestamped backup in backups/. Before starting, run `python scripts/backup.py` to snapshot the current state.

---

## CONTEXT — What Is The Cold Start Boot Sequence?

When the system starts for the first time, Genesis is alive, the Warden is running, the Agora is empty, the Library has textbooks, treasury has $500, and zero agents exist.

Genesis detects `active_agent_count == 0` and triggers `cold_start_boot_sequence()`.

This phase builds everything needed for that moment: the spawn protocol, the first-cycle orientation, the inter-agent handoff pipeline, and the initial configuration. By the end of this phase, we can flip the switch and watch five agents come to life.

---

## THE BOOT SEQUENCE — FULL SPECIFICATION

### The Gen 1 Roster (Hardcoded, Not AI-Decided)

Genesis does NOT use Claude API for boot sequence decisions. Gen 1 is a predefined roster. There's no value in asking Claude "what agents should I spawn?" when the answer is always the same for a cold start. Genesis uses Claude for spawn decisions *after* Gen 1, when it has data to reason about.

**The five founding agents:**

| Name | Role | Wave | Trading Capital | Thinking Budget |
|------|------|------|----------------|----------------|
| Scout-Alpha | scout | 1 | $0 | $0.50/day |
| Scout-Beta | scout | 1 | $0 | $0.50/day |
| Strategist-Prime | strategist | 2 | $0 | $0.50/day |
| Critic-One | critic | 2 | $0 | $0.50/day |
| Operator-First | operator | 3 | $50 | $0.50/day |

**Capital rationale:**
- Scouts, Strategists, and Critics don't trade — they think. Their "capital" is their thinking budget. Giving them trading capital would be meaningless since they have no trade actions.
- Operator-First gets $50 to start. Conservative. Genesis allocates more based on performance.
- $100 stays in reserve (20% of $500). $350 remains unallocated for future expansion.

**Both Scouts are intentionally identical in configuration** (same temperature, same system prompt, same action space). They differ only in starting watchlist. Personality differentiation emerges through experience, not pre-programming. This is a deliberate design decision — do NOT try to make them different.

---

### Spawn Waves — Condition-Based, Not Clock-Based

Agents do NOT all spawn at once. They come online in three waves, triggered by conditions rather than fixed timers:

```
WAVE 1 — The Eyes (Scouts)
    Trigger: Genesis detects 0 agents
    Action: Spawn Scout-Alpha immediately, Scout-Beta 1 minute later
    Both run orientation cycles upon spawn

WAVE 2 — The Brain (Strategist + Critic)
    Trigger: BOTH Scouts have completed >= 5 cycles each
             OR 30 minutes have elapsed since Wave 1
             (whichever comes first)
    Action: Spawn Strategist-Prime, then Critic-One 1 minute later
    Rationale: Scouts need a few cycles to populate the Agora 
               with observations before downstream agents have 
               anything to work with

WAVE 3 — The Hands (Operator)
    Trigger: First plan enters the pipeline (status = "pending_review" or "approved")
             OR 2 hours have elapsed since Wave 1
             (whichever comes first)
    Action: Spawn Operator-First
    Rationale: Operator has nothing to do until the pipeline 
               produces something. Spawning early just burns 
               thinking tax on idle cycles.
```

**Implementation:** Genesis tracks boot sequence state in a `boot_sequence_state` Redis key:

```json
{
    "status": "in_progress",
    "wave_1_complete": true,
    "wave_1_at": "2026-03-15T10:00:00Z",
    "scout_cycles_completed": {"scout-alpha": 6, "scout-beta": 5},
    "wave_2_complete": true,
    "wave_2_at": "2026-03-15T10:28:00Z",
    "wave_2_trigger": "condition_met",
    "wave_3_complete": false,
    "wave_3_trigger_reason": null,
    "first_plan_submitted": false
}
```

Genesis checks boot sequence progress on every cycle (every 5 minutes) until all waves are complete.

---

### Boot Sequence Error Handling

If any agent's orientation cycle fails (API timeout, malformed output, budget error):

```
BOOT SEQUENCE RETRY POLICY:
    
    If orientation cycle fails:
        → Wait 60 seconds
        → Retry orientation cycle once
        
    If retry also fails:
        → Spawn the agent anyway with status "orientation_failed"
        → Agent enters normal cycle schedule (it just missed orientation)
        → Post warning to Agora: "{agent_name} orientation failed — 
          entering service without Library briefing"
        → Genesis flags agent for closer monitoring in early evaluations
        
    NEVER abort the full boot sequence because one agent had a bad start.
    
    If ALL Wave 1 agents fail orientation:
        → Post critical alert to Agora
        → Email owner via alert system
        → Pause boot sequence for 15 minutes, then retry Wave 1
        → If second attempt also fails → abort boot sequence, 
          alert owner: "Cold start failed. Manual intervention required."
```

---

### Initial Watchlists

Scouts need starting views of the market. They can modify watchlists via `update_watchlist` action as they learn.

```
Scout-Alpha starting watchlist:
    - BTC/USDT  (the bellwether — most liquid, most data-rich)
    - ETH/USDT  (the ecosystem play)
    - SOL/USDT  (high-volatility, high-volume)

Scout-Beta starting watchlist:
    - BNB/USDT
    - XRP/USDT
    - ADA/USDT
    - AVAX/USDT
    - LINK/USDT
```

All USDT pairs for Gen 1 simplicity. All P&L denominated in USDT. No cross-currency confusion. Agents can discover other quote currencies on their own as the ecosystem matures.

---

### Market Data Payload Per Symbol

When the Context Assembler builds a Scout's context, each watched symbol includes this standardized data packet:

```
Per-symbol market data packet:
    current_price:      float   — last trade price
    change_24h_pct:     float   — 24-hour price change %
    volume_24h_usd:     float   — 24-hour volume in USD
    high_24h:           float   — 24-hour high
    low_24h:            float   — 24-hour low
    change_7d_pct:      float   — 7-day price change %
    change_30d_pct:     float   — 30-day price change %
    rsi_14:             float   — 14-period RSI (daily)
    above_ma_20:        bool    — price > 20-day moving average
    above_ma_50:        bool    — price > 50-day moving average
    market_regime:      str     — current system-wide regime from Genesis
```

This gives Scouts enough to form opinions without drowning them in raw OHLCV candles. The data is assembled by the Context Assembler pulling from the ExchangeService and RegimeDetector.

**For Strategists and Operators:** They receive a subset — current_price, change_24h_pct, volume_24h_usd, and market_regime only. They don't need the full scanning dataset.

**For Critics:** Same subset as Strategists, plus the full plan details and source opportunity data.

---

## THE ORIENTATION PROTOCOL

### The First Cycle Problem

A brand new agent has:
- Working memory: empty (about to be assembled)
- Short-term memory: empty (zero previous cycles)
- Long-term memory: empty (no reflections, no lessons, nothing)

Without intervention, the Context Assembler produces a sad context window — identity block, market data, and a vast empty space where experience should be.

### The Orientation Cycle

The first cycle for ANY new agent (not just Gen 1) is a special cycle type:

```
cycle_type: "orientation"
```

**What makes it different from a normal cycle:**

**1. Library content replaces the long-term memory slot.**

Since the agent has no long-term memory, that portion of the context budget gets filled with curated Library textbook content instead. The Context Assembler selects textbooks by role:

```
ORIENTATION_READING_LIST:
    ALL ROLES (always first):
        "08_thinking_efficiently.md" (condensed to ~300 tokens)
    
    scout:
        "01_market_mechanics.md" (condensed to ~250 tokens)
        "05_technical_analysis.md" (condensed to ~250 tokens)
    
    strategist:
        "02_strategy_categories.md" (condensed to ~250 tokens)
        "03_risk_management.md" (condensed to ~250 tokens)
    
    critic:
        "03_risk_management.md" (condensed to ~250 tokens)
        "02_strategy_categories.md" (condensed to ~250 tokens)
    
    operator:
        "01_market_mechanics.md" (condensed to ~250 tokens)
        "07_exchange_apis.md" (condensed to ~250 tokens)
```

"Condensed" means the Context Assembler pulls a pre-written summary extract stored alongside each textbook, NOT the full textbook content. These summaries are written to fit within ~250-300 tokens each.

**IMPORTANT: The textbook summaries must exist before boot.** If a textbook is still a placeholder, the orientation protocol skips that textbook gracefully and logs a warning. The agent proceeds with whatever Library content is available. At minimum, `08_thinking_efficiently.md`, `01_market_mechanics.md`, and `03_risk_management.md` must have real content (not placeholders) before running the boot sequence.

**2. The system prompt gets an orientation addendum.**

Appended to the normal role system prompt:

```
ORIENTATION ADDENDUM (first cycle only):

This is your FIRST CYCLE. You have no prior experience or memories.
You are reading foundational knowledge from The Library below.

Your objectives for this cycle:
1. Absorb the key concepts from your reading material
2. Assess the current market conditions you've been given
3. Choose your first action — even if it's go_idle with a note about 
   what you want to investigate next
4. Write a self-note about what you learned and what you want to focus on

There is no pressure to act immediately. Your first few cycles are for 
learning and calibrating. Reckless early action is more expensive than 
patient observation.
```

**3. The action space is unchanged.** Full action menu from cycle one. No training wheels. If a Scout spots something real on its first cycle, it can broadcast immediately.

**4. Orientation gets 150% of normal token budget.** The Library content injection is expensive. The first cycle is allowed a larger context window as a one-time startup cost. Subsequent cycles use normal budget.

**5. After orientation, normal cycles begin.** Cycle 2 onwards is the standard OODA loop. The agent now has one cycle of short-term memory (its orientation self-note seeds its early personality).

---

## THE INTER-AGENT HANDOFF PIPELINE

This is the workflow protocol that turns five independent agents into a functioning team.

### The Full Pipeline: Opportunity → Trade

```
STEP 1: SCOUT DISCOVERS
    Scout broadcasts "opportunity" to Agora
    → Opportunity record created in opportunities table
    → Posted to Agora channel "opportunities"
    → If urgency >= "medium" AND confidence >= 7:
        → Triggers Strategist interrupt wake-up

STEP 2: STRATEGIST PLANS
    Strategist sees opportunity in Agora feed (scheduled cycle or interrupt)
    → Strategist chooses one of:
        a) propose_plan (references source_opportunity_id)
           → Plan record created with status "pending_review"
           → Posted to Agora channel "plans"
           → Triggers Critic interrupt
        b) request_scout_intel (needs more data)
           → Posted to Agora, may trigger Scout interrupt
        c) go_idle (opportunity doesn't merit a plan)

STEP 3: CRITIC REVIEWS
    Critic wakes up (triggered by plan submission)
    → Critic sees plan + source opportunity + market context
    → Critic chooses one of:
        a) approve_plan
           → Plan status = "approved"
           → Posted to Agora
           → Triggers Operator interrupt
        b) reject_plan
           → Plan status = "rejected"
           → Strategist sees rejection reasoning in next cycle
        c) request_revision
           → Plan status = "needs_revision"
           → Expiration clock PAUSES (not stale, being worked on)
           → Triggers Strategist interrupt

STEP 4: OPERATOR EXECUTES
    Operator wakes up (triggered by plan approval)
    → Operator sees approved plan in context
    → Operator chooses one of:
        a) execute_trade (references plan_id)
           → Trade request submitted to Warden queue
           → Warden approves/rejects via trade gate
           → If approved → routed to Paper Trading engine (Phase 3C)
           → Position recorded, linked to plan_id + opportunity_id
        b) go_idle (conditions changed since approval — not safe)
           → Logged. This is ground-level judgment, not insubordination.
        c) adjust parameters (slightly different entry than planned)
           → Modified trade still goes through Warden

FULL CHAIN: opportunity → plan → review → trade
Every link is recorded. Credit and blame flow through the chain.
```

### Pipeline Rules

**Plans expire after 6 hours by default.** Markets move — stale plans are dangerous. The Strategist can re-propose if the opportunity still exists. The expiration timer:
- Starts when the plan is created
- **Pauses** when plan status is "needs_revision" (it's being actively worked on)
- **Resumes** when the revised plan is resubmitted
- Expired plans get status "expired" and are logged but not acted on

**One Critic per plan in Gen 1.** The design doc specifies dual-Critic review for high-stakes decisions (>20% of agent capital). With only one Critic, all plans get single review. Dual-review activates when the ecosystem has 2+ Critics.

**Operators can reject approved plans.** Conditions may have changed between Critic approval and Operator execution. An Operator going idle on an approved plan is logged and visible — it's tactical judgment, and Genesis can evaluate whether it was the right call.

**Opportunities expire after 2 hours.** A Scout opportunity that hasn't been picked up by a Strategist within 2 hours is marked "expired." Keeps the pipeline clean.

### Critic Secondary Mode

When no plans are pending review, the Critic doesn't just sit idle. The Critic's system prompt includes:

```
SECONDARY MODE (when no plans are pending):
When no plans are awaiting your review, you may proactively scan the 
Agora feed and current market conditions for systemic risks worth 
flagging. Use the flag_risk action to raise concerns visible to the 
entire ecosystem. This is productive use of your time. However, do 
not flag risks frivolously — each cycle costs thinking tax.
```

This keeps the Critic contributing to ecosystem risk awareness during dry spells, which also helps its idle rate metric during evaluations.

---

## GENESIS RECORD ZERO

The boot sequence event is recorded as a special Agora post — the founding document of the ecosystem.

```
Channel: genesis-log
Type: genesis_record_zero
Content:
{
    "event": "COLD_START_BOOT_SEQUENCE",
    "timestamp": "...",
    "treasury_balance": 500.00,
    "reserve": 100.00,
    "available_for_allocation": 400.00,
    "market_regime": "...",
    "agents_spawned": [
        {
            "name": "Scout-Alpha",
            "role": "scout",
            "generation": 1,
            "trading_capital": 0.00,
            "thinking_budget_daily": 0.50,
            "watchlist": ["BTC/USDT", "ETH/USDT", "SOL/USDT"],
            "mandate": "Scan crypto markets for trading opportunities",
            "spawn_wave": 1,
            "survival_clock_days": 21
        },
        {
            "name": "Scout-Beta",
            "role": "scout",
            "generation": 1,
            "trading_capital": 0.00,
            "thinking_budget_daily": 0.50,
            "watchlist": ["BNB/USDT", "XRP/USDT", "ADA/USDT", "AVAX/USDT", "LINK/USDT"],
            "mandate": "Scan crypto markets for trading opportunities",
            "spawn_wave": 1,
            "survival_clock_days": 21
        },
        {
            "name": "Strategist-Prime",
            "role": "strategist",
            "generation": 1,
            "trading_capital": 0.00,
            "thinking_budget_daily": 0.50,
            "mandate": "Build trading plans from Scout intelligence",
            "spawn_wave": 2,
            "survival_clock_days": 21
        },
        {
            "name": "Critic-One",
            "role": "critic",
            "generation": 1,
            "trading_capital": 0.00,
            "thinking_budget_daily": 0.50,
            "mandate": "Stress-test every plan. Flag risks proactively.",
            "spawn_wave": 2,
            "survival_clock_days": 21
        },
        {
            "name": "Operator-First",
            "role": "operator",
            "generation": 1,
            "trading_capital": 50.00,
            "thinking_budget_daily": 0.50,
            "mandate": "Execute approved plans with discipline and precision",
            "spawn_wave": 3,
            "survival_clock_days": 21
        }
    ],
    "notes": "This is the only human-defined structure in the system's history. From here, Genesis decides everything based on observed performance."
}
```

---

## SURVIVAL CLOCK — GEN 1 SPECIAL RULES

Gen 1 agents get a **21-day survival clock** instead of the default 14 days. They're the founding generation with zero institutional knowledge — evaluating them on the same timeline as future agents (who inherit knowledge from parents) would be unfair.

### Day-10 Health Check

At the midpoint (day 10), Genesis runs a lightweight health check on each Gen 1 agent. This is NOT a full performance evaluation — it's a "are you alive and contributing?" check.

```
DAY 10 HEALTH CHECK:

For each Gen 1 agent, Genesis checks:
    1. Has the agent completed at least 20 cycles? (minimum activity)
    2. Is the agent's idle rate below 90%? (not completely paralyzed)
    3. Has the agent produced at least ONE non-idle action? (any contribution)
    4. Is the agent's validation fail rate below 50%? (not broken)

If ALL checks pass → agent continues normally
If any check fails:
    → Genesis posts concern to Agora: "Health check flag on {agent_name}: {reason}"
    → Agent is NOT terminated — just flagged for owner awareness
    → If checks 1 AND 3 both fail (zero activity):
        → Genesis may terminate the agent early as non-functional
        → Capital reclaimed, slot opened for replacement
        → This is a mercy kill, not an evaluation failure
```

---

## DATABASE SCHEMA ADDITIONS

Create a new Alembic migration for Phase 3B:

**New table: `opportunities`**
```
id                  SERIAL PRIMARY KEY
scout_id            INT FK → agents
scout_name          VARCHAR
market              VARCHAR
signal              VARCHAR (volume_breakout, trend_reversal, support_bounce, etc.)
urgency             VARCHAR (low/medium/high)
confidence          INT (1-10, from the Scout's cycle output)
details             TEXT
source_cycle_id     INT FK → agent_cycles (the Scout cycle that produced this)
status              VARCHAR (fresh/claimed/expired) DEFAULT 'fresh'
claimed_by          INT NULLABLE FK → agents (Strategist that picked it up)
claimed_at          TIMESTAMP NULLABLE
expires_at          TIMESTAMP (default: created_at + 2 hours)
created_at          TIMESTAMP DEFAULT NOW()
```

**New table: `plans`**
```
id                      SERIAL PRIMARY KEY
strategist_id           INT FK → agents
strategist_name         VARCHAR
plan_name               VARCHAR
market                  VARCHAR
direction               VARCHAR (long/short)
entry_conditions        TEXT
exit_conditions         TEXT
position_size_pct       FLOAT
timeframe               VARCHAR
thesis                  TEXT
source_opportunity_id   INT NULLABLE FK → opportunities
status                  VARCHAR (pending_review/approved/rejected/needs_revision/executed/expired)
critic_id               INT NULLABLE FK → agents
critic_assessment       TEXT NULLABLE
critic_risk_notes       TEXT NULLABLE
critic_confidence       INT NULLABLE (1-10)
critic_decision_at      TIMESTAMP NULLABLE
operator_id             INT NULLABLE FK → agents
executed_at             TIMESTAMP NULLABLE
execution_result        JSONB NULLABLE
revision_count          INT DEFAULT 0
revision_paused_at      TIMESTAMP NULLABLE (when expiration clock paused)
revision_resumed_at     TIMESTAMP NULLABLE (when expiration clock resumed)
total_pause_seconds     INT DEFAULT 0 (accumulated pause time)
created_at              TIMESTAMP DEFAULT NOW()
expires_at              TIMESTAMP (default: created_at + 6 hours)
```

**New table: `boot_sequence_log`**
```
id                  SERIAL PRIMARY KEY
event_type          VARCHAR (wave_1_start, agent_spawned, wave_2_triggered, orientation_failed, etc.)
agent_id            INT NULLABLE FK → agents
agent_name          VARCHAR NULLABLE
wave_number         INT NULLABLE
trigger_reason      VARCHAR NULLABLE (condition_met / timeout / plan_submitted)
details             JSONB NULLABLE
created_at          TIMESTAMP DEFAULT NOW()
```

**Updates to `agents` table (add columns if not present):**
```
spawn_wave              INT NULLABLE (1, 2, or 3 — for Gen 1 tracking)
orientation_completed   BOOLEAN DEFAULT FALSE
orientation_failed      BOOLEAN DEFAULT FALSE
health_check_passed     BOOLEAN NULLABLE (NULL until day-10 check runs)
health_check_at         TIMESTAMP NULLABLE
initial_watchlist       JSONB NULLABLE (starting watchlist, for historical record)
```

Run migration: `alembic revision --autogenerate -m "phase_3b_boot_sequence"`
Then: `alembic upgrade head`

---

## IMPLEMENTATION STEPS

### STEP 1 — Verify Phase 3A Foundation

Before building anything, confirm:
- .venv activates and all dependencies are importable
- PostgreSQL database is accessible with all Phase 3A tables
- Redis/Memurai responds to PING
- Phase 3A modules exist: thinking_cycle.py, budget_gate.py, context_assembler.py, etc.
- Tests pass: `python -m pytest tests/ -v`

If anything is broken, fix it before proceeding.

---

### STEP 2 — Verify Library Textbook Content

Check that these three textbook files contain real content (not just placeholders):
- `data/library/textbooks/08_thinking_efficiently.md`
- `data/library/textbooks/01_market_mechanics.md`
- `data/library/textbooks/03_risk_management.md`

If they are still placeholders, STOP and alert the user. The boot sequence cannot run properly without at least these three textbooks having real content.

Also check for condensed summary versions of each textbook. If condensed summaries don't exist yet, create them:
- For each textbook, create a companion file: `data/library/textbooks/summaries/XX_filename_summary.md`
- Each summary should be 250-300 tokens (roughly 200-250 words)
- Summaries capture the key concepts and frameworks, not every detail
- These summaries are what the Context Assembler injects during orientation cycles

---

### STEP 3 — Database Migration

Create and run the Alembic migration for the three new tables (opportunities, plans, boot_sequence_log) and agent table updates described above.

---

### STEP 4 — Market Data Service Enhancement (src/common/market_data.py)

Create a market data assembly module that the Context Assembler uses to build the per-symbol data packets:

```
Class: MarketDataAssembler

    async get_symbol_packet(symbol: str) -> dict:
        Assembles the standardized data packet for one symbol:
        - current_price, change_24h_pct, volume_24h_usd, high_24h, low_24h
          → from ExchangeService.get_ticker()
        - change_7d_pct, change_30d_pct
          → calculated from ExchangeService.get_ohlcv(timeframe="1d", limit=30)
        - rsi_14
          → calculated from daily OHLCV using ta library
        - above_ma_20, above_ma_50
          → calculated from daily OHLCV
        - market_regime
          → from RegimeDetector's last recorded regime
        
        Returns the standardized dict. Handles missing data gracefully 
        (returns None for fields that can't be calculated due to 
        insufficient history).
    
    async get_scout_context(watchlist: list[str]) -> list[dict]:
        Returns full data packets for all watched symbols.
    
    async get_trader_context(symbols: list[str]) -> list[dict]:
        Returns reduced packets (price, 24h change, volume, regime only).
```

Include caching (Redis, 60-second TTL) so multiple agents requesting the same symbol in the same minute don't hammer the exchange API.

---

### STEP 5 — Opportunities Manager (src/agents/opportunities.py)

Create the opportunities lifecycle manager:

```
Class: OpportunityManager

    async create_from_cycle(agent_id, cycle_data) -> Opportunity:
        Called by ActionExecutor when a Scout chooses broadcast_opportunity.
        - Extract market, signal, urgency, confidence, details from cycle action params
        - Create opportunity record in DB
        - Set expires_at = now + 2 hours
        - Post to Agora channel "opportunities"
        - Return the created opportunity
    
    async claim(opportunity_id, strategist_id) -> bool:
        Called when a Strategist references an opportunity in propose_plan.
        - Update status to "claimed", set claimed_by and claimed_at
        - Return True if claimed, False if already claimed/expired
    
    async expire_stale() -> int:
        Called periodically (by Genesis or a maintenance task).
        - Find all opportunities where status="fresh" and expires_at < now
        - Update status to "expired"
        - Return count of expired opportunities
    
    async get_fresh(market: str = None) -> list[Opportunity]:
        Get all fresh (unclaimed, unexpired) opportunities.
        Optionally filter by market.
```

---

### STEP 6 — Plans Manager (src/agents/plans.py)

Create the plans lifecycle manager:

```
Class: PlanManager

    async create(strategist_id, plan_data, source_opportunity_id=None) -> Plan:
        Called by ActionExecutor when a Strategist chooses propose_plan.
        - Create plan record with status "pending_review"
        - Set expires_at = now + 6 hours
        - If source_opportunity_id: claim the opportunity
        - Post plan summary to Agora channel "plans"
        - Return the created plan
    
    async submit_review(critic_id, plan_id, decision, assessment, risk_notes, confidence) -> Plan:
        Called by ActionExecutor when a Critic approves/rejects/requests revision.
        - Update plan with critic's decision and details
        - If approved: status = "approved"
        - If rejected: status = "rejected"
        - If needs_revision: 
            status = "needs_revision"
            revision_paused_at = now  (pause expiration clock)
        - Post decision to Agora
        - Return updated plan
    
    async submit_revision(strategist_id, plan_id, revisions, updated_fields) -> Plan:
        Called when Strategist revises a plan.
        - Update plan fields
        - revision_count += 1
        - Calculate pause duration, add to total_pause_seconds
        - Resume expiration clock (revision_resumed_at = now)
        - Status back to "pending_review"
        - Re-triggers Critic interrupt
    
    async mark_executed(operator_id, plan_id, execution_result) -> Plan:
        Called when Operator executes a trade based on this plan.
        - status = "executed"
        - Record operator_id, executed_at, execution_result
    
    async expire_stale() -> int:
        Find plans where:
            status in ("pending_review", "approved")
            AND (now - created_at - total_pause_seconds) > 6 hours
        Update to "expired". Return count.
    
    async get_pending_review() -> list[Plan]:
        All plans with status "pending_review".
    
    async get_approved() -> list[Plan]:
        All approved plans not yet executed or expired.
    
    async effective_age(plan) -> timedelta:
        Returns the plan's effective age accounting for revision pauses.
        effective_age = (now - created_at) - total_pause_seconds
```

---

### STEP 7 — Orientation Protocol (src/agents/orientation.py)

Create the orientation cycle handler:

```
Class: OrientationProtocol

    READING_LISTS: dict mapping role → list of textbook filenames
    SUMMARY_DIR: path to data/library/textbooks/summaries/
    ORIENTATION_TOKEN_MULTIPLIER: 1.5  # 150% of normal budget
    
    async run_orientation(agent) -> OrientationResult:
        1. Load condensed textbook summaries for this agent's role
        2. If any summaries are missing/placeholder:
            → Log warning, skip that textbook
            → Proceed with whatever content IS available
        3. Build the orientation context:
            → Normal mandatory context (identity, state, warden limits)
            → Market data (full packets for scouts, reduced for others)
            → Library content (replaces the empty long-term memory slot)
            → Orientation addendum in system prompt
        4. Token budget = normal_budget * ORIENTATION_TOKEN_MULTIPLIER
        5. Run through the standard thinking cycle (Phase 3A) but with:
            → cycle_type = "orientation"
            → Modified context from above
        6. Record orientation completion on agent record
        7. Return result (success/failure + first self-note)
    
    def get_orientation_addendum() -> str:
        Returns the orientation system prompt addendum text.
    
    def load_summaries(role: str) -> list[dict]:
        Loads the condensed summary files for a role's reading list.
        Returns list of {filename, title, content} dicts.
        Skips any that are missing or still placeholder text.
```

---

### STEP 8 — Boot Sequence Orchestrator (src/genesis/boot_sequence.py)

This is the master controller for the cold start:

```
Class: BootSequenceOrchestrator

    GEN1_ROSTER: list of agent definitions (hardcoded)
    WAVE_2_MIN_SCOUT_CYCLES: 5
    WAVE_2_TIMEOUT_SECONDS: 1800  # 30 minutes
    WAVE_3_TIMEOUT_SECONDS: 7200  # 2 hours
    GEN1_SURVIVAL_CLOCK_DAYS: 21
    
    async run() -> BootSequenceResult:
        """Execute the full cold start boot sequence."""
        
        # Initialize boot state in Redis
        state = initialize_boot_state()
        log_boot_event("boot_sequence_started")
        
        # === WAVE 1: Scouts ===
        log_boot_event("wave_1_start")
        
        scout_alpha = await spawn_agent(GEN1_ROSTER["scout-alpha"])
        orientation_result = await run_orientation_with_retry(scout_alpha)
        log_boot_event("agent_spawned", agent=scout_alpha, orientation=orientation_result)
        
        await asyncio.sleep(60)  # 1 minute gap
        
        scout_beta = await spawn_agent(GEN1_ROSTER["scout-beta"])
        orientation_result = await run_orientation_with_retry(scout_beta)
        log_boot_event("agent_spawned", agent=scout_beta, orientation=orientation_result)
        
        state.wave_1_complete = True
        
        # === Start Scout cycles (add to scheduler) ===
        scheduler.register(scout_alpha)
        scheduler.register(scout_beta)
        
        # === WAVE 2: Wait for condition ===
        wave_2_triggered = False
        wave_2_start = now()
        
        while not wave_2_triggered:
            await asyncio.sleep(30)  # check every 30 seconds
            
            alpha_cycles = get_cycle_count(scout_alpha.id)
            beta_cycles = get_cycle_count(scout_beta.id)
            elapsed = (now() - wave_2_start).total_seconds()
            
            if alpha_cycles >= WAVE_2_MIN_SCOUT_CYCLES and beta_cycles >= WAVE_2_MIN_SCOUT_CYCLES:
                wave_2_triggered = True
                trigger_reason = "condition_met"
            elif elapsed >= WAVE_2_TIMEOUT_SECONDS:
                wave_2_triggered = True
                trigger_reason = "timeout"
        
        log_boot_event("wave_2_triggered", reason=trigger_reason)
        
        strategist = await spawn_agent(GEN1_ROSTER["strategist-prime"])
        orientation_result = await run_orientation_with_retry(strategist)
        log_boot_event("agent_spawned", agent=strategist, orientation=orientation_result)
        
        await asyncio.sleep(60)
        
        critic = await spawn_agent(GEN1_ROSTER["critic-one"])
        orientation_result = await run_orientation_with_retry(critic)
        log_boot_event("agent_spawned", agent=critic, orientation=orientation_result)
        
        scheduler.register(strategist)
        scheduler.register(critic)
        state.wave_2_complete = True
        
        # === WAVE 3: Wait for pipeline activity or timeout ===
        wave_3_triggered = False
        wave_3_start = now()
        
        while not wave_3_triggered:
            await asyncio.sleep(30)
            
            has_plan = await plan_manager.get_any_plan_exists()
            elapsed = (now() - wave_3_start).total_seconds()
            
            if has_plan:
                wave_3_triggered = True
                trigger_reason = "plan_submitted"
            elif elapsed >= WAVE_3_TIMEOUT_SECONDS:
                wave_3_triggered = True
                trigger_reason = "timeout"
        
        log_boot_event("wave_3_triggered", reason=trigger_reason)
        
        operator = await spawn_agent(GEN1_ROSTER["operator-first"])
        orientation_result = await run_orientation_with_retry(operator)
        log_boot_event("agent_spawned", agent=operator, orientation=orientation_result)
        
        scheduler.register(operator)
        state.wave_3_complete = True
        
        # === Post Genesis Record Zero ===
        await post_genesis_record_zero(all_agents)
        log_boot_event("boot_sequence_complete")
        
        return BootSequenceResult(success=True, agents_spawned=5)
    
    async run_orientation_with_retry(agent) -> OrientationResult:
        """Run orientation with one retry on failure."""
        result = await orientation_protocol.run_orientation(agent)
        if result.success:
            return result
        
        # Retry after 60 seconds
        log_boot_event("orientation_retry", agent=agent)
        await asyncio.sleep(60)
        result = await orientation_protocol.run_orientation(agent)
        
        if not result.success:
            agent.orientation_failed = True
            agent.save()
            log_boot_event("orientation_failed", agent=agent)
            agora.broadcast("system-alerts", 
                f"{agent.name} orientation failed — entering service without Library briefing")
        
        return result
    
    async spawn_agent(definition: dict) -> Agent:
        """Create an agent record and initialize it."""
        agent = Agent(
            name=definition["name"],
            role=definition["role"],
            generation=1,
            parent_id=None,
            allocated_capital=definition["trading_capital"],
            thinking_budget_daily=definition["thinking_budget"],
            survival_clock_expires=now() + timedelta(days=GEN1_SURVIVAL_CLOCK_DAYS),
            spawn_wave=definition["wave"],
            watched_markets=definition.get("watchlist", []),
            initial_watchlist=definition.get("watchlist", []),
            status="active"
        )
        db.insert(agent)
        return agent
```

---

### STEP 9 — Day-10 Health Check (src/genesis/health_check.py)

```
Class: Gen1HealthCheck

    MINIMUM_CYCLES = 20
    MAXIMUM_IDLE_RATE = 0.90
    MINIMUM_NON_IDLE_ACTIONS = 1
    MAXIMUM_VALIDATION_FAIL_RATE = 0.50
    
    async check_agent(agent) -> HealthCheckResult:
        """Run day-10 health check on a Gen 1 agent."""
        
        checks = {
            "minimum_activity": agent.cycle_count >= MINIMUM_CYCLES,
            "not_paralyzed": agent.idle_rate < MAXIMUM_IDLE_RATE,
            "has_contributed": count_non_idle_actions(agent.id) >= MINIMUM_NON_IDLE_ACTIONS,
            "not_broken": agent.validation_fail_rate < MAXIMUM_VALIDATION_FAIL_RATE,
        }
        
        all_passed = all(checks.values())
        failed_checks = [k for k, v in checks.items() if not v]
        
        # Update agent record
        agent.health_check_passed = all_passed
        agent.health_check_at = now()
        agent.save()
        
        if not all_passed:
            # Post concern to Agora
            agora.broadcast("genesis-log",
                f"Day-10 health check flag on {agent.name}: failed {failed_checks}")
            
            # If zero activity AND zero contribution → mercy kill candidate
            if not checks["minimum_activity"] and not checks["has_contributed"]:
                return HealthCheckResult(
                    passed=False, 
                    recommendation="terminate_non_functional",
                    failed_checks=failed_checks
                )
        
        return HealthCheckResult(passed=all_passed, failed_checks=failed_checks)
    
    async run_gen1_health_checks() -> list[HealthCheckResult]:
        """Run health checks on all Gen 1 agents at day 10."""
        gen1_agents = get_agents(generation=1)
        results = []
        for agent in gen1_agents:
            result = await check_agent(agent)
            results.append(result)
        return results
```

Integrate this into Genesis's main cycle: when any Gen 1 agent hits day 10, trigger the health check.

---

### STEP 10 — Update Context Assembler for Orientation + Market Data

Modify `src/agents/context_assembler.py` (from Phase 3A) to handle:

1. **Orientation cycle type**: When `cycle_type == "orientation"`, replace the long-term memory slot with Library textbook summaries via `OrientationProtocol.load_summaries()`. Apply the 1.5x token budget multiplier.

2. **Market data integration**: Use `MarketDataAssembler` to build per-symbol data packets. Full packets for Scouts, reduced packets for other roles. Replace any hardcoded or placeholder market data assembly.

3. **Role-based data filtering**: Ensure Critics receive plan details and source opportunity data when plans are pending review. Ensure Operators receive approved plan details.

---

### STEP 11 — Update Action Executor for Pipeline Integration

Modify `src/agents/action_executor.py` (from Phase 3A) to:

1. **broadcast_opportunity**: Create an opportunity record via `OpportunityManager.create_from_cycle()` in addition to the Agora broadcast. The opportunity ID gets stored in the cycle record.

2. **propose_plan**: Create a plan record via `PlanManager.create()`. Link to source opportunity if referenced. Trigger Critic interrupt via the cycle scheduler.

3. **approve_plan / reject_plan / request_revision**: Route through `PlanManager.submit_review()`. Handle the expiration clock pause/resume for revisions.

4. **revise_plan**: Route through `PlanManager.submit_revision()`.

5. **execute_trade**: Link the trade to its source plan via `PlanManager.mark_executed()`.

---

### STEP 12 — Update Cycle Scheduler for Wave Management

Modify `src/agents/cycle_scheduler.py` (from Phase 3A) to:

1. Support agents being registered/unregistered dynamically (Wave 2 and Wave 3 agents join the scheduler mid-boot).
2. Ensure interrupt triggers work for the pipeline (plan_submitted → wake critic, plan_approved → wake operator).
3. Add a `subscribe_to_pipeline_events()` method that listens for Agora messages on "plans" and "opportunities" channels and triggers appropriate interrupts.

---

### STEP 13 — Maintenance Tasks (src/agents/maintenance.py)

Create a periodic maintenance module that runs on Genesis's cycle:

```
Class: MaintenanceTasks

    async run_all():
        await expire_stale_opportunities()   # OpportunityManager.expire_stale()
        await expire_stale_plans()            # PlanManager.expire_stale()
        await check_gen1_health()             # if any Gen 1 agent at day 10
```

Hook this into Genesis's main cycle (run every cycle, the individual tasks check their own conditions).

---

### STEP 14 — Tests

**tests/test_boot_sequence.py:**
- Test Wave 1 spawns two scouts immediately
- Test Wave 2 triggers when both scouts reach 5 cycles
- Test Wave 2 triggers on timeout (30 min) if scouts are slow
- Test Wave 3 triggers when first plan is submitted
- Test Wave 3 triggers on timeout (2 hours)
- Test all 5 agents created with correct attributes (capital, watchlist, survival clock)
- Test Genesis Record Zero is posted to Agora
- Test orientation retry on failure
- Test boot sequence continues if one orientation fails
- Test boot state tracked in Redis

**tests/test_orientation.py:**
- Test orientation loads correct textbook summaries per role
- Test orientation uses 150% token budget
- Test orientation gracefully skips missing textbook summaries
- Test orientation addendum is included in system prompt
- Test cycle_type is "orientation" for first cycle
- Test subsequent cycles are "normal"

**tests/test_opportunities.py:**
- Test opportunity creation from Scout broadcast
- Test opportunity claiming by Strategist
- Test opportunity expiration after 2 hours
- Test duplicate claim prevention
- Test get_fresh filtering

**tests/test_plans.py:**
- Test plan creation with source opportunity link
- Test plan approval flow
- Test plan rejection flow
- Test plan revision with expiration pause/resume
- Test plan expiration accounting for pause time
- Test 6-hour default expiration
- Test get_pending_review and get_approved queries

**tests/test_health_check.py:**
- Test passing health check (all criteria met)
- Test failing on low activity
- Test failing on high idle rate
- Test mercy kill recommendation for zero-activity agents
- Test health check records on agent

**tests/test_market_data.py:**
- Test full symbol packet assembly
- Test reduced packet for non-scout roles
- Test caching (second call within 60s doesn't hit exchange)
- Test graceful handling of missing data (new symbol with <30 days history)

Run all: `python -m pytest tests/ -v`

---

### STEP 15 — Configuration Updates

Add to SyndicateConfig:

```python
# Phase 3B: Boot Sequence
gen1_survival_clock_days: int = 21
gen1_health_check_day: int = 10
boot_wave_2_min_scout_cycles: int = 5
boot_wave_2_timeout_seconds: int = 1800
boot_wave_3_timeout_seconds: int = 7200
orientation_token_multiplier: float = 1.5
opportunity_expiry_hours: int = 2
plan_expiry_hours: int = 6
market_data_cache_ttl_seconds: int = 60

# Gen 1 agent definitions
gen1_scout_alpha_watchlist: list = ["BTC/USDT", "ETH/USDT", "SOL/USDT"]
gen1_scout_beta_watchlist: list = ["BNB/USDT", "XRP/USDT", "ADA/USDT", "AVAX/USDT", "LINK/USDT"]
gen1_operator_capital: float = 50.0
gen1_thinking_budget: float = 0.50
```

Update .env.example with new variables.

---

### STEP 16 — Update CLAUDE.md

Add Phase 3B components to the architecture section:
- Boot Sequence Orchestrator (src/genesis/boot_sequence.py)
- Orientation Protocol (src/agents/orientation.py)
- Opportunities Manager (src/agents/opportunities.py)
- Plans Manager (src/agents/plans.py)
- Market Data Assembler (src/common/market_data.py)
- Day-10 Health Check (src/genesis/health_check.py)
- Maintenance Tasks (src/agents/maintenance.py)
- Library textbook summaries (data/library/textbooks/summaries/)

Update Phase Roadmap to show Phase 3B as COMPLETE.

---

### STEP 17 — Update CHANGELOG.md and CURRENT_STATUS.md

Log everything built in this session.

---

### STEP 18 — Git Commit and Push

```
git add .
git commit -m "Phase 3B: Cold Start Boot Sequence — spawn waves, orientation, pipeline"
git push origin main
```

---

## DESIGN DECISIONS (Reference for Claude Code)

These decisions were made in the War Room (Claude.ai chat) and are final:

1. **Gen 1 roster is hardcoded.** No Claude API for boot decisions. Genesis uses AI for spawning only after Gen 1, when it has data to reason about.
2. **Condition-based spawn waves**, not clock-based. Wave 2 triggers when Scouts have completed 5 cycles (or 30-min timeout). Wave 3 triggers when first plan enters pipeline (or 2-hour timeout).
3. **Only Operators get trading capital.** Scouts, Strategists, and Critics get $0 capital and $0.50/day thinking budget.
4. **Operator-First gets $50**, with $350 remaining unallocated for future expansion.
5. **Both Scouts are intentionally identical** except for watchlist. Personality differentiation emerges through experience, not pre-programming.
6. **Orientation cycle type** with Library textbook injection, 150% token budget, orientation addendum in system prompt. Full action space — no training wheels.
7. **Plans expire after 6 hours.** Expiration clock pauses during "needs_revision" status.
8. **Opportunities expire after 2 hours.** Unclaimed opportunities are auto-expired.
9. **Separate opportunities table** for clean pipeline tracing (not just FK to agent_cycles).
10. **Critic gets secondary scanning mode** — proactive risk flagging when no plans need review.
11. **21-day survival clock for Gen 1** with a day-10 health check (alive + contributing, not a performance eval).
12. **Boot sequence error handling:** retry once per agent, never abort the full sequence for one failure.
13. **Textbook summaries required before boot.** At minimum: Thinking Efficiently, Market Mechanics, Risk Management.
14. **No mentor system for Gen 1.** Library textbooks substitute for inherited knowledge. Mentor system activates in Phase 3F.

---

## DEFERRED ITEMS (Tracked for Future Phases)

The following items were identified during Phase 3B design:

**Phase 3D (Evaluation Cycle):**
- Watchlist overlap monitoring: if two Scouts have >80% overlap, one is redundant. Track as evaluation metric.

**Phase 3E (Personality Through Experience):**
- Scout differentiation is intentionally absent at spawn. Personality emerges from experience. This is by design, not an oversight.

---

Before you start, confirm you've read CLAUDE.md and the current project state. Then proceed through each step in order. Ask me if anything is unclear.
