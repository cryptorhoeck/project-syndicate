## PROJECT SYNDICATE — PHASE 3F CLAUDE CODE KICKOFF PROMPT
## Copy everything below the line and paste it into Claude Code
## ================================================================

I'm continuing Project Syndicate. Read the CLAUDE.md file first, then CURRENT_STATUS.md and CHANGELOG.md to confirm Phase 3E is complete.

This is Phase 3F — First Death, First Reproduction, First Dynasty. Phase 3 is split into 6 sub-phases:
- 3A: The Agent Thinking Cycle ← COMPLETE
- 3B: The Cold Start Boot Sequence ← COMPLETE
- 3C: Paper Trading Infrastructure ← COMPLETE
- 3D: The First Evaluation Cycle ← COMPLETE
- 3E: Personality Through Experience ← COMPLETE
- **3F: First Death, First Reproduction, First Dynasty** ← YOU ARE HERE

Work through each step in order. Use CMD commands only, never PowerShell. Always activate the .venv before any Python work.

**IMPORTANT:** Before modifying ANY existing file, create a timestamped backup in backups/. Before starting, run `python scripts/backup.py` to snapshot the current state.

---

## CONTEXT — What Is Phase 3F?

This is the final piece of Phase 3. After this, the system is complete enough to evolve on its own.

Phase 3D handles the death *decision* (evaluation → terminate). Phase 3F handles the full aftermath — what happens when an agent dies, how reproduction works, and how dynasties form and are tracked across generations.

Three systems:
1. **The Complete Death Protocol** — financial cleanup, knowledge preservation, institutional legacy
2. **The Reproduction Protocol** — who can reproduce, how offspring are built, what they inherit
3. **The Lineage System** — dynasties, family trees, generational analytics

---

## PART 1 — THE COMPLETE DEATH PROTOCOL

Phase 3D designed the evaluation decision and basic execution. Phase 3F adds the full aftermath — the institutional legacy an agent leaves behind.

### Death Sequence (10 Steps, In Order)

```
STEP 1: FREEZE (immediate)
    → pending_evaluation already set by Phase 3D
    → Agent stops receiving thinking cycles
    → No new actions can be taken

STEP 2: FINANCIAL CLEANUP
    → Cancel all pending limit orders → release cash reservations
    → Transfer open positions to inherited_positions table
    → Genesis has 24 hours to close inherited positions
    → Reclaim remaining cash balance to treasury
    → Record final financial snapshot (equity, P&L, fees paid)

STEP 3: RELATIONSHIP ARCHIVAL (Phase 3E)
    → Call RelationshipManager.archive_dead_agent_relationships()
    → Dead agent trust scores removed from live agents' context windows
    → Archived data preserved for post-mortem and lineage research

STEP 4: POST-MORTEM GENERATION (Phase 3D)
    → Genesis generates post-mortem via Claude API
    → Available to Genesis immediately for spawning decisions
    → Published to Library after 6 hours

STEP 5: KNOWLEDGE PRESERVATION
    → Agent's long-term memory entries marked "agent_terminated" but NOT deleted
    → These are raw material for offspring mentor packages
    → Agent's top self-notes and reflections → preserved in lineage record
    → Agent's final behavioral profile snapshot → preserved in lineage record
    → Agent's final evaluation scores → preserved in lineage record
    → Agent's evolved temperature → preserved in lineage record

STEP 6: LIBRARY CONTRIBUTIONS
    → If agent had positive lifetime P&L (Operators) or positive attributed P&L (others):
        Auto-generate strategy record (48-hour publication delay, per Phase 2B)
    → Flag notable reflection insights for Genesis to consider as pattern summaries

STEP 7: AGORA ANNOUNCEMENT
    → Post to "evaluations" channel:
        - Full evaluation results and cause of death
        - Lifetime stats: cycles, trades, P&L, prestige reached
        - Post-mortem summary (once available)
        - Dynasty info: "Scout-Alpha was the founder of Dynasty Alpha"
        - Lineage: "{agent} was Gen {N}, child of {parent}, grandchild of {grandparent}"

STEP 8: LINEAGE UPDATE
    → Update lineage record: died_at, cause_of_death, lifespan_days, final stats
    → Update dynasty stats: living_members -= 1, recalculate avg_lifespan
    → If this was the LAST living member of a dynasty:
        dynasty.status = "extinct"
        dynasty.extinct_at = now()
        Post to Agora: "Dynasty {name} has gone extinct after {gens} generations 
        and {days} days. Founded by {founder}. Peak members: {peak}. Total P&L: ${pnl}."

STEP 9: ROLE GAP CHECK (Phase 3D)
    → If critical role now empty, trigger emergency spawn

STEP 10: MEMORIAL RECORD
    → Agent added to "The Fallen" historical record for dashboard display
    → Record: name, generation, dynasty, lifespan, cause of death,
      best metric, worst metric, notable achievements, final prestige
```

**Key principle:** Death is permanent, but knowledge is immortal. The agent ceases to exist, but everything it learned persists in the Library, the Agora archives, the lineage record, and potentially in its offspring's inherited memories.

---

## PART 2 — THE REPRODUCTION PROTOCOL

### Reproduction Eligibility

Reproduction is earned, not given. ALL of these must be true:

```
REPRODUCTION REQUIREMENTS:

1. Agent has "Veteran" prestige or higher (survived 10+ evaluations, ~140 days)
2. Agent's composite score is in the top 50% of its role
3. Agent has positive True P&L (Operators) or positive attributed P&L (others)
4. Available capital in treasury > minimum spawn amount 
   ($20 for non-Operators, $50 for Operators)
5. Active agent count < MAX_AGENTS (default 20)
6. Agent has NOT reproduced in the last 3 evaluations (cooldown)
7. System is not in Yellow/Red/Circuit Breaker alert
8. Dynasty concentration check passes (see below)
```

**Why Veteran requirement (10 evaluations)?** An agent that's survived ~140 days has genuinely proven itself. This prevents flash-in-the-pan agents from reproducing based on one lucky streak. The knowledge package from a Veteran is actually worth inheriting.

### Dynasty Concentration Limits

Prevents any single dynasty from dominating the ecosystem:

