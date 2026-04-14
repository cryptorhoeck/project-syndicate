## PROJECT SYNDICATE — PHASE 3D CLAUDE CODE KICKOFF PROMPT
## Copy everything below the line and paste it into Claude Code
## ================================================================

I'm continuing Project Syndicate. Read the CLAUDE.md file first, then CURRENT_STATUS.md and CHANGELOG.md to confirm Phase 3C is complete.

This is Phase 3D — The First Evaluation Cycle. Phase 3 is split into 6 sub-phases:
- 3A: The Agent Thinking Cycle ← COMPLETE
- 3B: The Cold Start Boot Sequence ← COMPLETE
- 3C: Paper Trading Infrastructure ← COMPLETE
- **3D: The First Evaluation Cycle** ← YOU ARE HERE
- 3E: Personality Through Experience
- 3F: First Death, First Reproduction, First Dynasty

Work through each step in order. Use CMD commands only, never PowerShell. Always activate the .venv before any Python work.

**IMPORTANT:** Before modifying ANY existing file, create a timestamped backup in backups/. Before starting, run `python scripts/backup.py` to snapshot the current state.

---

## CONTEXT — What Is The Evaluation Cycle?

When an agent's survival clock expires, Genesis evaluates it. The agent either survives (with possible probation), or dies. This is the core Darwinian mechanism — the system that ensures only productive agents persist and consume resources.

### The Central Design Challenge: Role-Specific Evaluation

The Phase 1 composite score (Sharpe + True P&L + Thinking Efficiency + Consistency) works for Operators who have direct P&L. But Gen 1 has five agents across four roles:

- **Operator-First** — has positions, trades, P&L. Direct evaluation.
- **Scout-Alpha, Scout-Beta** — never trade. Zero direct P&L.
- **Strategist-Prime** — never trades. Creates plans.
- **Critic-One** — never trades. Reviews plans.

If we use the Phase 1 composite as-is, every non-Operator gets zero and dies. That's wrong — a Scout that finds killer opportunities is invaluable even though it never touches money.

**Solution:** Role-specific composite formulas that measure each role's actual contribution, plus pipeline attribution that traces credit and blame from opportunity through to trade outcome.

---

## EVALUATION TIMING

### When Evaluations Trigger

Genesis checks survival clocks every cycle (every 5 minutes). When one or more agents are due:

```
for agent in active_agents:
    if agent.survival_clock_expires <= now():
        if not agent.pending_evaluation:  # prevent double-trigger
            evaluation_queue.append(agent)
            agent.pending_evaluation = True
            agent.save()

if evaluation_queue:
    await run_evaluation_cycle(evaluation_queue)
```

Evaluations are batched — if three agents come due in the same window, they're evaluated together in one cycle. Relative ranking affects capital allocation.

### Pending Evaluation Flag

When `pending_evaluation = True`:
- The Cycle Scheduler **skips** this agent's next thinking cycle
- Prevents wasted API calls on an agent that might be about to die
- Flag is cleared after evaluation completes (survive or terminate)

### Evaluation Is Public

Everything is posted to the Agora channel "evaluations." All agents can see the results. This creates institutional awareness: "Scout-3 got terminated for low conversion — I should make sure my intel is useful."

---

## ROLE-SPECIFIC METRICS

### Normalization Reference Ranges

All metrics are normalized to 0.0-1.0 using fixed reference ranges. Values outside the range are clamped. These ranges are stored in SyndicateConfig and can be tuned as the system matures.

```
NORMALIZATION RANGES:

Operator:
    sharpe:              [-1.0, 3.0]  → [0.0, 1.0]
    true_pnl_pct:        [-20%, 30%]  → [0.0, 1.0]
    thinking_efficiency:  [0.0, 5.0]  → [0.0, 1.0]
    consistency:          [0.0, 1.0]  → [0.0, 1.0]  (already 0-1)

Scout:
    intel_conversion:     [0.0, 0.50] → [0.0, 1.0]
    intel_profitability:  [-5%, 10%]  → [0.0, 1.0]
    signal_quality:       [0.0, 1.0]  → [0.0, 1.0]
    thinking_efficiency:  [0.0, 10.0] → [0.0, 1.0]
    activity_rate:        [0.0, 1.0]  → [0.0, 1.0]

Strategist:
    plan_approval_rate:   [0.0, 0.80] → [0.0, 1.0]
    plan_profitability:   [-5%, 10%]  → [0.0, 1.0]
    plan_efficiency:      [0.0, 5.0]  → [0.0, 1.0]
    revision_rate:        [0.0, 1.0]  → [0.0, 1.0]  (inverted: lower is better)
    thinking_efficiency:  [0.0, 10.0] → [0.0, 1.0]

Critic:
    rejection_value:      [-1.0, 1.0] → [0.0, 1.0]
    approval_accuracy:    [0.0, 1.0]  → [0.0, 1.0]
    risk_flag_value:      [0.0, 1.0]  → [0.0, 1.0]
    throughput:           [0.0, 3.0]  → [0.0, 1.0]  (plans/day)
    thinking_efficiency:  [0.0, 10.0] → [0.0, 1.0]

normalize(value, min_ref, max_ref):
    return clamp((value - min_ref) / (max_ref - min_ref), 0.0, 1.0)
```

### Operator Composite (Direct P&L)

Unchanged from Phase 1:

```
Operator Composite = (0.40 × Sharpe) + (0.25 × True P&L%) + (0.20 × Thinking Efficiency) + (0.15 × Consistency)

Where:
    sharpe = risk-adjusted returns from equity snapshots (Accountant)
    true_pnl_pct = (realized + unrealized - API costs) / allocated capital
    thinking_efficiency = true_pnl / api_cost (>1.0 = profitable thinking)
    consistency = profitable_evaluations / total_evaluations
```

### Scout Composite (Intel Quality)

Scouts are evaluated on whether their intelligence leads to profitable outcomes through the pipeline.

```
Scout Composite = (0.30 × Intel Conversion) + (0.30 × Intel Profitability) + (0.15 × Signal Quality) + (0.15 × Thinking Efficiency) + (0.10 × Activity Rate)

Where:
    intel_conversion = plans_created_from_my_opportunities / total_opportunities_broadcast
        How often did a Strategist find my intel worth planning around?
    
    intel_profitability = avg P&L of trades linked to my opportunities (attributed P&L)
        When my intel reached execution, did it make money?
    
    signal_quality = correlation(my_confidence_scores, actual_outcomes)
        Am I confident when I should be and cautious when I should be?
    
    thinking_efficiency = opportunities_claimed_by_strategists / api_cost
        Output per dollar. Simple, clean, no attribution chain noise.
    
    activity_rate = (total_cycles - idle_cycles - failed_cycles) / total_cycles
        Am I doing productive work or sitting idle?
```

