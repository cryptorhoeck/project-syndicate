## PROJECT SYNDICATE — PHASE 3A CLAUDE CODE KICKOFF PROMPT
## Copy everything below the line and paste it into Claude Code
## ================================================================

I'm continuing Project Syndicate. Read the CLAUDE.md file first, then CURRENT_STATUS.md and CHANGELOG.md to confirm Phase 2D is complete.

This is Phase 3A — The Agent Thinking Cycle. Phase 3 is split into 6 sub-phases:
- **3A: The Agent Thinking Cycle** ← YOU ARE HERE
- 3B: The Cold Start Boot Sequence
- 3C: Paper Trading Infrastructure
- 3D: The First Evaluation Cycle
- 3E: Personality Through Experience
- 3F: First Death, First Reproduction, First Dynasty

Work through each step in order. Use CMD commands only, never PowerShell. Always activate the .venv before any Python work.

**IMPORTANT:** Before modifying ANY existing file, create a timestamped backup in backups/. Before starting, run `python scripts/backup.py` to snapshot the current state.

---

## CONTEXT — What Is The Thinking Cycle?

The thinking cycle is the heartbeat of every agent in the system. Every Scout, Strategist, Critic, and Operator runs the same fundamental loop — they differ in what data they see, what actions they can take, and what memories they accumulate. But the process is identical.

It's based on the **OODA Loop** (Observe, Orient, Decide, Act) from fighter pilot doctrine — decision-making under pressure with incomplete information.

**This is the single most important piece of code in the entire project.** Everything else — evaluation, evolution, dynasties — depends on how agents think.

---

## THE THINKING CYCLE V3 — FULL SPECIFICATION

### Overview: The Six Phases

Every agent cycle runs these six phases in order:

```
Phase 0: BUDGET CHECK    → Can I afford to think?
Phase 1: OBSERVE         → Assemble my context window (deterministic code, no AI)
Phase 2: ORIENT+DECIDE   → Single Claude API call — analyze situation, choose action
Phase 3: VALIDATE        → Is the output well-formed and legal?
Phase 4: ACT             → Execute the chosen action
Phase 5: RECORD          → Write everything to the black box
```

Every 10th cycle, Phase 2 is replaced with a **REFLECT** cycle instead of a normal Orient+Decide.

---

### Phase 0 — BUDGET CHECK (Pre-Cycle Gate)

Before any API call happens, the system checks whether the agent can afford to think.

```
Class: BudgetGate

    check(agent) -> BudgetStatus:
        estimated_cost = agent.role.avg_cycle_cost  # rolling average of last 20 cycles
        remaining_budget = agent.thinking_budget_remaining_today
        
        if remaining_budget < estimated_cost:
            return SKIP_CYCLE
            # → broadcast "resource_critical" to Agora
            # → agent should consider hibernation
            # → log: cycle skipped due to budget exhaustion
            
        if remaining_budget < (estimated_cost * 3):
            return SURVIVAL_MODE
            # → context assembler uses reduced token budget (50% of normal)
            # → system prompt gets a "be extremely concise" directive
            # → agent knows it's running low
            
        return NORMAL
```

**Design rationale:** This prevents agents from thinking themselves to death. An agent that burns budget on verbose, unproductive cycles will hit survival mode, then budget exhaustion, then hibernation or death. The thinking tax is self-regulating.

---

### Phase 1 — OBSERVE (Context Assembly)

The **Context Assembler** builds the agent's "mind" for this cycle. This is pure deterministic code — no AI involved. It decides what information reaches the agent's conscious awareness.

**The Context Assembler works within a token budget.** Not every piece of available information makes the cut. The assembler scores and ranks information by relevance, recency, and importance.

#### Token Budget Allocation

The budget allocation is **dynamic**, not fixed. It shifts based on the agent's current situation:

```
Class: ContextAssembler

    # Base allocation (percentage of total token budget)
    MANDATORY_BASE = 0.25    # identity, state, assignments, warden limits
    PRIORITY_BASE = 0.45     # market data, agora feed, recent history
    MEMORY_BASE = 0.20       # long-term lessons, patterns, relationships
    BUFFER = 0.10            # overflow room for any category
    
    # Dynamic modes (override base allocation)
    MODES:
        NORMAL:     mandatory=0.25, priority=0.45, memory=0.20, buffer=0.10
        CRISIS:     mandatory=0.40, priority=0.30, memory=0.20, buffer=0.10
        HUNTING:    mandatory=0.15, priority=0.55, memory=0.20, buffer=0.10
        SURVIVAL:   mandatory=0.50, priority=0.25, memory=0.15, buffer=0.10
    
    determine_mode(agent) -> Mode:
        if agent.pnl_trend == "bleeding" or agent.has_underwater_positions:
            return CRISIS
        if agent.role == "scout" and not agent.has_active_opportunity:
            return HUNTING
        if agent.budget_status == SURVIVAL_MODE:
            return SURVIVAL
        return NORMAL
```

#### What Goes Into Each Category

**Mandatory Context (always included):**
- Agent identity: role, name, generation, lineage summary, reputation score
- Current state: positions (if any), P&L (gross and true), budget remaining
- Active assignments from Genesis or Strategy Council
- Warden limits: current alert level, position size limits, any flags on this agent
- Cycle metadata: cycle number, time since last cycle, current market regime