```
DYNASTY CONCENTRATION CHECK:

    dynasty_share = dynasty.living_members / total_active_agents
    
    if dynasty_share > 0.40:
        → BLOCK reproduction
        → Log: "Dynasty {name} is {pct}% of ecosystem. 
          Diversification needed — reproduction denied."
        → This prevents echo chambers where one lineage 
          crowds out all others
    
    if dynasty_share > 0.25:
        → ALLOW but flag in Genesis reproduction prompt:
          "Warning: Dynasty {name} is {pct}% of ecosystem. 
          Consider whether more offspring from this lineage 
          adds diversity or just clones."
```

### When Reproduction Happens

During Genesis's evaluation cycle, AFTER evaluations and capital reallocation, BEFORE general spawn decisions:

```
Genesis Cycle Order (updated):
    1-4.  Health check, treasury, regime, agent health
    5.    EVALUATIONS — who lives, who dies
    6.    Capital reallocation
    7.    REPRODUCTION — offspring from proven agents ← HERE
    8.    Spawn decisions — new agents (not offspring)
    9-10. Agora monitoring, log cycle
```

Reproduction before general spawns because offspring get priority — they come from proven lineages.

### Population Pressure Management

With reproduction, population can grow toward MAX_AGENTS. Genesis should proactively manage this:

```
POPULATION PRESSURE (added to reproduction logic):

    available_slots = MAX_AGENTS - active_agent_count
    
    if available_slots <= 0:
        → Check: are there underperforming agents that could be replaced?
        → Genesis prompt addition: "The ecosystem is at capacity ({count}/{max}).
          To make room for offspring, consider whether any current agent 
          should be terminated to free a slot. Lowest composite: {agent_name} 
          ({score}). Would the offspring likely outperform this agent?"
        → Genesis can recommend proactive termination to make room
        → This is a MORE aggressive form of natural selection:
          not just "fail and die" but "be outcompeted and replaced"
    
    if available_slots <= 2:
        → Flag: "Near capacity. Only highest-priority reproductions."
```

### The Reproduction Flow

```
REPRODUCTION FLOW:

1. IDENTIFY CANDIDATES
    → Query all agents meeting eligibility requirements
    → Rank by composite score
    → Top candidate gets first opportunity
    → Process one reproduction per Genesis cycle (prevents spawn storms)

2. GENESIS ASKS CLAUDE: "SHOULD THIS AGENT REPRODUCE?"

    System prompt:
    "You are Genesis. An agent is eligible to reproduce. Decide whether 
     reproduction benefits the ecosystem, and if so, what mutations 
     the offspring should have."
    
    User prompt:
    "PARENT: {name} ({role}, Gen {gen}, {prestige_title})
     Composite: {score}, rank #{rank} of {total} {role}s
     
     Parent profile: {behavioral_profile_summary — facts, not labels}
     Strongest metrics: {top_3}
     Weakest metrics: {bottom_3}
     Parent temperature: {current_temp} (evolved from {original_temp})
     
     Dynasty: {dynasty_name}, {living_members} living, {total_generations} generations
     Dynasty concentration: {pct}% of ecosystem
     {concentration_warning if applicable}
     Generational improvement: {trend — improving/stable/declining}
     
     Ecosystem state:
     - {active_count} agents ({role_distribution})
     - Available slots: {available} of {max}
     {population_pressure_note if applicable}
     - Market regime: {regime}
     - Treasury available: ${available}
     - Pipeline health: {bottleneck if any}
     - Markets NOT covered by any Scout: {uncovered_markets}
     
     Respond in JSON:
     {
         'should_reproduce': true/false,
         'reasoning': 'why or why not (under 150 words)',
         'offspring_name': 'suggested name',
         'mutations': {
             'watchlist_changes': {'add': [...], 'remove': [...]},
             'temperature_adjustment': float or null,
             'founding_directive': 'a QUESTION for the offspring to explore 
                                   (not an instruction)'
         },
         'proactive_termination': null or {
             'agent_id': int,
             'reasoning': 'why this agent should be replaced'
         }
     }"

3. IF YES → BUILD THE OFFSPRING

    a. PROACTIVE TERMINATION (if recommended and at capacity):
        → Run full death protocol on the recommended agent
        → This frees a slot for the offspring
        → Log: "Proactive termination of {agent} to make room for 
          {parent}'s offspring"
    
    b. CREATE AGENT RECORD:
        name:               Genesis-suggested or "{parent_name}-II" / "-III" etc
        role:               same as parent (always)
        generation:         parent.generation + 1
        parent_id:          parent.id
        dynasty_id:         parent.dynasty_id
        allocated_capital:  per role defaults ($0 non-Operators, $50 Operators)
        thinking_budget:    same as parent's current budget
        api_temperature:    parent's evolved temperature ± random(0, 0.03)
                            clamped to role bounds
        survival_clock:     14 days standard (NOT the 21-day Gen 1 grace)
        watched_markets:    parent's watchlist with Genesis-suggested mutations
        initial_watchlist:  copy for historical record
        status:             "active"
    
    c. BUILD MENTOR PACKAGE (Phase 2B LibraryService.build_mentor_package):
        - Parent's strategy summary
        - Parent's top 5 profitable decisions + reasoning
        - Parent's top 5 failures + lessons
        - Parent's current market assessment
        - Grandparent's package (if exists)
        - Condensed at Gen 4+ (Phase 2B condensation logic)
    
    d. TRANSFER LONG-TERM MEMORY:
        - Copy all parent's active long-term memory entries to offspring
        - Mark as source="parent" (or "grandparent" for inherited memories)
        - Apply inheritance discount: confidence *= 0.75
        - Apply age decay for memories older than 30 days:
            age_days = (now - memory.created_at).days
            if age_days > 30:
                confidence *= 0.95 ** (age_days - 30)
            confidence = max(confidence, 0.10)  # floor: never below 0.10
        - Offspring can demote inherited memories through their own reflections
    
    e. TRANSFER TRUST RELATIONSHIPS:
        - Copy parent's active (non-archived) trust relationships
        - Inherit at 50% strength: 
            offspring_trust = parent_trust * 0.5 + trust_prior(0.5) * 0.5
        - This gives a head start without blind trust
        - Time decay still applies — inherited trust fades to neutral 
          if not reinforced by offspring's own interactions
    
    f. STORE LINEAGE RECORD:
        - Parent's behavioral profile snapshot at reproduction time
        - Parent's composite score at reproduction time
        - Mutations applied
        - Founding directive (the question Genesis posed)
        - Inherited memory count, inherited temperature
    
    g. RUN OFFSPRING ORIENTATION (modified from Phase 3B):
        - Orientation cycle with Library textbooks (reduced: 1 textbook)
        - PLUS mentor package content (replaces remaining Library slots)
        - Founding directive phrased as a QUESTION, not an instruction
        - 150% token budget (same as Gen 1 orientation startup cost)
        - Founding directive only appears in this ONE cycle, then disappears
    
    h. REGISTER WITH CYCLE SCHEDULER
    
    i. POST BIRTH ANNOUNCEMENT TO AGORA:
        Channel: "genesis-log"
        Content: {
            "event": "REPRODUCTION",
            "parent": parent_name,
            "offspring": offspring_name,
            "generation": gen,
            "dynasty": dynasty_name,
            "mutations": {watchlist, temperature, founding_directive},
            "inherited_memories": count,
            "inherited_temperature": temp,
            "posthumous": true/false
        }

4. UPDATE PARENT
    → parent.last_reproduction_at = now()
    → parent.offspring_count += 1
    → parent.reproduction_cooldown_until = now() + (3 * eval_period_days)
    → Post: "{parent_name} has spawned {offspring_name} (Gen {gen})"
```

