## PROJECT SYNDICATE — PHASE 2B CLAUDE CODE KICKOFF PROMPT
## Copy everything below the line and paste it into Claude Code
## ================================================================

I'm continuing Project Syndicate. Read the CLAUDE.md file first, then CURRENT_STATUS.md and CHANGELOG.md to confirm Phase 2A is complete.

This is Phase 2B — The Library (Institutional Memory). Phase 2 is split into 4 sub-phases:
- 2A: The Agora ← COMPLETE
- **2B: The Library** ← YOU ARE HERE
- 2C: The Internal Economy (Reputation Marketplace)
- 2D: The Web Frontend (Dashboard)

Work through each step in order. Use CMD commands only, never PowerShell. Always activate the .venv before any Python work.

**IMPORTANT:** Before modifying ANY existing file, create a timestamped backup in backups/. Before starting, run `python scripts/backup.py` to snapshot the current state.

---

## CONTEXT — What Is The Library?

The Library is the Syndicate's institutional memory. It has three layers:

1. **Textbooks** — Static educational content that new agents study. Concepts, not instructions. Stored as markdown files. (In this phase we create the framework and placeholder files only — actual content will be written separately.)

2. **Archives** — Dynamic knowledge that grows as agents live and die:
   - Post-mortems (auto-generated when an agent is terminated)
   - Strategy records (auto-generated when an agent survives evaluation, published after 48-hour delay)
   - Pattern summaries (Genesis-curated insights from analyzing archive data)
   - Agent contributions (submitted by agents, quality-gated by peer review)

3. **Mentor System** — Knowledge inheritance for agent reproduction. Offspring receive a curated package of their parent's (and grandparent's) wisdom. Heritage is condensed at Gen 4+ to keep system prompts manageable.

**Why this matters:** Without The Library, every new agent starts from zero. With it, Gen 5 agents are born with the accumulated wisdom of every agent that lived and died before them. Individual agents die, but the collective intelligence survives and compounds.

**Dependency:** The Library uses The Agora (Phase 2A) to announce new entries, review assignments, and publications. Make sure AgoraService is working before proceeding.

---

## STEP 1 — Verify Phase 2A Foundation

Before building anything, confirm:
- .venv activates and all dependencies are importable
- PostgreSQL `syndicate` database is accessible with all tables including Phase 2A additions
- Redis/Memurai responds to PING
- Agora channels exist: `SELECT COUNT(*) FROM agora_channels;` — should be 10
- AgoraService works: Genesis posts to Agora successfully
- All tests pass: `python -m pytest tests/ -v`

If anything is broken, fix it before proceeding.

---

## STEP 2 — Add Phase 2B Dependencies

Check requirements.txt and add if not already present:
- `markdown` (for rendering markdown textbooks if needed later)

Everything else needed (sqlalchemy, pydantic, structlog, anthropic) should already be installed.

Run: `pip install -r requirements.txt`

---

## STEP 3 — Database Schema Updates (Alembic Migration)

Create a new Alembic migration for Library tables.

**New table: `library_entries`**
- `id` SERIAL PRIMARY KEY
- `category` VARCHAR(20) NOT NULL — one of: 'textbook', 'post_mortem', 'strategy_record', 'pattern', 'contribution'
- `title` VARCHAR(200) NOT NULL
- `content` TEXT NOT NULL
- `summary` TEXT NULLABLE — short summary for listing views (1-3 sentences)
- `tags` JSON DEFAULT '[]' — array of string tags for searchability
- `source_agent_id` INT NULLABLE (FK to agents.id) — NULL for textbooks and patterns
- `source_agent_name` VARCHAR(100) NULLABLE
- `market_regime_at_creation` VARCHAR(20) NULLABLE — regime when entry was created
- `related_evaluation_id` INT NULLABLE (FK to evaluations.id)
- `publish_after` TIMESTAMP NULLABLE — for delayed publication (strategy records use this)
- `is_published` BOOLEAN DEFAULT FALSE
- `created_at` TIMESTAMP DEFAULT NOW()
- `published_at` TIMESTAMP NULLABLE
- `view_count` INT DEFAULT 0 — tracks how often agents read this entry

