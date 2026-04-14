## PROJECT SYNDICATE — PHASE 2C CLAUDE CODE KICKOFF PROMPT
## Copy everything below the line and paste it into Claude Code
## ================================================================

I'm continuing Project Syndicate. Read the CLAUDE.md file first, then CURRENT_STATUS.md and CHANGELOG.md to confirm Phase 2B is complete.

This is Phase 2C — The Internal Economy (Reputation Marketplace). Phase 2 is split into 4 sub-phases:
- 2A: The Agora ← COMPLETE
- 2B: The Library ← COMPLETE
- **2C: The Internal Economy** ← YOU ARE HERE
- 2D: The Web Frontend (Dashboard)

Work through each step in order. Use CMD commands only, never PowerShell. Always activate the .venv before any Python work.

**IMPORTANT:** Before modifying ANY existing file, create a timestamped backup in backups/. Before starting, run `python scripts/backup.py` to snapshot the current state.

---

## CONTEXT — What Is The Internal Economy?

The Internal Economy is a reputation-based marketplace that creates real economic incentives for agents to produce quality work. Without it, Scouts post intel with no accountability, Critics rubber-stamp plans, and there's no mechanism to reward agents who help others succeed.

**The currency is Reputation Points.** Every agent starts with 100. Reputation is earned through profitable trades, good intel, accurate reviews. It's spent to endorse intel and request reviews. It converts to real resource allocation through Genesis — higher reputation means more capital in allocation rounds.

**Three markets:**

1. **Intel Market** — Scouts post signals. Other agents endorse signals by staking reputation. Settlement is based on actual market outcomes (hybrid: trade-linked + time-based fallback). Everything is public — this is an endorsement/accountability system, not a paywall.

2. **Review Market** — Strategists request Critic reviews by posting a reputation budget. Critics accept, review, and get paid. Accuracy is tracked retroactively.

3. **Service Market** — Framework only for Phase 2C. Agents can list custom services. Full matching engine deferred to Phase 4.

**Anti-gaming detection** runs daily during Genesis cycle to catch wash trading, rubber-stamp critics, and intel spam.

---

## STEP 1 — Verify Phase 2B Foundation

Before building anything, confirm:
- .venv activates and all dependencies are importable
- PostgreSQL `syndicate` database is accessible with all tables including Phase 2B additions (library_entries, library_contributions)
- Redis/Memurai responds to PING
- The Agora works: channels exist, Genesis posts messages
- The Library works: textbook placeholders exist in data/library/textbooks/
- Tests pass: `python -m pytest tests/ -v`

If anything is broken, fix it before proceeding.

---

## STEP 2 — Add Phase 2C Dependencies

Check requirements.txt — these should already be present but verify:
- `numpy` (for settlement price calculations)
- `ccxt` (ExchangeService for live price feeds during settlement)

No new pip dependencies should be needed. If anything is missing, add and install.

---

## STEP 3 — Create Directory Structure

```
src/
└── economy/
    ├── __init__.py
    ├── economy_service.py      # Core service: reputation, transfers, escrow
    ├── intel_market.py          # Intel signal creation, endorsement, settlement
    ├── review_market.py         # Review requests, assignments, accuracy tracking
    ├── service_market.py        # Service listings (framework only)
    ├── settlement_engine.py     # Price-based settlement for intel signals
    ├── gaming_detection.py      # Anti-gaming analysis
    └── schemas.py               # Pydantic models
```

---

## STEP 4 — Database Schema Updates (Alembic Migration)

Create a new Alembic migration for Economy tables.

**New table: `intel_signals`**
- `id` SERIAL PRIMARY KEY
- `message_id` INT NOT NULL (FK to messages.id) — the Agora signal message
- `scout_agent_id` INT NOT NULL (FK to agents.id)
- `scout_agent_name` VARCHAR(100) NOT NULL
- `asset` VARCHAR(30) NOT NULL — e.g., "BTC/USDT"
- `direction` VARCHAR(10) NOT NULL — 'bullish', 'bearish', 'neutral'
- `confidence_level` INT DEFAULT 3 CHECK (confidence_level BETWEEN 1 AND 5)
- `price_at_creation` FLOAT NOT NULL — asset price when signal was posted (for settlement comparison)
- `expires_at` TIMESTAMP NOT NULL — when this signal should be settled
- `status` VARCHAR(20) DEFAULT 'active' — active, settled_profitable, settled_unprofitable, expired_no_endorsements
- `total_endorsement_stake` FLOAT DEFAULT 0.0
- `endorsement_count` INT DEFAULT 0
- `settlement_price` FLOAT NULLABLE — asset price at settlement time
- `settlement_price_change_pct` FLOAT NULLABLE — percentage change from creation to settlement
- `created_at` TIMESTAMP DEFAULT NOW()
- `settled_at` TIMESTAMP NULLABLE

**New table: `intel_endorsements`**
- `id` SERIAL PRIMARY KEY
- `signal_id` INT NOT NULL (FK to intel_signals.id)
- `endorser_agent_id` INT NOT NULL (FK to agents.id)
- `endorser_agent_name` VARCHAR(100) NOT NULL
- `stake_amount` FLOAT NOT NULL CHECK (stake_amount BETWEEN 5 AND 25) — reputation staked
- `linked_trade_id` INT NULLABLE (FK to transactions.id) — if endorser traded on this intel
- `settlement_status` VARCHAR(20) DEFAULT 'pending' — pending, settled_win, settled_loss, expired_refund
- `settlement_pnl` FLOAT NULLABLE — P&L of the linked trade (if trade-linked settlement)
- `scout_reputation_change` FLOAT NULLABLE — reputation effect on the Scout
- `endorser_reputation_change` FLOAT NULLABLE — reputation effect on the endorser
- `created_at` TIMESTAMP DEFAULT NOW()
- `settled_at` TIMESTAMP NULLABLE
- UNIQUE constraint on (signal_id, endorser_agent_id) — one endorsement per agent per signal

**New table: `review_requests`**
- `id` SERIAL PRIMARY KEY
- `requester_agent_id` INT NOT NULL (FK to agents.id)
- `requester_agent_name` VARCHAR(100) NOT NULL
- `proposal_message_id` INT NOT NULL (FK to messages.id) — the strategy proposal in the Agora
- `proposal_summary` TEXT — brief description of what needs reviewing
- `budget_reputation` FLOAT NOT NULL CHECK (budget_reputation BETWEEN 10 AND 25)
- `requires_two_reviews` BOOLEAN DEFAULT FALSE — true if strategy requests >20% capital
- `status` VARCHAR(20) DEFAULT 'open' — open, assigned, completed, expired
- `created_at` TIMESTAMP DEFAULT NOW()
- `expires_at` TIMESTAMP NOT NULL — auto-expire after 24 hours if unaccepted
- `completed_at` TIMESTAMP NULLABLE

**New table: `review_assignments`**
- `id` SERIAL PRIMARY KEY
- `review_request_id` INT NOT NULL (FK to review_requests.id)
- `critic_agent_id` INT NOT NULL (FK to agents.id)
- `critic_agent_name` VARCHAR(100) NOT NULL
- `verdict` VARCHAR(20) NULLABLE — approve, reject, conditional_approve
- `reasoning` TEXT NULLABLE
- `risk_score` INT NULLABLE CHECK (risk_score BETWEEN 1 AND 10)
- `review_message_id` INT NULLABLE (FK to messages.id) — the review posted to strategy-debate
- `reputation_earned` FLOAT NULLABLE — amount paid to Critic
- `accepted_at` TIMESTAMP DEFAULT NOW()
- `completed_at` TIMESTAMP NULLABLE
- `deadline_at` TIMESTAMP NOT NULL — must complete within 12 hours of accepting
- UNIQUE constraint on (review_request_id, critic_agent_id) — one assignment per Critic per request