### Posthumous Reproduction

If the parent dies during the SAME Genesis cycle that reproduction is checked (killed in step 5, reproduction in step 7), reproduction still proceeds:

```
POSTHUMOUS REPRODUCTION:

    If parent.status == "terminated" but was eligible before this cycle:
        → Reproduction proceeds normally
        → Mentor package built from the now-dead parent's preserved data
        → Birth announcement notes: "Born posthumously. Parent {parent_name} 
          died earlier this cycle."
        → Offspring inherits normally — parent's death doesn't degrade 
          the knowledge package
        → Dynasty continues through the offspring even though the parent fell
```

---

## PART 3 — THE MUTATION SYSTEM

Mutations are how evolution actually happens. Without mutation, offspring are clones — they'd converge to the same behavior and the ecosystem would stagnate.

### Mutation Types

```
1. WATCHLIST MUTATION (Scouts only)
    - Add 1-2 new markets not in parent's watchlist
    - Remove 0-1 markets from parent's watchlist
    - Genesis suggests these based on ecosystem gaps:
      "No one is watching DOT/USDT — add it"
    - Markets are SUGGESTIONS — offspring can update_watchlist 
      on their own

2. TEMPERATURE MUTATION (all roles)
    - Offspring inherits parent's evolved temperature
    - Random perturbation: ±uniform(0, 0.03)
    - Smaller than the 0.05 evolutionary drift — adds 
      diversity without overriding evolved adaptations
    - Clamped to role bounds
    - Creates temperature diversity within a dynasty

3. FOUNDING DIRECTIVE (all roles, via orientation prompt)
    - Genesis suggests an area for the offspring to explore
    - Phrased as a QUESTION, not an instruction:
      
      WRONG: "Explore BNB/USDT — your parent avoids it"
      RIGHT: "Your parent avoided BNB/USDT — is there opportunity 
              there, or was your parent right to avoid it?"
    
    - Questions invite exploration. Instructions demand compliance.
    - Only appears in the orientation cycle — disappears after
    - The Context Assembler must explicitly NOT include it in any 
      cycle after orientation. This is enforced, not a guideline.

4. MEMORY CONFIDENCE MUTATION
    - Parent's long-term memories inherited at confidence * 0.75
    - Additional age decay for memories > 30 days old
    - Forces offspring to re-validate inherited lessons
    - Memories the offspring confirms get boosted back to full
    - Memories that prove wrong get demoted faster (already weak)

5. WHAT DOES NOT MUTATE:
    - Role (offspring is always same role as parent)
    - Core action space (same actions available)
    - Evaluation criteria (judged the same way)
    - Risk limits (Warden doesn't care about lineage)
    - Thinking budget (same as parent's current budget)
```

**Why same role?** A Scout's knowledge doesn't translate to plan-building. Cross-role spawning wastes the knowledge inheritance. When Genesis needs a different role, it uses general spawn decisions (step 8), not reproduction.

---

## PART 4 — THE LINEAGE SYSTEM

### Dynasty Records

```
Dynasty:
    id                  SERIAL PRIMARY KEY
    founder_id          INT FK → agents
    founder_name        VARCHAR
    founder_role        VARCHAR
    dynasty_name        VARCHAR (auto: "Dynasty {founder_name}")
    founded_at          TIMESTAMP
    status              VARCHAR (active / extinct)
    extinct_at          TIMESTAMP NULLABLE
    
    # Aggregate stats (updated on every birth/death)
    total_generations   INT DEFAULT 1
    total_members       INT DEFAULT 1
    living_members      INT DEFAULT 1
    peak_members        INT DEFAULT 1
    total_pnl           FLOAT DEFAULT 0.0
    avg_lifespan_days   FLOAT NULLABLE
    longest_living_id   INT NULLABLE FK → agents
    best_performer_id   INT NULLABLE FK → agents
    best_performer_pnl  FLOAT DEFAULT 0.0
    avg_generational_improvement FLOAT NULLABLE
    
    created_at          TIMESTAMP DEFAULT NOW()
    updated_at          TIMESTAMP DEFAULT NOW()
```

### Lineage Records

```
Lineage:
    id                              SERIAL PRIMARY KEY
    agent_id                        INT FK → agents (UNIQUE)
    agent_name                      VARCHAR
    dynasty_id                      INT FK → dynasties
    parent_id                       INT NULLABLE FK → agents
    grandparent_id                  INT NULLABLE FK → agents
    generation                      INT
    
    # Knowledge inheritance snapshot
    mentor_package                  JSONB NULLABLE
    inherited_memories_count        INT DEFAULT 0
    inherited_temperature           FLOAT NULLABLE
    mutations_applied               JSONB NULLABLE
    founding_directive              TEXT NULLABLE
    posthumous_birth                BOOLEAN DEFAULT FALSE
    
    # Parent context at reproduction time
    parent_profile_snapshot         JSONB NULLABLE
    parent_composite_at_reproduction FLOAT NULLABLE
    parent_prestige_at_reproduction VARCHAR NULLABLE
    
    # Death (filled when agent dies)
    died_at                         TIMESTAMP NULLABLE
    cause_of_death                  VARCHAR NULLABLE
    lifespan_days                   FLOAT NULLABLE
    final_composite                 FLOAT NULLABLE
    final_pnl                       FLOAT NULLABLE
    final_prestige                  VARCHAR NULLABLE
    
    created_at                      TIMESTAMP DEFAULT NOW()
```