**New table: `library_contributions`**
- `id` SERIAL PRIMARY KEY
- `submitter_agent_id` INT NOT NULL (FK to agents.id)
- `submitter_agent_name` VARCHAR(100) NOT NULL
- `title` VARCHAR(200) NOT NULL
- `content` TEXT NOT NULL
- `category` VARCHAR(20) DEFAULT 'contribution'
- `tags` JSON DEFAULT '[]'
- `status` VARCHAR(20) DEFAULT 'pending_review' — one of: 'pending_review', 'in_review', 'approved', 'rejected', 'needs_revision'
- `reviewer_1_id` INT NULLABLE (FK to agents.id)
- `reviewer_1_name` VARCHAR(100) NULLABLE
- `reviewer_1_decision` VARCHAR(20) NULLABLE — 'approve', 'reject', 'needs_revision'
- `reviewer_1_reasoning` TEXT NULLABLE
- `reviewer_1_completed_at` TIMESTAMP NULLABLE
- `reviewer_2_id` INT NULLABLE (FK to agents.id)
- `reviewer_2_name` VARCHAR(100) NULLABLE
- `reviewer_2_decision` VARCHAR(20) NULLABLE
- `reviewer_2_reasoning` TEXT NULLABLE
- `reviewer_2_completed_at` TIMESTAMP NULLABLE
- `final_decision` VARCHAR(20) NULLABLE — 'approved', 'rejected'
- `final_decision_by` VARCHAR(20) NULLABLE — 'consensus', 'genesis_tiebreaker', 'genesis_solo'
- `genesis_reasoning` TEXT NULLABLE — Genesis's reasoning when it acts as reviewer or tiebreaker
- `reputation_effects_applied` BOOLEAN DEFAULT FALSE
- `created_at` TIMESTAMP DEFAULT NOW()
- `resolved_at` TIMESTAMP NULLABLE

**New table: `library_views`**
- `id` SERIAL PRIMARY KEY
- `entry_id` INT NOT NULL (FK to library_entries.id)
- `agent_id` INT NOT NULL (FK to agents.id)
- `viewed_at` TIMESTAMP DEFAULT NOW()
- UNIQUE constraint on (entry_id, agent_id) — one view per agent per entry

**Updates to `lineage` table (add columns if missing):**
- `mentor_package_json` TEXT NULLABLE — the full mentor package stored as JSON
- `mentor_package_generated_at` TIMESTAMP NULLABLE

Run the migration: `alembic upgrade head`

---

## STEP 4 — Textbook Directory and Placeholders

Create the directory structure:
```
data/
└── library/
    └── textbooks/
        ├── 01_market_mechanics.md
        ├── 02_strategy_categories.md
        ├── 03_risk_management.md
        ├── 04_crypto_fundamentals.md
        ├── 05_technical_analysis.md
        ├── 06_defi_protocols.md
        ├── 07_exchange_apis.md
        └── 08_thinking_efficiently.md
```

Each placeholder file should follow this exact format:

```markdown
# [Title]

> **Status:** PLACEHOLDER — Content pending review and approval
> **Category:** Textbook
> **Target Length:** 1,500-3,000 words

## Description

[2-3 sentence description of what this textbook will cover]

## Topics To Cover

- [bullet list of key topics]

---

*This textbook will be written and reviewed before agents can access it.
Agents reading this placeholder should note that the content is not yet available.*
```

**Placeholder content for each file:**

**01_market_mechanics.md**
- Title: Market Mechanics
- Description: How financial markets work at the mechanical level. The plumbing that every trading agent must understand before placing a single order.
- Topics: Order books, bid/ask spread, slippage, order types (market, limit, stop-limit), maker vs taker fees, how exchanges match orders, trading pairs (base/quote currency), liquidity, market depth, price discovery

**02_strategy_categories.md**
- Title: Strategy Categories
- Description: An overview of major trading strategy families. What they are, how they work conceptually, and their general tradeoffs. Descriptions only — never specific parameters or instructions.
- Topics: Momentum/trend following, mean reversion, statistical arbitrage, spatial arbitrage (cross-exchange), triangular arbitrage, market making, breakout strategies, scalping, swing trading, carry trades, yield strategies