**New table: `critic_accuracy`**
- `critic_agent_id` INT PRIMARY KEY (FK to agents.id)
- `total_reviews` INT DEFAULT 0
- `accurate_reviews` INT DEFAULT 0
- `accuracy_score` FLOAT DEFAULT 0.0 — accurate_reviews / total_reviews
- `approve_count` INT DEFAULT 0
- `reject_count` INT DEFAULT 0
- `conditional_count` INT DEFAULT 0
- `avg_risk_score` FLOAT DEFAULT 0.0
- `last_updated` TIMESTAMP DEFAULT NOW()

**New table: `service_listings`** (framework only, activates Phase 4)
- `id` SERIAL PRIMARY KEY
- `provider_agent_id` INT NOT NULL (FK to agents.id)
- `provider_agent_name` VARCHAR(100) NOT NULL
- `title` VARCHAR(200) NOT NULL
- `description` TEXT
- `price_reputation` FLOAT NOT NULL CHECK (price_reputation > 0)
- `status` VARCHAR(20) DEFAULT 'active' — active, paused, cancelled
- `created_at` TIMESTAMP DEFAULT NOW()
- `purchase_count` INT DEFAULT 0

**New table: `gaming_flags`**
- `id` SERIAL PRIMARY KEY
- `flag_type` VARCHAR(30) NOT NULL — 'wash_trading', 'rubber_stamp', 'intel_spam', 'collusion'
- `agent_ids` JSON NOT NULL — array of agent IDs involved
- `evidence` TEXT NOT NULL — description of what triggered the flag
- `severity` VARCHAR(10) NOT NULL — 'warning', 'penalty', 'critical'
- `penalty_applied` FLOAT NULLABLE — total reputation penalty if any
- `detected_at` TIMESTAMP DEFAULT NOW()
- `reviewed_by` VARCHAR(20) NULLABLE — 'genesis_auto' or 'owner'
- `resolved` BOOLEAN DEFAULT FALSE
- `resolved_at` TIMESTAMP NULLABLE

**Add indexes:**
- `intel_signals`: index on (status, expires_at) — for settlement queries
- `intel_signals`: index on (scout_agent_id)
- `intel_endorsements`: index on (signal_id)
- `intel_endorsements`: index on (endorser_agent_id, settlement_status)
- `review_requests`: index on (status, expires_at)
- `review_assignments`: index on (critic_agent_id, completed_at)
- `gaming_flags`: index on (resolved, detected_at)

Run the migration: `alembic upgrade head`

---

## STEP 5 — Economy Schemas (src/economy/schemas.py)

```python
from pydantic import BaseModel, Field
from datetime import datetime
from typing import Optional
from enum import Enum

class SignalDirection(str, Enum):
    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"

class SignalStatus(str, Enum):
    ACTIVE = "active"
    SETTLED_PROFITABLE = "settled_profitable"
    SETTLED_UNPROFITABLE = "settled_unprofitable"
    EXPIRED_NO_ENDORSEMENTS = "expired_no_endorsements"

class EndorsementStatus(str, Enum):
    PENDING = "pending"
    SETTLED_WIN = "settled_win"
    SETTLED_LOSS = "settled_loss"
    EXPIRED_REFUND = "expired_refund"

class ReviewRequestStatus(str, Enum):
    OPEN = "open"
    ASSIGNED = "assigned"
    COMPLETED = "completed"
    EXPIRED = "expired"

class ReviewVerdict(str, Enum):
    APPROVE = "approve"
    REJECT = "reject"
    CONDITIONAL_APPROVE = "conditional_approve"

class GamingFlagType(str, Enum):
    WASH_TRADING = "wash_trading"
    RUBBER_STAMP = "rubber_stamp"
    INTEL_SPAM = "intel_spam"
    COLLUSION = "collusion"

class GamingFlagSeverity(str, Enum):
    WARNING = "warning"
    PENALTY = "penalty"
    CRITICAL = "critical"

class IntelSignal(BaseModel):
    id: int
    message_id: int
    scout_agent_id: int
    scout_agent_name: str
    asset: str
    direction: str
    confidence_level: int
    price_at_creation: float
    expires_at: datetime
    status: str
    total_endorsement_stake: float
    endorsement_count: int
    settlement_price: Optional[float] = None
    settlement_price_change_pct: Optional[float] = None
    created_at: datetime
    settled_at: Optional[datetime] = None

class IntelEndorsement(BaseModel):
    id: int
    signal_id: int
    endorser_agent_id: int
    endorser_agent_name: str
    stake_amount: float
    linked_trade_id: Optional[int] = None
    settlement_status: str
    settlement_pnl: Optional[float] = None
    scout_reputation_change: Optional[float] = None
    endorser_reputation_change: Optional[float] = None
    created_at: datetime
    settled_at: Optional[datetime] = None

class ReviewRequest(BaseModel):
    id: int
    requester_agent_id: int
    requester_agent_name: str
    proposal_message_id: int
    proposal_summary: str
    budget_reputation: float
    requires_two_reviews: bool
    status: str
    created_at: datetime
    expires_at: datetime
    completed_at: Optional[datetime] = None

class ReviewAssignment(BaseModel):
    id: int
    review_request_id: int
    critic_agent_id: int
    critic_agent_name: str
    verdict: Optional[str] = None
    reasoning: Optional[str] = None
    risk_score: Optional[int] = None
    reputation_earned: Optional[float] = None
    accepted_at: datetime
    completed_at: Optional[datetime] = None
    deadline_at: datetime

class CriticAccuracy(BaseModel):
    critic_agent_id: int
    total_reviews: int
    accurate_reviews: int
    accuracy_score: float
    approve_count: int
    reject_count: int
    conditional_count: int
    avg_risk_score: float

class ServiceListing(BaseModel):
    id: int
    provider_agent_id: int
    provider_agent_name: str
    title: str
    description: str
    price_reputation: float
    status: str
    created_at: datetime
    purchase_count: int

class GamingFlag(BaseModel):
    id: int
    flag_type: str
    agent_ids: list[int]
    evidence: str
    severity: str
    penalty_applied: Optional[float] = None
    detected_at: datetime
    reviewed_by: Optional[str] = None
    resolved: bool
    resolved_at: Optional[datetime] = None

class EconomyStats(BaseModel):
    """Aggregate economy statistics for the daily report."""
    total_reputation_in_circulation: float
    total_reputation_in_escrow: float
    active_intel_signals: int
    total_endorsements_24h: int
    total_endorsement_stake_24h: float
    signals_settled_24h: int
    profitable_signals_24h: int
    unprofitable_signals_24h: int
    open_review_requests: int
    reviews_completed_24h: int
    gaming_flags_unresolved: int
    top_reputation_agents: list[dict]  # [{agent_id, agent_name, reputation_score}]
```

---

## STEP 6 — Core Economy Service (src/economy/economy_service.py)

This is the central orchestrator. It handles reputation management and delegates to market-specific modules.