### Memorial Records

```
Memorial (The Fallen):
    id                  SERIAL PRIMARY KEY
    agent_id            INT FK → agents
    agent_name          VARCHAR
    agent_role          VARCHAR
    dynasty_name        VARCHAR
    generation          INT
    lifespan_days       FLOAT
    cause_of_death      VARCHAR
    total_cycles        INT
    final_prestige      VARCHAR NULLABLE
    best_metric_name    VARCHAR
    best_metric_value   FLOAT
    worst_metric_name   VARCHAR
    worst_metric_value  FLOAT
    notable_achievement TEXT NULLABLE
    final_pnl           FLOAT
    epitaph             TEXT NULLABLE (one-line generated by Genesis)
    created_at          TIMESTAMP DEFAULT NOW()
```

### Dynasty Lifecycle

```
CREATION:
    When ANY agent spawns without a parent (Gen 1, emergency spawn, general spawn):
        → Create dynasty record with this agent as founder
        → Create lineage record (parent_id = NULL, generation = 1)

GROWTH:
    When an agent reproduces:
        → Create lineage record for offspring
        → offspring.dynasty_id = parent.dynasty_id
        → offspring.grandparent_id = parent.parent_id
        → Update dynasty: total_generations (if new max), 
          total_members, living_members, peak_members

EXTINCTION:
    When the last living member of a dynasty dies:
        → dynasty.status = "extinct"
        → dynasty.extinct_at = now()
        → Post to Agora: "Dynasty {name} has gone extinct after 
          {gens} generations and {days} days."
    
    Extinction is permanent. Dynasty knowledge lives on in the Library.
```

### Dynasty Analytics

```
Class: DynastyAnalytics

    async dynasty_performance(dynasty_id) -> DynastyReport:
        - Total P&L across all members (living and dead)
        - Average member lifespan
        - Generational improvement trend:
            For each parent→offspring pair:
                parent_peak = parent's peak composite score
                offspring_peak = offspring's peak composite score
                improvement = (offspring_peak - parent_peak) / parent_peak
            avg_improvement = mean(all improvements)
            Positive = compounding knowledge. Negative = degrading.
        - Dominant behavioral traits in this dynasty
        - Markets this dynasty gravitates toward
        - Win rate trend across generations
    
    async cross_dynasty_comparison() -> list[DynastyComparison]:
        - All active dynasties ranked by total P&L, member count, avg score
        - "Strong lineages" (consistently improving) vs 
          "weak lineages" (degrading over generations)
        - Used by Genesis for reproduction decisions
    
    async lineage_knowledge_depth(agent_id) -> int:
        - How many generations of knowledge does this agent carry?
        - Gen 1: Library only
        - Gen 2: parent + Library
        - Gen 3: parent + grandparent + Library
        - Gen 4+: condensed heritage
        - Deeper = more institutional knowledge = should perform better
        - If it doesn't, the dynasty's knowledge pipeline is broken
```

---

## PART 5 — OFFSPRING ORIENTATION (Modified for Reproduction)

Offspring get a modified orientation that prioritizes mentor knowledge over Library textbooks.

```
OFFSPRING ORIENTATION CONTEXT:

MANDATORY CONTEXT (includes lineage):
    "You are {name}, a {role} agent. Generation {gen}.
     Your parent was {parent_name} (Gen {gen-1}), who survived 
     {parent_eval_count} evaluations with a {parent_prestige} title.
     You are part of Dynasty {dynasty_name}."

LIBRARY CONTENT (reduced):
    Only 1 textbook summary: 08_thinking_efficiently.md
    (Remaining slots used for mentor content)

MENTOR PACKAGE (replaces long-term memory slot):
    → Parent's strategy summary
    → Parent's top lessons (at 75% confidence, with age decay applied)
    → Parent's key warnings (failures that cost the most)
    → Grandparent's condensed wisdom (if available)
    → Founding directive (phrased as a QUESTION)

MARKET DATA: same as normal orientation

ORIENTATION ADDENDUM:
    "This is your FIRST CYCLE. You are Generation {gen} of Dynasty {dynasty_name}.
     
     You carry knowledge from your parent, {parent_name}. This knowledge 
     is a starting point — not a prison. Your parent's lessons are in your 
     memory with reduced confidence. Confirm what works, discard what doesn't.
     
     Genesis asks: {founding_directive_question}
     This is a question to explore, not a command to follow.
     
     Your objectives:
     1. Review your inherited knowledge
     2. Assess current market conditions
     3. Choose your first action or go idle with a plan
     4. Write a self-note about how you'll build on your parent's legacy"

TOKEN BUDGET: 150% of normal (startup cost, same as Gen 1)
```

**Critical enforcement:** The founding directive ONLY appears in the orientation cycle. The Context Assembler must explicitly exclude it from all subsequent cycles. The `founding_directive` field on the agent record is consumed once and then ignored.

---

## DATABASE SCHEMA

Create a new Alembic migration for Phase 3F:

**New table: `dynasties`**
(Schema as defined in Part 4 above)

**New table: `lineage`**
(Schema as defined in Part 4 above — check if this table already exists from Phase 0/1 and extend it rather than creating new)

**New table: `memorials`**
(Schema as defined in Part 4 above)

**Updates to `agents` table:**
```
dynasty_id                  INT NULLABLE FK → dynasties
offspring_count             INT DEFAULT 0
last_reproduction_at        TIMESTAMP NULLABLE
reproduction_cooldown_until TIMESTAMP NULLABLE
founding_directive          TEXT NULLABLE (consumed on first orientation, then ignored)
founding_directive_consumed BOOLEAN DEFAULT FALSE
posthumous_birth            BOOLEAN DEFAULT FALSE
```

Run migration: `alembic revision --autogenerate -m "phase_3f_reproduction_dynasty"`
Then: `alembic upgrade head`

**NOTE:** Check if a `lineage` table already exists from earlier phases. If it does, modify it with the new columns rather than creating a duplicate. The Phase 1 design referenced lineage tracking and Phase 2B stores mentor packages in the lineage table.

---

## IMPLEMENTATION STEPS

### STEP 1 — Verify Phase 3E Foundation

Confirm:
- .venv activates and all dependencies work
- PostgreSQL accessible with all Phase 3E tables (behavioral_profiles, agent_relationships, divergence_scores, study_history)
- Redis/Memurai responds to PING
- Personality systems operational (profiles, temperature evolution, relationships)
- Tests pass: `python -m pytest tests/ -v`