**Priority Context (ranked by relevance score, included until budget fills):**
- Market data for watched markets (prices, volume, 24h change, key indicators)
- Agora feed filtered by: (a) mentions of this agent, (b) messages from agents this agent follows, (c) messages tagged with this agent's active markets/topics
- Recent decisions + outcomes (last 5-10 cycles with outcome data)
- Alerts or warnings from the Warden or Genesis directed at this agent
- Pending items: unanswered requests, plans awaiting review, open opportunities

**Long-Term Memory Injection (curated wisdom):**
- Key lessons learned (promoted self-notes that proved accurate)
- Performance patterns: "I do well in X conditions, poorly in Y"
- Relationship data: "Scout-7 provides reliable intel (3 confirmed), Scout-3 unreliable (2 bad tips)"
- Compressed reflection summaries from past reflection cycles
- Inherited knowledge from parent/grandparent (if applicable)

#### Relevance Scoring

Each piece of priority context gets a relevance score:

```
relevance_score = (
    recency_weight * time_decay(item.age) +         # newer = higher
    role_weight * role_relevance(item, agent.role) + # role-specific boost
    mention_weight * mentions_agent(item, agent) +   # direct mentions = high
    market_weight * market_overlap(item, agent) +    # shared market focus = boost
    outcome_weight * has_pending_outcome(item)        # unresolved items = boost
)
```

Items are sorted by score and packed into the context until the token budget for that category is full.

---

### Phase 2 — ORIENT + DECIDE (The API Call)

Single Claude API call. The agent receives its assembled context and produces structured output.

#### API Temperature Strategy (Per-Role)

Different roles think differently. Temperature is the free lever:

```
TEMPERATURE_MAP:
    scout:      0.7    # Creative, divergent. Spot unusual patterns.
    strategist: 0.5    # Balanced creativity and rigor.
    critic:     0.2    # Conservative, methodical. Poke holes.
    operator:   0.2    # Disciplined execution. No surprises.
```

These are starting values. They can be tuned per-agent over time based on performance (a future evolution lever — not implemented in Phase 3A, but the config should support per-agent overrides).

#### The System Prompt Template

```
SYSTEM PROMPT:
You are {agent_name}, a {role} agent in Project Syndicate.
Generation: {generation} | Reputation: {reputation_score} ({prestige_title})
Cycle: {cycle_number} | Budget remaining today: ${budget_remaining}

YOUR ROLE: {role_description}

Your thinking costs money. Every token in this response is deducted from your 
budget as "thinking tax." Unproductive thinking accelerates your death. 
Be decisive and concise.

AVAILABLE ACTIONS:
{action_list_for_role}

WARDEN LIMITS:
- Current system alert: {alert_level}
- Max position size: {max_position_pct}% of your capital
- {any_agent_specific_flags}

Respond ONLY in the following JSON format. No other text.
```

#### The User Prompt (Injected Context)

```
USER PROMPT:
=== MARKET DATA ===
{assembled_market_data}

=== AGORA FEED (Recent) ===
{assembled_agora_messages}

=== YOUR RECENT HISTORY ===
{recent_decisions_and_outcomes}

=== YOUR LONG-TERM MEMORY ===
{lessons_patterns_relationships}

=== ACTIVE ASSIGNMENTS ===
{current_tasks_or_none}

=== YOUR ASSESSMENT ===
Analyze the situation and choose your action.
```

#### Required Output Schema

```json
{
    "situation": "2-3 sentence assessment of what's happening right now",
    "confidence": {
        "score": 7,
        "reasoning": "One sentence explaining confidence level"
    },
    "recent_pattern": "Am I repeating a behavior? Is it working? (1 sentence)",
    "action": {
        "type": "broadcast_opportunity",
        "params": {
            "market": "SOL/USDT",
            "signal": "volume_breakout",
            "urgency": "medium",
            "details": "SOL volume 3x average with price consolidating at support"
        }
    },
    "reasoning": "Why this action, why now. Under 150 words — but not artificially constrained. Say what needs saying.",
    "self_note": "One thing to remember for next cycle. This is your Post-it note to your future self."
}
```

**CRITICAL DESIGN DECISION — Verbosity is a choice, not a constraint.** The schema asks for concise output, but does NOT enforce a hard word limit on reasoning. Agents that write 30-word reasonings and make good decisions will have better thinking efficiency scores. Agents that write 500-word essays and still lose money will die from the thinking tax. The market sorts this out — we don't need to be the word police.

---

### Phase 2 (Alternate) — REFLECT (Every 10th Cycle)

Every 10th cycle, the normal Orient+Decide is replaced with a reflection cycle. The agent doesn't take an action — it reviews and learns.

#### Reflection System Prompt Addition

```
This is a REFLECTION cycle. You are not choosing an action.
Instead, review your last 10 cycles and produce a reflection.

RECENT CYCLE HISTORY (last 10):
{full_cycle_summaries_with_outcomes}

YOUR CURRENT LONG-TERM MEMORY:
{current_lessons_and_patterns}

Produce a reflection in this JSON format:
```

#### Reflection Output Schema

```json
{
    "what_worked": "Specific behaviors/decisions that produced good outcomes",
    "what_failed": "Specific behaviors/decisions that produced bad outcomes",
    "pattern_detected": "Any recurring pattern, positive or negative",
    "lesson": "The ONE most important lesson from this period",
    "confidence_trend": "improving / stable / declining — with one sentence why",
    "strategy_note": "Optional: any adjustment to approach going forward",
    "memory_promotion": [
        "self_note from cycle 43 about SOL volatility — confirmed accurate",
        "scout-7 reliability — 4th good tip in a row"
    ],
    "memory_demotion": [
        "earlier belief that low-volume = safe — disproven by cycle 38 loss"
    ]
}
```