```
Class: EconomyService

    __init__(self, db_session_factory, agora_service, exchange_service=None):
        - Store dependencies
        - Initialize structlog logger
        - Create IntelMarket, ReviewMarket, ServiceMarket, SettlementEngine, GamingDetector instances
        - STARTING_REPUTATION = 100.0
        - NEGATIVE_REPUTATION_THRESHOLD = -50.0
        - MIN_REPUTATION_FOR_INTEL = 50.0  # Minimum rep to create intel signals
        - MIN_REPUTATION_FOR_ENDORSEMENT = 25.0  # Minimum rep to endorse
        - MIN_ENDORSEMENT_STAKE = 5.0
        - MAX_ENDORSEMENT_STAKE = 25.0
        - MIN_REVIEW_BUDGET = 10.0
        - MAX_REVIEW_BUDGET = 25.0

    # ──────────────────────────────────────────────
    # REPUTATION MANAGEMENT
    # ──────────────────────────────────────────────

    async def initialize_agent_reputation(self, agent_id: int):
        """Called when a new agent is spawned. Sets starting reputation to 100."""
        
        1. Update agents table: set reputation_score = STARTING_REPUTATION
        2. Log to reputation_transactions: amount=100, reason="initial_balance"
        3. Log: agent_id, starting_reputation

    async def get_balance(self, agent_id: int) -> float:
        """Get an agent's current reputation balance."""
        
        - Query agents table for reputation_score
        - Return the value

    async def transfer_reputation(
        self,
        from_agent_id: int,
        to_agent_id: int,
        amount: float,
        reason: str,
        related_trade_id: int = None,
    ) -> bool:
        """Transfer reputation between agents. Returns True if successful."""
        
        1. Check from_agent has sufficient balance (reputation_score >= amount)
           - If not: log warning, return False
        2. Deduct from sender: UPDATE agents SET reputation_score = reputation_score - amount
        3. Credit to receiver: UPDATE agents SET reputation_score = reputation_score + amount
        4. Log to reputation_transactions:
           - from_agent_id, to_agent_id, amount, reason, related_trade_id
        5. Post to Agora: channel="agent-chat", message_type=ECONOMY,
           content="{from_name} → {to_name}: {amount} rep ({reason})"
        6. Check if sender is now below NEGATIVE_REPUTATION_THRESHOLD
           - If yes: flag for immediate evaluation (post to genesis-log with importance=2)
        7. Return True

    async def apply_reward(self, agent_id: int, amount: float, reason: str):
        """Give reputation to an agent (from the system, not from another agent)."""
        
        1. UPDATE agents SET reputation_score = reputation_score + amount
        2. Log to reputation_transactions: from_agent_id=0 (system), to_agent_id=agent_id
        3. Log: agent_id, amount, reason

    async def apply_penalty(self, agent_id: int, amount: float, reason: str):
        """Deduct reputation from an agent (system penalty)."""
        
        1. UPDATE agents SET reputation_score = reputation_score - amount
        2. Log to reputation_transactions: from_agent_id=agent_id, to_agent_id=0 (system)
        3. Check NEGATIVE_REPUTATION_THRESHOLD
        4. Log: agent_id, amount, reason

    async def escrow_reputation(self, agent_id: int, amount: float, reason: str) -> bool:
        """Hold reputation in escrow (deduct from balance, track separately).
        
        Escrow is implemented by deducting from reputation_score and logging
        the transaction with reason prefixed by 'escrow:'. To release escrow,
        either transfer to the recipient (if earned) or refund to the escrower.
        """
        
        1. Check balance >= amount
        2. Deduct from reputation_score
        3. Log to reputation_transactions with reason="escrow:{reason}"
        4. Return True/False

    async def release_escrow(self, agent_id: int, amount: float, reason: str):
        """Refund escrowed reputation back to the agent."""
        
        1. Credit reputation_score
        2. Log to reputation_transactions with reason="escrow_release:{reason}"

    async def get_transaction_history(
        self, agent_id: int, limit: int = 50
    ) -> list[dict]:
        """Get reputation transaction history for an agent."""
        
        - Query reputation_transactions WHERE from_agent_id = agent_id OR to_agent_id = agent_id
        - Order by timestamp DESC, limit

    async def check_negative_reputation_agents(self) -> list[int]:
        """Get all agents below the negative reputation threshold.
        Called by Genesis to flag for immediate evaluation."""
        
        - Query agents WHERE reputation_score < NEGATIVE_REPUTATION_THRESHOLD AND status = 'active'
        - Return list of agent IDs

    # ──────────────────────────────────────────────
    # DELEGATED MARKET OPERATIONS
    # ──────────────────────────────────────────────
    
    # These delegate to the specific market modules below.
    # The EconomyService acts as the single entry point.

    # Intel Market (delegates to self.intel_market)
    async def create_intel_signal(self, ...) -> IntelSignal: ...
    async def endorse_intel(self, ...) -> IntelEndorsement: ...
    async def link_trade_to_endorsement(self, ...) -> bool: ...
    async def get_active_signals(self, ...) -> list[IntelSignal]: ...
    
    # Review Market (delegates to self.review_market)
    async def request_review(self, ...) -> ReviewRequest: ...
    async def accept_review(self, ...) -> ReviewAssignment: ...
    async def submit_review(self, ...) -> ReviewAssignment: ...
    async def get_open_review_requests(self, ...) -> list[ReviewRequest]: ...
    
    # Service Market (delegates to self.service_market)
    async def create_service_listing(self, ...) -> ServiceListing: ...
    async def get_service_listings(self, ...) -> list[ServiceListing]: ...
    async def cancel_service_listing(self, ...) -> bool: ...

    # Settlement (delegates to self.settlement_engine)
    async def run_settlement_cycle(self) -> dict: ...
    
    # Gaming Detection (delegates to self.gaming_detector)
    async def run_gaming_detection(self) -> list[GamingFlag]: ...
    async def get_unresolved_flags(self) -> list[GamingFlag]: ...

    # Stats
    async def get_economy_stats(self) -> EconomyStats: ...
```

---

## STEP 7 — Intel Market (src/economy/intel_market.py)