---

### STEP 2 — Database Migration

Create and run the Alembic migration. Check for existing lineage table — extend rather than duplicate.

---

### STEP 3 — Dynasty Manager (src/dynasty/dynasty_manager.py)

```
Class: DynastyManager

    async create_dynasty(founder: Agent) -> Dynasty:
        """Create a new dynasty with this agent as founder."""
        dynasty = Dynasty(
            founder_id=founder.id,
            founder_name=founder.name,
            founder_role=founder.role,
            dynasty_name=f"Dynasty {founder.name}",
            founded_at=now(),
            status="active"
        )
        db.insert(dynasty)
        founder.dynasty_id = dynasty.id
        founder.save()
        return dynasty
    
    async record_birth(parent: Agent, offspring: Agent) -> None:
        """Update dynasty stats when an offspring is born."""
        dynasty = get_dynasty(parent.dynasty_id)
        dynasty.total_members += 1
        dynasty.living_members += 1
        if offspring.generation > dynasty.total_generations:
            dynasty.total_generations = offspring.generation
        if dynasty.living_members > dynasty.peak_members:
            dynasty.peak_members = dynasty.living_members
        dynasty.save()
    
    async record_death(agent: Agent) -> None:
        """Update dynasty stats when an agent dies."""
        dynasty = get_dynasty(agent.dynasty_id)
        dynasty.living_members -= 1
        
        # Update avg lifespan
        all_dead = get_dead_members(dynasty.id)
        if all_dead:
            dynasty.avg_lifespan_days = mean(m.lifespan_days for m in all_dead)
        
        # Check extinction
        if dynasty.living_members <= 0:
            dynasty.status = "extinct"
            dynasty.extinct_at = now()
            agora.broadcast("genesis-log",
                f"Dynasty {dynasty.dynasty_name} has gone extinct after "
                f"{dynasty.total_generations} generations and "
                f"{(now() - dynasty.founded_at).days} days. "
                f"Founded by {dynasty.founder_name}. "
                f"Peak members: {dynasty.peak_members}. "
                f"Total P&L: ${dynasty.total_pnl:.2f}.")
        
        dynasty.save()
    
    async update_dynasty_pnl(dynasty_id) -> None:
        """Recalculate total dynasty P&L from all members."""
        members = get_all_members(dynasty_id)
        dynasty = get_dynasty(dynasty_id)
        dynasty.total_pnl = sum(m.final_pnl or m.realized_pnl or 0 for m in members)
        
        # Update best performer
        best = max(members, key=lambda m: m.final_pnl or m.realized_pnl or 0)
        dynasty.best_performer_id = best.id
        dynasty.best_performer_pnl = best.final_pnl or best.realized_pnl or 0
        
        dynasty.save()
```

---

### STEP 4 — Lineage Manager (src/dynasty/lineage_manager.py)

```
Class: LineageManager

    async create_lineage_record(agent: Agent, parent: Agent = None, 
                                 mentor_package = None, mutations = None,
                                 founding_directive = None) -> Lineage:
        """Create lineage record for a new agent."""
        lineage = Lineage(
            agent_id=agent.id,
            agent_name=agent.name,
            dynasty_id=agent.dynasty_id,
            parent_id=parent.id if parent else None,
            grandparent_id=parent.parent_id if parent else None,
            generation=agent.generation,
            mentor_package=mentor_package,
            inherited_memories_count=count_inherited_memories(agent.id),
            inherited_temperature=agent.api_temperature,
            mutations_applied=mutations,
            founding_directive=founding_directive,
            posthumous_birth=agent.posthumous_birth,
            parent_profile_snapshot=get_latest_profile(parent.id) if parent else None,
            parent_composite_at_reproduction=parent.composite_score if parent else None,
            parent_prestige_at_reproduction=parent.prestige_title if parent else None,
        )
        db.insert(lineage)
        return lineage
    
    async record_death(agent: Agent, evaluation: Evaluation) -> None:
        """Update lineage record when agent dies."""
        lineage = get_lineage(agent.id)
        lineage.died_at = now()
        lineage.cause_of_death = evaluation.genesis_decision or evaluation.pre_filter_result
        lineage.lifespan_days = (now() - agent.created_at).total_seconds() / 86400
        lineage.final_composite = agent.composite_score
        lineage.final_pnl = agent.realized_pnl + agent.unrealized_pnl
        lineage.final_prestige = agent.prestige_title
        lineage.save()
    
    async get_family_tree(dynasty_id) -> list[dict]:
        """Get full family tree for a dynasty."""
        members = get_all_lineage_records(dynasty_id)
        return build_tree(members)  # hierarchical structure for dashboard
    
    async get_ancestors(agent_id, depth=3) -> list[Lineage]:
        """Get lineage chain: parent, grandparent, great-grandparent."""
        chain = []
        current = get_lineage(agent_id)
        for _ in range(depth):
            if current and current.parent_id:
                parent_lineage = get_lineage_by_agent(current.parent_id)
                if parent_lineage:
                    chain.append(parent_lineage)
                    current = parent_lineage
                else:
                    break
            else:
                break
        return chain
```

---

### STEP 5 — Memorial Manager (src/dynasty/memorial_manager.py)

```
Class: MemorialManager

    async create_memorial(agent: Agent, evaluation: Evaluation) -> Memorial:
        """Create a memorial record for The Fallen."""
        
        # Find best and worst metrics from evaluation
        metrics = evaluation.metric_breakdown
        best = max(metrics.items(), key=lambda x: x[1].get("normalized", 0))
        worst = min(metrics.items(), key=lambda x: x[1].get("normalized", 1))
        
        # Generate epitaph via Claude (one line, included in eval API cost)
        epitaph = await generate_epitaph(agent, evaluation)
        
        memorial = Memorial(
            agent_id=agent.id,
            agent_name=agent.name,
            agent_role=agent.role,
            dynasty_name=get_dynasty(agent.dynasty_id).dynasty_name,
            generation=agent.generation,
            lifespan_days=(now() - agent.created_at).total_seconds() / 86400,
            cause_of_death=evaluation.genesis_decision or "pre_filter_terminate",
            total_cycles=agent.cycle_count,
            final_prestige=agent.prestige_title,
            best_metric_name=best[0],
            best_metric_value=best[1].get("raw", 0),
            worst_metric_name=worst[0],
            worst_metric_value=worst[1].get("raw", 0),
            notable_achievement=determine_achievement(agent),
            final_pnl=agent.realized_pnl + agent.unrealized_pnl,
            epitaph=epitaph
        )
        db.insert(memorial)
        return memorial
    
    async generate_epitaph(agent, evaluation) -> str:
        """One-line epitaph for the dashboard. Cheap — appended to existing eval prompt."""
        # Example epitaphs:
        # "Burned bright but brief — the SOL specialist who couldn't survive the crash."
        # "140 days of steady intel. The foundation of Dynasty Alpha."
        # "Approved everything, questioned nothing. The rubber stamp that broke."
        pass  # Generated as part of post-mortem prompt, extracted from response
```