**Memory promotion/demotion** is how the agent curates its own long-term memory. Lessons that get promoted persist and influence future context assembly. Lessons that get demoted are deprioritized or removed. This is self-directed learning.

Reflections are stored in the agent's long-term memory AND are visible to Genesis during evaluations. Genesis should weight quantitative performance over qualitative self-assessment (an agent that says "I'm doing great" while losing money is delusional, not optimistic).

---

### Phase 3 — VALIDATE (Output Processing)

Before any action executes, the output goes through validation:

```
Class: OutputValidator

    validate(agent, raw_output) -> ValidationResult:
    
        # Step 1: JSON Parse
        try:
            parsed = json.loads(raw_output)
        except JSONDecodeError:
            return MALFORMED_JSON
            
        # Step 2: Schema Validation
        # Does it have all required fields? Are types correct?
        if not matches_schema(parsed, agent.role):
            return INVALID_SCHEMA
            
        # Step 3: Action Space Check
        # Is this action available to this role?
        if parsed["action"]["type"] not in agent.role.available_actions:
            return INVALID_ACTION
            
        # Step 4: Warden Pre-Check
        # Would this action violate any risk limits?
        if action_is_trade(parsed["action"]):
            warden_result = warden.pre_check(agent, parsed["action"])
            if warden_result.rejected:
                return WARDEN_REJECTED(warden_result.reason)
                
        # Step 5: Sanity Check
        # Are the parameters reasonable?
        if not sanity_check(agent, parsed["action"]):
            return SANITY_FAILURE
            
        return VALID(parsed)
```

**Failure handling:**

```
MALFORMED_JSON:
    → ONE retry with a repair prompt: "Your output was not valid JSON. 
       Here's what you sent: {raw}. Respond with corrected JSON only."
    → Retry costs double thinking tax (penalty for sloppy output)
    → If retry also fails → log as failed cycle, no action, move on
    
INVALID_SCHEMA:
    → Same retry logic as malformed JSON
    
INVALID_ACTION:
    → NO retry. Log the hallucinated action. Record as failed cycle.
    → This is a mark against the agent — hallucinating actions wastes resources
    
WARDEN_REJECTED:
    → Action blocked. Log the attempt and the Warden's reason.
    → Notify Genesis. Repeated Warden violations are a serious flag.
    → Agent receives the rejection reason in its next cycle's context
    
SANITY_FAILURE:
    → Action blocked. Log with details (e.g., "tried to spend $200 with $15 budget")
    → No retry. Record as failed cycle.
```

---

### Phase 4 — ACT (Execute & Broadcast)

Execute the validated action through the appropriate system:

```
Class: ActionExecutor

    execute(agent, validated_action) -> ActionResult:
    
        match validated_action.type:
        
            # === SCOUT ACTIONS ===
            case "broadcast_opportunity":
                → Post to Agora channel "opportunities" with agent attribution
                → Tag relevant markets and urgency level
                → Can trigger Strategist interrupt (see cycle scheduling)
                
            case "request_deeper_analysis":
                → Post to Agora channel "requests" asking for specific data
                → Targeted at Strategists or other Scouts
                
            case "update_watchlist":
                → Modify agent's watched markets list in database
                → Affects what market data appears in future context assembly
                
            # === STRATEGIST ACTIONS ===
            case "propose_plan":
                → Create plan record in database with status "pending_review"
                → Post plan summary to Agora channel "plans"
                → Auto-triggers Critic review cycle (interrupt)
                
            case "revise_plan":
                → Update existing plan with revisions
                → Re-triggers Critic review
                
            case "request_scout_intel":
                → Post specific intel request to Agora
                → Can trigger Scout interrupt if urgent
                
            # === CRITIC ACTIONS ===
            case "approve_plan":
                → Update plan status to "approved"
                → Post approval + reasoning to Agora
                → Plan becomes eligible for Operator execution
                
            case "reject_plan":
                → Update plan status to "rejected" with reasons
                → Post rejection to Agora
                → Strategist receives rejection in next cycle context
                
            case "request_revision":
                → Update plan status to "needs_revision" with specific asks
                → Triggers Strategist interrupt
                
            case "flag_risk":
                → Post risk flag to Agora channel "risk-flags"
                → Visible to Genesis, Warden, and all agents
                
            # === OPERATOR ACTIONS ===
            case "execute_trade":
                → Submit trade request to Warden queue (Redis)
                → Warden processes through trade gate
                → If approved → route to Paper Trading engine
                → Record trade in positions table
                
            case "adjust_position":
                → Modify stop-loss, take-profit, or position size
                → Goes through Warden if size increases
                
            case "close_position":
                → Submit close order to Paper Trading engine
                → Record outcome, calculate P&L
                
            case "hedge":
                → Submit hedge trade through Warden
                → Linked to the position being hedged
                
            # === UNIVERSAL ACTIONS (all roles) ===
            case "go_idle":
                → Log idle decision with reasoning
                → No action taken — but the idle is recorded
                → Costs: only this cycle's thinking tax
                
        return ActionResult(success, details, cost)
```

---

### Phase 5 — RECORD (The Black Box)

Everything from the cycle gets written to permanent storage:

