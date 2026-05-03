# Phase 10: The Wire — External Intelligence Pipeline (Kickoff)

> **Status:** Ready for Claude Code execution
> **Prerequisite:** Phase 9A (Colony Maturity + SIP Governance) must be complete and merged.
> **Estimated build size:** Large. Comparable to Phase 8C in scope. Plan for ~2–3 CC sessions with `CURRENT_STATUS.md` handoffs if context fills.
> **Pre-build action (Andrew):** Shut down all running services (Arena loop, dashboard, OODA workers, Memurai if separately managed). This build adds DB tables, new background workers, new Agora event classes, and modifies Scout/Strategist/Critic context injection. Same migration risk profile as 9A.

---

## 1. Vision

The Wire is Project Syndicate's **external intelligence pipeline** — a curated, pre-digested firehose of market-relevant data that feeds agents without drowning them.

**Core principle:** Without good data, the colony is dead in the water. Speed we can't win. Capital we don't have. The only durable edge for a Darwinian swarm of small agents is **better information processing** — weirder signals, faster digestion, structured delivery.

**Anti-principle (equally important):** Hoovering raw data is a trap. Thinking Tax compounds against breadth. Noise drowns signal. Every source is an attack surface. The Wire's job is *curation and digestion*, not maximum ingestion.

---

## 2. Architectural Decisions (Locked)

These are **not** open for redesign during build. If CC wants to deviate, surface to War Room first.

| Decision | Choice | Rationale |
|---|---|---|
| Source tier | Tier A (free, high signal) + Tier B (macro context) at launch | Earn the right to spend money. Tier C deferred to Phase 10.5. |
| Coverage | Crypto-first with macro context | Matches current Kraken-only execution scope; macro defines regime. |
| Architecture | Hybrid push/pull | Ticker (severity ≥3, free) + Archive (queryable, token-costed). |
| Content class | Factual only | Sentiment is Phase 11 of The Wire (separate phase). |
| Digestion costs | Genesis treasury (system overhead) | Phase 1 = infrastructure. Revisit if Wire spend > 10% of treasury. |
| Wire write access | Read-only for agents | Source/severity adjustments are governance questions for later. |
| Source rollout | Staged within Phase 10 | Tier 1 build = 3 sources end-to-end. Tier 2 = add the rest. |
| Severity-5 events | Auto-trigger Genesis regime review | Counts as environmental awareness, not strategy injection. |
| Failure guards | Tier 0 non-negotiable | Library reflection bug taught us silent failures are the primary risk. |

---

## 3. Source Catalog (Phase 10 Launch Set)

### Tier A — Crypto factual, free, high signal

| Source | Method | Cadence | Severity floor | Notes |
|---|---|---|---|---|
| **Kraken announcements** | RSS / scrape `https://blog.kraken.com/category/announcement` | 5 min | Auto-3 (always relevant) | Listings, delistings, maintenance, deposit/withdrawal pauses. Massive short-term price impact. |
| **CryptoPanic free** | API `https://cryptopanic.com/api/v1/posts/` | 10 min | Severity assigned by Haiku | Free tier: 100 req/day. Aggregates ~100 crypto news sources. |
| **DefiLlama** | API `https://api.llama.fi/` | 30 min | Severity 2+ on TVL deltas >5% | TVL changes, stablecoin supply, protocol flows. Free, no auth. |
| **Etherscan large transfers** | API (free tier) | 15 min | Severity 2 default, 4+ if exchange wallet | Whale watcher. 5 req/sec free tier — plenty. |
| **Funding rates (Kraken perps)** | ccxt (already integrated) | 5 min | Severity 2+ on extreme funding (>0.1% / 8h) | Crowded trade detector. Free, no extra dependency. |

### Tier B — Macro context, free