---

### STEP 6 — Reproduction Engine (src/dynasty/reproduction.py)

The core reproduction logic:

```
Class: ReproductionEngine

    REPRODUCTION_COOLDOWN_EVALS = 3
    DYNASTY_CONCENTRATION_HARD_LIMIT = 0.40
    DYNASTY_CONCENTRATION_WARNING = 0.25
    MEMORY_INHERITANCE_DISCOUNT = 0.75
    MEMORY_AGE_DECAY_FACTOR = 0.95
    MEMORY_AGE_DECAY_START_DAYS = 30
    MEMORY_CONFIDENCE_FLOOR = 0.10
    TRUST_INHERITANCE_FACTOR = 0.50
    TEMPERATURE_MUTATION_RANGE = 0.03
    MAX_REPRODUCTIONS_PER_CYCLE = 1
    
    async check_and_reproduce() -> ReproductionResult:
        """Called during Genesis evaluation cycle, step 7."""
        
        candidates = await self.get_eligible_candidates()
        if not candidates:
            return ReproductionResult(reproduced=False, reason="no_eligible_candidates")
        
        # Process top candidate only (one per cycle)
        parent = candidates[0]
        
        # Dynasty concentration check
        concentration = await self.check_dynasty_concentration(parent)
        if concentration.blocked:
            return ReproductionResult(reproduced=False, reason="dynasty_concentration_blocked")
        
        # Ask Genesis
        decision = await self.genesis_reproduction_decision(parent, concentration)
        
        if not decision.should_reproduce:
            log.info(f"Genesis denied reproduction for {parent.name}: {decision.reasoning}")
            return ReproductionResult(reproduced=False, reason=decision.reasoning)
        
        # Handle proactive termination if at capacity
        if decision.proactive_termination and active_agent_count >= MAX_AGENTS:
            target = get_agent(decision.proactive_termination.agent_id)
            await death_protocol.execute(target, reason="proactive_replacement")
        
        # Check if parent died this cycle (posthumous reproduction)
        parent_alive = parent.status == "active"
        
        # Build the offspring
        offspring = await self.build_offspring(parent, decision, posthumous=not parent_alive)
        
        return ReproductionResult(reproduced=True, offspring=offspring, parent=parent)
    
    async get_eligible_candidates() -> list[Agent]:
        """Get all agents meeting reproduction requirements, ranked by composite."""
        candidates = []
        for agent in get_active_agents():
            if (agent.prestige_title in ["Veteran", "Elite", "Legendary"]
                and agent.composite_score >= get_role_median(agent.role)
                and get_attributed_pnl(agent) > 0
                and agent.reproduction_cooldown_until is None or agent.reproduction_cooldown_until <= now()
                and not system_in_alert()):
                candidates.append(agent)
        
        return sorted(candidates, key=lambda a: a.composite_score, reverse=True)
    
    async build_offspring(parent, decision, posthumous=False) -> Agent:
        """Build and initialize the offspring agent."""
        
        # a. Temperature mutation
        temp = parent.api_temperature + random.uniform(-TEMPERATURE_MUTATION_RANGE, TEMPERATURE_MUTATION_RANGE)
        temp = clamp(temp, *get_temperature_bounds(parent.role))
        
        # b. Create agent record
        offspring = Agent(
            name=decision.offspring_name,
            role=parent.role,
            generation=parent.generation + 1,
            parent_id=parent.id,
            dynasty_id=parent.dynasty_id,
            allocated_capital=get_role_default_capital(parent.role),
            thinking_budget_daily=parent.thinking_budget_daily,
            api_temperature=temp,
            survival_clock_expires=now() + timedelta(days=14),
            watched_markets=apply_watchlist_mutations(parent.watched_markets, decision.mutations),
            initial_watchlist=apply_watchlist_mutations(parent.watched_markets, decision.mutations),
            founding_directive=decision.mutations.founding_directive,
            founding_directive_consumed=False,
            posthumous_birth=posthumous,
            status="active"
        )
        db.insert(offspring)
        
        # c. Build mentor package
        mentor_package = await library_service.build_mentor_package(parent.id)
        
        # d. Transfer long-term memory with inheritance discount + age decay
        await self.transfer_memories(parent.id, offspring.id)
        
        # e. Transfer trust relationships at 50% strength
        await self.transfer_relationships(parent.id, offspring.id)
        
        # f. Create lineage record
        await lineage_manager.create_lineage_record(
            agent=offspring, parent=parent,
            mentor_package=mentor_package,
            mutations=decision.mutations,
            founding_directive=decision.mutations.founding_directive
        )
        
        # g. Create dynasty record if needed, update dynasty stats
        await dynasty_manager.record_birth(parent, offspring)
        
        # h. Run orientation
        await orientation_protocol.run_orientation(offspring)  # Modified for offspring
        
        # i. Register with scheduler
        scheduler.register(offspring)
        
        # j. Agora announcement
        agora.broadcast("genesis-log", {
            "event": "REPRODUCTION",
            "parent": parent.name,
            "offspring": offspring.name,
            "generation": offspring.generation,
            "dynasty": get_dynasty(parent.dynasty_id).dynasty_name,
            "mutations": decision.mutations,
            "inherited_memories": count_inherited_memories(offspring.id),
            "inherited_temperature": temp,
            "posthumous": posthumous
        })
        
        # k. Update parent
        if parent.status == "active":
            parent.last_reproduction_at = now()
            parent.offspring_count += 1
            parent.reproduction_cooldown_until = now() + timedelta(
                days=REPRODUCTION_COOLDOWN_EVALS * 14)
            parent.save()
        
        return offspring
    
    async transfer_memories(parent_id, offspring_id):
        """Copy parent's long-term memories with inheritance discount and age decay."""
        parent_memories = get_active_long_term_memories(parent_id)
        
        for memory in parent_memories:
            # Inheritance discount
            confidence = memory.confidence * MEMORY_INHERITANCE_DISCOUNT
            
            # Age decay for memories older than 30 days
            age_days = (now() - memory.created_at).days
            if age_days > MEMORY_AGE_DECAY_START_DAYS:
                decay = MEMORY_AGE_DECAY_FACTOR ** (age_days - MEMORY_AGE_DECAY_START_DAYS)
                confidence *= decay
            
            # Floor
            confidence = max(confidence, MEMORY_CONFIDENCE_FLOOR)
            
            # Determine source label
            if memory.source in ("parent", "grandparent"):
                source = "grandparent"  # inherited memory being inherited again
            else:
                source = "parent"
            
            offspring_memory = LongTermMemory(
                agent_id=offspring_id,
                memory_type=memory.memory_type,
                content=memory.content,
                confidence=confidence,
                source=source,
                source_cycle=memory.source_cycle,
                times_confirmed=0,  # offspring hasn't confirmed anything yet
                times_contradicted=0,
                is_active=True
            )
            db.insert(offspring_memory)
    
    async transfer_relationships(parent_id, offspring_id):
        """Copy parent's trust relationships at 50% strength."""
        parent_relationships = get_active_relationships(parent_id)
        
        for rel in parent_relationships:
            # 50% inheritance: blend parent trust with neutral prior
            inherited_trust = rel.trust_score * TRUST_INHERITANCE_FACTOR + 0.5 * (1 - TRUST_INHERITANCE_FACTOR)
            
            offspring_rel = AgentRelationship(
                agent_id=offspring_id,
                target_agent_id=rel.target_agent_id,
                target_agent_name=rel.target_agent_name,
                trust_score=inherited_trust,
                interaction_count=0,  # offspring has no direct interactions yet
                positive_outcomes=0,
                negative_outcomes=0,
                last_assessment=f"Inherited from parent at {inherited_trust:.2f} trust"
            )
            db.insert(offspring_rel)
```