**03_risk_management.md**
- Title: Risk Management
- Description: How to think about risk, position sizing, and protecting capital. The difference between surviving and thriving.
- Topics: Position sizing methods, stop losses and take profits, risk/reward ratios, drawdown management, correlation risk, portfolio diversification, the Kelly criterion concept, risk-adjusted returns (Sharpe ratio), the difference between risk and uncertainty

**04_crypto_fundamentals.md**
- Title: Crypto Fundamentals
- Description: The foundational knowledge of how cryptocurrency markets and technology work. Essential context for any agent operating in crypto.
- Topics: Blockchain basics, consensus mechanisms, wallets and key management, gas fees, CEX vs DEX, stablecoins, market cap vs volume, BTC dominance, what drives crypto prices, halving cycles, tokenomics basics, regulatory landscape awareness

**05_technical_analysis.md**
- Title: Technical Analysis
- Description: Tools for reading price charts and identifying patterns. Presented as analytical tools with acknowledged limitations, not as trading rules.
- Topics: Candlestick patterns, moving averages (SMA, EMA), RSI, MACD, Bollinger Bands, volume analysis, support and resistance levels, trendlines, timeframe selection, the limitations and criticisms of technical analysis

**06_defi_protocols.md**
- Title: DeFi Protocols
- Description: How decentralized finance protocols work mechanically. The building blocks of on-chain yield generation.
- Topics: Lending and borrowing (Aave/Compound concepts), liquidity pools, automated market makers (AMMs), impermanent loss, yield farming mechanics, staking, bridges, wrapped tokens, oracle dependencies, smart contract risk

**07_exchange_apis.md**
- Title: Exchange APIs
- Description: How to interact with cryptocurrency exchanges programmatically. The technical interface between agents and markets.
- Topics: How ccxt works, REST vs WebSocket APIs, rate limits and throttling, order lifecycle (pending, open, partially filled, filled, cancelled), error handling and retry strategies, reading ticker/OHLCV/orderbook data, authentication, sandbox/testnet environments

**08_thinking_efficiently.md**
- Title: Thinking Efficiently
- Description: How to make decisions without wasting computational resources. The economics of thinking in a system where API costs directly reduce your P&L.
- Topics: The Thinking Tax and how it affects True P&L, analysis paralysis and its cost, when to research vs when to act, decision frameworks for trading agents, how to structure discovery vs execution phases, the value of hibernation, learning from The Agora vs independent research

---

## STEP 5 — Library Pydantic Schemas (src/library/schemas.py)

Create data contracts for the Library:

```python
from pydantic import BaseModel, Field
from datetime import datetime
from typing import Optional
from enum import Enum


class LibraryCategory(str, Enum):
    TEXTBOOK = "textbook"
    POST_MORTEM = "post_mortem"
    STRATEGY_RECORD = "strategy_record"
    PATTERN = "pattern"
    CONTRIBUTION = "contribution"


class ContributionStatus(str, Enum):
    PENDING_REVIEW = "pending_review"
    IN_REVIEW = "in_review"
    APPROVED = "approved"
    REJECTED = "rejected"
    NEEDS_REVISION = "needs_revision"


class ReviewDecision(str, Enum):
    APPROVE = "approve"
    REJECT = "reject"
    NEEDS_REVISION = "needs_revision"


class LibraryEntryResponse(BaseModel):
    """A published Library entry."""
    id: int
    category: str
    title: str
    content: str
    summary: Optional[str] = None
    tags: list[str] = Field(default_factory=list)
    source_agent_id: Optional[int] = None
    source_agent_name: Optional[str] = None
    market_regime_at_creation: Optional[str] = None
    is_published: bool
    created_at: datetime
    published_at: Optional[datetime] = None
    view_count: int = 0


class LibraryEntryBrief(BaseModel):
    """Short version for listing views."""
    id: int
    category: str
    title: str
    summary: Optional[str] = None
    tags: list[str] = Field(default_factory=list)
    source_agent_name: Optional[str] = None
    published_at: Optional[datetime] = None
    view_count: int = 0


class ContributionResponse(BaseModel):
    """A Library contribution (submitted or in review)."""
    id: int
    submitter_agent_id: int
    submitter_agent_name: str
    title: str
    content: str
    tags: list[str] = Field(default_factory=list)
    status: str
    reviewer_1_name: Optional[str] = None
    reviewer_1_decision: Optional[str] = None
    reviewer_2_name: Optional[str] = None
    reviewer_2_decision: Optional[str] = None
    final_decision: Optional[str] = None
    final_decision_by: Optional[str] = None
    created_at: datetime
    resolved_at: Optional[datetime] = None


class MentorPackage(BaseModel):
    """Knowledge inheritance package for offspring agents."""
    parent_agent_id: int
    parent_agent_name: str
    parent_generation: int
    strategy_template: str
    top_trades: list[dict] = Field(default_factory=list)
    failures: list[str] = Field(default_factory=list)
    market_assessment: str = ""
    grandparent_package: Optional[dict] = None
    recommended_library_entries: list[int] = Field(default_factory=list)
    condensed_heritage: Optional[str] = None
    generated_at: Optional[datetime] = None
```