```
Class: IntelMarket

    __init__(self, db_session_factory, economy_service_ref, agora_service):
        - Store dependencies
        - Note: economy_service_ref is a reference back to the parent EconomyService
          for reputation operations. Use dependency injection or pass as parameter.
        - Initialize structlog logger

    async def create_signal(
        self,
        scout_agent_id: int,
        scout_agent_name: str,
        message_id: int,
        asset: str,
        direction: SignalDirection,
        confidence_level: int,
        price_at_creation: float,
        expires_at: datetime,
    ) -> IntelSignal:
        """Create a new intel signal linked to an Agora message."""
        
        1. Validate scout has minimum reputation (MIN_REPUTATION_FOR_INTEL = 50)
           - If not: raise error / return None with explanation
        
        2. Validate asset format (should contain '/' like "BTC/USDT")
        
        3. Validate expires_at is in the future and within 7 days max
        
        4. Insert into intel_signals table
        
        5. Post to Agora: channel="trade-signals", message_type=SIGNAL,
           content="📊 Intel Signal from {scout_name}: {asset} {direction} (confidence: {level}/5). Expires {expires_at}.",
           metadata={"signal_id": signal.id, "asset": asset, "direction": direction}
        
        6. Return the created IntelSignal

    async def endorse_signal(
        self,
        signal_id: int,
        endorser_agent_id: int,
        endorser_agent_name: str,
        stake_amount: float,
    ) -> IntelEndorsement:
        """Endorse an intel signal by staking reputation."""
        
        1. Load the signal — verify it exists and status = 'active'
        2. Verify signal hasn't expired
        3. Verify endorser != scout (can't endorse your own signal)
        4. Verify endorser hasn't already endorsed this signal (unique constraint)
        5. Validate stake_amount is between MIN_ENDORSEMENT_STAKE (5) and MAX_ENDORSEMENT_STAKE (25)
        6. Verify endorser has minimum reputation (MIN_REPUTATION_FOR_ENDORSEMENT = 25)
        7. Verify endorser has sufficient balance for the stake
        
        8. Escrow the stake: economy_service.escrow_reputation(endorser_id, stake_amount, f"intel_endorsement:{signal_id}")
        
        9. Update signal: increment endorsement_count, add to total_endorsement_stake
        
        10. Insert into intel_endorsements table
        
        11. Post to Agora: channel="trade-signals", message_type=ECONOMY,
            content="{endorser_name} endorsed {scout_name}'s {asset} signal (staked {stake_amount} rep)"
        
        12. Return the created IntelEndorsement

    async def link_trade_to_endorsement(
        self,
        endorser_agent_id: int,
        signal_id: int,
        trade_id: int,
    ) -> bool:
        """Link a completed trade to an endorsement for trade-based settlement."""
        
        1. Find the endorsement for this (signal_id, endorser_agent_id)
        2. Verify it exists and settlement_status = 'pending'
        3. Update linked_trade_id = trade_id
        4. Return True

    async def get_active_signals(
        self,
        asset: Optional[str] = None,
        scout_id: Optional[int] = None,
    ) -> list[IntelSignal]:
        """Get all active (unsettled, unexpired) signals."""
        
        - Query intel_signals WHERE status = 'active' AND expires_at > NOW()
        - Optional filters by asset and scout_id
        - Order by created_at DESC

    async def get_signals_ready_for_settlement(self) -> list[IntelSignal]:
        """Get signals that have expired and need settlement processing."""
        
        - Query intel_signals WHERE status = 'active' AND expires_at <= NOW()
        - Return list for the settlement engine

    async def get_endorsements_for_signal(self, signal_id: int) -> list[IntelEndorsement]:
        """Get all endorsements for a specific signal."""

    async def get_agent_signal_stats(self, agent_id: int) -> dict:
        """Get intel signal statistics for an agent (as scout)."""
        
        - Total signals created
        - Total endorsements received
        - Average endorsement stake
        - Settlement record: profitable vs unprofitable
        - Return as dict
```

---

## STEP 8 — Settlement Engine (src/economy/settlement_engine.py)

**This is the most complex piece. It needs live market data to determine if predictions were correct.**

```
Class: SettlementEngine

    __init__(self, db_session_factory, economy_service_ref, exchange_service, agora_service):
        - Store dependencies
        - Initialize structlog logger
        - DIRECTION_THRESHOLD_PCT = 0.5  # Price must move at least 0.5% for directional settlement
        - TRADE_LINKED_SCOUT_WIN_MULTIPLIER = 1.0  # Scout gets full stake as reward
        - TRADE_LINKED_SCOUT_LOSS_MULTIPLIER = 1.0  # Scout loses full stake worth of reputation
        - TRADE_LINKED_ENDORSER_WIN_BONUS = 2.0  # Endorser gets stake back + 2 rep bonus
        - TIME_BASED_SCOUT_WIN_MULTIPLIER = 0.5  # Scout gets half stake (less certain)
        - TIME_BASED_SCOUT_LOSS_MULTIPLIER = 0.5  # Scout loses half stake worth
        - TIME_BASED_ENDORSER_REFUND = True  # Endorser always gets stake back in time-based

    async def run_settlement_cycle(self) -> dict:
        """Process all signals ready for settlement. Called by Genesis periodically.
        
        Returns summary: {settled: int, profitable: int, unprofitable: int, expired: int, errors: int}
        """
        
        1. Get all signals ready for settlement (expired but still 'active')
        
        2. For each signal:
           a. If endorsement_count == 0:
              - Set status = 'expired_no_endorsements'
              - No reputation changes
              - Log and continue
           
           b. If endorsements exist:
              - Call settle_signal(signal)
        
        3. Return summary dict

    async def settle_signal(self, signal: IntelSignal):
        """Settle a single signal and all its endorsements."""
        
        1. FETCH CURRENT PRICE:
           - Call exchange_service.get_ticker(signal.asset)
           - If exchange_service is None or call fails:
             - Log error
             - Extend the signal expiry by 1 hour and retry next cycle
             - Return without settling
           - Store as settlement_price
        
        2. CALCULATE PRICE CHANGE:
           - price_change_pct = ((settlement_price - price_at_creation) / price_at_creation) * 100
           - Update signal: settlement_price, settlement_price_change_pct
        
        3. DETERMINE IF SIGNAL WAS CORRECT:
           - If direction == 'bullish': correct if price_change_pct > DIRECTION_THRESHOLD_PCT
           - If direction == 'bearish': correct if price_change_pct < -DIRECTION_THRESHOLD_PCT
           - If direction == 'neutral': correct if abs(price_change_pct) < DIRECTION_THRESHOLD_PCT
           - signal_was_correct: bool
        
        4. UPDATE SIGNAL STATUS:
           - If signal_was_correct: status = 'settled_profitable'
           - If not: status = 'settled_unprofitable'
           - Set settled_at = NOW()
        
        5. SETTLE EACH ENDORSEMENT:
           For each endorsement on this signal:
           
           a. CHECK FOR TRADE-LINKED SETTLEMENT:
              - If linked_trade_id is NOT NULL:
                - Query the trade from transactions table
                - trade_pnl = trade's pnl field
                
                If trade_pnl > 0 (endorser's trade was profitable):
                  - Scout reward: stake_amount * TRADE_LINKED_SCOUT_WIN_MULTIPLIER
                    → economy_service.apply_reward(scout_id, reward, "intel_signal_win")
                  - Endorser: gets stake back + TRADE_LINKED_ENDORSER_WIN_BONUS
                    → economy_service.release_escrow(endorser_id, stake_amount, "endorsement_win")
                    → economy_service.apply_reward(endorser_id, TRADE_LINKED_ENDORSER_WIN_BONUS, "endorsement_judgment_bonus")
                  - Set endorsement: settlement_status='settled_win', settlement_pnl=trade_pnl
                
                If trade_pnl <= 0 (endorser's trade lost money):
                  - Scout penalty: stake_amount * TRADE_LINKED_SCOUT_LOSS_MULTIPLIER
                    → economy_service.apply_penalty(scout_id, penalty, "intel_signal_loss")
                  - Endorser: loses staked reputation (already in escrow, don't refund)
                    → Log the loss, don't release escrow
                  - Set endorsement: settlement_status='settled_loss', settlement_pnl=trade_pnl
              
              - Set scout_reputation_change and endorser_reputation_change on the endorsement record
           
           b. TIME-BASED FALLBACK SETTLEMENT:
              - If linked_trade_id IS NULL (no trade was made):
                
                If signal_was_correct (market moved in predicted direction):
                  - Scout reward: stake_amount * TIME_BASED_SCOUT_WIN_MULTIPLIER (half credit)
                    → economy_service.apply_reward(scout_id, reward, "intel_signal_time_win")
                  - Endorser: gets full stake back (they were right to endorse but didn't trade)
                    → economy_service.release_escrow(endorser_id, stake_amount, "endorsement_time_refund")
                  - Set endorsement: settlement_status='settled_win'
                
                If signal was incorrect:
                  - Scout penalty: stake_amount * TIME_BASED_SCOUT_LOSS_MULTIPLIER (half penalty)
                    → economy_service.apply_penalty(scout_id, penalty, "intel_signal_time_loss")
                  - Endorser: gets full stake back (they didn't trade, no loss — but endorsing bad intel is a warning)
                    → economy_service.release_escrow(endorser_id, stake_amount, "endorsement_time_refund")
                  - Set endorsement: settlement_status='settled_loss'
                    (Note: endorser "loses" here in terms of settlement_status for tracking, 
                     but their stake is refunded since they didn't trade on it)
              
              - Set reputation changes on the endorsement record
        
        6. POST SETTLEMENT SUMMARY TO AGORA:
           - channel="trade-results", message_type=ECONOMY, importance=1
           - content: "{asset} {direction} signal by {scout_name}: {correct/incorrect}. 
             Price moved {price_change_pct}%. {endorsement_count} endorsements settled."
        
        7. Log settlement details with structlog
```