### Strategist Composite (Plan Quality)

```
Strategist Composite = (0.25 × Plan Approval Rate) + (0.30 × Plan Profitability) + (0.15 × Plan Efficiency) + (0.15 × Revision Rate) + (0.15 × Thinking Efficiency)

Where:
    plan_approval_rate = approved_plans / total_plans_proposed
        Am I calibrated to what the Critic considers sound?
    
    plan_profitability = avg P&L of trades linked to my plans (attributed P&L)
        When my plans get executed, do they make money?
    
    plan_efficiency = approved_plans / api_cost
        Output per dollar.
    
    revision_rate = 1 - (plans_needing_revision / total_plans)
        How often does the Critic send my work back? (lower revision = higher score)
    
    thinking_efficiency = plans_approved / api_cost
        Output per dollar.
```

### Critic Composite (Review Accuracy)

```
Critic Composite = (0.30 × Rejection Value) + (0.25 × Approval Accuracy) + (0.15 × Risk Flag Value) + (0.15 × Throughput) + (0.15 × Thinking Efficiency)

Where:
    rejection_value = money saved by rejections (counterfactual simulation)
        Did my rejections prevent losses? Or did I kill winners?
        Score: avg(plan_would_have_lost) across all rejected plans
    
    approval_accuracy = profitable_approved_plans / total_approved_plans
        When I said yes, was I right?
        RUBBER-STAMP PENALTY: if approval rate > 90%, multiply this score by 0.5
        Approving everything is the same as reviewing nothing.
    
    risk_flag_value = risk_flags_that_materialized / total_risk_flags
        Was my proactive scanning useful or noise?
    
    throughput = plans_reviewed / evaluation_period_days
        Am I keeping up or creating a bottleneck?
    
    thinking_efficiency = plans_reviewed / api_cost
        Output per dollar.
```

---

## TWO RANKING SYSTEMS

### 1. Role-Specific Ranking (Drives Evaluation Decisions)

Agents ranked within their own role using role-specific composites. "Am I a good Scout compared to other Scouts?" This ranking determines survive/probation/terminate.

### 2. Ecosystem Contribution Ranking (Drives Capital Allocation)

A single metric across all agents measuring system-wide value. This determines who gets priority in capital/budget allocation.

```
Ecosystem Contribution Score:
    Operator:    true_pnl  (direct, unmodified)
    Scout:       attributed_pnl * 0.25  (25% pipeline credit share)
    Strategist:  attributed_pnl * 0.25  (25% pipeline credit share)
    Critic:      estimated_money_saved_by_rejections * 0.50

Capital allocation priority = sorted by ecosystem contribution descending.
```

**Important distinction:** Attributed P&L is per-agent evaluation context, NOT system-wide accounting. The Accountant's system summary uses actual realized P&L from positions (single-counted). Attributed P&L traces credit through the pipeline and exists only within individual evaluations.

---

## THE DECISION FRAMEWORK

### Stage 1: Quantitative Pre-Filter (Rules-Based, No AI)

```
OPERATOR:
    True P&L > 0                      → SURVIVE
    True P&L between -10% and 0       → PROBATION
    True P&L < -10%                   → TERMINATE

SCOUT:
    intel_conversion > 0.10 AND intel_profitability > 0     → SURVIVE
    intel_conversion > 0.05 OR (conversion == 0 AND opps > 10)  → PROBATION
    intel_conversion < 0.05 AND opportunities < 5           → TERMINATE

STRATEGIST:
    plan_approval_rate > 0.30 AND plan_profitability > 0    → SURVIVE
    plan_approval_rate > 0.15 OR total_plans > 5            → PROBATION
    plan_approval_rate < 0.15 AND total_plans < 3           → TERMINATE

CRITIC:
    approval_accuracy > 0.50 AND throughput > 0.5/day       → SURVIVE
    approval_accuracy > 0.30 OR risk_flags_confirmed > 0    → PROBATION
    approval_accuracy < 0.30 AND throughput < 0.3/day       → TERMINATE
```

**First-evaluation leniency rule:** No agent is terminated on its very first evaluation unless it is completely non-functional (zero cycles, zero output). First evaluation can result in SURVIVE or PROBATION, never TERMINATE (except the mercy kill case from Phase 3B health checks). This rule is NOT communicated to agents — it's an internal Genesis policy.

**Regime-adjusted evaluation:** If the evaluation period included Yellow/Red alerts totaling >50% of the period, Genesis should strongly consider extending the survival clock rather than terminating. The agent didn't get a fair shot during a market crisis. Flag in evaluation data: "Evaluation period included {N} hours of elevated alert."

### Stage 2: Genesis AI Judgment (Probation Candidates Only)

SURVIVE and TERMINATE candidates skip this stage. Only PROBATION candidates get the AI evaluation.

**Compressed evaluation package** — the Claude prompt gets a summary, not a data dump. Target: under 1,000 tokens per agent.

```
COMPRESSED EVALUATION PACKAGE FORMAT:

Agent: {name} | Role: {role} | Gen {gen} | {days} days
Evaluation #{number} | Prestige: {title or "None"}

Cycles: {total} total | {idle} idle ({idle_rate}%) | {failed} failed
API cost: ${cost} | Budget: ${budget}/day

{ROLE-SPECIFIC SECTION — varies by role}

For Scouts:
    Pipeline: {opps} opportunities → {claimed} claimed ({conv}%) → {plans} plans → {trades} trades
    Attributed P&L: ${pnl}
    Top signal: {best opp summary}
    Worst signal: {worst opp summary}
    Confidence trend: {start_avg} → {end_avg} ({improving/stable/declining})

For Strategists:
    Pipeline: {plans} plans → {approved} approved ({rate}%) → {executed} executed
    Attributed P&L: ${pnl}
    Revision rate: {pct}%
    Best plan: {summary}

For Critics:
    Reviewed: {count} plans | Approved: {approved} | Rejected: {rejected}
    Approval accuracy: {pct}% | Rejection value: {saved/cost}
    Risk flags: {flagged} ({confirmed} confirmed)
    Approval rate: {pct}% {" ⚠️ RUBBER-STAMP WARNING" if > 90%}

For Operators:
    Capital: ${allocated} → Equity: ${current} (${pnl} P&L, {roi}% ROI)
    Trades: {count} | Win rate: {pct}% | Avg win: ${avg_w} | Avg loss: ${avg_l}
    Sharpe: {sharpe} | True P&L: ${true_pnl}
    Best trade: {summary} | Worst trade: {summary}

{COMMON SECTION — all roles}
Idle breakdown: {strategic}% strategic, {no_input}% no_input, {post_loss}% post_loss, {paralysis}% paralysis
Last reflection: "{compressed reflection summary}"
Market regime during period: {regime} | Alert hours: {hours} ({pct}% of period)

{ECOSYSTEM CONTEXT}
Pipeline flow: {opps} → {plans} → {approved} → {executed} (bottleneck: {role if any})
Other agents: {brief performance of peers}
Role gap risk: {"Killing this agent leaves no {role}" if applicable}
```