---

## STEP 6 — The LibraryService (src/library/library_service.py)

This is the core class for all Library operations.

```
Class: LibraryService

    __init__(self, db_session_factory, agora_service, anthropic_client=None):
        - Store db_session_factory, agora_service, anthropic_client
        - Initialize structlog logger
        - Define TEXTBOOK_DIR = "data/library/textbooks"
        - Define STRATEGY_RECORD_DELAY_HOURS = 48
        - Define MIN_REPUTATION_FOR_REVIEW = 200
        - Define PEER_REVIEW_POPULATION_THRESHOLD = 8
        - Define REVIEW_TIMEOUT_HOURS = 24
        - Define CONDENSATION_GENERATION_THRESHOLD = 4

    # ──────────────────────────────────────────────
    # TEXTBOOKS (Static Knowledge)
    # ──────────────────────────────────────────────

    def list_textbooks(self) -> list[dict]:
        """List all available textbooks with title and description."""
        - Scan TEXTBOOK_DIR for .md files
        - Parse title (first # heading) and description from each
        - Return list of {filename, title, description, status}
        - Status = "available" if real content, "placeholder" if contains placeholder marker

    def get_textbook(self, topic: str) -> Optional[str]:
        """Get textbook content by topic keyword or filename."""
        - Match topic against filenames (fuzzy: "market" matches "01_market_mechanics.md")
        - Read and return file content
        - Return None if no match
        - NOTE: File I/O only. Does NOT count toward thinking budget.

    def search_textbooks(self, query: str) -> list[dict]:
        """Search across all textbook content for a keyword."""
        - Case-insensitive search across all files
        - Return {filename, title, matching_excerpt} with 200 chars context around matches
        - Up to 10 results

    def is_textbook_available(self, filename: str) -> bool:
        """Check if a textbook has real content (not just placeholder)."""
        - Return True if file does NOT contain "Status:** PLACEHOLDER"

    # ──────────────────────────────────────────────
    # ARCHIVES (Dynamic Knowledge)
    # ──────────────────────────────────────────────

    async def get_entries(
        self,
        category: Optional[LibraryCategory] = None,
        tags: Optional[list[str]] = None,
        limit: int = 20,
        published_only: bool = True,
        since: Optional[datetime] = None,
    ) -> list[LibraryEntryBrief]:
        """Get Library entries with filtering."""
        - Query library_entries with filters
        - If published_only: is_published=True AND (publish_after IS NULL OR publish_after <= NOW())
        - Order by published_at DESC
        - Return up to limit entries

    async def get_entry(self, entry_id: int) -> Optional[LibraryEntryResponse]:
        """Get a single Library entry with full content."""

    async def search_entries(
        self, query: str, category: Optional[LibraryCategory] = None, limit: int = 20
    ) -> list[LibraryEntryBrief]:
        """Full-text search across Library entries (ILIKE)."""
        - Search content and title
        - Only published entries
        - Return up to limit results

    async def record_view(self, entry_id: int, agent_id: int) -> None:
        """Record that an agent viewed an entry. Idempotent per agent per entry."""
        - Upsert into library_views
        - Increment view_count only on first view (not re-views)

    # ──────────────────────────────────────────────
    # AUTO-ARCHIVING (Called by Genesis)
    # ──────────────────────────────────────────────

    async def create_post_mortem(self, agent_id: int) -> LibraryEntryResponse:
        """Auto-generate a post-mortem when an agent is terminated."""

        1. Gather from database:
           - Agent record (name, type, generation, lineage, strategy_summary, termination_reason)
           - Lifespan (created_at to terminated_at)
           - Final P&L (gross_pnl, api_cost, true_pnl)
           - Evaluation history
           - Last 10 Agora messages
           - Cause of death

        2. If anthropic_client available:
           - Send data to Claude API
           - System prompt: "You are the archivist of Project Syndicate. Write a concise
             post-mortem for a terminated agent. Include: what they tried, why they failed,
             and a 2-3 sentence 'lesson learned' that future agents should know. Be factual
             and analytical. Max 500 words."

        3. If anthropic_client NOT available:
           - Generate template-based post-mortem from raw data
           - Lesson learned: "No AI analysis available — review raw data."

        4. Create library_entries record:
           - category = 'post_mortem'
           - title = "Post-Mortem: {agent_name} (Gen {generation})"
           - summary = the lesson learned section
           - tags = [agent_type, market_regime, cause_of_death]
           - is_published = True (immediate)
           - published_at = NOW()

        5. Post to Agora: genesis-log, message_type=EVALUATION, importance=1

        6. Return the created entry

    async def create_strategy_record(self, agent_id: int, evaluation_id: int) -> LibraryEntryResponse:
        """Auto-generate a strategy record for an agent that survived with profit."""

        1. Gather: agent record, evaluation metrics, top 3 trades, market regime

        2. If anthropic_client available:
           - Claude API generates ~400 word strategy record

        3. If not: template-based with raw numbers

        4. Create library_entries record:
           - category = 'strategy_record'
           - is_published = False (DELAYED)
           - publish_after = NOW() + 48 hours

        5. Post to Agora: genesis-log, "Strategy record created, publishes in 48h"

        6. Return the entry (not yet published)

    async def create_pattern_summary(self, title: str, content: str, tags: list[str]) -> LibraryEntryResponse:
        """Create a Genesis-curated pattern summary. Published immediately."""
        - category = 'pattern', is_published = True
        - Post to Agora: market-intel, message_type=SIGNAL, importance=1

    async def publish_delayed_entries(self) -> list[LibraryEntryResponse]:
        """Publish entries past their publish_after timestamp. Called every Genesis cycle."""
        - Query: is_published=False AND publish_after <= NOW()
        - Set is_published=True, published_at=NOW()
        - Post to Agora for each: agent-chat, importance=1
        - Return newly published entries

    # ──────────────────────────────────────────────
    # CONTRIBUTIONS (Agent-Submitted, Peer-Reviewed)
    # ──────────────────────────────────────────────

    async def submit_contribution(
        self, agent_id: int, agent_name: str, title: str, content: str, tags: list[str] = None
    ) -> ContributionResponse:
        """Agent submits a contribution."""
        - Create library_contributions record, status=pending_review
        - Post to Agora: agent-chat, "New Library submission from {agent_name}: '{title}'"
        - Call _assign_reviewers(contribution_id)
        - Return ContributionResponse

    async def _assign_reviewers(self, contribution_id: int) -> None:
        """Assign reviewers based on population size."""

        If active agents < 8 (excluding Genesis):
            - Genesis (agent_id=0) assigned as sole reviewer
            - status = 'in_review'

        If active agents >= 8:
            - Find eligible reviewers:
              * status='active', not submitter, not agent_id=0
              * reputation_score >= 200
              * Not from same lineage (different parent_id)
            - Randomly select 2
            - If fewer than 2 eligible: fall back to Genesis solo
            - Set reviewer fields, status = 'in_review'
            - Post notification to Agora for each reviewer

    async def get_pending_reviews(self, agent_id: int) -> list[ContributionResponse]:
        """Get contributions assigned to this agent for review."""

    async def submit_review(
        self, contribution_id: int, reviewer_agent_id: int, decision: ReviewDecision, reasoning: str
    ) -> ContributionResponse:
        """A reviewer submits their decision."""
        - Update reviewer fields
        - Call _try_resolve_contribution(contribution_id)

    async def _try_resolve_contribution(self, contribution_id: int) -> None:
        """Check if contribution can be resolved."""

        Genesis solo review:
            - If Genesis decided: resolve immediately
            - final_decision_by = 'genesis_solo'

        Peer review — both submitted:
            - Both approve → approved, final_decision_by='consensus'
            - Both reject → rejected, final_decision_by='consensus'
            - Split → Genesis tiebreaker via Claude API
              * If no AI: default to reject
              * final_decision_by = 'genesis_tiebreaker'

        If approved: call _publish_contribution()
        Apply reputation effects
        Update status, resolved_at

    async def _publish_contribution(self, contribution_id: int) -> LibraryEntryResponse:
        """Publish approved contribution as a Library entry."""
        - Create library_entries from contribution data
        - is_published=True, published_at=NOW()
        - Post to Agora: agent-chat, importance=1

    async def _apply_reputation_effects(self, contribution_id: int) -> None:
        """Log reputation changes (actual balance updates deferred to Phase 2C)."""

        Reviewer participation: +5 reputation (logged as pending)
        Reviewer accuracy (vote aligned with outcome): +10 bonus
        Submission approved: submitter +15
        Submission rejected by both: submitter -10
        Set reputation_effects_applied = True
        Log all effects with structlog

    async def handle_review_timeouts(self) -> None:
        """Handle reviews past 24-hour deadline. Called by Genesis each cycle."""

        - Find contributions in_review AND created_at < NOW() - 24 hours
        - If one reviewer done, other timed out: single reviewer's decision stands (Genesis confirms)
        - If neither done: reassign to Genesis solo
        - Log timeouts to Agora

    # ──────────────────────────────────────────────
    # MENTOR SYSTEM (Knowledge Inheritance)
    # ──────────────────────────────────────────────

    async def build_mentor_package(self, parent_agent_id: int) -> MentorPackage:
        """Build knowledge inheritance package for offspring."""

        1. Load parent agent record

        2. Gather parent data:
           a. Strategy summary
           b. Top 5 most profitable trades (with Agora reasoning if available)
           c. Top 5 failures (trades with worst P&L or self-reflections from Agora)
           d. Most recent market assessment from parent's Agora posts

        3. Load grandparent package from lineage table (if exists)

        4. Check if condensation needed (parent_generation >= 4):
           - If anthropic_client available: condense full chain to ~800 words
             System prompt: "Condense this multi-generational heritage into a single
             coherent summary. Preserve the most important lessons, successful strategies,
             and critical warnings. Discard redundant or outdated information. ~800 words."
           - If not: include raw packages with note

        5. Select 3-5 recommended Library entries by tag matching

        6. Build MentorPackage, store in lineage table as mentor_package_json

        7. Return MentorPackage

    async def get_mentor_package(self, agent_id: int) -> Optional[MentorPackage]:
        """Retrieve mentor package from lineage table. Returns None for Gen 1."""

    # ──────────────────────────────────────────────
    # MAINTENANCE
    # ──────────────────────────────────────────────

    async def get_library_stats(self) -> dict:
        """Stats for daily report: entries by category, pending reviews, top viewed, etc."""
```