---

## STEP 9 — Review Market (src/economy/review_market.py)

```
Class: ReviewMarket

    __init__(self, db_session_factory, economy_service_ref, agora_service):
        - Store dependencies
        - Initialize structlog logger
        - REVIEW_REQUEST_EXPIRY_HOURS = 24
        - REVIEW_COMPLETION_DEADLINE_HOURS = 12
        - HIGH_CAPITAL_THRESHOLD_PCT = 0.20  # Strategies using >20% capital need 2 reviews

    async def request_review(
        self,
        requester_agent_id: int,
        requester_agent_name: str,
        proposal_message_id: int,
        proposal_summary: str,
        budget_reputation: float,
        capital_percentage: float = 0.0,  # What % of agent's capital this strategy uses
    ) -> ReviewRequest:
        """Request a Critic review for a strategy proposal."""
        
        1. Validate budget is between MIN_REVIEW_BUDGET (10) and MAX_REVIEW_BUDGET (25)
        
        2. Determine if two reviews required:
           - requires_two = capital_percentage > HIGH_CAPITAL_THRESHOLD_PCT
           - If requires_two: total budget needed = budget_reputation * 2
        
        3. Verify requester has sufficient reputation for the total budget
        
        4. Escrow the budget:
           - economy_service.escrow_reputation(requester_id, total_budget, "review_request:{proposal_message_id}")
        
        5. Insert into review_requests with:
           - expires_at = NOW() + REVIEW_REQUEST_EXPIRY_HOURS
           - requires_two_reviews = requires_two
        
        6. Post to Agora: channel="strategy-debate", message_type=PROPOSAL,
           content="Review requested by {requester_name}: {proposal_summary} (budget: {budget} rep)"
           metadata={"review_request_id": request.id, "requires_two_reviews": requires_two}
        
        7. Return ReviewRequest

    async def get_open_requests(
        self,
        critic_agent_id: Optional[int] = None,
    ) -> list[ReviewRequest]:
        """Get open review requests that need Critics."""
        
        - Query review_requests WHERE status = 'open' AND expires_at > NOW()
        - If critic_agent_id provided: exclude requests where the critic already has an assignment
        - Order by budget_reputation DESC (highest-paying first)

    async def accept_review(
        self,
        request_id: int,
        critic_agent_id: int,
        critic_agent_name: str,
    ) -> ReviewAssignment:
        """A Critic accepts a review request."""
        
        1. Load the request — verify status = 'open' or 'assigned' (if needs 2 reviews)
        2. Verify critic is not the requester
        3. Count existing assignments for this request
           - If 1 assignment exists and requires_two_reviews: allow second Critic
           - If 1 assignment exists and NOT requires_two: reject (already assigned)
           - If 2 assignments exist: reject (fully assigned)
        4. Verify this critic doesn't already have an assignment for this request
        
        5. Insert into review_assignments with:
           - deadline_at = NOW() + REVIEW_COMPLETION_DEADLINE_HOURS
        
        6. Update request status:
           - If all needed reviewers assigned: status = 'assigned'
           - If still need more: keep status = 'open'
        
        7. Post to Agora: channel="strategy-debate", message_type=SYSTEM,
           content="{critic_name} accepted review for: {proposal_summary}"
        
        8. Return ReviewAssignment

    async def submit_review(
        self,
        assignment_id: int,
        verdict: ReviewVerdict,
        reasoning: str,
        risk_score: int,
        review_message_id: int,
    ) -> ReviewAssignment:
        """Critic submits their review."""
        
        1. Load the assignment — verify it exists and completed_at is NULL
        2. Verify not past deadline (warn but allow — better late than never)
        
        3. Update assignment: verdict, reasoning, risk_score, review_message_id, completed_at = NOW()
        
        4. Calculate and pay the Critic:
           - Load the review_request to get budget_reputation
           - If requires_two_reviews: payment = budget / 2 (split between Critics)
           - If single reviewer: payment = full budget
           - economy_service.apply_reward(critic_id, payment, "review_completed:{request_id}")
           - Set reputation_earned on the assignment
        
        5. Update critic_accuracy table:
           - Increment total_reviews
           - Increment approve_count, reject_count, or conditional_count based on verdict
           - Recalculate avg_risk_score
           - (accuracy_score gets updated later via retroactive tracking — see update_critic_accuracy)
        
        6. Check if all assignments for this request are complete:
           - If yes: set request status = 'completed', completed_at = NOW()
        
        7. Post to Agora: channel="strategy-debate", message_type=EVALUATION,
           content="{critic_name} reviewed {proposal_summary}: {verdict} (risk: {risk_score}/10)"
        
        8. Return updated ReviewAssignment

    async def update_critic_accuracy(
        self,
        critic_agent_id: int,
        was_accurate: bool,
    ):
        """Update a Critic's accuracy score after a strategy outcome is known.
        
        Called by Genesis when a strategy that was reviewed produces results.
        """
        
        1. Load critic_accuracy record (create if doesn't exist)
        2. If was_accurate: increment accurate_reviews
        3. Recalculate accuracy_score = accurate_reviews / total_reviews
        4. Set last_updated = NOW()

    async def expire_stale_requests(self) -> int:
        """Expire review requests that weren't accepted in time.
        
        Called periodically by Genesis.
        """
        
        1. Query review_requests WHERE status = 'open' AND expires_at < NOW()
        2. For each expired request:
           - Release escrowed budget back to requester
           - Set status = 'expired'
           - Post to Agora: channel="strategy-debate", message_type=SYSTEM,
             content="Review request expired (no Critics accepted): {summary}"
        3. Return count of expired requests

    async def check_overdue_assignments(self) -> int:
        """Check for assignments past their deadline.
        
        Called periodically by Genesis.
        """
        
        1. Query review_assignments WHERE completed_at IS NULL AND deadline_at < NOW()
        2. For each overdue assignment:
           - Post warning to Agora
           - If overdue by > 24 hours: release the Critic from the assignment
             and re-open the request for a new Critic
        3. Return count of overdue assignments

    async def get_critic_stats(self, critic_agent_id: int) -> Optional[CriticAccuracy]:
        """Get a Critic's full accuracy statistics."""
```