```
Class: CycleRecorder

    record(cycle_data) -> None:
    
        # 1. Write to PostgreSQL (permanent record)
        cycle_record = {
            "agent_id": agent.id,
            "agent_name": agent.name,
            "generation": agent.generation,
            "cycle_number": agent.cycle_count,
            "timestamp": now(),
            "cycle_type": "normal" | "reflection" | "survival",
            
            # What the agent saw (compressed)
            "context_summary": compress_context(assembled_context),
            "context_mode": "normal" | "crisis" | "hunting" | "survival",
            "context_token_count": context_tokens_used,
            
            # What the agent thought
            "situation_assessment": parsed_output.situation,
            "confidence_score": parsed_output.confidence.score,
            "confidence_reasoning": parsed_output.confidence.reasoning,
            "recent_pattern": parsed_output.recent_pattern,
            
            # What the agent did
            "action_type": parsed_output.action.type,
            "action_params": parsed_output.action.params,  # JSON
            "reasoning": parsed_output.reasoning,
            "self_note": parsed_output.self_note,
            
            # Validation result
            "validation_passed": True/False,
            "validation_retries": 0/1,
            "warden_flags": 0/N,
            
            # Outcome (filled asynchronously when result is known)
            "outcome": null,  # filled later
            "outcome_pnl": null,  # filled later
            
            # Cost accounting
            "input_tokens": api_response.usage.input_tokens,
            "output_tokens": api_response.usage.output_tokens,
            "api_cost_usd": calculate_cost(input_tokens, output_tokens),
            
            # Timing
            "cycle_duration_ms": elapsed_time,
            "api_latency_ms": api_call_time,
        }
        
        db.insert("agent_cycles", cycle_record)
        
        # 2. Post to Agora (public transparency)
        agora.broadcast(
            channel="agent-activity",
            agent=agent.id,
            summary=f"{agent.name} [{agent.role}]: {action_type} — {one_line_summary}",
            details=cycle_record  # full record available on drill-down
        )
        
        # 3. Update agent's short-term memory (Redis for speed)
        redis.lpush(f"agent:{agent.id}:recent_cycles", cycle_record)
        redis.ltrim(f"agent:{agent.id}:recent_cycles", 0, 49)  # keep last 50
        
        # 4. Update agent's running stats
        agent.cycle_count += 1
        agent.total_api_cost += api_cost_usd
        agent.thinking_budget_used_today += api_cost_usd
        agent.last_cycle_at = now()
        agent.save()
```

---

## CYCLE SCHEDULING

### Base Frequencies

```
CYCLE_INTERVALS:
    scout:      300    # 5 minutes
    strategist: 900    # 15 minutes
    critic:     null   # on-demand only (triggered by plan submission)
    operator:   
        active: 60     # 1 minute during active trades
        idle:   900    # 15 minutes when no positions
```

### Interrupt System

Certain Agora events can **wake an agent outside its normal schedule**. Interrupts are not free — they still cost thinking tax and count as a cycle.

```
Class: CycleScheduler

    INTERRUPT_TRIGGERS:
        # Strategists wake up when Scouts find something
        "opportunity_broadcast" → wake strategists (if confidence >= 7)
        
        # Critics wake up when plans need review
        "plan_submitted" → wake critics
        
        # Operators wake up when plans are approved
        "plan_approved" → wake operators
        
        # Everyone wakes up for system alerts
        "warden_alert" → wake all active agents
        
        # Targeted interrupts
        "agent_mentioned" → wake the mentioned agent (if idle)
    
    # Rate limiting: no agent can be interrupted more than once per minute
    # This prevents cascade storms where agents keep waking each other up
    
    INTERRUPT_COOLDOWN = 60  # seconds
    
    schedule_cycle(agent) -> ScheduleResult:
        # Check if agent is in cooldown
        if agent.last_cycle_at + INTERRUPT_COOLDOWN > now():
            return DEFER(next_eligible_time)
            
        # Check if agent has budget
        budget_status = budget_gate.check(agent)
        if budget_status == SKIP_CYCLE:
            return SKIP(reason="budget_exhausted")
            
        # Queue the cycle
        return QUEUED(priority=agent.interrupt_priority or "normal")
```

### Cycle Queue Processing

All cycles go through a central queue to prevent concurrency issues:

```
Class: CycleQueue

    # Redis-based priority queue
    # Priority levels: critical > interrupt > scheduled > idle
    
    enqueue(agent_id, priority, trigger_reason):
        score = priority_to_score(priority) + time_tiebreaker()
        redis.zadd("cycle_queue", {agent_id: score})
        
    process_next():
        # Pop highest priority agent from queue
        agent_id = redis.zpopmax("cycle_queue")
        
        # Run their thinking cycle
        result = thinking_cycle.run(agent_id)
        
        # Schedule their next regular cycle
        next_time = now() + agent.cycle_interval
        scheduler.schedule_at(agent_id, next_time)
    
    # The main loop processes one cycle at a time
    # This prevents two agents from executing contradictory trades simultaneously
    # Throughput is limited by API latency (~2-5 seconds per cycle)
    # At 5 agents, worst case is ~25 seconds to process all queued cycles
    # This is acceptable for Phase 3A. Parallel processing is a Phase 4 optimization.
```

**Design decision: Sequential processing, not parallel.** With only 5 agents in Phase 3, sequential is simpler and eliminates all concurrency bugs. When we scale to 20+ agents in Phase 4, we can add parallel lanes with deconfliction. Build simple now, optimize later.

---

## ROLE-SPECIFIC ACTION SPACES

### Scout Actions