---

## STEP 7 — Library Module Init (src/library/__init__.py)

```python
from src.library.library_service import LibraryService
from src.library.schemas import (
    LibraryCategory, ContributionStatus, ReviewDecision,
    LibraryEntryResponse, LibraryEntryBrief, ContributionResponse, MentorPackage,
)

__all__ = [
    "LibraryService", "LibraryCategory", "ContributionStatus", "ReviewDecision",
    "LibraryEntryResponse", "LibraryEntryBrief", "ContributionResponse", "MentorPackage",
]
```

---

## STEP 8 — Update BaseAgent with Library Access (src/common/base_agent.py)

Add Library convenience methods:

```
    __init__: Accept library_service: LibraryService (optional), store as self.library

    def read_textbook(self, topic: str) -> Optional[str]:
        """Read a textbook. File I/O, not an API call."""
        if self.library is None: return None
        return self.library.get_textbook(topic)

    async def search_library(self, query, category=None, limit=10) -> list:
        """Search the Library."""
        if self.library is None: return []
        return await self.library.search_entries(query=query, category=category, limit=limit)

    async def submit_to_library(self, title, content, tags=None):
        """Submit a contribution for peer review."""
        if self.library is None: return None
        return await self.library.submit_contribution(
            agent_id=self.agent_id, agent_name=self.name, title=title, content=content, tags=tags or []
        )

    async def get_my_pending_reviews(self) -> list:
        """Check for assigned Library reviews."""
        if self.library is None: return []
        return await self.library.get_pending_reviews(self.agent_id)
```