| Source | Method | Cadence | Severity floor | Notes |
|---|---|---|---|---|
| **FRED API** | `https://api.stlouisfed.org/fred/` (free key) | Daily | Severity 2 baseline | DXY, 10Y yield, VIX, M2. Slow-moving regime indicators. |
| **TradingEconomics calendar** | `https://api.tradingeconomics.com/calendar` (free guest tier) | Daily | Severity 3 within 4h of event | FOMC, CPI, NFP. Agents must know "FOMC in 4h, reduce size." |
| **Fear & Greed Index** | `https://api.alternative.me/fng/` | Daily | Severity 2 on regime change | Crude but works as a regime tag. |

**Total monthly cost at launch: $0.** Free API keys required for FRED and Etherscan. CryptoPanic and TradingEconomics work guest-tier for Phase 10.

### Tier 1 of build (start here, validate end-to-end)

Wire up only **3 sources** first, prove the pipeline:
1. Kraken announcements
2. CryptoPanic free
3. DefiLlama

Once these flow cleanly through ingest → Haiku digest → publish → agent consumption, add the remaining 5 sources in build Tier 2.

---

## 4. Database Schema

All migrations under `alembic/versions/` with prefix `phase_10_wire_`. Linearize chain after generation (lessons learned from cleanup).

### `wire_sources`
Static catalog of registered sources. Seeded by Alembic data migration.

```sql
CREATE TABLE wire_sources (
    id SERIAL PRIMARY KEY,
    name VARCHAR(64) UNIQUE NOT NULL,            -- e.g. 'kraken_announcements'
    display_name VARCHAR(128) NOT NULL,
    tier CHAR(1) NOT NULL CHECK (tier IN ('A','B','C')),
    fetch_interval_seconds INT NOT NULL,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    requires_api_key BOOLEAN NOT NULL DEFAULT FALSE,
    api_key_env_var VARCHAR(64),                 -- e.g. 'FRED_API_KEY'
    base_url TEXT NOT NULL,
    config_json JSONB,                           -- source-specific config (severity rules, etc.)
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

### `wire_raw_items`
Raw items pulled from each source before digestion. Retain 30 days then prune.

```sql
CREATE TABLE wire_raw_items (
    id BIGSERIAL PRIMARY KEY,
    source_id INT NOT NULL REFERENCES wire_sources(id),
    external_id VARCHAR(256) NOT NULL,           -- source-provided unique ID, used for dedup at fetch time
    fetched_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    occurred_at TIMESTAMPTZ,                     -- when the event itself happened (if known)
    raw_payload JSONB NOT NULL,
    digestion_status VARCHAR(16) NOT NULL DEFAULT 'pending'
        CHECK (digestion_status IN ('pending','digested','rejected','dead_letter')),
    digestion_attempts INT NOT NULL DEFAULT 0,
    UNIQUE(source_id, external_id)
);