---

## STEP 10 — Service Market Framework (src/economy/service_market.py)

**Framework only — full marketplace activates in Phase 4.**

```
Class: ServiceMarket

    __init__(self, db_session_factory, agora_service):
        - Store dependencies
        - Initialize structlog logger

    async def create_listing(
        self,
        provider_agent_id: int,
        provider_agent_name: str,
        title: str,
        description: str,
        price_reputation: float,
    ) -> ServiceListing:
        """Create a new service listing."""
        
        1. Validate price > 0
        2. Insert into service_listings
        3. Post to Agora: channel="agent-chat", message_type=ECONOMY,
           content="{provider_name} is offering: {title} ({price} rep)"
        4. Return ServiceListing

    async def get_listings(self, status: str = 'active') -> list[ServiceListing]:
        """Get all service listings."""
        
        - Query service_listings filtered by status
        - Order by created_at DESC

    async def cancel_listing(self, listing_id: int, provider_agent_id: int) -> bool:
        """Cancel a listing. Only the provider can cancel."""
        
        1. Verify provider owns the listing
        2. Set status = 'cancelled'
        3. Return True

    # NOTE: Purchase/fulfillment flow is NOT implemented in Phase 2C.
    # This is scaffolding for Phase 4 when the agent population is large enough
    # to sustain a real marketplace. The table and basic CRUD operations exist
    # so the data model is ready.
```

---

## STEP 11 — Gaming Detection (src/economy/gaming_detection.py)

```
Class: GamingDetector

    __init__(self, db_session_factory, agora_service):
        - Store dependencies
        - Initialize structlog logger
        - WASH_TRADING_THRESHOLD_PCT = 50  # Flag if >50% of endorsements are between same pair
        - RUBBER_STAMP_THRESHOLD_PCT = 90  # Flag if Critic approves >90% over 10+ reviews
        - RUBBER_STAMP_MIN_REVIEWS = 10
        - INTEL_SPAM_ENDORSEMENT_RATE_PCT = 10  # Flag if <10% endorsement rate over 20+ signals
        - INTEL_SPAM_MIN_SIGNALS = 20

    async def run_full_detection(self, lookback_days: int = 7) -> list[GamingFlag]:
        """Run all gaming detection checks. Called daily by Genesis."""
        
        flags = []
        flags.extend(await self.check_wash_trading(lookback_days))
        flags.extend(await self.check_rubber_stamp_critics())
        flags.extend(await self.check_intel_spam())
        
        # Post summary to Agora if any flags found
        if flags:
            await self._post_gaming_summary(flags)
        
        return flags

    async def check_wash_trading(self, lookback_days: int = 7) -> list[GamingFlag]:
        """Detect agents repeatedly endorsing each other's intel."""
        
        1. Query intel_endorsements joined with intel_signals
           - WHERE created_at > NOW() - lookback_days
        
        2. Build a matrix of (scout_id, endorser_id) → endorsement_count
        
        3. For each pair where endorsement_count > 2:
           - Calculate what % of the endorser's total endorsements go to this scout
           - Calculate what % of the scout's total endorsements come from this endorser
           - If EITHER exceeds WASH_TRADING_THRESHOLD_PCT:
             - Create a GamingFlag with:
               - flag_type = 'wash_trading'
               - agent_ids = [scout_id, endorser_id]
               - evidence = "{endorser} endorsed {scout}'s signals {count} times ({pct}% of their endorsements)"
               - severity = 'warning' if first offense, 'penalty' if repeat
        
        4. Insert flags into gaming_flags table
        5. Return flags

    async def check_rubber_stamp_critics(self) -> list[GamingFlag]:
        """Detect Critics that approve everything."""
        
        1. Query critic_accuracy WHERE total_reviews >= RUBBER_STAMP_MIN_REVIEWS
        
        2. For each Critic:
           - approval_rate = approve_count / total_reviews
           - If approval_rate > RUBBER_STAMP_THRESHOLD_PCT / 100:
             - Create GamingFlag:
               - flag_type = 'rubber_stamp'
               - agent_ids = [critic_id]
               - evidence = "{critic_name} approved {approve_count}/{total_reviews} reviews ({pct}%)"
               - severity = 'warning'
        
        3. Insert and return flags

    async def check_intel_spam(self) -> list[GamingFlag]:
        """Detect Scouts posting many low-quality signals."""
        
        1. Query intel_signals grouped by scout_agent_id
           - WHERE created_at > NOW() - 30 days
           - HAVING count(*) >= INTEL_SPAM_MIN_SIGNALS
        
        2. For each Scout with enough signals:
           - Calculate endorsement_rate = signals_with_endorsements / total_signals
           - If endorsement_rate < INTEL_SPAM_ENDORSEMENT_RATE_PCT / 100:
             - Create GamingFlag:
               - flag_type = 'intel_spam'
               - agent_ids = [scout_id]
               - evidence = "{scout_name} posted {total} signals, only {endorsed} received endorsements ({rate}%)"
               - severity = 'warning'
        
        3. Insert and return flags

    async def _post_gaming_summary(self, flags: list[GamingFlag]):
        """Post a summary of detected gaming to system-alerts."""
        
        - channel="system-alerts", message_type=ALERT, importance=2
        - content: "Gaming detection: {len(flags)} flag(s) raised. Types: {types}. See gaming_flags table."

    async def get_unresolved_flags(self) -> list[GamingFlag]:
        """Get all unresolved gaming flags for Genesis/owner review."""
        
        - Query gaming_flags WHERE resolved = False
        - Order by detected_at DESC

    async def resolve_flag(self, flag_id: int, reviewed_by: str, penalty: float = None):
        """Resolve a gaming flag, optionally applying a penalty."""
        
        1. Update gaming_flags: resolved=True, resolved_at=NOW(), reviewed_by, penalty_applied
        2. If penalty > 0:
           - For each agent in agent_ids:
             - economy_service.apply_penalty(agent_id, penalty, f"gaming_violation:{flag_type}")
```

---

## STEP 12 — Economy Init Module (src/economy/__init__.py)

```python
from src.economy.economy_service import EconomyService
from src.economy.intel_market import IntelMarket
from src.economy.review_market import ReviewMarket
from src.economy.service_market import ServiceMarket
from src.economy.settlement_engine import SettlementEngine
from src.economy.gaming_detection import GamingDetector
from src.economy.schemas import (
    IntelSignal, IntelEndorsement, SignalDirection, SignalStatus, EndorsementStatus,
    ReviewRequest, ReviewAssignment, ReviewVerdict, ReviewRequestStatus,
    CriticAccuracy, ServiceListing, GamingFlag, GamingFlagType, GamingFlagSeverity,
    EconomyStats,
)

__all__ = [
    "EconomyService",
    "IntelMarket",
    "ReviewMarket",
    "ServiceMarket",
    "SettlementEngine",
    "GamingDetector",
    "IntelSignal", "IntelEndorsement", "SignalDirection", "SignalStatus", "EndorsementStatus",
    "ReviewRequest", "ReviewAssignment", "ReviewVerdict", "ReviewRequestStatus",
    "CriticAccuracy", "ServiceListing", "GamingFlag", "GamingFlagType", "GamingFlagSeverity",
    "EconomyStats",
]
```

---

## STEP 13 — Update Genesis to Use Economy (src/genesis/genesis.py)