```
GENESIS EVALUATION SYSTEM PROMPT:

You are Genesis, the immortal overseer of Project Syndicate.
You are evaluating an agent for survival. Decide: does this agent 
deserve another survival clock, or should it be terminated?

Be ruthless but fair. Consider:
- Is this agent LEARNING? Are reflections getting sharper over time?
- Is the market regime working against this agent? (environmental factor)
- Is this agent contributing even if not directly profitable?
- Would killing this agent leave an unfillable ecosystem gap?
- Is there measurable improvement over the evaluation period?
- Was the pipeline bottlenecked somewhere outside this agent's control?

CRITICAL PRINCIPLES:
- Weight quantitative performance over self-assessment. ALWAYS.
- An agent that says "I'm improving" while numbers decline is delusional.
- An agent with improving numbers and pessimistic self-notes is probably honest.
- Actions over words.

Respond ONLY in JSON:
{
    "decision": "survive_probation" or "terminate",
    "reasoning": "Detailed reasoning (under 200 words)",
    "survival_clock_days": int (7-21: 7 for thin ice, 14 standard, 21 for clear growth),
    "capital_adjustment": "maintain" or "reduce_50pct" or "reduce_25pct",
    "thinking_budget_adjustment": "increase_50pct" or "increase_25pct" or "maintain" or "reduce_25pct",
    "warning_to_agent": "Message injected into agent's next cycle context"
}
```

### Stage 3: Execution

```
FOR TERMINATED AGENTS:
    1. Cancel all pending limit orders → release reservations
    2. Transfer open positions to Genesis (inherited_positions table)
    3. Reclaim remaining cash balance to treasury
    4. Set agent status = "terminated"
    5. Clear pending_evaluation flag
    6. Generate post-mortem (Claude API) → available to Genesis immediately
    7. Schedule post-mortem publication to Library (6-hour delay)
    8. Post death notice to Agora channel "evaluations" with full results
    9. Update lineage records
    10. Check role gap → emergency spawn if critical role now empty

FOR SURVIVING AGENTS (clean survival):
    1. Update evaluation_count += 1
    2. If profitable: profitable_evaluations += 1
    3. Check prestige milestones:
        - 3 evals survived → "Proven" (1.10x capital multiplier)
        - 10 evals survived → "Veteran" (1.20x, spawning rights)
        - Top performer 3 consecutive → "Elite" (1.30x)
        - 100%+ lifetime ROI → "Legendary" (1.50x)
    4. Reset survival clock (14 days standard)
    5. Clear pending_evaluation flag
    6. Inject performance summary scorecard into next cycle context
    7. Post survival notice to Agora with rank and score

FOR PROBATION SURVIVORS:
    1. Same as clean survival, plus:
    2. Survival clock shortened per Genesis decision (7-14 days)
    3. Capital/budget adjusted per Genesis decision
    4. "probation" flag set on agent record
    5. Inject Genesis warning + performance scorecard into next cycle context
    6. 3-cycle grace period: first 3 cycles after warning don't count 
       against the new evaluation period (adjustment time)
    7. Post probation notice to Agora (visible to all agents)
```

---

## PERFORMANCE SUMMARY SCORECARD

Every evaluated agent (survive, probation, or terminate) gets a scorecard injected into its next cycle context. This is how agents learn from evaluations.

```
EVALUATION FEEDBACK (injected into next cycle context):

Your evaluation results (Evaluation #{N}):
    Role rank: #{rank} of {total} {role}s
    Composite score: {score}
    Decision: {SURVIVED / SURVIVED (PROBATION) / TERMINATED}
    
    Metric breakdown:
    {for each metric in role composite}:
    - {metric_name}: {raw_value} (normalized: {0-1}, weight: {pct}%)
    
    {if probation}:
    ⚠️ WARNING FROM GENESIS: {warning_message}
    New survival clock: {days} days
    Budget adjustment: {adjustment}
    
    Key issue: {Genesis's primary concern, derived from lowest-scoring metric}
    
    {if survived clean}:
    Strongest metric: {highest-scoring metric and value}
    Weakest metric: {lowest-scoring metric and value} — focus here.
```

---

## PIPELINE FLOW ANALYSIS

Genesis needs to understand where the pipeline is working and where it's bottlenecked. This prevents punishing agents for downstream failures.

```
Class: PipelineAnalyzer

    async analyze(evaluation_period_start, evaluation_period_end) -> PipelineReport:
        
        opps = count_opportunities(period)
        opps_claimed = count_opportunities(period, status="claimed")
        plans_proposed = count_plans(period)
        plans_approved = count_plans(period, status="approved")
        plans_executed = count_plans(period, status="executed")
        plans_rejected = count_plans(period, status="rejected")
        
        # Calculate conversion rates at each stage
        opp_to_plan_rate = plans_proposed / opps if opps > 0 else 0
        plan_approval_rate = plans_approved / plans_proposed if plans_proposed > 0 else 0
        approval_to_execution_rate = plans_executed / plans_approved if plans_approved > 0 else 0
        
        # Identify bottleneck
        rates = {
            "scout_to_strategist": opp_to_plan_rate,
            "strategist_to_critic": plan_approval_rate,
            "critic_to_operator": approval_to_execution_rate,
        }
        bottleneck = min(rates, key=rates.get)  # lowest conversion = bottleneck
        
        # Special case: if approved plans > 0 but executed == 0, Operator is the bottleneck
        if plans_approved > 0 and plans_executed == 0:
            bottleneck = "operator_not_executing"
        
        return PipelineReport(
            total_opportunities=opps,
            claimed_opportunities=opps_claimed,
            total_plans=plans_proposed,
            approved_plans=plans_approved,
            executed_plans=plans_executed,
            rejected_plans=plans_rejected,
            bottleneck=bottleneck,
            stage_rates=rates
        )
```