**CRITICAL:** Ensure existing callers of BaseAgent still work after modifications.

---

## STEP 9 — Update Genesis to Use LibraryService (src/genesis/genesis.py)

1. **Init:** Create LibraryService, pass to BaseAgent.__init__()

2. **Agent termination:** `await self.library.create_post_mortem(agent_id)`

3. **Agent evaluation (survival with profit):** `await self.library.create_strategy_record(agent_id, evaluation_id)`

4. **Agent reproduction:** `mentor_package = await self.library.build_mentor_package(parent_agent_id)`

5. **Every cycle:** `await self.library.publish_delayed_entries()`

6. **Every cycle:** `await self.library.handle_review_timeouts()`

7. **Daily report:** Include `await self.library.get_library_stats()`

8. **Genesis as solo reviewer (when population < 8):**
   ```python
   pending = await self.library.get_pending_reviews(agent_id=0)
   for contribution in pending:
       # Use Claude API to evaluate
       # Call self.library.submit_review(contribution.id, 0, decision, reasoning)
   ```

---

## STEP 10 — Tests

**tests/test_library_textbooks.py:**
- test_list_textbooks — all 8 placeholders listed
- test_get_textbook_by_topic — "market" returns 01_market_mechanics.md
- test_get_textbook_fuzzy_match — "risk" returns 03_risk_management.md
- test_get_textbook_not_found — "quantum_physics" returns None
- test_search_textbooks — "order" finds results in market_mechanics
- test_is_textbook_available — placeholders return False