Add Economy integration to the Genesis cycle. **Check what currently exists first, then add:**

1. **Initialization:** Create EconomyService during Genesis.__init__(), pass exchange_service and agora_service

2. **Agent spawning:** When creating a new agent:
   ```python
   # Initialize reputation for new agent
   await self.economy.initialize_agent_reputation(agent_id=new_agent_id)
   ```

3. **Evaluation cycle additions:**
   ```python
   # Check for agents with negative reputation (flag for immediate evaluation)
   neg_rep_agents = await self.economy.check_negative_reputation_agents()
   for agent_id in neg_rep_agents:
       # Add to evaluation queue even if survival clock hasn't expired
       self.log.warning("negative_reputation_evaluation", agent_id=agent_id)
   ```

4. **Settlement cycle (run every Genesis cycle):**
   ```python
   # Run intel signal settlement
   settlement_results = await self.economy.run_settlement_cycle()
   if settlement_results.get("settled", 0) > 0:
       self.log.info("settlement_cycle", **settlement_results)
   ```

5. **Review maintenance (run hourly):**
   ```python
   if should_run_hourly_maintenance():
       expired = await self.economy.review_market.expire_stale_requests()
       overdue = await self.economy.review_market.check_overdue_assignments()
   ```

6. **Gaming detection (run daily, during daily report generation):**
   ```python
   # Run gaming detection once per day
   gaming_flags = await self.economy.run_gaming_detection()
   if gaming_flags:
       self.log.warning("gaming_flags_detected", count=len(gaming_flags))
   ```

7. **Daily report additions:**
   ```python
   economy_stats = await self.economy.get_economy_stats()
   # Include in daily report: reputation circulation, settlement results, 
   # review activity, gaming flags
   ```

---

## STEP 14 — Update BaseAgent for Economy Access (src/common/base_agent.py)

Add economy convenience methods to BaseAgent so all agents can interact with the Economy:

```python
# In __init__, accept economy_service parameter (optional, like agora_service)

async def create_intel_signal(
    self, asset: str, direction: str, confidence: int, expires_hours: int = 48
) -> Optional[IntelSignal]:
    """Post an intel signal to the market."""
    if self.economy is None:
        return None
    
    # First post to Agora as a regular signal
    msg = await self.post_to_agora(
        channel="market-intel",
        content=f"Signal: {asset} {direction} (confidence {confidence}/5)",
        message_type=MessageType.SIGNAL,
        metadata={"asset": asset, "direction": direction, "confidence": confidence},
    )
    if msg is None:
        return None
    
    # Get current price from exchange
    price = await self._get_current_price(asset)
    
    # Create the economic signal
    from datetime import timedelta
    expires_at = datetime.utcnow() + timedelta(hours=expires_hours)
    return await self.economy.create_intel_signal(
        scout_agent_id=self.agent_id,
        scout_agent_name=self.name,
        message_id=msg.id,
        asset=asset,
        direction=direction,
        confidence_level=confidence,
        price_at_creation=price,
        expires_at=expires_at,
    )

async def endorse_intel(self, signal_id: int, stake: float) -> Optional[IntelEndorsement]:
    """Endorse someone else's intel signal."""
    if self.economy is None:
        return None
    return await self.economy.endorse_intel(
        signal_id=signal_id,
        endorser_agent_id=self.agent_id,
        endorser_agent_name=self.name,
        stake_amount=stake,
    )

async def request_strategy_review(
    self, proposal_message_id: int, summary: str, budget: float, capital_pct: float = 0.0
) -> Optional[ReviewRequest]:
    """Request a Critic to review a strategy proposal."""
    if self.economy is None:
        return None
    return await self.economy.request_review(
        requester_agent_id=self.agent_id,
        requester_agent_name=self.name,
        proposal_message_id=proposal_message_id,
        proposal_summary=summary,
        budget_reputation=budget,
        capital_percentage=capital_pct,
    )

async def accept_and_submit_review(
    self, request_id: int, verdict: str, reasoning: str, risk_score: int
) -> Optional[ReviewAssignment]:
    """Accept and complete a review request (for Critic agents)."""
    if self.economy is None:
        return None
    # Accept
    assignment = await self.economy.accept_review(
        request_id=request_id,
        critic_agent_id=self.agent_id,
        critic_agent_name=self.name,
    )
    if assignment is None:
        return None
    # Post review to Agora
    review_msg = await self.post_to_agora(
        channel="strategy-debate",
        content=f"Review of proposal: {verdict}. Risk: {risk_score}/10. {reasoning}",
        message_type=MessageType.EVALUATION,
    )
    # Submit
    return await self.economy.submit_review(
        assignment_id=assignment.id,
        verdict=verdict,
        reasoning=reasoning,
        risk_score=risk_score,
        review_message_id=review_msg.id if review_msg else None,
    )

async def get_my_reputation(self) -> float:
    """Get current reputation balance."""
    if self.economy is None:
        return 0.0
    return await self.economy.get_balance(self.agent_id)
```

---

## STEP 15 — Tests

**tests/test_economy_service.py:**

```
Reputation management:
- test_initialize_reputation — new agent gets 100 rep
- test_transfer_reputation — transfer between agents, verify balances
- test_transfer_insufficient_balance — try to transfer more than balance, verify failure
- test_apply_reward — verify reward increases balance
- test_apply_penalty — verify penalty decreases balance
- test_negative_reputation_detection — push agent below -50, verify flagged
- test_escrow_and_release — escrow funds, verify deducted, release, verify restored
- test_escrow_insufficient — try to escrow more than balance, verify failure
- test_transaction_history — make several transactions, verify history
```

**tests/test_intel_market.py:**

```
Signal creation:
- test_create_signal — create a signal, verify in database
- test_create_signal_low_reputation — agent with rep < 50 cannot create signals
- test_create_signal_invalid_asset — bad asset format rejected
- test_create_signal_past_expiry — expiry in past rejected

Endorsement:
- test_endorse_signal — endorse, verify stake escrowed
- test_endorse_own_signal — cannot endorse own signal
- test_endorse_duplicate — cannot endorse same signal twice
- test_endorse_expired_signal — cannot endorse after expiry
- test_endorse_below_min_stake — stake < 5 rejected
- test_endorse_above_max_stake — stake > 25 rejected
- test_endorse_insufficient_reputation — not enough rep to stake
- test_link_trade_to_endorsement — link a trade, verify stored

Queries:
- test_get_active_signals — create mixed signals, filter active only
- test_get_active_signals_by_asset — filter by asset
- test_get_signals_ready_for_settlement — create expired signals, verify found
- test_agent_signal_stats — create signals and endorsements, verify stats
```

**tests/test_settlement_engine.py:**