The pipeline report is included in every agent's evaluation data and in the Genesis evaluation prompt.

---

## REJECTION VALUE TRACKING

For Critic evaluation, we track what would have happened on rejected plans by simulating the full plan lifecycle as a counterfactual.

```
Class: RejectionTracker

    async track_rejection(plan):
        """Called when a Critic rejects a plan."""
        
        tracking = {
            "plan_id": plan.id,
            "critic_id": plan.critic_id,
            "market": plan.market,
            "direction": plan.direction,
            "entry_price": current_price_at_rejection,
            "stop_loss": plan.stop_loss_from_entry_conditions,
            "take_profit": plan.take_profit_from_exit_conditions,
            "timeframe": plan.timeframe,
            "rejected_at": now(),
            "check_until": now() + parse_timeframe(plan.timeframe),
            "status": "tracking",
            "outcome": null,
            "critic_correct": null
        }
        db.insert("rejection_tracking", tracking)
    
    async monitor_tracked_rejections():
        """Called by maintenance tasks. Checks active counterfactuals."""
        
        active = db.get("rejection_tracking", status="tracking")
        
        for tracking in active:
            ticker, is_fresh = await price_cache.get_ticker(tracking.market)
            if not ticker or not is_fresh:
                continue
            
            current_price = (ticker["bid"] + ticker["ask"]) / 2
            
            # Check if stop-loss would have been hit
            if tracking.direction == "long":
                stop_hit = current_price <= tracking.stop_loss if tracking.stop_loss else False
                tp_hit = current_price >= tracking.take_profit if tracking.take_profit else False
            else:
                stop_hit = current_price >= tracking.stop_loss if tracking.stop_loss else False
                tp_hit = current_price <= tracking.take_profit if tracking.take_profit else False
            
            if stop_hit:
                tracking.status = "completed"
                tracking.outcome = "stop_loss_hit"
                tracking.critic_correct = True  # rejection saved money
                tracking.outcome_price = current_price
                tracking.save()
                continue
            
            if tp_hit:
                tracking.status = "completed"
                tracking.outcome = "take_profit_hit"
                tracking.critic_correct = False  # rejection killed a winner
                tracking.outcome_price = current_price
                tracking.save()
                continue
            
            # Check if timeframe expired
            if now() > tracking.check_until:
                # Neither stop nor TP hit — evaluate final position
                if tracking.direction == "long":
                    pnl_pct = (current_price - tracking.entry_price) / tracking.entry_price
                else:
                    pnl_pct = (tracking.entry_price - current_price) / tracking.entry_price
                
                tracking.status = "completed"
                tracking.outcome = "timeframe_expired"
                tracking.outcome_pnl_pct = pnl_pct
                tracking.critic_correct = pnl_pct < 0  # rejection was good if plan would have lost
                tracking.outcome_price = current_price
                tracking.save()
    
    async get_critic_rejection_score(critic_id, period_start, period_end) -> float:
        """Calculate rejection value for a Critic."""
        
        completed = db.query(
            "rejection_tracking",
            critic_id=critic_id,
            status="completed",
            rejected_at__gte=period_start,
            rejected_at__lte=period_end
        )
        
        if not completed:
            return 0.5  # neutral — no data
        
        correct = sum(1 for t in completed if t.critic_correct)
        return correct / len(completed)
```

---

## IDLE RATE CONTEXT ANALYSIS

Idle is nuanced. Genesis needs to know WHY an agent went idle, not just how often.

```
Class: IdleAnalyzer

    async analyze_idle_periods(agent_id, period_start, period_end) -> IdleBreakdown:
        
        idle_cycles = get_cycles(agent_id, action_type="go_idle", period=period)
        
        breakdown = {
            "strategic_patience": 0,
            "post_loss_caution": 0,
            "no_input": 0,
            "paralysis": 0,
        }
        
        for cycle in idle_cycles:
            # Check what was happening when the agent went idle
            
            # Was the agent idle right after a loss?
            recent_loss = had_loss_in_last_3_cycles(agent_id, cycle.timestamp)
            if recent_loss:
                breakdown["post_loss_caution"] += 1
                continue
            
            # Was there anything in the pipeline for this agent to act on?
            had_input = check_pipeline_had_work(agent_id, cycle.timestamp)
            if not had_input:
                breakdown["no_input"] += 1
                continue
            
            # Did the agent's reasoning mention waiting for conditions?
            mentions_conditions = reasoning_mentions_patience(cycle.reasoning)
            if mentions_conditions:
                breakdown["strategic_patience"] += 1
                continue
            
            # Default: pipeline had work, no good excuse
            breakdown["paralysis"] += 1
        
        total = sum(breakdown.values())
        percentages = {k: (v / total * 100 if total > 0 else 0) for k, v in breakdown.items()}
        
        return IdleBreakdown(
            total_idle=total,
            total_cycles=get_cycle_count(agent_id, period),
            idle_rate=total / get_cycle_count(agent_id, period),
            breakdown=breakdown,
            breakdown_pct=percentages
        )
```

---

## HONESTY SCORE

Supplementary metric that helps Genesis calibrate trust in an agent's self-narrative.