```python
SCOUT_ACTIONS = {
    "broadcast_opportunity": {
        "description": "Share a discovered opportunity with the ecosystem",
        "params": {
            "market": "str — trading pair (e.g., SOL/USDT)",
            "signal": "str — type of signal (volume_breakout, trend_reversal, support_bounce, etc.)",
            "urgency": "str — low/medium/high",
            "details": "str — what you see and why it matters"
        },
        "costs_thinking_tax": False  # already paid for this cycle
    },
    "request_deeper_analysis": {
        "description": "Ask the ecosystem for more information on something you've spotted",
        "params": {
            "topic": "str — what you need analyzed",
            "target_role": "str — who should respond (strategist/scout/any)",
            "context": "str — what you already know"
        }
    },
    "update_watchlist": {
        "description": "Change which markets you're actively monitoring",
        "params": {
            "add_markets": "list[str] — markets to start watching",
            "remove_markets": "list[str] — markets to stop watching",
            "reason": "str — why this change"
        }
    },
    "go_idle": {
        "description": "Nothing worth doing right now. Save your budget.",
        "params": {
            "reason": "str — why idle is the right call"
        }
    }
}
```

### Strategist Actions

```python
STRATEGIST_ACTIONS = {
    "propose_plan": {
        "description": "Submit a trading plan for Critic review",
        "params": {
            "plan_name": "str — descriptive name",
            "market": "str — trading pair",
            "direction": "str — long/short",
            "entry_conditions": "str — when to enter",
            "exit_conditions": "str — take profit and stop loss",
            "position_size_pct": "float — % of allocated capital",
            "timeframe": "str — expected duration",
            "thesis": "str — the core reasoning behind this plan",
            "source_opportunity_id": "int|null — the Scout opportunity that inspired this"
        }
    },
    "revise_plan": {
        "description": "Update an existing plan based on Critic feedback or new data",
        "params": {
            "plan_id": "int — which plan to revise",
            "revisions": "str — what changed and why",
            "updated_fields": "dict — the specific parameter changes"
        }
    },
    "request_scout_intel": {
        "description": "Ask Scouts for specific market intelligence",
        "params": {
            "market": "str — which market",
            "question": "str — what you need to know",
            "urgency": "str — low/medium/high"
        }
    },
    "go_idle": {
        "description": "No actionable plan right now. Save your budget.",
        "params": {
            "reason": "str — why idle is the right call"
        }
    }
}
```

### Critic Actions

```python
CRITIC_ACTIONS = {
    "approve_plan": {
        "description": "This plan passes review. Green light for execution.",
        "params": {
            "plan_id": "int — which plan",
            "assessment": "str — what makes this plan sound",
            "risk_notes": "str — risks the Operator should be aware of",
            "confidence": "int — 1-10, how confident in approval"
        }
    },
    "reject_plan": {
        "description": "This plan has fatal flaws. Do not execute.",
        "params": {
            "plan_id": "int — which plan",
            "reasons": "str — specific reasons for rejection",
            "fatal_flaws": "list[str] — the dealbreakers"
        }
    },
    "request_revision": {
        "description": "Plan has potential but needs changes before approval.",
        "params": {
            "plan_id": "int — which plan",
            "issues": "str — what needs to change",
            "suggestions": "str — how to fix it"
        }
    },
    "flag_risk": {
        "description": "Raise a risk concern visible to the entire ecosystem.",
        "params": {
            "risk_type": "str — market/position/systemic/agent",
            "description": "str — what the risk is",
            "severity": "str — low/medium/high/critical",
            "affected_agents": "list[str] — who is exposed"
        }
    },
    "go_idle": {
        "description": "No plans to review. Nothing to flag.",
        "params": {
            "reason": "str — why idle"
        }
    }
}
```

### Operator Actions

```python
OPERATOR_ACTIONS = {
    "execute_trade": {
        "description": "Enter a position based on an approved plan.",
        "params": {
            "plan_id": "int — which approved plan",
            "market": "str — trading pair",
            "direction": "str — long/short",
            "order_type": "str — market/limit",
            "limit_price": "float|null — for limit orders",
            "position_size_usd": "float — dollar amount",
            "stop_loss": "float — stop loss price",
            "take_profit": "float — take profit price"
        }
    },
    "adjust_position": {
        "description": "Modify an existing position's parameters.",
        "params": {
            "position_id": "int — which position",
            "new_stop_loss": "float|null",
            "new_take_profit": "float|null",
            "add_size_usd": "float|null — increase position (goes through Warden)",
            "reduce_size_pct": "float|null — reduce position by this %"
        }
    },
    "close_position": {
        "description": "Exit a position entirely.",
        "params": {
            "position_id": "int — which position",
            "order_type": "str — market/limit",
            "limit_price": "float|null",
            "reason": "str — why closing"
        }
    },
    "hedge": {
        "description": "Open a hedge against an existing position.",
        "params": {
            "position_id": "int — position being hedged",
            "hedge_market": "str — what to trade as hedge",
            "hedge_direction": "str — long/short",
            "hedge_size_usd": "float",
            "thesis": "str — why this hedge helps"
        }
    },
    "go_idle": {
        "description": "No trades to make or manage right now.",
        "params": {
            "reason": "str — why idle"
        }
    }
}
```

---

## MEMORY ARCHITECTURE — THE THREE TIERS

### Tier 1: Working Memory (This Cycle Only)

- The assembled context window
- Exists only during the API call
- Gone after the cycle completes
- Analogy: your conscious awareness right now

### Tier 2: Short-Term Memory (Last 50 Cycles)

- Stored in Redis for fast access: `agent:{id}:recent_cycles`
- Full cycle records including self-notes and outcomes
- FIFO — oldest entries pushed out as new ones arrive
- Used by Context Assembler to populate "recent history" section
- Analogy: your memory of the last few days

