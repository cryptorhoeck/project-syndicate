## PROJECT SYNDICATE — PHASE 3E CLAUDE CODE KICKOFF PROMPT
## Copy everything below the line and paste it into Claude Code
## ================================================================

I'm continuing Project Syndicate. Read the CLAUDE.md file first, then CURRENT_STATUS.md and CHANGELOG.md to confirm Phase 3D is complete.

This is Phase 3E — Personality Through Experience. Phase 3 is split into 6 sub-phases:
- 3A: The Agent Thinking Cycle ← COMPLETE
- 3B: The Cold Start Boot Sequence ← COMPLETE
- 3C: Paper Trading Infrastructure ← COMPLETE
- 3D: The First Evaluation Cycle ← COMPLETE
- **3E: Personality Through Experience** ← YOU ARE HERE
- 3F: First Death, First Reproduction, First Dynasty

Work through each step in order. Use CMD commands only, never PowerShell. Always activate the .venv before any Python work.

**IMPORTANT:** Before modifying ANY existing file, create a timestamped backup in backups/. Before starting, run `python scripts/backup.py` to snapshot the current state.

---

## CONTEXT — What Is Personality Through Experience?

Most AI systems inject personality — pre-programmed traits, persona prompts, assigned temperaments. That's a costume, not a character.

In Syndicate, personality is **discovered**. An agent that lost three times on SOL during weekends doesn't "get assigned the cautious trait." It writes a self-note saying "avoid SOL weekends," that note becomes long-term memory, and every future decision is colored by that burn. That's not a trait — it's a scar. Scars are more real than costumes.