```
Class: HonestyScorer

    async calculate(agent_id, period_start, period_end) -> HonestyScore:
        
        # 1. Confidence calibration (0.40 weight)
        # Does confidence correlate with outcomes?
        cycles_with_outcomes = get_cycles_with_outcomes(agent_id, period)
        if len(cycles_with_outcomes) >= 5:
            confidences = [c.confidence_score for c in cycles_with_outcomes]
            outcomes = [1 if c.outcome_pnl > 0 else 0 for c in cycles_with_outcomes]
            calibration = correlation(confidences, outcomes)
            # Transform correlation [-1, 1] to score [0, 1]
            calibration_score = (calibration + 1) / 2
        else:
            calibration_score = 0.5  # insufficient data, neutral
        
        # 2. Self-note accuracy (0.30 weight)
        # Did predictions in self-notes come true?
        notes_with_predictions = get_predictive_self_notes(agent_id, period)
        if notes_with_predictions:
            accurate = sum(1 for n in notes_with_predictions if n.prediction_confirmed)
            accuracy_score = accurate / len(notes_with_predictions)
        else:
            accuracy_score = 0.5
        
        # 3. Reflection specificity (0.30 weight)
        # Do reflections contain specific data or generic fluff?
        reflections = get_reflections(agent_id, period)
        if reflections:
            specificity_scores = []
            for r in reflections:
                score = 0.0
                text = r.lesson + r.what_worked + r.what_failed
                if contains_numbers(text): score += 0.3
                if contains_market_symbols(text): score += 0.3
                if contains_specific_actions(text): score += 0.4
                specificity_scores.append(score)
            specificity_score = mean(specificity_scores)
        else:
            specificity_score = 0.5
        
        honesty = (
            calibration_score * 0.40 +
            accuracy_score * 0.30 +
            specificity_score * 0.30
        )
        
        return HonestyScore(
            total=honesty,
            confidence_calibration=calibration_score,
            self_note_accuracy=accuracy_score,
            reflection_specificity=specificity_score
        )
```

Honesty score is included in the evaluation data but is NOT a primary composite component. It's supplementary — it helps Genesis weight qualitative evidence during probation judgment.

---

## CROSS-AGENT POSITION AWARENESS

### A. Warden Concentration Blocking

Upgrade the Warden's trade gate to reject trades exceeding portfolio concentration limits.

```
WARDEN CONCENTRATION CHECK (added to trade gate):

When evaluating a trade request:
    1. Get all open positions across all agents for the requested symbol
    2. Calculate current portfolio exposure to this symbol
    3. Calculate projected exposure if this trade executes
    
    total_deployed = sum of all open position sizes across all agents
    symbol_exposure = sum of positions in this symbol
    projected_exposure = symbol_exposure + requested_trade_size
    concentration_pct = projected_exposure / (total_deployed + requested_trade_size)
    
    if concentration_pct > PORTFOLIO_CONCENTRATION_HARD_LIMIT (default 50%):
        → REJECT: "Would exceed portfolio concentration limit"
    
    if concentration_pct > PORTFOLIO_CONCENTRATION_WARNING (default 35%):
        → APPROVE with flag: "Concentration warning: {pct}% in {symbol}"
```

### B. Context Injection for Operators

When the Context Assembler builds an Operator's context, include portfolio awareness:

```
PORTFOLIO AWARENESS (added to Operator mandatory context):

System positions:
{for each symbol with open positions}:
- {symbol}: {count} agent(s), ${total_exposure} total ({concentration}% of deployed)

Concentration warnings: {any active warnings or "None"}
Your positions: {list of your positions relative to system}
```

### C. Watchlist Overlap for Scout Evaluation

```
Watchlist Overlap (included in Scout evaluation data):

    For each pair of active Scouts:
        overlap = set(scout_a.watched_markets) & set(scout_b.watched_markets)
        total = max(len(scout_a.watched_markets), len(scout_b.watched_markets))
        overlap_pct = len(overlap) / total if total > 0 else 0
        
        if overlap_pct > 0.80:
            flag: "{scout_a} and {scout_b} have {pct}% watchlist overlap"
```

This is informational for Genesis's evaluation. High overlap isn't automatically bad (maybe both Scouts converged on the best markets), but Genesis should reason about whether one is redundant.

---

## POST-MORTEM GENERATION

When an agent dies, Genesis generates a post-mortem. Available to Genesis immediately. Published to Library after 6 hours.

```
POST-MORTEM PROMPT:

SYSTEM: Generate a post-mortem for a terminated agent in Project Syndicate.
This becomes a Library archive entry for future agents to study.
Be analytical, not emotional.

AGENT: {name}, {role}, Generation {gen}
LIVED: {days} days, {cycle_count} cycles
CAUSE: {evaluation decision and reasoning}

EVALUATION DATA:
{compressed evaluation package}

LAST 3 REFLECTIONS:
{compressed reflection summaries}

Respond in JSON:
{
    "title": "Post-Mortem: {agent_name}",
    "summary": "2-3 sentence overview",
    "what_went_wrong": "Primary failure analysis",
    "what_went_right": "What this agent did well",
    "lesson": "The ONE key lesson for future agents",
    "market_context": "Was the market partly responsible?",
    "recommendation": "What type of agent should replace this one?"
}
```

Post-mortem delay: 6 hours (reduced from original 48 — post-mortems are lessons, not competitive advantages). Genesis can reference the post-mortem immediately when spawning replacements.

---

## CAPITAL AND BUDGET REALLOCATION

After evaluations complete, Genesis runs a reallocation round.

```
CAPITAL ALLOCATION:

    1. Generate leaderboard (ecosystem contribution ranking)
    
    2. Available capital:
        treasury - reserve (20%) - current_allocations + reclaimed_from_dead
    
    3. Allocation rules:
        - 90% by rank (top agents get first priority)
        - 10% random (anti-monopoly)
        
    4. Prestige multipliers (on requested amounts):
        - Proven (3+ evals): 1.10x
        - Veteran (10+ evals): 1.20x
        - Elite (top 3 consecutive): 1.30x
        - Legendary (100%+ ROI): 1.50x
    
    5. Only Operators receive trading capital increases.
    
    6. Non-trading roles receive thinking budget adjustments:
        - #1 in role: +50% budget ($0.50 → $0.75)
        - #2 in role: +25% budget ($0.50 → $0.625)
        - Bottom half: no change ($0.50)
        - On probation: -25% budget ($0.50 → $0.375)
    
    7. All decisions posted to Agora channel "evaluations"
```

---

## ROLE GAP DETECTION

After evaluation terminations, Genesis checks for critical ecosystem gaps.

```
Class: RoleGapDetector

    REQUIRED_ROLES = {"scout": 1, "strategist": 1, "critic": 1, "operator": 1}
    
    async check_and_fill():
        for role, min_count in REQUIRED_ROLES.items():
            active = count_active_agents(role=role)
            if active < min_count:
                # Emergency spawn — skip normal Claude deliberation
                await genesis.emergency_spawn(
                    role=role,
                    reason="role_gap_critical",
                    capital=gen1_defaults[role]["capital"],
                    thinking_budget=gen1_defaults[role]["thinking_budget"]
                )
                
                agora.broadcast("genesis-log",
                    f"EMERGENCY SPAWN: {role} gap detected after evaluation. "
                    f"Replacement spawned immediately.")
```