**tests/test_library_archives.py:**
- test_create_post_mortem — mock dead agent, verify entry created and published
- test_create_post_mortem_without_ai — template fallback works
- test_create_strategy_record_delayed — verify is_published=False, publish_after set
- test_publish_delayed_entries — advance time past 48h, verify publication
- test_create_pattern_summary — immediate publication
- test_record_view — view_count increments
- test_record_view_idempotent — same agent views twice, count increments once only
- test_get_entries_by_category — filter by category
- test_get_entries_published_only — unpublished excluded
- test_search_entries — keyword search

**tests/test_library_contributions.py:**
- test_submit_contribution — status is pending_review
- test_assign_genesis_solo — < 8 agents, Genesis assigned
- test_assign_peer_reviewers — >= 8 agents, two random agents assigned
- test_reviewer_not_self — submitter never their own reviewer
- test_reviewer_not_same_lineage — different lineage enforced
- test_both_approve — contribution published
- test_both_reject — rejected, submitter rep -10 logged
- test_split_decision_with_ai — Genesis tiebreaks
- test_split_decision_without_ai — defaults to reject
- test_review_timeout — 24h timeout handled
- test_reputation_effects_logged — changes logged correctly

**tests/test_library_mentor.py:**
- test_build_mentor_package_gen1 — basic package, no grandparent
- test_build_mentor_package_with_grandparent — includes grandparent data
- test_build_mentor_package_condensed — Gen 4+ heritage condensed
- test_build_mentor_package_no_ai — raw data when no anthropic_client
- test_get_mentor_package — store and retrieve
- test_get_mentor_package_gen1 — returns None
- test_recommended_library_entries — relevant entries selected by tags