```
Settlement:
- test_settle_signal_no_endorsements — signal expires with no endorsements, verify expired status
- test_settle_signal_bullish_correct — bullish signal, price went up, verify profitable settlement
- test_settle_signal_bullish_incorrect — bullish signal, price went down, verify unprofitable
- test_settle_signal_bearish_correct — bearish signal, price went down, verify profitable
- test_settle_signal_neutral_correct — neutral signal, price flat, verify profitable
- test_settle_signal_direction_threshold — price moved but less than 0.5%, verify neutral behavior

Trade-linked settlement:
- test_trade_linked_profitable — endorser made profitable trade, verify scout rewarded + endorser gets stake back + bonus
- test_trade_linked_unprofitable — endorser's trade lost money, verify scout penalized + endorser loses stake

Time-based settlement:
- test_time_based_correct — no trade linked, signal was correct, verify scout gets half reward + endorser refunded
- test_time_based_incorrect — no trade linked, signal wrong, verify scout penalized + endorser refunded

Mixed endorsements:
- test_mixed_settlement — same signal: one endorser traded (profitable), one didn't. Verify both settled correctly.

Error handling:
- test_settlement_no_exchange — exchange_service is None, verify signal not settled (retried later)
- test_settlement_exchange_error — exchange call fails, verify graceful handling

Full cycle:
- test_run_settlement_cycle — create multiple signals with endorsements, run cycle, verify all processed
```

**tests/test_review_market.py:**

```
Review requests:
- test_request_review — create request, verify escrowed budget
- test_request_review_two_required — high capital strategy, verify requires_two_reviews=True
- test_request_review_insufficient_reputation — not enough rep for budget

Review flow:
- test_accept_review — Critic accepts, verify assignment created
- test_accept_own_request — cannot review own request
- test_accept_already_full — single-review request already assigned, verify rejection
- test_accept_second_reviewer — two-review request, second Critic accepts successfully
- test_submit_review — submit verdict, verify Critic paid from escrow
- test_submit_review_two_critics — both submit, verify request completed and budget split
- test_expire_stale_requests — create old request, run expiry, verify refunded
- test_overdue_assignment — create assignment past deadline, verify flagged

Critic accuracy:
- test_update_accuracy — update accuracy, verify score calculation
- test_get_critic_stats — create reviews, verify stats
```

**tests/test_gaming_detection.py:**

```
- test_wash_trading_detection — create repeated endorsements between same pair, verify flag
- test_wash_trading_below_threshold — endorsements below 50%, verify no flag
- test_rubber_stamp_detection — Critic approves 10/10 reviews, verify flag
- test_rubber_stamp_below_threshold — Critic approves 8/10, verify no flag
- test_rubber_stamp_insufficient_reviews — Critic has only 5 reviews, verify no flag (min 10)
- test_intel_spam_detection — Scout posts 25 signals with < 10% endorsement rate, verify flag
- test_intel_spam_below_threshold — Scout posts 25 signals with 15% endorsement rate, verify no flag
- test_resolve_flag — create flag, resolve it, verify resolved status
- test_resolve_flag_with_penalty — resolve with penalty, verify reputation deducted
- test_full_detection_cycle — run all checks, verify flags consolidated
```

**tests/test_economy_integration.py:**

```
- test_genesis_initializes_reputation_on_spawn — mock agent spawn, verify rep = 100
- test_genesis_settlement_cycle — mock signals and trades, run Genesis cycle, verify settlements
- test_genesis_gaming_detection_daily — run Genesis daily cycle, verify gaming detection ran
- test_negative_rep_triggers_evaluation — push agent below -50, verify Genesis flags for evaluation
- test_base_agent_create_signal — use BaseAgent helper, verify signal created
- test_base_agent_endorse — use BaseAgent helper, verify endorsement created
- test_base_agent_request_review — use BaseAgent helper, verify review request created
- test_full_intel_lifecycle — Scout creates signal → Trader endorses → Trader trades → settlement → reputation changes
- test_full_review_lifecycle — Strategist requests → Critic accepts → Critic reviews → payment → accuracy update
```

Run all tests: `python -m pytest tests/ -v`

---

## STEP 16 — Update Process Runners

Update `scripts/run_genesis.py` to initialize EconomyService and pass to Genesis:

```python
from src.economy import EconomyService

economy = EconomyService(
    db_session_factory=db_session_factory,
    agora_service=agora,
    exchange_service=exchange_service,  # May be None if no API keys — settlement will gracefully defer
)

genesis = GenesisAgent(
    db_session_factory=db_session_factory,
    agora_service=agora,
    library_service=library,
    mentor_service=mentor,
    economy_service=economy,
    # ... other params
)
```

Make sure `run_all.py` still works. Run the full system for 60 seconds and verify:
- No errors in console
- Economy tables exist (verify via psql)
- Genesis logs show economy-related operations

---

## STEP 17 — Update CLAUDE.md

Add to the Architecture Quick Reference section:
```
### Internal Economy (Phase 2C)
- Reputation-based marketplace — agents earn, spend, and stake reputation
- Starting balance: 100 rep per agent
- Intel Market: endorsement model (no paywall), hybrid settlement (trade-linked + time-based)
- Review Market: Strategists pay Critics for reviews, accuracy tracked retroactively
- Service Market: framework only (activates Phase 4)
- Settlement Engine: uses live price feeds via ExchangeService, runs every Genesis cycle
- Gaming Detection: wash trading, rubber-stamp critics, intel spam — runs daily
- Negative reputation (-50) triggers immediate evaluation
- All economy events posted to Agora with message_type=ECONOMY
```

Update the Phase Roadmap to show Phase 2C as COMPLETE.

---

## STEP 18 — Update CHANGELOG.md and CURRENT_STATUS.md

Log everything built in this session. CURRENT_STATUS.md should note:
- Phase 2C complete
- Service Market is framework only — full marketplace in Phase 4
- Settlement engine requires exchange_service for live settlements (gracefully defers if None)
- Next up: Phase 2D (Web Frontend / Dashboard)

---

## STEP 19 — Git Commit and Push

```
git add .
git commit -m "Phase 2C: Internal Economy — intel market, review market, settlement engine, gaming detection"
git push origin main
```

---

## DESIGN DECISIONS (Reference for Claude Code)

These decisions were made in the War Room (Claude.ai chat) and are final:

1. **Intel model: endorsement, not paywall.** All intel is public in the Agora. Scouts earn reputation via accountability (settlement), not by selling exclusive access. Agents can propose a paywall model via SIP later.
2. **Settlement: hybrid Option C.** Trade-linked if endorser traded, time-based fallback if they didn't. 48-hour window.
3. **Settlement multipliers:** Trade-linked: scout gets/loses full stake, endorser gets stake+2 bonus or loses stake. Time-based: scout gets/loses half stake, endorser always refunded.
4. **Direction threshold:** Price must move ≥0.5% to count as directional. Less than that = "neutral" was correct.
5. **Intel minimum reputation:** 50 to create signals, 25 to endorse. Prevents brand-new agents from gambling immediately.
6. **Endorsement stakes:** 5-25 reputation per endorsement.
7. **Review budgets:** 10-25 reputation. Two reviews required if strategy uses >20% of capital.
8. **Review deadlines:** Requests expire after 24 hours. Assignments have 12-hour completion deadline.
9. **Gaming detection:** Runs daily during Genesis cycle. Thresholds: 50% wash trading, 90% rubber stamp (min 10 reviews), 10% endorsement rate (min 20 signals).
10. **Reputation effects:** Starting=100, negative threshold=-50 triggers immediate evaluation.
11. **Service market:** Tables and CRUD only. Full marketplace deferred to Phase 4.
12. **Warden does NOT interact with the Economy.** Financial safety is separate from reputation economics.
13. **Escrow model:** Reputation deducted on escrow, logged as "escrow:{reason}", refunded via release_escrow(). Simple accounting, no separate escrow table needed.

---

Before you start, confirm you've read CLAUDE.md and the current project state. Then proceed through each step in order. Ask me if anything is unclear.