CREATE INDEX ix_wire_raw_items_status ON wire_raw_items(digestion_status, fetched_at);
CREATE INDEX ix_wire_raw_items_source_fetched ON wire_raw_items(source_id, fetched_at DESC);
```

### `wire_events`
Digested, structured, agent-consumable events. The Archive.

```sql
CREATE TABLE wire_events (
    id BIGSERIAL PRIMARY KEY,
    raw_item_id BIGINT REFERENCES wire_raw_items(id),  -- nullable for synthesized events
    canonical_hash CHAR(64) NOT NULL,            -- SHA256 of (coin, event_type, summary) for dedup
    coin VARCHAR(32),                            -- e.g. 'BTC', 'SOL', NULL for macro
    is_macro BOOLEAN NOT NULL DEFAULT FALSE,
    event_type VARCHAR(64) NOT NULL,             -- 'listing', 'hack', 'tvl_change', 'funding_extreme', 'macro_calendar', etc.
    severity SMALLINT NOT NULL CHECK (severity BETWEEN 1 AND 5),
    direction VARCHAR(16) CHECK (direction IN ('bullish','bearish','neutral')),
    summary TEXT NOT NULL,                       -- ≤200 chars, Haiku-generated
    source_url TEXT,
    digested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    occurred_at TIMESTAMPTZ NOT NULL,
    haiku_cost_usd NUMERIC(10,6) NOT NULL DEFAULT 0,
    duplicate_of BIGINT REFERENCES wire_events(id),  -- if dedup matched, point to canonical
    published_to_ticker BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE INDEX ix_wire_events_coin_severity ON wire_events(coin, severity DESC, occurred_at DESC);
CREATE INDEX ix_wire_events_severity_recent ON wire_events(severity DESC, occurred_at DESC) WHERE duplicate_of IS NULL;
CREATE INDEX ix_wire_events_canonical ON wire_events(canonical_hash);
CREATE INDEX ix_wire_events_macro ON wire_events(is_macro, occurred_at DESC) WHERE is_macro = TRUE;
```

### `wire_source_health`
Heartbeat tracking for failure detection. One row per source, updated each cycle.

```sql
CREATE TABLE wire_source_health (
    source_id INT PRIMARY KEY REFERENCES wire_sources(id),
    last_fetch_attempt TIMESTAMPTZ,
    last_fetch_success TIMESTAMPTZ,
    last_fetch_error TEXT,
    consecutive_failures INT NOT NULL DEFAULT 0,
    items_last_24h INT NOT NULL DEFAULT 0,
    status VARCHAR(16) NOT NULL DEFAULT 'unknown'
        CHECK (status IN ('healthy','degraded','failing','disabled','unknown')),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

### `wire_query_log`
Tracks Archive queries by agents. Used for cost accounting and abuse detection.

```sql
CREATE TABLE wire_query_log (
    id BIGSERIAL PRIMARY KEY,
    agent_id INT NOT NULL REFERENCES agents(id),
    query_params JSONB NOT NULL,                 -- {coin, lookback_hours, min_severity, event_types}
    results_count INT NOT NULL,
    token_cost INT NOT NULL,                     -- thinking tax for the query itself
    queried_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX ix_wire_query_log_agent_time ON wire_query_log(agent_id, queried_at DESC);
```

### `wire_treasury_ledger`
Tracks Genesis treasury spend on Wire infrastructure. Separate from agent thinking tax.

```sql
CREATE TABLE wire_treasury_ledger (
    id BIGSERIAL PRIMARY KEY,
    cost_category VARCHAR(32) NOT NULL,          -- 'haiku_digestion', 'severity_assessment', 'dedup'
    cost_usd NUMERIC(10,6) NOT NULL,
    related_event_id BIGINT REFERENCES wire_events(id),
    incurred_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX ix_wire_treasury_ledger_time ON wire_treasury_ledger(incurred_at DESC);
```

---

## 5. Module Structure

New top-level package: `syndicate/wire/`

```
syndicate/wire/
    __init__.py
    constants.py                    # severity bands, event types, dedup window
    models.py                       # SQLAlchemy models for the 6 tables above
    sources/
        __init__.py
        base.py                     # WireSource ABC: fetch(), parse(), to_raw_items()
        kraken_announcements.py
        cryptopanic.py
        defillama.py
        etherscan_transfers.py
        funding_rates.py
        fred.py
        trading_economics.py
        fear_greed.py
    ingestors/
        __init__.py
        scheduler.py                # APScheduler-driven fetch loop
        runner.py                   # per-source execution, error handling, heartbeat updates
    digest/
        __init__.py
        haiku_digester.py           # raw_item → wire_event via Haiku
        severity.py                 # severity assignment rules (deterministic where possible)
        deduper.py                  # canonical_hash + cross-source dedup window
        prompts.py                  # digestion prompt templates
    publishing/
        __init__.py
        ticker.py                   # push severity ≥3 to Agora as wire_event class
        archive.py                  # query API for agents
    health/
        __init__.py
        monitor.py                  # heartbeat checks, volume floor, diversity check
        alerts.py                   # log alerts via structlog + Agora system events
    integration/
        __init__.py
        agent_context.py            # build Wire context block for Scout/Strategist/Critic OODA
        genesis_regime.py           # severity-5 → Genesis regime review hook
        operator_halt.py            # severity-5 of class 'exchange_outage' → Operator pause
    cli.py                          # admin commands (force fetch, test source, view health)
```

---

## 6. Build Tiers

### Tier 1 — Pipeline Skeleton + 3 Sources

**Goal:** Prove end-to-end flow with smallest viable surface area. No agent integration yet.

**Deliverables:**
1. All 6 DB tables created via Alembic migration `phase_10_wire_001_schema.py`
2. Seed migration `phase_10_wire_002_seed_sources.py` populating `wire_sources` for all 8 sources but with `enabled = FALSE` for the 5 deferred to Tier 2
3. `WireSource` ABC and 3 concrete sources: Kraken announcements, CryptoPanic free, DefiLlama
4. `IngestorScheduler` running each source on its declared cadence
5. `HaikuDigester` converting raw items → `wire_events` with severity, direction, summary
6. `Deduper` collapsing duplicates within 24h window (canonical_hash + soft match)
7. `wire_source_health` updated every cycle
8. `wire_treasury_ledger` recording Haiku spend per event
9. CLI commands: `python -m syndicate.wire.cli fetch <source>`, `python -m syndicate.wire.cli health`, `python -m syndicate.wire.cli digest-pending`
10. Test suite: 35+ tests covering happy path, source failure, malformed Haiku output, dedup correctness, schema validation

**Tier 1 acceptance criteria:**
- Run scheduler for 30 minutes against live free APIs → ≥10 wire_events in DB, no dead-letter items
- `python -m syndicate.wire.cli health` shows all 3 sources healthy
- Force one source to fail (bad URL) → health monitor flags it as `failing` within 2 cycles
- Treasury ledger shows accurate Haiku cost per event (verifiable by hand from API response)

### Tier 2 — Remaining Sources + Failure Hardening

**Goal:** Complete source coverage and bake in Tier 0 non-negotiable failure guards.

**Deliverables:**
1. Implement remaining 5 sources: Etherscan transfers, funding rates, FRED, TradingEconomics, Fear & Greed
2. Enable them in `wire_sources` seed
3. **Heartbeat enforcement:** monitor flags source `degraded` if no fetch in 2× expected interval, `failing` if 5 consecutive failures, `disabled` if 20 consecutive failures (auto-disable to prevent infinite retries)
4. **Volume floor check:** if total wire_events in last 6 hours < 3, log `wire.volume_floor_breach` system event in Agora
5. **Source diversity check:** if any single source produced >70% of last 24h events, log `wire.diversity_breach`
6. **Schema validation on Haiku output:** strict JSON schema; on first parse fail → retry with explicit format reminder; on second fail → mark raw item `dead_letter` and alert
7. **"Silent feed" integration test:** simulate all sources returning empty for 6 hours → assert volume_floor_breach is logged
8. Heartbeat dashboard CLI: `python -m syndicate.wire.cli health --verbose` shows per-source last-success age, items 24h, status

**Tier 2 acceptance criteria:**
- All 8 sources enabled, all healthy after 1 hour run
- Kill network access mid-run → all sources flip to `failing` within their cadence + 2 cycles
- Inject malformed mock Haiku response → raw item ends in `dead_letter`, never silently digested as empty
- Force 6h of empty results → `wire.volume_floor_breach` event present in Agora

### Tier 3 — Agent Integration + Push/Pull APIs + Genesis Hook

**Goal:** Make The Wire actually useful to the colony.

**Deliverables:**
1. **Ticker (push):** `WireTicker.publish_to_agora(event)` for any wire_event with severity ≥3 and `duplicate_of IS NULL`. Publishes as new Agora event class `wire.ticker`. Mark `published_to_ticker = TRUE`.
2. **Archive (pull):** `WireArchive.query(coin=None, lookback_hours=24, min_severity=1, event_types=None, limit=20)` — synchronous query API. Returns structured results. Records every call in `wire_query_log` with token cost.
3. **Token cost model for queries:**
   - Base: 50 tokens per query
   - +10 tokens per result returned (scaling discourages giant queries)
   - +20 tokens if lookback_hours > 24
   - Charged against agent's thinking budget like any other API call
4. **Scout integration:** Last 5 ticker events injected into Scout OODA context as `recent_signals` block. Free.
5. **Strategist integration:** Strategists can call `WireArchive.query()` during plan formulation. Token cost charged.
6. **Critic integration:** Critics can call `WireArchive.query()` during critique. Token cost charged. Critics get a small free baseline (3 queries per critique) to prevent under-critique on cost grounds.
7. **Operator halt hook:** Any wire_event with severity 5 AND event_type in (`exchange_outage`, `withdrawal_halt`, `chain_halt`) triggers `OperatorHaltSignal` → all in-flight Operator decisions for affected coin/exchange paused pending Genesis review.
8. **Genesis regime review hook:** Any severity-5 event triggers `Genesis.review_regime(trigger_event_id=...)`. Genesis evaluates whether current regime tag should change. This does NOT inject strategy — it re-evaluates the existing regime detection logic with the new signal as input.
9. **Agora event class registration:** New event class `wire.ticker` and system events `wire.volume_floor_breach`, `wire.diversity_breach`, `wire.source_disabled`.
10. Wire dashboard widget (Jinja2/HTMX): live ticker tape (last 20 severity-3+ events), source health grid, treasury spend gauge.

**Tier 3 acceptance criteria:**
- Spawn Scout in test → its OODA context contains `recent_signals` block populated from Wire
- Strategist queries archive → query logged in `wire_query_log` with correct token cost
- Inject synthetic severity-5 `exchange_outage` event → Operator halt signal raised, Genesis regime review triggered
- Dashboard widget renders without errors, shows live ticker
- 759 baseline tests + new Wire tests all pass

---

## 7. Severity Scale (Authoritative Definition)

This is the contract between The Wire and the rest of the colony. Codify in `syndicate/wire/constants.py`.

| Severity | Meaning | Examples | Push to ticker? | Auto-actions |
|---|---|---|---|---|
| 1 | Trivial / background noise | Minor protocol update, small TVL drift | No | None |
| 2 | Notable | Moderate funding rate, regional regulatory chatter, mid-tier listing | No | None |
| 3 | Material | Major listing/delisting, significant TVL move (>10%), FOMC within 4h | Yes | None |
| 4 | High-impact | Confirmed exchange hack, regulatory action against major venue, surprise rate decision | Yes | Genesis notified (advisory) |
| 5 | Critical / regime-level | Exchange withdrawal halt, chain halt, major protocol exploit, market-wide circuit breaker territory | Yes | Genesis regime review + Operator halt for affected scope |

**Severity assignment rules:**
- Deterministic where possible (e.g., Kraken listing announcement → always severity 3 minimum, deposit/withdrawal halt → always severity 5)
- Haiku assigns severity for ambiguous cases (news items, TVL deltas, on-chain anomalies) per a rubric in `digest/prompts.py`
- Hard cap: Haiku cannot assign severity 5 without a deterministic match. If Haiku tries, downgrade to 4 and log `wire.haiku_severity_capped`. Severity 5 has automatic side effects — we don't trust an LLM alone with that trigger.

---

## 8. Haiku Digestion Prompt (Template)

Codify in `syndicate/wire/digest/prompts.py`. Strict JSON output. Schema-validated.

```
You are The Wire — Project Syndicate's intelligence digestion service.
Your job: convert one raw market data item into a structured event for autonomous trading agents.

Output ONLY valid JSON matching this schema:
{
  "coin": "BTC|ETH|SOL|... or null for macro",
  "is_macro": true|false,
  "event_type": "listing|delisting|hack|exploit|tvl_change|funding_extreme|whale_transfer|exchange_outage|withdrawal_halt|chain_halt|macro_calendar|macro_data|regulatory|other",
  "severity": 1-4,   // NEVER assign 5; that is reserved for deterministic rules
  "direction": "bullish|bearish|neutral",
  "summary": "max 200 chars, factual, no speculation, no emoji"
}

RULES:
- Be terse. Agents pay tokens to read this.
- If item is unclear, set severity 1 and direction neutral.
- Never invent details not in the source.
- Never assign severity 5 — system will downgrade and log a violation.

RAW ITEM:
{raw_payload}

OUTPUT (JSON only, no preamble):
```

---

## 9. Test Suite Requirements

**Minimum 50 new tests across these categories.** All must pass before Phase 10 is considered complete.

### Unit (20 tests)
- Each source's `parse()` against fixture payloads (8 tests)
- Severity rules (deterministic + Haiku-bounded) (5 tests)
- Dedup canonical_hash collision and soft-match (3 tests)
- Token cost calculator for Archive queries (4 tests)

### Integration (20 tests)
- Full ingest → digest → publish flow per source against recorded fixtures (8 tests)
- Heartbeat: source down → status transitions through degraded → failing → disabled (3 tests)
- Volume floor breach detection (2 tests)
- Diversity breach detection (2 tests)
- Malformed Haiku output → dead_letter, never silent empty (2 tests) — **Library bug callback**
- Severity-5 event → Operator halt signal raised (1 test)
- Severity-5 event → Genesis regime review triggered (1 test)
- Haiku attempts severity 5 → downgraded + violation logged (1 test)

### End-to-end (10 tests)
- Spawn Scout → wire context block populated in OODA prompt (2 tests)
- Strategist runs Archive query → logged + charged (2 tests)
- Critic baseline-free queries respected, 4th query charged (1 test)
- Dashboard widget renders with live data (1 test)
- 24h synthetic run with mocked sources → expected event volume, no dead letters (2 tests)
- "Silent feed" scenario: all sources empty 6h → volume_floor_breach raised (2 tests)

---

## 10. CLAUDE.md Updates

After Phase 10 completes, update `CLAUDE.md` to add a new section:

```markdown
### Phase 10 — The Wire (External Intelligence Pipeline)

The Wire is the colony's external data layer. It pulls from 8 free crypto + macro sources,
digests raw items into structured events via Haiku, and exposes them to agents via two channels:

- **Ticker (push):** Severity ≥3 events broadcast to The Agora as `wire.ticker` events.
  Free for agents to consume in OODA context.
- **Archive (pull):** Queryable history via `WireArchive.query(...)`. Token-costed against
  agent thinking budget.

**Key modules:** `syndicate/wire/sources/`, `syndicate/wire/digest/`, `syndicate/wire/publishing/`,
`syndicate/wire/integration/`.

**Severity 5 events** auto-trigger Genesis regime review and (for exchange/chain outages)
Operator halt for the affected scope. Severity 5 cannot be assigned by Haiku — only by
deterministic rules in `digest/severity.py`.

**Treasury accounting:** Wire's Haiku digestion costs are billed to Genesis treasury
(`wire_treasury_ledger`), not to individual agents. This is system infrastructure overhead.

**Failure guards (non-negotiable, do not weaken):** heartbeats per source, 6h volume floor,
24h source diversity check, schema-validated Haiku output with dead-letter on parse fail.
These exist because of the Library reflection injection bug — silent failures are the
primary risk class for this codebase.

**What's NOT in Phase 10 (deferred):**
- Sentiment data (Twitter/Reddit) — Phase 11
- Paid premium sources (CryptoPanic Pro, Messari, Glassnode) — Phase 10.5, gated on observed agent demand
- Wire-derived auto-signals (e.g., "mean reversion setup detected") — never. Agents discover.
- Agent write access to Wire (proposing sources, adjusting severity) — governance question, future SIP
```

Update Phase tracker section to mark Phase 10 complete and Phase 11 (Wire sentiment layer) as next.

---

## 11. CHANGELOG.md Entry (Template)

```markdown
## [Phase 10] - The Wire (External Intelligence Pipeline)

### Added
- New `syndicate/wire/` package: 8 sources, ingestor scheduler, Haiku digestion, dedup, publish.
- 6 new database tables: wire_sources, wire_raw_items, wire_events, wire_source_health,
  wire_query_log, wire_treasury_ledger.
- Ticker (push) and Archive (pull) APIs for agent consumption.
- Severity-5 hooks: Operator halt for exchange/chain outages, Genesis regime review.
- Wire dashboard widget (Jinja2/HTMX): live ticker, source health, treasury spend.
- 50+ new tests covering unit, integration, end-to-end including silent-failure scenarios.

### Changed
- Scout OODA context now includes `recent_signals` block from Wire ticker.
- Strategist and Critic prompts updated to reference Archive query availability.
- Genesis treasury ledger extended to track Wire Haiku spend separately.

### Deferred
- Sentiment data sources → Phase 11.
- Paid premium sources → Phase 10.5.
```

---

## 12. File Checklist (CC Use)

CC: produce/touch the following files. Tick each as you go.

**Migrations:**
- [ ] `alembic/versions/phase_10_wire_001_schema.py`
- [ ] `alembic/versions/phase_10_wire_002_seed_sources.py`
- [ ] Verify Alembic chain linearizes cleanly after generation

**Core package:**
- [ ] `syndicate/wire/__init__.py`
- [ ] `syndicate/wire/constants.py`
- [ ] `syndicate/wire/models.py`
- [ ] `syndicate/wire/cli.py`

**Sources (8):**
- [ ] `syndicate/wire/sources/base.py`
- [ ] `syndicate/wire/sources/kraken_announcements.py`
- [ ] `syndicate/wire/sources/cryptopanic.py`
- [ ] `syndicate/wire/sources/defillama.py`
- [ ] `syndicate/wire/sources/etherscan_transfers.py`
- [ ] `syndicate/wire/sources/funding_rates.py`
- [ ] `syndicate/wire/sources/fred.py`
- [ ] `syndicate/wire/sources/trading_economics.py`
- [ ] `syndicate/wire/sources/fear_greed.py`

**Ingest:**
- [ ] `syndicate/wire/ingestors/scheduler.py`
- [ ] `syndicate/wire/ingestors/runner.py`

**Digest:**
- [ ] `syndicate/wire/digest/haiku_digester.py`
- [ ] `syndicate/wire/digest/severity.py`
- [ ] `syndicate/wire/digest/deduper.py`
- [ ] `syndicate/wire/digest/prompts.py`

**Publishing:**
- [ ] `syndicate/wire/publishing/ticker.py`
- [ ] `syndicate/wire/publishing/archive.py`

**Health:**
- [ ] `syndicate/wire/health/monitor.py`
- [ ] `syndicate/wire/health/alerts.py`

**Integration:**
- [ ] `syndicate/wire/integration/agent_context.py`
- [ ] `syndicate/wire/integration/genesis_regime.py`
- [ ] `syndicate/wire/integration/operator_halt.py`

**Dashboard:**
- [ ] `syndicate/dashboard/templates/widgets/wire_ticker.html`
- [ ] `syndicate/dashboard/routes/wire.py`

**Tests:**
- [ ] `tests/wire/test_sources_*.py` (8 files)
- [ ] `tests/wire/test_digester.py`
- [ ] `tests/wire/test_severity.py`
- [ ] `tests/wire/test_deduper.py`
- [ ] `tests/wire/test_health.py`
- [ ] `tests/wire/test_silent_failure.py` ← **Library bug callback test**
- [ ] `tests/wire/test_ticker.py`
- [ ] `tests/wire/test_archive.py`
- [ ] `tests/wire/test_integration_scout.py`
- [ ] `tests/wire/test_integration_genesis_severity_5.py`
- [ ] `tests/wire/test_integration_operator_halt.py`
- [ ] `tests/wire/test_e2e_24h_run.py`

**Config:**
- [ ] `.env.example` updated with `FRED_API_KEY=`, `ETHERSCAN_API_KEY=` (free tier keys)

**Docs:**
- [ ] `CLAUDE.md` updated with Phase 10 section
- [ ] `CHANGELOG.md` entry added
- [ ] `README.md` Phase tracker updated

---

## 13. Pre-Build Checklist (Andrew)

Before invoking `claude --dangerously-skip-permissions`:

- [ ] Phase 9A merged to main, all 9A tests passing
- [ ] All running services stopped: Arena loop, dashboard, OODA workers, scheduler
- [ ] Memurai verified running (Wire scheduler will use Redis for distributed locks)
- [ ] PostgreSQL backup taken (`pg_dump` to timestamped file in `backups/`)
- [ ] Free API keys registered:
  - [ ] FRED: https://fred.stlouisfed.org/docs/api/api_key.html
  - [ ] Etherscan: https://etherscan.io/apis
- [ ] `.env` updated with new keys
- [ ] Branch created: `git checkout -b phase-10-the-wire`

---

## 14. Post-Build Validation (Andrew)

After CC reports completion:

- [ ] Run full test suite: `pytest tests/ -v` — expect 759 prior + 50+ new = 809+ passing
- [ ] Run Wire scheduler standalone for 1 hour: `python -m syndicate.wire.cli run-scheduler`
- [ ] Verify wire_events accumulating: `python -m syndicate.wire.cli health --verbose`
- [ ] Verify treasury ledger: SQL `SELECT SUM(cost_usd) FROM wire_treasury_ledger;` should be small but non-zero
- [ ] Spawn one Scout in paper mode → confirm Wire context appears in its OODA log
- [ ] Inject synthetic severity-5 event via CLI → verify Genesis regime review log + Operator halt log
- [ ] Dashboard widget renders correctly at `/dashboard/wire`
- [ ] Commit and tag: `git tag phase-10-wire-complete`

---

## 15. Known Risks & Mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Free API rate limits hit during high-volume periods | Medium | Low | Per-source rate limiter in `runner.py`; backoff on 429 |
| Haiku cost spirals on a noisy source | Low | Medium | `wire_treasury_ledger` daily cap; if exceeded, source auto-disabled with alert |
| Severity 5 false positive triggers needless Operator halt | Low | High | Deterministic-only severity 5; halt is per-coin-per-exchange, not colony-wide; auto-resume after 30 min if no follow-up event |
| Source returns malformed data → Haiku hallucinates structure | Medium | High | Strict JSON schema validation; dead-letter on second parse fail; **explicit silent-failure test** (Library callback) |
| Scout context bloat from too many Wire signals | Medium | Medium | Cap injected `recent_signals` at 5 most recent severity ≥3 events; older agents query Archive instead |
| Paid Tier C creep without justification | Low | Low | Phase 10.5 gating: must show ≥X agent queries against a free source for that data class before paying |

---

## 16. Definition of Done

Phase 10 is complete when **all** of the following are true:

1. All 8 sources are enabled and `wire_source_health.status = 'healthy'` after a 1-hour live run
2. All file checklist items in Section 12 are produced and committed
3. All tests pass: 759 baseline + 50+ Wire tests
4. The silent-failure test (`tests/wire/test_silent_failure.py`) passes — this is the **most important test** in Phase 10
5. CLAUDE.md, CHANGELOG.md, README.md updated
6. Andrew's post-build validation checklist (Section 14) is fully ticked
7. Branch merged to main and tagged `phase-10-wire-complete`

---

**End of kickoff. CC: when you're ready to begin, acknowledge this doc, confirm Phase 9A is merged, and start with Tier 1.**