Phases 3A-3D already built the foundation:
- Self-notes (Post-it notes to future self)
- Reflections (periodic self-review, memory promotion/demotion)
- Long-term memory (curated lessons that persist and shape context)
- Evaluation feedback (scorecards telling agents what's working)
- Probation warnings (pressure that forces behavioral adaptation)

Phase 3E adds the systems that make personality **visible, measurable, and inheritable:**
1. **Behavioral Profile** — auto-generated personality fingerprint from actual behavior
2. **Temperature Evolution** — thinking style that drifts based on performance
3. **Reflection-Cycle Library Access** — targeted "study sessions" for ongoing learning
4. **Dynamic Identity Section** — system prompt that evolves with the agent
5. **Relationship Memory** — formalized trust scoring between agents
6. **Divergence Tracking** — measuring how identical agents become different

---

## PART 1 — THE BEHAVIORAL PROFILE

An auto-generated summary of an agent's emergent identity, computed entirely from actual behavior — never self-reported.

### The Seven Traits

```
RISK APPETITE
    Source: position sizes as % of capital, stop-loss tightness, 
           hedging frequency, idle rate after losses
    Scale: ultra_conservative → conservative → moderate → aggressive → reckless
    Min data: 10+ closed positions (Operators only; non-Operators get N/A)

MARKET FOCUS
    Source: watchlist evolution, market distribution of actions,
           which markets appear in profitable vs losing self-notes
    Output: ranked list of markets by engagement + performance
    Min data: 20+ non-idle cycles
    Example: "SOL specialist (78%), BTC secondary (15%), dropped ADA at cycle 34"

TIMING PATTERN
    Source: when agent is most active vs idle, which hours produce 
           profitable actions vs losses
    Output: activity heatmap by hour-of-day, day-of-week
    Min data: 50+ cycles across 3+ different days

DECISION STYLE
    Source: avg reasoning length, confidence distribution, 
           cycles between opportunity and action, idle-to-action ratio
    Scale: impulsive → reactive → deliberate → cautious → paralyzed
    Min data: 15+ action cycles

COLLABORATION PATTERN
    Source: how often agent's work feeds pipeline, how often agent 
           references others, trust relationship scores
    Scale: independent → cooperative → dependent
    Min data: 5+ pipeline-connected outcomes

LEARNING VELOCITY
    Source: rate of memory promotions, new lessons per reflection,
           reflection specificity trend, metric improvement between evals
    Scale: stagnant → slow_learner → steady → fast_learner → adaptive
    Min data: 2+ evaluations

RESILIENCE
    Source: behavior after losses — adapt, freeze, or repeat mistake?
           Recovery time (cycles from loss to next profitable action)
    Scale: fragile → shaky → steady → resilient → antifragile
    Min data: 3+ loss events with subsequent recovery data
```

### Minimum Data Thresholds

If insufficient data exists for a metric, it returns `"insufficient_data"` instead of a score. The profile is only "complete" after an agent's first evaluation. Before that, it's partial. The dashboard should show "Emerging..." for metrics without enough data.

### Who Sees the Profile

- **Genesis** — included in evaluation data packages for richer judgment context
- **Owner** — visible on the dashboard with historical trending
- **Agents** — do NOT see their own profile. Agents shouldn't think "I'm conservative" — they should just BE conservative because their memories tell them to be careful. Self-awareness of the label would create a self-reinforcing loop.

### Profile Classification

```
def classify(score, thresholds, labels):
    """Map a 0-1 score to a label using threshold boundaries."""
    for i, threshold in enumerate(thresholds):
        if score < threshold:
            return labels[i]
    return labels[-1]

# Example: risk appetite
risk_label = classify(risk_score, 
    [0.2, 0.4, 0.6, 0.8], 
    ["ultra_conservative", "conservative", "moderate", "aggressive", "reckless"])
```

---

## PART 2 — TEMPERATURE EVOLUTION

Phase 3A set default temperatures per role and built per-agent override support. Phase 3E adds the evolution mechanism.

### When Temperature Changes

**Trigger:** Every evaluation cycle, after survival is confirmed. Dead agents don't evolve.

### How Temperature Drifts

```
Temperature Evolution Logic:

    1. Analyze recent cycles for performance-creativity correlation:
       
       # Compute action diversity for this evaluation period
       # High diversity = agent tried many different action types/markets
       # Low diversity = agent stuck to one pattern
       
       action_diversity = compute_action_entropy(agent, period)
       
       # Correlate diversity with profitability
       # "Did exploring help or hurt?"
       
       diversity_profitable = correlate(
           diversity_per_window,
           profitability_per_window,
           window_size=10_cycles
       )
    
    2. Determine signal:
       if diversity_profitable > 0.2:   # exploration helps
           signal = +1  (drift warmer / more creative)
       elif diversity_profitable < -0.2:  # focus helps
           signal = -1  (drift cooler / more disciplined)
       else:
           signal = 0   (no clear signal)
    
    3. Apply momentum requirement:
       # Temperature only changes if the signal persists for 
       # 2+ consecutive evaluations in the same direction.
       # One eval isn't enough. Prevents oscillation from noise.
       
       if signal == agent.last_temperature_signal and signal != 0:
           agent.api_temperature += signal * 0.05
       
       agent.last_temperature_signal = signal
    
    4. Clamp to role bounds:
       TEMPERATURE_BOUNDS = {
           "scout":      (0.3, 0.9),
           "strategist": (0.2, 0.7),
           "critic":     (0.1, 0.4),
           "operator":   (0.1, 0.4),
       }
       agent.api_temperature = clamp(
           agent.api_temperature, 
           *TEMPERATURE_BOUNDS[agent.role]
       )
    
    5. Record the change:
       Store: old_temp, new_temp, signal, reasoning in eval record
```

### Drift Rate

Maximum ±0.05 per evaluation. Over 10 evaluations (140 days), an agent could drift from 0.2 to 0.7. That's the right timescale for genuine personality shift — fast enough to matter, slow enough to be organic.

### Inheritance

When an agent reproduces (Phase 3F), offspring inherit the parent's evolved temperature, not the role default. A Scout dynasty that evolved toward 0.85 passes that temperature to the next generation.

---

## PART 3 — REFLECTION-CYCLE LIBRARY ACCESS

Phase 3B gave agents Library access during orientation. Phase 3E extends it to reflection cycles as targeted "study sessions."

### How It Works

```
REFLECTION LIBRARY INJECTION:

Every 10th cycle is already a reflection (Phase 3A).
Phase 3E adds an optional Library section.

TRIGGER CONDITIONS (all must be met):
    1. Agent has a low-scoring metric from its last evaluation
    2. There's a Library resource relevant to that weakness
    3. Agent hasn't studied this resource recently (cooldown: 5 reflections / 50 cycles)

TOKEN BUDGET:
    Library content uses the BUFFER portion (10%) of the context token budget.
    If the buffer is already needed for other content, Library injection is SKIPPED.
    The agent's own reflection is ALWAYS more important than studying.

LIBRARY SELECTION — Weak Metric to Textbook Mapping:

    Scout:
        signal_quality low       → 05_technical_analysis.md summary
        intel_conversion low     → 02_strategy_categories.md summary
        thinking_efficiency low  → 08_thinking_efficiently.md summary

    Strategist:
        plan_approval_rate low   → 03_risk_management.md summary
        revision_rate high       → 02_strategy_categories.md summary
        thinking_efficiency low  → 08_thinking_efficiently.md summary

    Critic:
        approval_accuracy low    → 03_risk_management.md summary
        rejection_value low      → 02_strategy_categories.md summary
        thinking_efficiency low  → 08_thinking_efficiently.md summary

    Operator:
        sharpe low               → 03_risk_management.md summary
        true_pnl negative        → 01_market_mechanics.md summary
        thinking_efficiency low  → 08_thinking_efficiently.md summary

ALSO INCLUDE (if available and within budget):
    - Post-mortems from agents with similar failure modes
    - Strategy records from agents who scored high in the weak area
    - Pattern summaries related to the weakness

REFLECTION PROMPT ADDITION:
    "Library reading is available below. It was selected because your 
     last evaluation identified {weakest_metric} as an area for growth. 
     Studying costs nothing extra — it's part of this cycle."

STUDY COOLDOWN:
    Same resource not offered more than once per 5 reflection cycles.
    Tracked per agent in a study_history table or Redis set.
```

### Key Principle: Passive, Not Active

Agents don't choose to request books. The system offers relevant material when it detects a weakness. The agent can focus on it or ignore it — the LLM will naturally engage with whatever seems most useful in context. No additional API cost — it's context within the existing reflection cycle.

---

## PART 4 — DYNAMIC IDENTITY SECTION

The system prompt currently says "You are Scout-Alpha, a scout agent." Every cycle, same words. Phase 3E makes the identity section evolve based on the agent's actual history.

### Identity Templates

```
NEW AGENT (< 30 cycles):
    "You are {name}, a {role} agent. Generation {gen}.
     You are new. Learn quickly. Your survival depends on it."

ESTABLISHED AGENT (30-100 cycles):
    "You are {name}, a {role} agent. Generation {gen}.
     Reputation: {score} ({prestige_title}).
     You have {long_term_memory_count} lessons from experience.
     Strongest area: {strongest_metric_description}.
     Area to improve: {weakest_metric_description}."

VETERAN AGENT (100+ cycles):
    "You are {name}, a {role} agent. Generation {gen}.
     Reputation: {score} ({prestige_title}).
     Survived {evaluation_count} evaluations.
     Recent pattern: {top_2_behavioral_facts}.
     Your edge: {strongest_metric_description}.
     Watch out for: {weakest_metric_description}."

PROBATION AGENT (any cycle count):
    Append: "⚠️ You are on PROBATION. {warning_from_genesis}.
     You have {days} days to improve or face termination.
     Focus on: {weakest_metric_description}."
```

### Critical Design Rule: Facts, Not Labels

The identity section describes what the agent HAS DONE, not what the agent IS. This prevents self-reinforcing loops where being told "you're conservative" makes the agent more conservative.

```
WRONG: "Known for: conservative risk appetite"
RIGHT: "Recent pattern: small position sizes, tight stops"

WRONG: "Your edge: fast learning"
RIGHT: "Trend: your signal quality improved 0.3 → 0.6 over 2 evaluations"

WRONG: "Watch out for: reckless trading"
RIGHT: "Risk: your last 3 large positions all hit stop-loss"
```

Facts. The agent draws its own conclusions. If it sees "last 3 large positions hit stop-loss" and takes even bigger positions, natural selection sorts it out.

### Length Constraint

2-4 lines maximum. The identity section frames the agent's thinking at a high level. Detailed lessons come from long-term memory in the context window. Don't duplicate.

---

## PART 5 — RELATIONSHIP MEMORY

Phase 3A's long-term memory includes relationship assessments. Phase 3E formalizes how trust forms and evolves between agents.

### How Relationships Form

Trust updates happen automatically through pipeline outcome tracking:

```
AUTOMATIC TRUST UPDATES:

When a trade closes with P&L:
    → Trace the pipeline chain: opportunity → plan → trade
    
    Operator updates trust in Strategist:
        Profitable trade → positive_interaction for Strategist
        Losing trade → negative_interaction for Strategist
    
    Strategist updates trust in Scout:
        Plan approved and profitable → positive for Scout
        Plan approved and lost → negative for Scout
    
    Strategist updates trust in Critic:
        Critic approved a plan that profited → positive for Critic
        Critic approved a plan that lost → negative for Critic
        Critic rejected a plan that would have profited → negative for Critic
        Critic rejected a plan that would have lost → positive for Critic
        (Critic rejection outcomes from RejectionTracker Phase 3D)

SELF-NOTE EXTRACTION:
    When an agent's self-note mentions another agent by name:
        Memory Manager extracts and logs the reference
        Example: "Scout-7 has given me 3 good leads" 
        → positive sentiment → trust increment
```

### Trust Score Calculation

```
trust_score = bayesian_weighted_avg(
    interactions,
    prior=0.5,  # neutral starting point
    weights=[DECAY_FACTOR ** (now - i.timestamp).days for i in interactions]
)

DECAY_FACTOR = 0.95  # 5% decay per day

# An interaction from 14 days ago has ~49% the weight of today's
# Ensures trust reflects CURRENT reliability, not ancient history
```

### Context Injection

Trust relationships appear in the long-term memory section of the context:

```
"Trust relationships:
 - Scout-Alpha: 0.82 trust (4 positive, 1 negative outcomes, recent)
 - Scout-Beta: 0.45 trust (2 positive, 2 negative outcomes, mixed)"
```

This naturally influences reasoning. A Strategist that trusts Scout-Alpha more will prioritize Alpha's opportunities. That preference wasn't programmed — it emerged from experience.

### Database Schema

```
New table: agent_relationships

    id                  SERIAL PRIMARY KEY
    agent_id            INT FK → agents  (the agent holding this belief)
    target_agent_id     INT FK → agents  (the agent being assessed)
    target_agent_name   VARCHAR
    trust_score         FLOAT DEFAULT 0.5
    interaction_count   INT DEFAULT 0
    positive_outcomes   INT DEFAULT 0
    negative_outcomes   INT DEFAULT 0
    last_interaction_at TIMESTAMP NULLABLE
    last_assessment     TEXT NULLABLE  (most recent relationship note)
    archived            BOOLEAN DEFAULT FALSE
    archived_at         TIMESTAMP NULLABLE
    archive_reason      VARCHAR NULLABLE (target_agent_terminated / holder_agent_terminated)
    created_at          TIMESTAMP DEFAULT NOW()
    updated_at          TIMESTAMP DEFAULT NOW()
    
    UNIQUE constraint on (agent_id, target_agent_id)
```

---

## PART 6 — DIVERGENCE TRACKING

Scout-Alpha and Scout-Beta start identical (by design — Phase 3B). Phase 3E measures how much they diverge through experience.

### Divergence Score

```
def compute_divergence(agent_a_profile, agent_b_profile) -> float:
    """
    Cosine distance between behavioral profile score vectors.
    0.0 = identical profiles (still clones)
    1.0 = completely different (maximum divergence)
    """
    scores_a = agent_a_profile.raw_scores_vector()  # [risk, speed, collab, ...]
    scores_b = agent_b_profile.raw_scores_vector()
    
    # Only compare metrics where both agents have sufficient data
    valid_pairs = [
        (a, b) for a, b in zip(scores_a, scores_b) 
        if a is not None and b is not None
    ]
    
    if len(valid_pairs) < 3:
        return None  # insufficient comparable data
    
    vec_a = [p[0] for p in valid_pairs]
    vec_b = [p[1] for p in valid_pairs]
    
    return cosine_distance(vec_a, vec_b)
```

### When Divergence Is Calculated

Computed at every evaluation cycle for all same-role agent pairs. Included in:
- Evaluation data packages for Genesis
- Dashboard for the owner
- Genesis spawn decisions ("do I need another Scout, or are my two Scouts already doing different things?")

### What Low Divergence Means

If two same-role agents remain near-identical (divergence < 0.15) after 50+ cycles, Genesis should consider whether one is redundant. Not automatic termination — but flagged for Genesis to reason about during evaluation.

---

## DATABASE SCHEMA

Create a new Alembic migration for Phase 3E:

**New table: `behavioral_profiles`**
```
id                      SERIAL PRIMARY KEY
agent_id                INT FK → agents
evaluation_id           INT NULLABLE FK → evaluations (snapshot at eval time)
risk_appetite_score     FLOAT NULLABLE
risk_appetite_label     VARCHAR NULLABLE
market_focus_data       JSONB NULLABLE (ranked market list)
market_focus_entropy    FLOAT NULLABLE
timing_heatmap          JSONB NULLABLE
decision_style_score    FLOAT NULLABLE
decision_style_label    VARCHAR NULLABLE
collaboration_score     FLOAT NULLABLE
collaboration_label     VARCHAR NULLABLE
learning_velocity_score FLOAT NULLABLE
learning_velocity_label VARCHAR NULLABLE
resilience_score        FLOAT NULLABLE
resilience_label        VARCHAR NULLABLE
raw_scores              JSONB (all numeric scores for trending/divergence)
is_complete             BOOLEAN DEFAULT FALSE (all metrics have data)
dominant_regime         VARCHAR NULLABLE (dominant market regime during profiling period)
regime_distribution     JSONB NULLABLE (e.g., {"bull": 0.6, "crab": 0.3, "bear": 0.1})
created_at              TIMESTAMP DEFAULT NOW()
```

**New table: `agent_relationships`**
(Schema as defined in Part 5 above)

**New table: `divergence_scores`**
```
id                  SERIAL PRIMARY KEY
agent_a_id          INT FK → agents
agent_b_id          INT FK → agents
agent_a_role        VARCHAR
divergence_score    FLOAT
comparable_metrics  INT (how many metrics had data for both)
evaluation_id       INT NULLABLE FK → evaluations
computed_at         TIMESTAMP DEFAULT NOW()
```

**New table: `study_history`**
```
id                  SERIAL PRIMARY KEY
agent_id            INT FK → agents
resource_type       VARCHAR (textbook_summary/post_mortem/strategy_record/pattern)
resource_id         VARCHAR (filename or library_entry_id)
studied_at_cycle    INT
studied_at          TIMESTAMP DEFAULT NOW()
```

**Updates to `agents` table:**
```
api_temperature             FLOAT NULLABLE (per-agent override, already exists from 3A — verify)
last_temperature_signal     INT DEFAULT 0 (-1, 0, or +1)
temperature_history         JSONB DEFAULT '[]' (list of {eval_id, old_temp, new_temp, signal})
identity_tier               VARCHAR DEFAULT 'new' (new/established/veteran)
behavioral_profile_id       INT NULLABLE FK → behavioral_profiles (latest)
```

Run migration: `alembic revision --autogenerate -m "phase_3e_personality"`
Then: `alembic upgrade head`

---

## IMPLEMENTATION STEPS

### STEP 1 — Verify Phase 3D Foundation

Confirm:
- .venv activates and all dependencies work
- PostgreSQL accessible with all Phase 3D tables (evaluations, rejection_tracking, post_mortems)
- Redis/Memurai responds to PING
- Evaluation engine operational
- Tests pass: `python -m pytest tests/ -v`

---

### STEP 2 — Database Migration

Create and run the Alembic migration for new tables and agent column updates.

---

### STEP 3 — Behavioral Profile Calculator (src/personality/behavioral_profile.py)

Implement the full profile computation engine:

```
Class: BehavioralProfileCalculator

    MINIMUM_THRESHOLDS = {
        "risk_appetite": 10,       # closed positions
        "market_focus": 20,        # non-idle cycles
        "timing_pattern": 50,      # cycles across 3+ days
        "decision_style": 15,      # action cycles
        "collaboration": 5,        # pipeline outcomes
        "learning_velocity": 2,    # evaluations
        "resilience": 3,           # loss events
    }
    
    async compute(agent_id, evaluation_id=None) -> BehavioralProfile:
        Computes all 7 traits. Returns "insufficient_data" for any 
        trait that doesn't meet minimum thresholds.
        
        If evaluation_id provided, stores as a snapshot linked to that eval.
        
        Also stamps the profile with the dominant market regime during the 
        profiling period. This is critical context — an agent classified 
        as "conservative" during a bear crash was being SMART, not timid.
        Genesis needs regime context to interpret profile labels correctly.
        
        Returns BehavioralProfile with:
            - Per-trait: score (float), label (str), has_data (bool)
            - raw_scores dict (for divergence calculation)
            - is_complete (bool — all traits have data)
            - dominant_regime (str — most common regime during period)
            - regime_distribution (dict — % time in each regime)
```

Include helper methods:
- `compute_risk_appetite()` — from position sizing, stop tightness, post-loss idle rate
- `compute_market_focus()` — from action market distribution, watchlist history
- `compute_timing_pattern()` — from cycle timestamps grouped by hour/day
- `compute_decision_style()` — from reasoning length, confidence distribution, response times
- `compute_collaboration()` — from pipeline connection rates
- `compute_learning_velocity()` — from composite score trend across evaluations
- `compute_resilience()` — from loss-to-recovery cycle counts
- `compute_regime_context()` — query market_regimes table for the profiling period, calculate dominant regime and distribution

---

### STEP 4 — Temperature Evolution Engine (src/personality/temperature_evolution.py)

```
Class: TemperatureEvolution

    DRIFT_AMOUNT = 0.05
    SIGNAL_THRESHOLD = 0.2
    
    TEMPERATURE_BOUNDS = {
        "scout":      (0.3, 0.9),
        "strategist": (0.2, 0.7),
        "critic":     (0.1, 0.4),
        "operator":   (0.1, 0.4),
    }
    
    async evolve(agent, evaluation_period) -> TemperatureResult:
        1. Compute action diversity for the period
        2. Correlate diversity with profitability
        3. Determine signal (+1, 0, -1)
        4. Check momentum (same signal as last eval?)
        5. Apply drift if momentum confirmed
        6. Clamp to role bounds
        7. Record change in agent's temperature_history
        8. Return TemperatureResult with old_temp, new_temp, signal, reasoning
```

Integrate into the evaluation engine: called after survival is confirmed, before the evaluation record is finalized.

---

### STEP 5 — Reflection Library Integration (src/personality/reflection_library.py)

```
Class: ReflectionLibrarySelector

    COOLDOWN_REFLECTIONS = 5  # same resource not offered within 5 reflections
    
    WEAKNESS_TO_RESOURCE = {
        # role → {metric → textbook_summary_filename}
        "scout": {
            "signal_quality": "05_technical_analysis_summary.md",
            "intel_conversion": "02_strategy_categories_summary.md",
            "thinking_efficiency": "08_thinking_efficiently_summary.md",
        },
        "strategist": { ... },
        "critic": { ... },
        "operator": { ... },
    }
    
    async select_for_reflection(agent) -> ReflectionLibraryContent | None:
        1. Check agent's last evaluation scorecard for weakest metric
        2. Look up relevant resource from mapping
        3. Check study_history for cooldown (has agent studied this in last 5 reflections?)
        4. If cooldown active → check for Library archive entries instead:
           - Post-mortems from agents with similar failure modes
           - Strategy records from agents strong in the weak area
        5. If nothing available or buffer has no room → return None
        6. Load condensed content, record in study_history
        7. Return content + context string for injection
```

Integrate into Context Assembler: when `cycle_type == "reflection"` and buffer has room, call this selector and inject content.

---

### STEP 6 — Dynamic Identity Builder (src/personality/identity_builder.py)

```
Class: DynamicIdentityBuilder

    async build_identity_section(agent) -> str:
        1. Determine tier: new (<30 cycles), established (30-100), veteran (100+)
        2. Select template for tier
        3. Fill template with FACTS (not labels):
           - Strongest/weakest metrics from last evaluation scorecard
           - Behavioral trends from recent data (not profile labels)
           - Prestige title if earned
           - Probation warning if active
        4. Cap at 2-4 lines
        5. Return formatted identity string
    
    def format_metric_as_fact(metric_name, value, trend=None) -> str:
        """Convert a metric into a factual statement, not a label."""
        
        # Examples:
        # "signal_quality 0.6, up from 0.3" → 
        #   "Trend: your signal quality improved 0.3 → 0.6 over 2 evaluations"
        # "risk_appetite reckless" → 
        #   "Risk: your last 3 large positions all hit stop-loss"
        
        # Use raw data to construct factual descriptions.
        # NEVER use personality labels (conservative, aggressive, etc.)
    
    # ARCHITECTURAL CONSTRAINT — ENFORCED IN CODE:
    # 
    # The DynamicIdentityBuilder must NEVER import from or reference
    # BehavioralProfile label fields (risk_appetite_label, decision_style_label, etc.)
    # 
    # It should ONLY accept:
    #   - Raw metric values (floats, counts, percentages)
    #   - Trend data (previous value → current value)
    #   - Specific factual data (trade outcomes, cycle counts, market names)
    #
    # This is not a guideline — it's a code-level constraint.
    # If someone refactors and accidentally passes profile labels into the
    # identity builder, they'll get labeled personality traits in the system 
    # prompt, which creates self-reinforcing loops that prevent personality
    # evolution. The separation must be enforced by the module's interface.
    #
    # The DynamicIdentityBuilder's constructor/build method should accept:
    #   - agent record (name, role, gen, cycle_count, prestige, probation status)
    #   - evaluation scorecard (metric names + raw values + trends)
    #   - recent trade/action stats (from cycle records)
    # 
    # It should NOT accept:
    #   - BehavioralProfile object
    #   - Any field ending in "_label"
    #   - Any personality classification strings
```

Integrate into Context Assembler: replace the static identity string in the system prompt with the dynamic version.

---

### STEP 7 — Relationship Manager (src/personality/relationship_manager.py)

```
Class: RelationshipManager

    TRUST_DECAY_FACTOR = 0.95  # 5% decay per day
    TRUST_PRIOR = 0.5          # neutral starting point
    
    async record_interaction(agent_id, target_id, outcome: "positive" | "negative"):
        1. Get or create relationship record
        2. Increment positive_outcomes or negative_outcomes
        3. Recalculate trust_score with time-decay weighting
        4. Update last_interaction_at
        5. Save
    
    async update_from_pipeline_outcome(trade_result):
        """Called when a trade closes. Traces pipeline and updates all relationships."""
        1. Get the trade's source plan → source opportunity → all agents in chain
        2. Update Operator→Strategist trust based on trade P&L
        3. Update Strategist→Scout trust based on opportunity quality
        4. Update Strategist→Critic trust based on review accuracy
           (uses RejectionTracker data for rejected plans)
    
    async update_from_self_note(agent_id, self_note_text):
        """Extract agent mentions from self-notes and log sentiment."""
        1. Scan for agent names in the self-note text
        2. Classify sentiment (simple: positive words near name = positive)
        3. Record interaction with inferred sentiment
    
    async get_trust_summary(agent_id) -> list[dict]:
        """Get formatted trust relationships for context injection."""
        relationships = get_relationships(agent_id)
        return [
            {
                "agent_name": r.target_agent_name,
                "trust": round(r.trust_score, 2),
                "positive": r.positive_outcomes,
                "negative": r.negative_outcomes,
                "status": "trusted" if r.trust_score > 0.65 else 
                          "neutral" if r.trust_score > 0.35 else "distrusted"
            }
            for r in relationships
            if r.interaction_count >= 2  # don't show relationships with < 2 data points
            and r.target_agent_is_active  # NEVER inject trust data for dead agents
        ]
    
    async archive_dead_agent_relationships(dead_agent_id):
        """Called when an agent dies. Archives all relationships involving this agent."""
        # Mark all relationships WHERE this agent is the TARGET as archived
        # Other agents shouldn't see trust scores for dead agents in their context
        db.update("agent_relationships",
            {"target_agent_id": dead_agent_id},
            {"archived": True, "archived_at": now(), "archive_reason": "target_agent_terminated"}
        )
        # Also mark relationships this dead agent HELD as archived
        # (they're historical record now, not active)
        db.update("agent_relationships",
            {"agent_id": dead_agent_id},
            {"archived": True, "archived_at": now(), "archive_reason": "holder_agent_terminated"}
        )
        # Note: archived relationships are preserved for post-mortem analysis
        # and lineage research, but never injected into live agent context
    
    def calculate_trust(positive, negative, interactions, timestamps) -> float:
        """Bayesian trust with time decay."""
        if not interactions:
            return TRUST_PRIOR
        
        weights = [TRUST_DECAY_FACTOR ** (now() - ts).days for ts in timestamps]
        weighted_positive = sum(w for w, outcome in zip(weights, outcomes) if outcome == "positive")
        weighted_total = sum(weights)
        
        # Bayesian update: prior pulls toward 0.5, evidence pulls toward reality
        prior_weight = 2  # equivalent to 2 neutral observations
        trust = (weighted_positive + prior_weight * TRUST_PRIOR) / (weighted_total + prior_weight)
        
        return trust
```

Integrate into:
- Action Executor: call `update_from_pipeline_outcome()` when position closes
- Memory Manager: call `update_from_self_note()` when self-notes are processed
- Context Assembler: include trust summary in long-term memory section

---

### STEP 8 — Divergence Calculator (src/personality/divergence.py)

```
Class: DivergenceCalculator

    async compute_pairwise(role: str = None) -> list[DivergenceResult]:
        """Compute divergence for all same-role agent pairs."""
        
        agents = get_active_agents(role=role) if role else get_active_agents()
        
        # Group by role
        by_role = group_by(agents, "role")
        results = []
        
        for role, role_agents in by_role.items():
            if len(role_agents) < 2:
                continue
            
            for a, b in combinations(role_agents, 2):
                profile_a = get_latest_profile(a.id)
                profile_b = get_latest_profile(b.id)
                
                if not profile_a or not profile_b:
                    continue
                
                score = cosine_distance(
                    profile_a.raw_scores_vector(),
                    profile_b.raw_scores_vector(),
                    ignore_none=True  # only compare metrics both have data for
                )
                
                comparable = count_comparable_metrics(profile_a, profile_b)
                
                if comparable >= 3:  # need at least 3 comparable metrics
                    results.append(DivergenceResult(
                        agent_a=a, agent_b=b, role=role,
                        score=score, comparable_metrics=comparable
                    ))
        
        return results
    
    async store_snapshot(results, evaluation_id=None):
        """Store divergence scores linked to an evaluation."""
        for r in results:
            db.insert("divergence_scores", {
                "agent_a_id": r.agent_a.id,
                "agent_b_id": r.agent_b.id,
                "agent_a_role": r.role,
                "divergence_score": r.score,
                "comparable_metrics": r.comparable_metrics,
                "evaluation_id": evaluation_id,
            })
```

Integrate into evaluation engine: compute and store divergence after profiles are updated.

---

### STEP 9 — Update Context Assembler

Modify `src/agents/context_assembler.py` for three integrations:

1. **Dynamic identity section**: Replace static identity string with output from `DynamicIdentityBuilder.build_identity_section()`. Place in system prompt.

2. **Trust relationships in memory**: Include `RelationshipManager.get_trust_summary()` in the long-term memory section of the context. Only for agents with 2+ relationship data points.

3. **Library content in reflections**: When `cycle_type == "reflection"`, check if `ReflectionLibrarySelector` returns content. If yes AND buffer has room, inject into the reflection context. If buffer is full, skip — the agent's own reflection is always more important.

---

### STEP 10 — Update Evaluation Engine Integration

Modify `src/genesis/evaluation_engine.py` to:

1. **Compute behavioral profiles** after evaluation decisions are made (for surviving agents). Store as snapshots linked to the evaluation. Include dominant market regime during the profiling period (`dominant_regime` and `regime_distribution` fields) so Genesis can distinguish "this agent IS conservative" from "this agent was conservative during a crash."

2. **Run temperature evolution** after survival is confirmed. Record old/new temperature in the evaluation record.

3. **Compute divergence scores** for all same-role pairs after profiles are updated.

4. **Include profile and divergence** in the evaluation data package sent to the Agora and stored in the evaluation record.

5. **Personality drift alarm**: Compare the new profile against the previous evaluation's profile. If any trait label changes by 2+ tiers between consecutive profiles (e.g., conservative → aggressive, skipping moderate), flag it in the evaluation record and broadcast to Agora:

```
PERSONALITY DRIFT DETECTION:

    TIER_DISTANCES = {
        "ultra_conservative": 0, "conservative": 1, "moderate": 2, 
        "aggressive": 3, "reckless": 4,
        # similar for other trait scales
    }
    
    for trait in profile.traits:
        old_label = previous_profile.get(trait)
        new_label = current_profile.get(trait)
        
        if old_label and new_label and old_label != "insufficient_data" and new_label != "insufficient_data":
            distance = abs(TIER_DISTANCES[new_label] - TIER_DISTANCES[old_label])
            
            if distance >= 2:
                flag: "PERSONALITY DRIFT: {agent_name} {trait} shifted from 
                       {old_label} to {new_label} in one evaluation period.
                       Possible destabilization or regime-driven adaptation."
                
                # Include in evaluation data for Genesis
                # Also broadcast to Agora channel "genesis-log"
```

This doesn't auto-terminate — rapid personality shift could be smart adaptation to a regime change. But Genesis should know about it and reason about whether it's healthy or destabilizing.

---

### STEP 11 — Update Action Executor for Relationship Tracking

Modify `src/agents/action_executor.py`:
- When the Position Monitor closes a position (via the Accountant bridge), trigger `RelationshipManager.update_from_pipeline_outcome()`.

Modify `src/agents/memory_manager.py`:
- When processing self-notes, call `RelationshipManager.update_from_self_note()`.

Modify Genesis death protocol (`src/genesis/evaluation_engine.py` termination flow):
- When an agent is terminated, call `RelationshipManager.archive_dead_agent_relationships(agent_id)`.
- This prevents dead agent trust scores from appearing in live agents' context windows.
- Archived relationships are preserved for post-mortem analysis and lineage research.

---

### STEP 12 — Dashboard Updates

Add to the FastAPI endpoints:

```
GET /api/agents/{id}/profile
    Returns: current behavioral profile with history

GET /api/agents/{id}/relationships  
    Returns: trust relationships with scores and interaction counts

GET /api/divergence
    Query params: role (optional)
    Returns: pairwise divergence scores for same-role agents

GET /api/agents/{id}/temperature-history
    Returns: temperature evolution over time with signals
```

Add dashboard views:
- Agent detail page: behavioral profile radar chart, trust network, temperature history
- Ecosystem view: divergence chart for same-role pairs

---

### STEP 13 — Tests

**tests/test_behavioral_profile.py:**
- Test risk appetite calculation from position data
- Test minimum data threshold returns "insufficient_data"
- Test market focus from action distribution
- Test decision style classification
- Test learning velocity from evaluation history
- Test resilience from loss recovery data
- Test profile is_complete flag
- Test classification boundaries
- Test dominant_regime and regime_distribution are populated
- Test personality drift detection: 2+ tier shift flagged
- Test personality drift: 1 tier shift NOT flagged
- Test personality drift: insufficient_data on previous profile skips check

**tests/test_temperature_evolution.py:**
- Test warm drift when diversity correlates with profit
- Test cool drift when focus correlates with profit
- Test no drift when signal is unclear
- Test momentum requirement (no change on single eval signal)
- Test drift applied on 2 consecutive same-direction signals
- Test clamping to role bounds
- Test temperature history recording

**tests/test_reflection_library.py:**
- Test correct textbook selected for weak metric
- Test cooldown prevents re-offering same resource
- Test fallback to archive entries when textbook on cooldown
- Test returns None when buffer has no room
- Test study_history recorded

**tests/test_dynamic_identity.py:**
- Test new agent tier (< 30 cycles)
- Test established agent tier (30-100 cycles)
- Test veteran agent tier (100+ cycles)
- Test probation appendage
- Test facts-not-labels formatting (no personality words like "conservative", "aggressive", "reckless", "cautious", "impulsive" in output)
- Test length constraint (2-4 lines)
- Test builder rejects BehavioralProfile object as input (architectural enforcement)
- Test builder output contains no "_label" field values from profiles

**tests/test_relationship_manager.py:**
- Test trust starts at 0.5 (neutral prior)
- Test positive outcome increases trust
- Test negative outcome decreases trust
- Test time decay (old interactions weigh less)
- Test pipeline outcome traces full chain
- Test self-note extraction finds agent names
- Test trust summary filters agents with < 2 interactions
- Test trust summary filters archived relationships (dead agents)
- Test archive_dead_agent_relationships marks all target relationships as archived
- Test archive_dead_agent_relationships marks all holder relationships as archived
- Test archived relationships are preserved in DB (not deleted)

**tests/test_divergence.py:**
- Test identical profiles produce divergence ≈ 0.0
- Test very different profiles produce divergence near 1.0
- Test metrics with insufficient data on one side are excluded
- Test minimum 3 comparable metrics required
- Test snapshot storage linked to evaluation

Run all: `python -m pytest tests/ -v`

---

### STEP 14 — Configuration Updates

Add to SyndicateConfig:

```python
# Phase 3E: Personality
temperature_drift_amount: float = 0.05
temperature_signal_threshold: float = 0.2
temperature_bounds_scout: list = [0.3, 0.9]
temperature_bounds_strategist: list = [0.2, 0.7]
temperature_bounds_critic: list = [0.1, 0.4]
temperature_bounds_operator: list = [0.1, 0.4]

trust_decay_factor: float = 0.95
trust_prior: float = 0.5
trust_min_interactions_to_show: int = 2

reflection_library_cooldown: int = 5  # reflections between same resource
divergence_low_threshold: float = 0.15  # below this = near-identical
divergence_min_comparable_metrics: int = 3

profile_min_positions: int = 10
profile_min_cycles: int = 20
profile_min_cycle_days: int = 3
profile_min_actions: int = 15
profile_min_pipeline_outcomes: int = 5
profile_min_evaluations: int = 2
profile_min_losses: int = 3

identity_new_threshold: int = 30  # cycles
identity_established_threshold: int = 100  # cycles
personality_drift_tier_threshold: int = 2  # trait label tiers shifted to trigger alarm
```

Update .env.example with new variables.

---

### STEP 15 — Update CLAUDE.md

Add Phase 3E components to the architecture section:
- Behavioral Profile Calculator (src/personality/)
- Temperature Evolution Engine
- Reflection Library Selector
- Dynamic Identity Builder
- Relationship Manager
- Divergence Calculator
- New dashboard endpoints for profile/relationships/divergence

Update Phase Roadmap to show Phase 3E as COMPLETE.

---

### STEP 16 — Update CHANGELOG.md and CURRENT_STATUS.md

Log everything built in this session.

---

### STEP 17 — Git Commit and Push

```
git add .
git commit -m "Phase 3E: Personality Through Experience — profiles, temperature evolution, relationships, divergence"
git push origin main
```

---

## DESIGN DECISIONS (Reference for Claude Code)

1. **Personality is computed from behavior, never declared.** The Behavioral Profile is auto-generated from position data, cycle records, pipeline outcomes, and evaluation history. Agents never self-report their personality.
2. **Agents don't see their own Behavioral Profile.** It's for Genesis and the owner. Self-awareness of labels creates self-reinforcing loops.
3. **Facts, not labels in the dynamic identity section.** "Your last 3 large positions hit stop-loss" NOT "You are reckless." The agent draws its own conclusions.
4. **Temperature evolves slowly (±0.05 per eval) with momentum requirement.** Must see same signal 2 consecutive evaluations before drifting. Prevents noisy oscillation.
5. **Temperature inherits to offspring.** A dynasty that evolved to 0.85 passes that to the next generation (Phase 3F).
6. **Library access during reflections is passive and targeted.** System offers relevant material when it detects weakness. Agent doesn't request it. Uses buffer portion of token budget — skipped if buffer full.
7. **Study cooldown of 5 reflections** per resource prevents repetitive injection.
8. **Trust scores use Bayesian updating with time decay (0.95/day).** Recent interactions matter more than old ones. Neutral prior of 0.5.
9. **Trust updates automatically from pipeline outcomes.** When a trade closes, trust flows back through the chain: Operator→Strategist→Scout, Strategist→Critic.
10. **Divergence score (cosine distance)** measures how different same-role agents have become. Low divergence (<0.15) flagged to Genesis as potential redundancy.
11. **Minimum data thresholds** for every profile metric. Insufficient data returns "insufficient_data", not a guess. Profile shows "Emerging..." for incomplete metrics.
12. **Profile snapshots stored at each evaluation** for historical trending on the dashboard.
13. **Relationship data requires 2+ interactions** before appearing in agent context. Single data points are too noisy.
14. **Dead agent relationships are archived, not deleted.** When an agent dies, all trust relationships involving it are marked archived. Live agents never see dead agent trust scores in their context. Archived data preserved for post-mortem analysis and lineage research.
15. **Personality drift alarm** flags any trait that shifts 2+ tiers between consecutive evaluations (e.g., conservative → aggressive). Doesn't auto-terminate — could be smart regime adaptation. But Genesis is informed and can reason about whether the shift is healthy.
16. **Facts-not-labels is an architectural constraint, not a guideline.** The DynamicIdentityBuilder's interface must NEVER accept BehavioralProfile label fields. It only accepts raw metrics, trend data, and factual observations. This is enforced at the module boundary to prevent accidental self-reinforcing loops.
17. **Behavioral profiles are regime-stamped.** Every profile snapshot records the dominant market regime and regime distribution during the profiling period. "Conservative during a bear crash" is smart; "conservative during a bull run" is underperforming. Genesis needs this context to interpret profiles correctly.

---

Before you start, confirm you've read CLAUDE.md and the current project state. Then proceed through each step in order. Ask me if anything is unclear.