### Tier 3: Long-Term Memory (Persistent Until Death)

- Stored in PostgreSQL: `agent_long_term_memory` table
- Curated by the agent itself during reflection cycles (memory_promotion / memory_demotion)
- Contains:
  - Confirmed lessons ("SOL volatile during Asian session" — confirmed 3x)
  - Performance patterns ("I perform well in trending markets, poorly in chop")
  - Relationship assessments ("Scout-7 reliable: 4/5 tips confirmed")
  - Strategy preferences that emerged from experience (NOT pre-programmed)
  - Reflection summaries
- Compressed — not full cycle records, just extracted wisdom
- Analogy: your life experience and personality

### Memory Transfer on Reproduction

When an agent reproduces:
- **Long-term memory** is passed to offspring via the Mentor System
- Offspring starts with parent's (and grandparent's) accumulated wisdom
- This is how dynasties compound knowledge across generations
- Offspring can demote inherited memories that don't match their own experience

---

## DATABASE SCHEMA ADDITIONS

Create a new Alembic migration for Phase 3A:

**New table: `agent_cycles`**
```
id                  SERIAL PRIMARY KEY
agent_id            INT FK → agents
cycle_number        INT
cycle_type          VARCHAR (normal/reflection/survival)
timestamp           TIMESTAMP WITH TIME ZONE
context_mode        VARCHAR (normal/crisis/hunting/survival)
context_tokens      INT
situation           TEXT
confidence_score    INT (1-10)
confidence_reason   TEXT
recent_pattern      TEXT
action_type         VARCHAR
action_params       JSONB
reasoning           TEXT
self_note           TEXT
validation_passed   BOOLEAN
validation_retries  INT DEFAULT 0
warden_flags        INT DEFAULT 0
outcome             TEXT NULLABLE
outcome_pnl         FLOAT NULLABLE
input_tokens        INT
output_tokens       INT
api_cost_usd        FLOAT
cycle_duration_ms   INT
api_latency_ms      INT
created_at          TIMESTAMP DEFAULT NOW()
```

**New table: `agent_long_term_memory`**
```
id                  SERIAL PRIMARY KEY
agent_id            INT FK → agents
memory_type         VARCHAR (lesson/pattern/relationship/reflection/inherited)
content             TEXT
confidence          FLOAT (0.0 to 1.0 — how sure the agent is about this memory)
source              VARCHAR (self/parent/grandparent)
source_cycle        INT NULLABLE (which cycle created this)
times_confirmed     INT DEFAULT 0
times_contradicted  INT DEFAULT 0
promoted_at         TIMESTAMP
demoted_at          TIMESTAMP NULLABLE
is_active           BOOLEAN DEFAULT TRUE
created_at          TIMESTAMP DEFAULT NOW()
```

**New table: `agent_reflections`**
```
id                  SERIAL PRIMARY KEY
agent_id            INT FK → agents
cycle_number        INT (the reflection cycle number)
what_worked         TEXT
what_failed         TEXT
pattern_detected    TEXT
lesson              TEXT
confidence_trend    VARCHAR (improving/stable/declining)
confidence_reason   TEXT
strategy_note       TEXT NULLABLE
memory_promotions   JSONB (list of self-notes promoted)
memory_demotions    JSONB (list of memories demoted)
created_at          TIMESTAMP DEFAULT NOW()
```

**Updates to `agents` table (add columns if not present):**
```
cycle_count         INT DEFAULT 0
last_cycle_at       TIMESTAMP NULLABLE
avg_cycle_cost      FLOAT DEFAULT 0.0
avg_cycle_tokens    INT DEFAULT 0
idle_rate           FLOAT DEFAULT 0.0 (% of cycles that were go_idle)
validation_fail_rate FLOAT DEFAULT 0.0
warden_violation_count INT DEFAULT 0
current_context_mode VARCHAR DEFAULT 'normal'
api_temperature     FLOAT NULLABLE (per-agent override, null = use role default)
watched_markets     JSONB DEFAULT '[]'
```

Run migration: `alembic revision --autogenerate -m "phase_3a_thinking_cycle"`
Then: `alembic upgrade head`

---

## IMPLEMENTATION STEPS

### STEP 1 — Verify Phase 2D Foundation

Before building anything, confirm:
- .venv activates and all dependencies are importable
- PostgreSQL database is accessible with all Phase 2 tables
- Redis/Memurai responds to PING
- All Phase 2D files exist and the frontend works
- Tests pass: `python -m pytest tests/ -v`

If anything is broken, fix it before proceeding.

---

### STEP 2 — Add Phase 3A Dependencies

Add to requirements.txt and install:
- `tiktoken` — OpenAI's token counter (works for estimating Claude token counts)
- `jsonschema` — JSON schema validation for agent outputs

Run: `pip install -r requirements.txt`

---

### STEP 3 — Database Migration

Create and run the Alembic migration for the three new tables and agent table updates described above.

---

### STEP 4 — Budget Gate (src/agents/budget_gate.py)

Implement the BudgetGate class exactly as specified in Phase 0 of the thinking cycle. This is the pre-cycle check that determines if an agent can afford to think.

Include:
- NORMAL / SURVIVAL_MODE / SKIP_CYCLE status returns
- Rolling average cost calculation from last 20 cycles
- Agora broadcast on resource_critical
- Logging of all budget decisions

---

### STEP 5 — Context Assembler (src/agents/context_assembler.py)

This is the largest and most important module. Implement:

- Dynamic mode detection (NORMAL/CRISIS/HUNTING/SURVIVAL)
- Token budget allocation per mode
- Mandatory context assembly (identity, state, assignments, warden limits)
- Priority context with relevance scoring and ranking
- Long-term memory injection with active/inactive filtering
- Token counting (use tiktoken for estimation)
- Context serialization into the prompt format

The Context Assembler must be testable — feed it a mock agent state and verify it produces correctly structured, correctly sized context.

---

### STEP 6 — Output Validator (src/agents/output_validator.py)

Implement the validation pipeline:
- JSON parsing with error capture
- Schema validation against role-specific output schemas
- Action space verification
- Warden pre-check integration (call existing Warden trade gate for trade actions)
- Sanity checks (position size vs budget, etc.)
- Retry logic with repair prompts for malformed JSON (one retry, double tax)
- Failure logging and categorization

---

### STEP 7 — Action Executor (src/agents/action_executor.py)

Implement action routing:
- Route each action type to the appropriate system
- Scout actions → Agora broadcasts
- Strategist actions → Plan database + Agora
- Critic actions → Plan status updates + Agora
- Operator actions → Warden trade queue → Paper Trading (Phase 3C placeholder for now)
- Universal go_idle → log only
- Return ActionResult with success/failure, details, and cost

For Operator trade actions, create a placeholder that logs the trade request and returns a mock result. The actual Paper Trading engine is Phase 3C.

---

### STEP 8 — Cycle Recorder (src/agents/cycle_recorder.py)

Implement the black box recorder:
- Write full cycle record to PostgreSQL (agent_cycles table)
- Post summary to Agora agent-activity channel
- Update agent's short-term memory in Redis (lpush + ltrim to 50)
- Update agent's running stats (cycle_count, total_api_cost, etc.)
- Update idle rate and validation fail rate (rolling calculations)

---

### STEP 9 — Memory Manager (src/agents/memory_manager.py)

Implement the three-tier memory system:
- Working memory: handled by Context Assembler (no persistent storage)
- Short-term memory: Redis list operations (read last N, push new, trim)
- Long-term memory: PostgreSQL CRUD for agent_long_term_memory table
- Reflection processing: promote/demote memories based on reflection output
- Memory inheritance: copy parent's long-term memory to offspring (used in Phase 3F)
- Memory retrieval for context assembly: get active long-term memories, sorted by confidence

---

### STEP 10 — Cycle Scheduler (src/agents/cycle_scheduler.py)

Implement the scheduling system:
- Base frequency per role (configurable in SyndicateConfig)
- Interrupt triggers from Agora events (subscribe to relevant Redis channels)
- Interrupt cooldown (60 seconds minimum between cycles per agent)
- Priority queue in Redis (critical > interrupt > scheduled > idle)
- Sequential cycle processing (one at a time for Phase 3A)
- Drift correction: if a cycle takes longer than expected, adjust next scheduled time
- Main processing loop that pops from queue and runs thinking cycles

---

### STEP 11 — The Thinking Cycle Engine (src/agents/thinking_cycle.py)

The master orchestrator that ties everything together. This is the `run(agent_id)` function:

```
async def run_cycle(agent_id):
    agent = load_agent(agent_id)
    
    # Phase 0: Budget Check
    budget_status = budget_gate.check(agent)
    if budget_status == SKIP:
        return CycleResult(skipped=True, reason="budget")
    
    # Determine cycle type
    is_reflection = (agent.cycle_count % 10 == 0) and (agent.cycle_count > 0)
    
    # Phase 1: Observe
    context = context_assembler.assemble(
        agent, 
        mode=budget_status,
        cycle_type="reflection" if is_reflection else "normal"
    )
    
    # Phase 2: Orient + Decide (or Reflect)
    system_prompt = build_system_prompt(agent, is_reflection)
    user_prompt = build_user_prompt(context, is_reflection)
    
    api_response = await call_claude_api(
        system=system_prompt,
        user=user_prompt,
        temperature=get_temperature(agent),
        model="claude-sonnet-4-20250514"
    )
    
    # Phase 3: Validate
    validation = output_validator.validate(agent, api_response.content)
    if not validation.passed:
        if validation.retryable:
            # One retry with repair prompt
            api_response = await retry_with_repair(agent, api_response, validation)
            validation = output_validator.validate(agent, api_response.content)
        if not validation.passed:
            # Log failure, record cycle, move on
            cycle_recorder.record_failed(agent, validation, api_response)
            return CycleResult(failed=True, reason=validation.failure_type)
    
    # Phase 4: Act
    if is_reflection:
        result = await process_reflection(agent, validation.parsed)
    else:
        result = await action_executor.execute(agent, validation.parsed)
    
    # Phase 5: Record
    cycle_recorder.record(agent, context, api_response, validation, result)
    
    return CycleResult(success=True, action=validation.parsed.action.type)
```

---

### STEP 12 — Role Definitions (src/agents/roles.py)

Create the role configuration module:
- Define each role (scout, strategist, critic, operator) with:
  - Available actions (the action space dictionaries from above)
  - Default temperature
  - Default cycle interval
  - Role description (for system prompt)
  - Output schema (for validation)
  - Average context budget

---

### STEP 13 — Claude API Client (src/agents/claude_client.py)

Create a thin wrapper around the Anthropic API:
- Send system + user prompts
- Track token usage (input + output)
- Calculate cost per call
- Handle API errors (timeout, rate limit, server error) with retries
- Log all API calls with timing and cost
- Respect daily budget limits