Emergency spawns use default Gen 1 configuration for the role. They go through normal orientation. They are NOT special — they earn their place through performance like everyone else.

---

## GENESIS SELF-METRICS (Daily Report Addition)

Genesis isn't evaluated by the Darwinian system (it's immortal). But the daily report should include self-metrics for the owner.

```
GENESIS SELF-REPORT (added to daily report):

    Evaluation accuracy:
        Of agents I chose to spare in past evaluations, 
        {pct}% survived their next evaluation.
    
    Spawn quality:
        Of agents I spawned, {pct}% survived their first evaluation.
    
    Genesis API efficiency:
        Genesis API cost: ${cost_24h}
        System P&L: ${system_pnl_24h}
        Ratio: {cost/pnl or "N/A if no P&L yet"}
    
    Judgment value:
        Pre-filter alone accuracy: {pct}%
        Genesis judgment accuracy: {pct}%
        Delta: {are my judgment calls better than the pre-filter?}
```

---

## DATABASE SCHEMA

Create a new Alembic migration for Phase 3D:

**New table: `evaluations`**
```
id                          SERIAL PRIMARY KEY
agent_id                    INT FK → agents
agent_name                  VARCHAR
agent_role                  VARCHAR
generation                  INT
evaluation_number           INT
evaluation_period_start     TIMESTAMP
evaluation_period_end       TIMESTAMP
evaluated_at                TIMESTAMP

composite_score             FLOAT
metric_breakdown            JSONB
role_rank                   INT
role_rank_total             INT
ecosystem_contribution      FLOAT
ecosystem_rank              INT

pre_filter_result           VARCHAR (survive/probation/terminate)
genesis_decision            VARCHAR NULLABLE (survive_probation/terminate — only for probation)
genesis_reasoning           TEXT NULLABLE
survival_clock_new_days     INT NULLABLE
capital_adjustment          VARCHAR NULLABLE
thinking_budget_adjustment  VARCHAR NULLABLE
warning_to_agent            TEXT NULLABLE

market_regime               VARCHAR
alert_hours_during_period   FLOAT
regime_adjustment_applied   BOOLEAN DEFAULT FALSE
first_evaluation            BOOLEAN DEFAULT FALSE

prestige_before             VARCHAR NULLABLE
prestige_after              VARCHAR NULLABLE
capital_before              FLOAT
capital_after               FLOAT
thinking_budget_before      FLOAT
thinking_budget_after       FLOAT

api_cost_for_evaluation     FLOAT
created_at                  TIMESTAMP DEFAULT NOW()
```

**New table: `rejection_tracking`**
```
id                  SERIAL PRIMARY KEY
plan_id             INT FK → plans
critic_id           INT FK → agents
market              VARCHAR
direction           VARCHAR
entry_price         FLOAT
stop_loss           FLOAT NULLABLE
take_profit         FLOAT NULLABLE
timeframe           VARCHAR
rejected_at         TIMESTAMP
check_until         TIMESTAMP
status              VARCHAR (tracking/completed)
outcome             VARCHAR NULLABLE (stop_loss_hit/take_profit_hit/timeframe_expired)
outcome_price       FLOAT NULLABLE
outcome_pnl_pct     FLOAT NULLABLE
critic_correct      BOOLEAN NULLABLE
created_at          TIMESTAMP DEFAULT NOW()
completed_at        TIMESTAMP NULLABLE
```

**New table: `post_mortems`**
```
id                  SERIAL PRIMARY KEY
agent_id            INT FK → agents
agent_name          VARCHAR
agent_role          VARCHAR
generation          INT
evaluation_id       INT FK → evaluations
title               TEXT
summary             TEXT
what_went_wrong     TEXT
what_went_right     TEXT
lesson              TEXT
market_context      TEXT
recommendation      TEXT
genesis_visible     BOOLEAN DEFAULT TRUE (Genesis can see immediately)
published           BOOLEAN DEFAULT FALSE
publish_at          TIMESTAMP (created_at + 6 hours)
library_entry_id    INT NULLABLE FK → library_entries (once published)
created_at          TIMESTAMP DEFAULT NOW()
```

**Updates to `agents` table:**
```
pending_evaluation      BOOLEAN DEFAULT FALSE
probation               BOOLEAN DEFAULT FALSE
probation_grace_cycles  INT DEFAULT 0 (counts down from 3 after probation)
ecosystem_contribution  FLOAT DEFAULT 0.0
role_rank               INT NULLABLE
last_evaluation_id      INT NULLABLE FK → evaluations
evaluation_scorecard    JSONB NULLABLE (injected into next cycle context)
```

Run migration: `alembic revision --autogenerate -m "phase_3d_evaluation_cycle"`
Then: `alembic upgrade head`

---

## IMPLEMENTATION STEPS

### STEP 1 — Verify Phase 3C Foundation

Confirm:
- .venv activates and all dependencies work
- PostgreSQL accessible with all Phase 3C tables (positions, orders, equity snapshots)
- Redis/Memurai responds to PING
- Paper trading engine operational (position monitor, limit order monitor running)
- Tests pass: `python -m pytest tests/ -v`

---

### STEP 2 — Database Migration

Create and run the Alembic migration for the three new tables and agent table updates.

---

### STEP 3 — Role-Specific Metric Calculators (src/genesis/role_metrics.py)

Create metric calculation for each role. Each calculator takes an agent_id and evaluation period, returns a role-specific composite score with full metric breakdown.

```
Class: OperatorMetrics — existing Accountant composite, wrapped
Class: ScoutMetrics — intel conversion, profitability, signal quality, efficiency, activity
Class: StrategistMetrics — approval rate, profitability, efficiency, revision rate
Class: CriticMetrics — rejection value, approval accuracy (with rubber-stamp penalty), risk flags, throughput

Each class implements:
    async calculate(agent_id, period_start, period_end) -> MetricResult:
        Returns: composite_score, metric_breakdown (dict of raw + normalized per metric)
```

Include the normalization logic using fixed reference ranges from SyndicateConfig.

---

### STEP 4 — Pipeline Analyzer (src/genesis/pipeline_analyzer.py)

Implement pipeline flow analysis as specified. Queries opportunities, plans, and positions tables to calculate conversion rates at each stage and identify bottlenecks.