---

### STEP 7 — Dynasty Analytics (src/dynasty/dynasty_analytics.py)

```
Class: DynastyAnalytics

    async dynasty_performance(dynasty_id) -> DynastyReport:
        - Total P&L across all members
        - Avg lifespan
        - Generational improvement: for each parent→offspring pair,
          compare peak composite scores. Mean improvement across all pairs.
        - Dominant behavioral traits (from profile snapshots)
        - Market focus distribution
        - Win rate trend across generations
    
    async cross_dynasty_comparison() -> list[DynastyComparison]:
        - All active dynasties ranked by total P&L, member count, avg score
        - Strong lineages vs weak lineages
    
    async generational_improvement(dynasty_id) -> float:
        """Calculate whether later generations outperform earlier ones."""
        pairs = get_parent_offspring_pairs(dynasty_id)
        if not pairs:
            return 0.0
        
        improvements = []
        for parent, offspring in pairs:
            if parent.final_composite and offspring.composite_score:
                # Use peak composite for fair comparison
                parent_peak = get_peak_composite(parent.id)
                offspring_peak = get_peak_composite(offspring.id)
                if parent_peak > 0:
                    improvements.append((offspring_peak - parent_peak) / parent_peak)
        
        return mean(improvements) if improvements else 0.0
```

Integrate into Genesis reproduction decision: include dynasty analytics in the reproduction prompt.

---

### STEP 8 — Update Death Protocol

Modify the evaluation engine's termination flow (src/genesis/evaluation_engine.py) to call the full death protocol:

1. Call existing Phase 3D termination steps
2. Add: `await dynasty_manager.record_death(agent)`
3. Add: `await lineage_manager.record_death(agent, evaluation)`
4. Add: `await memorial_manager.create_memorial(agent, evaluation)`
5. Add: Knowledge preservation (mark memories, preserve profile)
6. Add: Library contribution check (strategy record if profitable)
7. Add: Enhanced Agora announcement with dynasty info

---

### STEP 9 — Update Genesis Main Cycle

Modify `src/genesis/genesis.py` to include reproduction in the cycle:

After step 6 (capital reallocation), before step 8 (spawn decisions):
```
# Step 7: Reproduction
reproduction_result = await reproduction_engine.check_and_reproduce()
if reproduction_result.reproduced:
    log.info(f"Reproduction: {reproduction_result.offspring.name} born from {reproduction_result.parent.name}")
```

Also update the boot sequence to create dynasty records for Gen 1 agents:
```
# After spawning each Gen 1 agent in boot_sequence.py:
await dynasty_manager.create_dynasty(agent)
await lineage_manager.create_lineage_record(agent, parent=None)
```

---

### STEP 10 — Update Orientation Protocol

Modify `src/agents/orientation.py` to handle offspring orientation:

1. Detect if agent has a parent_id (offspring vs fresh spawn)
2. If offspring: use modified orientation template (1 textbook + mentor package)
3. If offspring: include founding directive as a question
4. If offspring: include lineage identity in system prompt addendum
5. After orientation completes: set `founding_directive_consumed = True`

Modify Context Assembler to enforce:
- If `founding_directive_consumed == True`, NEVER include founding_directive in context
- This is a hard check, not a guideline

---

### STEP 11 — Dashboard Updates

Add to the FastAPI endpoints:

```
GET /api/dynasties
    Returns: all dynasties with stats, ordered by status then total P&L

GET /api/dynasties/{id}
    Returns: dynasty detail with full family tree

GET /api/dynasties/{id}/tree
    Returns: hierarchical tree structure for visualization
    Nodes: agent name, generation, status, composite score, lifespan

GET /api/dynasties/{id}/analytics
    Returns: dynasty performance report, generational improvement

GET /api/memorials
    Returns: The Fallen — all memorial records, paginated

GET /api/memorials/{agent_id}
    Returns: single memorial with epitaph
```

Add dashboard views:
- **Dynasty page**: family tree visualization (simple hierarchical layout), dynasty stats, generational improvement chart
- **The Fallen**: memorial wall showing dead agents with epitaphs, cause of death, key stats
- **Agent detail**: add lineage info (parent, children, dynasty, generation)

---

### STEP 12 — Tests

**tests/test_dynasty_manager.py:**
- Test dynasty creation for founder agent
- Test dynasty stats update on birth
- Test dynasty stats update on death
- Test dynasty extinction when last member dies
- Test extinction Agora announcement
- Test peak members tracking

