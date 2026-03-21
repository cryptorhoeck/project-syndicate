## PROJECT SYNDICATE — DEFERRED ITEMS TRACKER
## Last updated: Phase 3F Design Session (March 2026)
## ================================================================
## 
## This document tracks design decisions, requirements, and features
## identified during design sessions that belong in LATER phases.
## Nothing here is forgotten — it's queued.
##
## Format: [Phase] → Item → Context (where it was identified)
## ================================================================

---

## PHASE 3B — Cold Start Boot Sequence

- [x] **First-cycle cold start problem**: New agents spawn with zero memory. Need a special first-cycle design with heavier Library injection and orientation briefing from Genesis or a mentor agent. Without this, first cycles are expensive flailing. *(Identified: Phase 3A thinking cycle design)* → **RESOLVED: Orientation Protocol designed in Phase 3B**

- [x] **Library integration hook**: Agents should access The Library during reflection cycles and during first spawn orientation. Not injected into every regular cycle (wastes context budget). A deliberate "study session" the agent can choose. *(Identified: Phase 3A gap analysis #11)* → **RESOLVED: Orientation cycle injects textbook summaries. Reflection-cycle Library access deferred to Phase 3E.**

- [x] **Inter-agent workflow pipeline**: The Scout → Strategist → Critic → Operator pipeline needs explicit design. Current interrupt system handles triggering, but the full handoff protocol (how a Scout opportunity becomes a Strategist plan becomes a Critic review becomes an Operator trade) needs its own design in 3B. *(Identified: Phase 3A gap analysis #1)* → **RESOLVED: Full pipeline designed with opportunities table, plans table, expiration rules, and revision pause/resume.**

---

## PHASE 3C — Paper Trading Infrastructure

- [x] **Replace trade action placeholder**: Phase 3A Operator trade actions return mock results. Phase 3C builds the real Paper Trading engine that simulates against live market data. *(Identified: Phase 3A implementation, Step 7)* → **RESOLVED: Full paper trading engine designed with realistic slippage, fees, position monitoring, and paper/live switch architecture.**

---

## PHASE 3D — The First Evaluation Cycle

- [x] **Cross-agent position awareness**: Individual agents only see their own positions. Need Warden to inject portfolio-level context (e.g., "Portfolio already 40% exposed to SOL ecosystem"). Without this, correlated positions create hidden concentration risk. *(Identified: Phase 3A gap analysis #7)* → **RESOLVED: Warden concentration blocking at 50% hard limit / 35% warning. Portfolio awareness injected into Operator context. Phase 3C alerting upgraded to blocking.**

- [x] **Gaming self-notes protection**: Agents could write optimistic self-notes while their P&L bleeds. Genesis evaluation must weight quantitative record (P&L, Sharpe, win rate) over qualitative record (self-notes, reflections). Actions over words. *(Identified: Phase 3A gap analysis #8)* → **RESOLVED: Honesty Score (supplementary, not primary). Genesis prompt explicitly states "weight quantitative over self-assessment." Confidence calibration measures whether self-reports match reality.**

- [x] **Idle rate as evaluation metric**: Track what % of cycles an agent goes idle. Distinguish between "strategic patience" (idle while waiting for setup) and "paralysis" (idle because the agent doesn't know what to do). Not to punish caution, but to identify dead weight. *(Identified: Phase 3A gap analysis #9)* → **RESOLVED: Idle Analyzer classifies idle cycles into 4 categories (strategic_patience, post_loss_caution, no_input, paralysis). Breakdown included in evaluation data package.**

- [x] **Genesis evaluation philosophy**: Quantitative metrics are primary evidence. Reflections and self-notes are supplementary. An agent that says "I'm learning so much!" while losing money is delusional, not optimistic. *(Identified: Phase 3A gap analysis #8)* → **RESOLVED: Baked into Genesis evaluation prompt. Honesty score is supplementary only. Role-specific composites are 100% quantitative. No appeals process.**

- [x] **Watchlist overlap monitoring**: If two Scouts have >80% watchlist overlap, one is redundant and a candidate for termination or reassignment. Track as evaluation metric, not a hard constraint — market-driven convergence is fine, but Genesis should notice it. *(Identified: Phase 3B gap analysis #9)* → **RESOLVED: Watchlist overlap check included in Scout evaluation data. Flagged to Genesis when >80%. Informational, not automatic termination.**

---

## PHASE 3E — Personality Through Experience

- [x] **Temperature evolution**: Per-agent temperature overrides that evolve based on performance. An agent that performs well at 0.7 might drift toward 0.8. One that keeps making mistakes might get cooled down. The config supports per-agent overrides already (Phase 3A), but the evolution mechanism belongs here. *(Identified: Phase 3A temperature strategy design)* → **RESOLVED: Temperature drifts ±0.05 per evaluation with momentum requirement (2 consecutive same-direction signals). Clamped to role bounds. Inherits to offspring.**

- [x] **Scout differentiation is intentionally absent at spawn.** Both Gen 1 Scouts have identical configuration except for watchlist. Personality and specialization emerge through experience — one may discover it's good at volume breakouts while the other develops instinct for trend reversals. This is by design, not an oversight. Document explicitly so future phases don't "fix" it. *(Identified: Phase 3B gap analysis #2)* → **RESOLVED: Documented as design decision #2 in Phase 3E. Divergence tracking added to measure how identical agents become different over time. Low divergence (<0.15) flagged to Genesis.**

- [x] **Reflection-cycle Library access**: Agents should be able to optionally access Library content during reflection cycles (not just orientation). A deliberate "study session" for ongoing learning. *(Identified: Phase 3A gap analysis #11, partially resolved in 3B for orientation only)* → **RESOLVED: Passive Library injection during reflections. Targeted by weakest metric from last evaluation. Uses buffer portion of token budget. 5-reflection cooldown per resource. Falls back to archive entries (post-mortems, strategy records) when textbooks on cooldown.**

---

## PHASE 2 BACKLOG — Internal Economy

- [ ] **Internal Economy actions in action spaces**: Every agent role needs economy actions added to their menus: `request_intel`, `offer_intel`, `hire_agent`, `trade_reputation`. Without these in the action space, agents can't participate in the economy. Add once the economy system is active and tested. *(Identified: Phase 3A gap analysis #6)*

---

## PHASE 3F — First Death, First Reproduction, First Dynasty

- [x] **Temperature inheritance**: Offspring inherit parent's evolved temperature, not role default. A Scout dynasty that evolved to 0.85 passes that to next generation. Config and storage are built in Phase 3E — the inheritance mechanism belongs in 3F's reproduction logic. *(Identified: Phase 3E temperature evolution design)* → **RESOLVED: Offspring inherit parent's temperature ± random 0.03 perturbation. Clamped to role bounds. Creates diversity within dynasties.**

- [x] **Behavioral profile inheritance**: Parent's profile is included in lineage record. Offspring doesn't inherit the profile itself (they build their own), but Genesis can see the parent's profile when deciding spawn parameters. *(Identified: Phase 3E behavioral profile design)* → **RESOLVED: Parent's profile snapshot stored in lineage record at reproduction time. Genesis references it during reproduction decisions. Offspring builds its own profile from scratch.**

- [x] **Relationship inheritance**: Should offspring inherit parent's trust relationships? Argument for: don't waste time re-evaluating agents the parent already assessed. Argument against: parent's trust data may be outdated, and offspring should form their own opinions. Needs design decision. *(Identified: Phase 3E relationship memory design)* → **RESOLVED: Inherit at 50% strength (blend parent trust with neutral prior). Gives head start without blind trust. Time decay still applies — inherited trust fades to neutral if not reinforced by offspring's own interactions.**

---

## PHASE 4 — The Arena

- [ ] **Route reflection cycles to Batch API**: Reflection cycles (every 10th) are not time-sensitive. Route them through BatchProcessor for 50% savings. Requires async result handling in the thinking cycle. *(Identified: Phase 3.5 batch processor design)*

- [ ] **Route Genesis evaluation summaries to Batch API**: Daily evaluations can be batched. 50% savings on the most expensive single operation in the system. *(Identified: Phase 3.5 batch processor design)*

- [ ] **Haiku quality monitoring**: Track validation failure rates per model. If Haiku fails significantly more than Sonnet, adjust routing thresholds or tighten output schemas. Data needed: ~100 cycles per model. *(Identified: Phase 3.5 model router design)*

- [ ] **Dynamic model routing based on agent performance**: High-performing agents could earn Sonnet access for all cycles. Low-performers get Haiku-only to reduce their burn rate. Ties into the thinking budget tier system. *(Identified: Phase 3.5 model router design)*

## PHASE 4 — Natural Selection (continued)

- [ ] **Parallel cycle processing**: Phase 3A uses sequential processing (one cycle at a time). At 20+ agents, this becomes a bottleneck. Phase 4 should add parallel processing lanes with deconfliction logic. *(Identified: Phase 3A cycle scheduler design)*

- [ ] **Multi-step reasoning chains**: Phase 3A is one API call per cycle. Some complex decisions (e.g., Strategist building a multi-leg trade) might benefit from chained thinking. If implemented, thinking tax must scale proportionally. *(Identified: Phase 3A gap analysis #3 — deferred past Phase 3)*

- [ ] **Partial fill simulation**: At $20-50 positions, orders always fill completely against deep order books. When scaling to larger positions, partial fills become realistic and the paper trading engine should simulate them. *(Identified: Phase 3C known simplifications)*

- [ ] **Perpetual futures support**: Phase 3C simulates spot trading only. Adding perpetual futures requires funding rate simulation (every 8 hours), margin mechanics, and liquidation logic. *(Identified: Phase 3C known simplifications)*

- [ ] **Margin calls / forced liquidation on shorts**: Phase 3C uses simplified shorts with no margin simulation. Warden position limits prevent catastrophic exposure for now. At scale with larger short positions, proper margin and liquidation mechanics should be added. *(Identified: Phase 3C known simplifications)*

- [ ] **Normalization range tuning from historical data**: Phase 3D uses fixed reference ranges for metric normalization (e.g., Sharpe [-1.0, 3.0]). Once sufficient historical data exists (50+ agent evaluations), ranges should be recalibrated based on actual population distributions. Could be a SIP proposal or owner override. *(Identified: Phase 3D audit #7)*

- [ ] **Genesis judgment value tracking**: Phase 3D adds Genesis self-metrics to the daily report, including whether Genesis's probation judgments are better than the pre-filter alone. If data shows Genesis judgment consistently matches or underperforms the pre-filter, consider simplifying to pre-filter only (saves API cost). *(Identified: Phase 3D audit #21)*

---

## PHASE 8 — Go Live

- [ ] **LiveTradingService implementation**: Build the live implementation of TradeExecutionService that routes orders to real exchanges via ccxt. Same interface as PaperTradingService. Switch is one env variable: `TRADING_MODE=live`. *(Identified: Phase 3C architecture)*

- [ ] **Exchange state reconciliation**: Live trading needs reconciliation between DB state and exchange state. If the system crashes and restarts, positions on the exchange may differ from what's in the database. Build a reconciliation check on startup. *(Identified: Phase 3C architecture)*

---

## NOTES

- Items are checked off when implemented, not when designed
- If an item's scope changes during design of its target phase, update here
- This tracker should be referenced at the START of every phase design session
- Copy relevant items into kickoff docs when building their target phase

---