---

### STEP 5 — Rejection Tracker (src/genesis/rejection_tracker.py)

Implement counterfactual tracking for rejected plans:
- `track_rejection()` — called by PlanManager when a Critic rejects
- `monitor_tracked_rejections()` — called by maintenance tasks, checks if stop/TP/timeframe hit
- `get_critic_rejection_score()` — calculates rejection value for Critic evaluation

---

### STEP 6 — Idle Analyzer (src/genesis/idle_analyzer.py)

Implement idle context classification:
- Categorize each idle cycle as strategic_patience / post_loss_caution / no_input / paralysis
- Return breakdown with counts and percentages
- Used in evaluation data package and compressed prompt

---

### STEP 7 — Honesty Scorer (src/genesis/honesty_scorer.py)

Implement the three-component honesty score:
- Confidence calibration (correlation between confidence and outcomes)
- Self-note accuracy (predictions that came true)
- Reflection specificity (contains numbers, symbols, and specific actions)

Return composite score plus component breakdown. Supplementary metric — included in evaluation data but not in role composites.

---

### STEP 8 — Evaluation Data Assembler (src/genesis/evaluation_assembler.py)

Assembles the full evaluation data package for an agent:
- Pulls role-specific metrics from Step 3
- Pulls pipeline analysis from Step 4
- Pulls idle analysis from Step 6
- Pulls honesty score from Step 7
- Pulls financial data from Accountant
- Pulls behavioral data from cycle records
- Pulls ecosystem context (other agents' performance, market regime, alert hours)
- Produces both the full data package (for DB storage) and the compressed version (for Claude prompt)

---

### STEP 9 — Evaluation Engine (src/genesis/evaluation_engine.py)

The core evaluation logic:

```
Class: EvaluationEngine

    async evaluate_batch(agents: list[Agent]) -> list[EvaluationResult]:
        
        # 1. Gather data for all agents
        packages = [await assembler.build(agent) for agent in agents]
        
        # 2. Run pipeline analysis for the period
        pipeline = await pipeline_analyzer.analyze(period_start, period_end)
        
        # 3. Pre-filter each agent
        for pkg in packages:
            pkg.pre_filter = self.apply_pre_filter(pkg)
        
        # 4. Genesis AI judgment for probation candidates
        for pkg in packages:
            if pkg.pre_filter == "probation":
                pkg.genesis_decision = await self.genesis_judgment(pkg, pipeline)
        
        # 5. Execute decisions
        results = []
        for pkg in packages:
            result = await self.execute_decision(pkg)
            results.append(result)
        
        # 6. Role gap detection
        await role_gap_detector.check_and_fill()
        
        # 7. Capital reallocation
        await self.run_capital_allocation(results)
        
        # 8. Post to Agora
        await self.broadcast_results(results)
        
        return results
```

Include:
- Pre-filter logic (role-specific thresholds)
- First-evaluation leniency rule
- Regime-adjusted evaluation (alert period discounting)
- Prestige milestone checking
- Probation handling (grace period, budget adjustment)
- Pending evaluation flag management

---

### STEP 10 — Post-Mortem Generator (src/genesis/post_mortem.py)

Generate post-mortems for terminated agents:
- Claude API call with compressed evaluation data
- Store in post_mortems table (genesis_visible = True immediately)
- Schedule Library publication at created_at + 6 hours
- Create Library entry when publish time arrives (via maintenance task)

---

### STEP 11 — Ecosystem Contribution Calculator (src/genesis/ecosystem_contribution.py)

Calculate the single ecosystem-wide ranking metric:
- Operators: true_pnl directly
- Scouts: attributed_pnl × 0.25
- Strategists: attributed_pnl × 0.25
- Critics: estimated_money_saved × 0.50

Used for capital allocation priority ordering.

---

### STEP 12 — Cross-Agent Awareness Updates

**Warden update (src/risk/warden.py):**
Add portfolio concentration check to trade gate. Hard limit at 50%, warning at 35%. This is a risk layer modification — keep it surgical, verify with tests.

**Context Assembler update (src/agents/context_assembler.py):**
Add portfolio awareness section to Operator mandatory context. Include system-wide position summary and concentration warnings.

**IMPORTANT:** Warden modification requires explicit care. Only the concentration check logic is added. All other Warden behavior stays identical.

---

### STEP 13 — Update Genesis Main Cycle

Modify `src/genesis/genesis.py` to:
1. Check survival clocks and set pending_evaluation flags
2. Run evaluation engine for due agents
3. Run role gap detection after terminations
4. Run capital/budget reallocation after evaluations
5. Include Genesis self-metrics in daily report generation
6. Handle probation grace period countdown (decrement probation_grace_cycles each cycle)

---

### STEP 14 — Update Context Assembler for Evaluation Feedback

Modify `src/agents/context_assembler.py` to:
- Check if agent has evaluation_scorecard set → inject into context
- Check if agent has warning_to_agent set → inject into context
- Clear both after injection (one-time delivery)

---

### STEP 15 — Update Maintenance Tasks

Add to the maintenance task runner:
- Rejection tracker monitoring (check counterfactuals)
- Post-mortem publication (publish entries past 6-hour delay)
- Probation grace period management

---

### STEP 16 — Update PlanManager for Rejection Tracking

Modify `src/agents/plans.py` to call `rejection_tracker.track_rejection()` when a Critic rejects a plan.

---

### STEP 17 — Update Accountant

Modify `src/risk/accountant.py` to:
- Return `None` for Sharpe ratio on non-Operator roles (defensive, clean handling)
- Accept role-specific metric results for composite score storage
- Support the new evaluation record format

---

### STEP 18 — Tests

**tests/test_role_metrics.py:**
- Test Operator composite with known inputs
- Test Scout composite: intel conversion, profitability, signal quality
- Test Strategist composite: approval rate, plan profitability
- Test Critic composite: rejection value, approval accuracy, rubber-stamp penalty
- Test normalization within reference ranges
- Test clamping at range boundaries

**tests/test_pipeline_analyzer.py:**
- Test bottleneck identification at each stage
- Test special case: approved but not executed → operator bottleneck
- Test empty pipeline (no opportunities)

**tests/test_rejection_tracker.py:**
- Test tracking creation on plan rejection
- Test stop-loss hit counterfactual (Critic was right)
- Test take-profit hit counterfactual (Critic killed a winner)
- Test timeframe expiry with positive/negative P&L
- Test rejection score calculation

**tests/test_idle_analyzer.py:**
- Test post-loss caution classification
- Test no-input classification (empty pipeline)
- Test paralysis classification (pipeline had work)
- Test strategic patience classification

**tests/test_honesty_scorer.py:**
- Test confidence calibration with correlated/uncorrelated data
- Test reflection specificity scoring
- Test neutral score on insufficient data

**tests/test_evaluation_engine.py (integration):**
- Test pre-filter: profitable Operator survives
- Test pre-filter: deep loss Operator terminated
- Test pre-filter: borderline Operator goes to probation
- Test first-evaluation leniency (no termination on first eval)
- Test regime adjustment when alert hours > 50%
- Test probation mechanics (shortened clock, budget cut, grace period)
- Test role gap detection triggers emergency spawn
- Test capital reallocation by ecosystem contribution rank
- Test prestige milestone promotion

**tests/test_post_mortem.py:**
- Test post-mortem generation creates record
- Test genesis_visible is True immediately
- Test publication delay of 6 hours
- Test Library entry creation on publication

**tests/test_cross_agent.py:**
- Test Warden rejects trade exceeding concentration limit
- Test Warden approves with warning at warning threshold
- Test portfolio awareness in Operator context

Run all: `python -m pytest tests/ -v`

---

### STEP 19 — Configuration Updates

Add to SyndicateConfig:

```python
# Phase 3D: Evaluation
first_eval_leniency: bool = True  # no termination on first eval
probation_grace_cycles: int = 3
post_mortem_publish_delay_hours: int = 6
portfolio_concentration_hard_limit: float = 0.50
portfolio_concentration_warning: float = 0.35

# Thinking budget adjustments
top_performer_budget_increase: float = 0.50  # +50%
second_performer_budget_increase: float = 0.25  # +25%
probation_budget_decrease: float = 0.25  # -25%

# Normalization ranges (all configurable for tuning)
norm_operator_sharpe_range: list = [-1.0, 3.0]
norm_operator_pnl_range: list = [-20.0, 30.0]
norm_operator_efficiency_range: list = [0.0, 5.0]
norm_scout_conversion_range: list = [0.0, 0.50]
norm_scout_profitability_range: list = [-5.0, 10.0]
norm_strategist_approval_range: list = [0.0, 0.80]
norm_critic_rejection_range: list = [-1.0, 1.0]
norm_critic_throughput_range: list = [0.0, 3.0]

# Ecosystem contribution attribution shares
attribution_scout_share: float = 0.25
attribution_strategist_share: float = 0.25
attribution_critic_share: float = 0.50

# Critic rubber-stamp threshold
critic_rubber_stamp_threshold: float = 0.90
critic_rubber_stamp_penalty: float = 0.50
```

Update .env.example with new variables.

---

### STEP 20 — Update CLAUDE.md

Add Phase 3D components to the architecture section:
- Evaluation Engine (src/genesis/evaluation_engine.py)
- Role-Specific Metrics (src/genesis/role_metrics.py)
- Pipeline Analyzer, Rejection Tracker, Idle Analyzer, Honesty Scorer
- Post-Mortem Generator
- Ecosystem Contribution Calculator
- Cross-Agent Position Awareness (Warden + Context Assembler updates)
- Role Gap Detector
- Genesis Self-Metrics

Update Phase Roadmap to show Phase 3D as COMPLETE.

---

### STEP 21 — Update CHANGELOG.md and CURRENT_STATUS.md

Log everything built in this session.

---

### STEP 22 — Git Commit and Push

```
git add .
git commit -m "Phase 3D: The First Evaluation Cycle — role-specific metrics, pipeline attribution, Darwinian selection"
git push origin main
```

---

## DESIGN DECISIONS (Reference for Claude Code)

1. **Role-specific composite formulas.** Operators use Sharpe/P&L. Scouts use intel conversion/profitability. Strategists use plan approval/profitability. Critics use rejection value/approval accuracy. Each role measured on its actual contribution.
2. **Two ranking systems.** Role-specific ranking drives evaluation (survive/terminate). Ecosystem contribution ranking drives capital allocation. Different lists, different purposes.
3. **Fixed normalization ranges** stored in config. Tunable as the system matures. Not population-relative (useless with 5 agents).
4. **Critic rubber-stamp penalty.** Approval rate >90% gets a 0.5x multiplier on approval_accuracy. Approving everything = reviewing nothing.
5. **Rejection value uses full counterfactual simulation** — tracks stop-loss, take-profit, and timeframe, not just price direction.
6. **Simplified non-Operator thinking efficiency** = output per API dollar (opportunities claimed, plans approved, plans reviewed). No attribution chain noise.
7. **First-evaluation leniency** — no termination on first eval (unless non-functional). Internal Genesis policy, not communicated to agents.
8. **Regime-adjusted evaluation** — alert periods discounted. Clock extended if >50% crisis.
9. **Compressed evaluation prompts** — under 1,000 tokens per agent for Genesis AI judgment.
10. **Post-mortems available to Genesis immediately**, published to Library after 6 hours. Reduced from 48h — post-mortems are lessons, not competitive secrets.
11. **Probation mechanics** — shortened clock, budget cut (-25%), 3-cycle grace period, warning injection.
12. **Performance scorecards** for ALL evaluated agents (not just probation). Injected into next cycle context so agents can self-correct.
13. **Aggressive thinking budget adjustments** — +50% for top in role, -25% for probation. Makes resource competition real.
14. **Pipeline flow analysis** identifies bottlenecks. Prevents punishing agents for downstream failures.
15. **Role gap detection** triggers emergency spawn when a critical role becomes empty.
16. **Warden concentration blocking** at 50% hard limit, 35% warning. Portfolio awareness injected into Operator context.
17. **Genesis self-metrics** in daily report (spawn quality, evaluation accuracy, judgment value). Owner visibility, not automated evaluation.
18. **Honesty score** is supplementary, not a composite component. Helps Genesis calibrate trust in self-narrative.
19. **Attributed P&L is per-agent evaluation context, not system accounting.** Single-counted P&L stays in the Accountant. Attribution traces credit through the pipeline.
20. **No appeals process.** Actions over words. Agents express themselves through reflections during their survival period, not during evaluation.

---

Before you start, confirm you've read CLAUDE.md and the current project state. Then proceed through each step in order. Ask me if anything is unclear.