Use `anthropic` Python SDK. Add to requirements.txt if not already present.

---

### STEP 14 — Tests

**tests/test_budget_gate.py:**
- Test NORMAL status when budget is healthy
- Test SURVIVAL_MODE when budget is low
- Test SKIP_CYCLE when budget is exhausted
- Test rolling average calculation

**tests/test_context_assembler.py:**
- Test mode detection (normal, crisis, hunting, survival)
- Test token budget allocation per mode
- Test relevance scoring and ranking
- Test that assembled context fits within token budget
- Test mandatory context is always included

**tests/test_output_validator.py:**
- Test valid JSON passes
- Test malformed JSON fails with retryable flag
- Test invalid action type fails with non-retryable flag
- Test Warden rejection for oversized trades
- Test sanity check catches impossible parameters

**tests/test_cycle_scheduler.py:**
- Test base frequency scheduling
- Test interrupt triggering
- Test cooldown enforcement
- Test priority ordering in queue

**tests/test_memory_manager.py:**
- Test short-term memory push/trim
- Test long-term memory promotion from reflection
- Test long-term memory demotion
- Test memory inheritance (copy parent to offspring)

**tests/test_thinking_cycle.py (integration):**
- Test full cycle with mock Claude API response
- Test reflection cycle every 10th cycle
- Test failed validation with retry
- Test budget exhaustion skips cycle
- Test the full pipeline: budget → context → API → validate → act → record

Run all: `python -m pytest tests/ -v`

---

### STEP 15 — Configuration Updates

Add to SyndicateConfig:
```
# Phase 3A: Thinking Cycle
scout_cycle_interval: int = 300
strategist_cycle_interval: int = 900
operator_active_cycle_interval: int = 60
operator_idle_cycle_interval: int = 900
interrupt_cooldown_seconds: int = 60
max_retries_per_cycle: int = 1
retry_tax_multiplier: float = 2.0
reflection_every_n_cycles: int = 10
short_term_memory_size: int = 50
context_token_budget_normal: int = 3000
context_token_budget_survival: int = 1500

# API Temperature defaults
scout_temperature: float = 0.7
strategist_temperature: float = 0.5
critic_temperature: float = 0.2
operator_temperature: float = 0.2
```

Update .env.example with new variables.

---

### STEP 16 — Update CLAUDE.md

Add Phase 3A components to the architecture section:
- Thinking Cycle Engine (src/agents/thinking_cycle.py)
- Budget Gate, Context Assembler, Output Validator, Action Executor, Cycle Recorder
- Memory Manager (three-tier architecture)
- Cycle Scheduler (sequential processing with interrupt support)
- Role Definitions
- Claude API Client

Update Phase Roadmap to show Phase 3A as COMPLETE.

---

### STEP 17 — Update CHANGELOG.md and CURRENT_STATUS.md

Log everything built in this session.

---

### STEP 18 — Git Commit and Push

```
git add .
git commit -m "Phase 3A: The Agent Thinking Cycle — OODA loop, memory, scheduling"
git push origin main
```

---

## DESIGN DECISIONS (Reference for Claude Code)

These decisions were made in the War Room (Claude.ai chat) and are final:

1. **OODA Loop architecture:** Every agent runs the same 6-phase cycle (Budget → Observe → Orient+Decide → Validate → Act → Record)
2. **Single API call per cycle:** Orient and Decide happen in one call. No multi-step reasoning chains (yet).
3. **Temperature per role:** Scout 0.7, Strategist 0.5, Critic 0.2, Operator 0.2. Configurable per-agent for future evolution.
4. **Dynamic context budgets:** Four modes (Normal, Crisis, Hunting, Survival) shift token allocation based on agent state.
5. **Verbosity is a choice:** No hard word limits on reasoning. Thinking tax is the natural regulator.
6. **Sequential cycle processing:** One cycle at a time through the queue. Parallel processing deferred to Phase 4.
7. **Reflection every 10 cycles:** Mandatory self-review. Agent curates own long-term memory through promotion/demotion.
8. **Three-tier memory:** Working (this cycle) → Short-term (last 50 cycles, Redis) → Long-term (persistent, PostgreSQL).
9. **Bounded action spaces:** Agents pick from role-specific menus. Reasoning is free-form, output is structured JSON.
10. **One retry for malformed output:** Costs double thinking tax. Second failure = failed cycle, no action.
11. **Interrupt system with cooldown:** Agora events can wake agents early, but minimum 60 seconds between cycles per agent.
12. **Paper trading placeholder:** Operator trade actions create mock results in Phase 3A. Real paper trading engine is Phase 3C.

---

## DEFERRED ITEMS (Tracked for Future Phases)

The following items were identified during Phase 3A design and belong in later phases:

**Phase 3B (Cold Start Boot Sequence):**
- First-cycle cold start problem (zero memory orientation)
- Library integration during agent spawning
- Inter-agent workflow pipeline design (Scout → Strategist → Critic → Operator)

**Phase 3D (Evaluation Cycle):**
- Cross-agent position awareness (Warden injecting portfolio-level context)
- Gaming self-notes protection (quantitative > qualitative in Genesis evaluation)
- Idle rate tracking as an evaluation metric
- Genesis evaluation weighting: actions over words

**Phase 2 Backlog (Internal Economy):**
- Internal Economy actions in agent action spaces (request_intel, offer_intel, hire_agent, trade_reputation)
- These actions get added to all role menus once the economy system is active

---

Before you start, confirm you've read CLAUDE.md and the current project state. Then proceed through each step in order. Ask me if anything is unclear.