**tests/test_lineage_manager.py:**
- Test lineage record creation for Gen 1 (no parent)
- Test lineage record creation for offspring (with parent)
- Test grandparent_id set correctly
- Test death record update
- Test family tree retrieval
- Test ancestor chain retrieval

**tests/test_reproduction_engine.py:**
- Test eligibility: Veteran with positive P&L is eligible
- Test eligibility: non-Veteran is NOT eligible
- Test eligibility: agent on cooldown is NOT eligible
- Test eligibility: system in alert blocks reproduction
- Test dynasty concentration block at 40%
- Test dynasty concentration warning at 25%
- Test memory inheritance with 75% discount
- Test memory age decay (30+ day old memories lose additional confidence)
- Test memory confidence floor at 0.10
- Test trust inheritance at 50% strength
- Test temperature mutation within ±0.03 and clamped to bounds
- Test offspring gets correct role (same as parent)
- Test offspring survival clock is 14 days (not 21)
- Test posthumous reproduction works when parent dies same cycle
- Test proactive termination frees slot at capacity
- Test one reproduction per cycle max
- Test founding directive is a question not instruction
- Test parent cooldown set after reproduction

**tests/test_memorial_manager.py:**
- Test memorial creation with correct stats
- Test best/worst metric extraction from evaluation

**tests/test_dynasty_analytics.py:**
- Test generational improvement positive (offspring > parent)
- Test generational improvement negative (offspring < parent)
- Test cross-dynasty comparison ranking
- Test dynasty with no offspring pairs returns 0.0 improvement

**tests/test_offspring_orientation.py:**
- Test offspring orientation includes mentor package
- Test offspring orientation has reduced Library (1 textbook)
- Test founding directive appears in orientation cycle
- Test founding directive does NOT appear in subsequent cycles
- Test founding_directive_consumed flag set after orientation

**tests/test_death_protocol.py (integration):**
- Test full death protocol: financial cleanup → relationship archival → 
  post-mortem → knowledge preservation → lineage update → dynasty update → memorial
- Test dynasty extinction on last member death
- Test role gap triggers emergency spawn after death

Run all: `python -m pytest tests/ -v`

---

### STEP 13 — Configuration Updates

Add to SyndicateConfig:

```python
# Phase 3F: Reproduction & Dynasties
reproduction_cooldown_evals: int = 3
reproduction_min_prestige: str = "Veteran"
dynasty_concentration_hard_limit: float = 0.40
dynasty_concentration_warning: float = 0.25
memory_inheritance_discount: float = 0.75
memory_age_decay_factor: float = 0.95
memory_age_decay_start_days: int = 30
memory_confidence_floor: float = 0.10
trust_inheritance_factor: float = 0.50
temperature_mutation_range: float = 0.03
max_reproductions_per_cycle: int = 1
offspring_survival_clock_days: int = 14
```

Update .env.example with new variables.

---

### STEP 14 — Update CLAUDE.md

Add Phase 3F components to the architecture section:
- Dynasty Manager (src/dynasty/)
- Lineage Manager
- Memorial Manager
- Reproduction Engine
- Dynasty Analytics
- Updated death protocol
- Updated Genesis cycle with reproduction step
- Updated orientation for offspring
- Dynasty and memorial dashboard endpoints

Update Phase Roadmap to show Phase 3F as COMPLETE and all of Phase 3 as COMPLETE.

---

### STEP 15 — Update CHANGELOG.md and CURRENT_STATUS.md

Log everything built. Note that Phase 3 is now fully complete.

---

### STEP 16 — Git Commit and Push

```
git add .
git commit -m "Phase 3F: First Death, First Reproduction, First Dynasty — evolution begins"
git push origin main
```

---

## DESIGN DECISIONS (Reference for Claude Code)

1. **Death is permanent, knowledge is immortal.** Agents cease to exist but their lessons persist in the Library, Agora, lineage records, and offspring memories.
2. **10-step death protocol** ensures clean financial cleanup, knowledge preservation, relationship archival, dynasty updates, and memorialization.
3. **Reproduction requires Veteran prestige (10+ evaluations, ~140 days).** Prevents flash-in-the-pan agents from spawning based on luck.
4. **Dynasty concentration limit: 40% hard block, 25% warning.** Prevents any lineage from crowding out diversity.
5. **One reproduction per Genesis cycle.** Prevents spawn storms.
6. **Reproduction cooldown: 3 evaluations (~42 days).** Prevents a single agent from flooding the ecosystem.
7. **Offspring are always the same role as parent.** Cross-role spawning wastes the knowledge inheritance. Genesis uses general spawns for role filling.
8. **Temperature inheritance: parent's evolved temp ± random 0.03.** Adds diversity within dynasties without overriding evolved adaptations.
9. **Memory inheritance: 75% confidence discount + age decay for memories > 30 days.** Forces offspring to re-validate, prevents stale knowledge from persisting unchanged.
10. **Trust inheritance: 50% strength.** Head start that decays to neutral if not reinforced. Offspring form their own opinions.
11. **Founding directives are QUESTIONS, not instructions.** "Is there opportunity in BNB?" not "Explore BNB." Questions invite exploration, instructions demand compliance. Appears only in orientation, then disappears.
12. **Posthumous reproduction is valid.** If parent dies in the same Genesis cycle, offspring still spawns. Parent's knowledge is already captured.
13. **Population pressure management.** At capacity, Genesis may recommend proactive termination of underperformers to make room for offspring.
14. **Generational improvement tracking.** Dynasty analytics measure whether later generations outperform earlier ones. Negative trend = knowledge pipeline is broken.
15. **Memorial records ("The Fallen")** preserve dead agents for the dashboard with epitaphs, key stats, and notable achievements.
16. **Offspring survival clock is 14 days standard**, not the 21-day Gen 1 grace. They have inherited knowledge — no extra leash.
17. **Offspring orientation prioritizes mentor package over Library textbooks.** Only 1 textbook (Thinking Efficiently); remaining context budget goes to parent's wisdom.
18. **Context Assembler enforces founding_directive_consumed flag.** Hard check — the directive NEVER leaks into post-orientation cycles.
19. **Dynasty extinction is permanent.** Dead dynasties don't resurrect. Their knowledge lives on in the Library.
20. **Gen 1 agents each found their own dynasty** during the boot sequence. Dynasty records created alongside agent records.

---

Before you start, confirm you've read CLAUDE.md and the current project state. Then proceed through each step in order. Ask me if anything is unclear.