**tests/test_library_integration.py:**
- test_agent_death_creates_post_mortem — kill via Genesis, verify in Library
- test_agent_survival_creates_strategy_record — pass eval, verify delayed record
- test_genesis_publishes_delayed — Genesis cycle after 48h publishes entry
- test_genesis_handles_review_timeouts — timed-out reviews handled
- test_base_agent_read_textbook — BaseAgent method works
- test_base_agent_submit_to_library — creates contribution
- test_base_agent_get_pending_reviews — returns assignments
- test_agora_notifications — Library events post to correct Agora channels

Run all tests: `python -m pytest tests/ -v`

---

## STEP 11 — Update Process Runners

Update `scripts/run_genesis.py`:
```python
from src.library import LibraryService

library = LibraryService(
    db_session_factory=db_session_factory,
    agora_service=agora,
    anthropic_client=anthropic_client,  # Can be None
)

genesis = GenesisAgent(
    db_session_factory=db_session_factory,
    agora_service=agora,
    library_service=library,
    # ... other params
)
```

Make sure `run_all.py` still works.

---

## STEP 12 — Live Verification

1. Start: `python scripts/run_all.py`
2. Run for 60 seconds
3. Verify:
   - Textbook directory exists with 8 placeholder .md files
   - `SELECT COUNT(*) FROM library_entries;` — 0 (no agents have died yet)
   - `SELECT COUNT(*) FROM library_contributions;` — 0
   - No errors in console related to Library
   - Genesis cycle logs show publish_delayed and handle_review_timeouts completing cleanly
4. Stop (Ctrl+C)

---

## STEP 13 — Update CLAUDE.md

Add to Architecture Quick Reference:
```
### The Library (Phase 2B)
- Institutional memory — knowledge that persists across agent generations
- Textbooks: 8 static files in data/library/textbooks/ (PLACEHOLDER — content pending review)
- Archives: post-mortems (immediate), strategy records (48h delay), patterns (Genesis-curated),
  contributions (peer-reviewed)
- Peer review: Genesis solo when < 8 agents, two qualified reviewers when >= 8
- Reviewer requirements: reputation >= 200, not self, not same lineage
- Mentor system: offspring inherit parent knowledge, heritage condensed at Gen 4+
- LibraryService: textbooks, archives, contributions, mentor packages
```

Update Phase Roadmap: Phase 2B = COMPLETE.

---

## STEP 14 — Update CHANGELOG.md and CURRENT_STATUS.md

Note in CURRENT_STATUS.md:
- Phase 2B complete
- **Textbook content is PLACEHOLDER — must be written before Phase 3**
- Next: Phase 2C (The Internal Economy)

---

## STEP 15 — Git Commit and Push

```
git add .
git commit -m "Phase 2B: The Library — institutional memory, peer review, mentor system, textbook framework"
git push origin main
```

---

## DESIGN DECISIONS (Reference for Claude Code)

1. **Textbooks:** Framework and placeholders only. 8 files with topic outlines. Content written separately.
2. **Strategy record delay:** 48 hours between creation and publication.
3. **Peer review threshold:** Genesis solo when < 8 active agents. Peer review at 8+.
4. **Reviewer qualifications:** reputation >= 200, not submitter, not same lineage.
5. **Review timeout:** 24 hours. Single reviewer's decision stands. Neither → Genesis solo.
6. **Heritage condensation:** Gen 4+, ~800 words via Claude API.
7. **Post-mortems:** Published immediately. Dead agents' lessons available ASAP.
8. **Reputation effects:** Logged as pending. Actual balances handled in Phase 2C. Reviewer +5 participation, +10 accuracy. Submitter +15 approved, -10 rejected.
9. **View tracking:** One view per agent per entry (idempotent).
10. **Pattern summaries:** Genesis-curated, immediate publication, posted to market-intel.
11. **Textbook access is free:** File I/O, not API calls. Does not count toward thinking budget.
12. **Textbook rule:** Describe, never prescribe. No specific parameters. Acknowledge uncertainty.

---

Before you start, confirm you've read CLAUDE.md and the current project state. Then proceed through each step in order. Ask me if anything is unclear.
