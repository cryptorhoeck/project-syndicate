## PROJECT SYNDICATE — PHASE 2A CLAUDE CODE KICKOFF PROMPT
## Copy everything below the line and paste it into Claude Code
## ================================================================

I'm continuing Project Syndicate. Read the CLAUDE.md file first, then CURRENT_STATUS.md and CHANGELOG.md to confirm Phase 1 is complete.

This is Phase 2A — The Agora (Central Nervous System). Phase 2 is split into 4 sub-phases:
- **2A: The Agora** ← YOU ARE HERE
- 2B: The Library (Knowledge Layer)
- 2C: The Internal Economy (Reputation Marketplace)
- 2D: The Web Frontend (Dashboard)

Work through each step in order. Use CMD commands only, never PowerShell. Always activate the .venv before any Python work.

**IMPORTANT:** Before modifying ANY existing file, create a timestamped backup in backups/. Before starting, run `python scripts/backup.py` to snapshot the current state.

---

## CONTEXT — What Is The Agora?

The Agora is the Syndicate's central nervous system. Every agent thought, every trade decision, every debate, every evaluation — it all flows through The Agora. Think of it as a combination of:
- A real-time chat system (Redis pub/sub for instant delivery)
- A permanent record (PostgreSQL for history and querying)
- A public square (every agent can read every channel — full transparency)

**Why this matters:** Without The Agora, agents are isolated processes that can't learn from each other. With it, a Scout's discovery can inspire a Trader's action, a Critic's warning can prevent a loss, and dead agents' last words become lessons for the next generation.

The `messages` table already exists from Phase 0. This phase builds the service layer on top of it.

---

## STEP 1 — Verify Phase 1 Foundation

Before building anything, confirm:
- .venv activates and all dependencies are importable
- PostgreSQL `syndicate` database is accessible with all tables from Phase 0 + Phase 1
- Redis/Memurai responds to PING
- Genesis registers itself as agent id=0 (the bug fix from post-Phase-1)
- Tests pass: `python -m pytest tests/ -v`
- Genesis, Warden, and Heartbeat all start cleanly via `run_all.py` (run for ~30 seconds, confirm no crashes)

If anything is broken, fix it before proceeding.

---

## STEP 2 — Add Phase 2A Dependencies

Add these to requirements.txt (if not already present) and install:
- `jinja2` (templating — needed later for Phase 2D but install now since FastAPI uses it)
- `python-multipart` (FastAPI form handling)
- `htmx` is client-side JS, no pip install needed — just noting for later

Verify these are already installed from earlier phases:
- `redis`
- `fastapi`
- `uvicorn`
- `structlog`
- `pydantic`

Run: `pip install -r requirements.txt`

---

## STEP 3 — Database Schema Updates (Alembic Migration)

Create a new Alembic migration for Agora-specific additions.

**First, check what already exists.** The `messages` table from Phase 0 should have: id, agent_id, channel, content, metadata_json, timestamp. If it does, we're adding to it — not rebuilding.

**Updates to `messages` table (add columns if missing):**
- `message_type` VARCHAR(20) DEFAULT 'chat' — one of: 'thought', 'proposal', 'signal', 'alert', 'chat', 'system', 'evaluation', 'trade', 'economy'
- `agent_name` VARCHAR(100) NULLABLE — denormalized for fast display (avoids joining agents table on every read)
- `parent_message_id` INT NULLABLE (FK to messages.id) — for threaded replies
- `importance` INT DEFAULT 0 — 0=normal, 1=important, 2=critical (used for filtering)
- `expires_at` TIMESTAMP NULLABLE — for time-sensitive signals that shouldn't clutter history

**New table: `agora_read_receipts`**
- `id` SERIAL PRIMARY KEY
- `agent_id` INT NOT NULL (FK to agents.id)
- `channel` VARCHAR(50) NOT NULL
- `last_read_at` TIMESTAMP NOT NULL DEFAULT NOW()
- `last_read_message_id` INT NULLABLE (FK to messages.id)
- UNIQUE constraint on (agent_id, channel)

**New table: `agora_channels`**
- `name` VARCHAR(50) PRIMARY KEY
- `description` TEXT
- `is_system` BOOLEAN DEFAULT FALSE — system channels can't be created by agents
- `created_at` TIMESTAMP DEFAULT NOW()
- `message_count` INT DEFAULT 0 — denormalized counter, updated on insert

Seed the channels table with the 10 default channels after migration (use a data migration or post-migration script):

| Channel Name | Description | is_system |
|---|---|---|
| `market-intel` | Market discoveries, price movements, opportunities | false |
| `strategy-proposals` | Formal strategy proposals for debate | false |
| `strategy-debate` | Critiques, counter-arguments, stress tests | false |
| `trade-signals` | Pre-trade announcements: "I'm about to trade X because Y" | false |
| `trade-results` | Post-trade outcomes, P&L updates | false |
| `system-alerts` | Warden alerts, Dead Man's Switch, circuit breaker events | true |
| `genesis-log` | Genesis spawn/kill/evaluate decisions, capital allocation | true |
| `agent-chat` | Free-form agent discussion, ideas, collaboration | false |
| `sip-proposals` | System Improvement Proposals | false |
| `daily-report` | Genesis daily narrative report | true |

Run the migration: `alembic upgrade head`

---

## STEP 4 — Agora Message Schema (src/agora/schemas.py)

Create Pydantic models for Agora messages. These are the data contracts every agent uses.

```
from pydantic import BaseModel, Field
from datetime import datetime
from typing import Optional
from enum import Enum

class MessageType(str, Enum):
    THOUGHT = "thought"         # Internal reasoning an agent chose to share
    PROPOSAL = "proposal"       # Formal submission (strategy, SIP, etc.)
    SIGNAL = "signal"           # Actionable intel (trade signal, opportunity)
    ALERT = "alert"             # System-level alert (Warden, circuit breaker)
    CHAT = "chat"               # Informal discussion
    SYSTEM = "system"           # System events (agent born, agent died, regime change)
    EVALUATION = "evaluation"   # Evaluation results
    TRADE = "trade"             # Trade execution reports
    ECONOMY = "economy"         # Internal economy transactions (intel purchase, review, etc.)

class AgoraMessage(BaseModel):
    """The standard message format for The Agora."""
    agent_id: int
    agent_name: str
    channel: str
    content: str
    message_type: MessageType = MessageType.CHAT
    metadata: dict = Field(default_factory=dict)
    importance: int = Field(default=0, ge=0, le=2)  # 0=normal, 1=important, 2=critical
    parent_message_id: Optional[int] = None
    expires_at: Optional[datetime] = None

class AgoraMessageResponse(BaseModel):
    """What gets returned when reading messages."""
    id: int
    agent_id: int
    agent_name: str
    channel: str
    content: str
    message_type: str
    metadata: dict
    importance: int
    parent_message_id: Optional[int]
    timestamp: datetime
    expires_at: Optional[datetime] = None

class ChannelInfo(BaseModel):
    """Channel metadata."""
    name: str
    description: str
    is_system: bool
    message_count: int
    latest_message_at: Optional[datetime] = None

class ReadReceipt(BaseModel):
    """Tracks where an agent has read up to in a channel."""
    agent_id: int
    channel: str
    last_read_at: datetime
    last_read_message_id: Optional[int]
```

---

## STEP 5 — The AgoraService (src/agora/agora_service.py)

This is the core class. Every agent in the Syndicate uses this to communicate.

```
Class: AgoraService

    __init__(self, db_session_factory, redis_client):
        - Store db_session_factory and redis_client
        - Initialize structlog logger
        - Define SYSTEM_CHANNELS list (channels where is_system=True)
        - Define RATE_LIMIT: 10 messages per 5-minute window per agent
        - Define RATE_LIMIT_EXEMPT: [0]  (Genesis agent_id=0 is exempt)

    # ──────────────────────────────────────────────
    # POSTING MESSAGES
    # ──────────────────────────────────────────────

    async post_message(message: AgoraMessage) -> AgoraMessageResponse:
        """Post a message to The Agora. This is THE primary method."""
        
        1. Validate channel exists in agora_channels table
           - If channel doesn't exist and is not a system channel, auto-create it
           - If channel is a system channel that doesn't exist, raise error
        
        2. Rate limit check (skip if agent_id in RATE_LIMIT_EXEMPT):
           - Query Redis key `agora:rate:{agent_id}` — this is a counter with 300s TTL
           - If counter >= RATE_LIMIT (10), reject the message with a structured error
           - If under limit, increment counter
           - If key doesn't exist, set it to 1 with 300s TTL
        
        3. Filter expired messages check:
           - If message has expires_at and it's already in the past, reject
        
        4. Write to PostgreSQL:
           - Insert into messages table with all fields from AgoraMessage
           - Update agora_channels.message_count (increment by 1)
           - Return the created message with its id and timestamp
        
        5. Publish to Redis pub/sub:
           - Publish to channel `agora:{channel_name}`
           - Payload: JSON serialized AgoraMessageResponse
           - This is fire-and-forget — if no subscribers are listening, that's fine
           - Messages are always persisted in PostgreSQL regardless of pub/sub delivery
        
        6. Log the post:
           - structlog: agent_id, agent_name, channel, message_type, content_length
        
        7. Return AgoraMessageResponse with the database-assigned id and timestamp

    async post_system_message(channel: str, content: str, metadata: dict = None) -> AgoraMessageResponse:
        """Convenience method for system-level messages (Genesis, Warden, etc.)."""
        - Creates an AgoraMessage with agent_id=0, agent_name="System", message_type=SYSTEM
        - Calls post_message()
        - Used by infrastructure components that aren't agents

    # ──────────────────────────────────────────────
    # READING MESSAGES
    # ──────────────────────────────────────────────

    async read_channel(
        channel: str,
        since: Optional[datetime] = None,
        limit: int = 50,
        message_types: Optional[list[MessageType]] = None,
        min_importance: int = 0,
        include_expired: bool = False,
    ) -> list[AgoraMessageResponse]:
        """Read messages from a channel with filtering."""
        
        1. Query messages table:
           - Filter by channel
           - If since is provided, only messages with timestamp > since
           - If message_types provided, filter by message_type IN (types)
           - If min_importance > 0, filter by importance >= min_importance
           - If include_expired is False, exclude messages where expires_at < now()
           - Order by timestamp DESC
           - Limit to `limit` results
        
        2. Return list of AgoraMessageResponse

    async read_channel_since_last_read(
        agent_id: int,
        channel: str,
        limit: int = 50,
    ) -> list[AgoraMessageResponse]:
        """Read only NEW messages since this agent last read this channel."""
        
        1. Get the agent's read receipt for this channel
           - Query agora_read_receipts for (agent_id, channel)
           - If no receipt exists, return all messages (agent has never read this channel)
        
        2. Call read_channel() with since=last_read_at
        
        3. Return the messages (but DON'T auto-update the read receipt — 
           the agent should call mark_read() explicitly after processing)

    async read_multiple_channels(
        channels: list[str],
        since: Optional[datetime] = None,
        limit: int = 100,
    ) -> dict[str, list[AgoraMessageResponse]]:
        """Read from multiple channels at once. Returns dict keyed by channel name."""
        
        - Calls read_channel() for each channel
        - Returns {channel_name: [messages]}
        - Useful for agents that monitor several channels each cycle

    async get_recent_activity(
        limit: int = 20,
        min_importance: int = 0,
    ) -> list[AgoraMessageResponse]:
        """Get the most recent messages across ALL channels. Used by the web frontend."""
        
        - Query messages table, no channel filter
        - Order by timestamp DESC
        - Apply importance filter if specified
        - Exclude expired messages
        - Return up to `limit` messages

    async search_messages(
        query: str,
        channel: Optional[str] = None,
        agent_id: Optional[int] = None,
        limit: int = 20,
    ) -> list[AgoraMessageResponse]:
        """Full-text search across Agora messages."""
        
        - Use PostgreSQL ILIKE for simple text search: content ILIKE '%{query}%'
        - Optionally filter by channel and/or agent_id
        - Order by timestamp DESC
        - Return up to `limit` results
        
        Note: For Phase 2 this is basic ILIKE search. If performance becomes an issue 
        with large message volumes, we can add PostgreSQL full-text search (tsvector) later.

    # ──────────────────────────────────────────────
    # READ RECEIPTS
    # ──────────────────────────────────────────────

    async mark_read(agent_id: int, channel: str, up_to_message_id: Optional[int] = None) -> ReadReceipt:
        """Mark a channel as read up to a specific message (or now)."""
        
        1. Upsert into agora_read_receipts:
           - If receipt exists for (agent_id, channel), update last_read_at and last_read_message_id
           - If not, create a new receipt
        
        2. Return the ReadReceipt

    async get_unread_counts(agent_id: int) -> dict[str, int]:
        """Get count of unread messages per channel for an agent."""
        
        - For each channel in agora_channels:
          - Get the agent's read receipt (if any)
          - Count messages in that channel with timestamp > last_read_at
          - If no receipt, count ALL messages in that channel
        - Return {channel_name: unread_count}
        
        This is used by agents to decide which channels to prioritize reading.

    # ──────────────────────────────────────────────
    # CHANNEL MANAGEMENT
    # ──────────────────────────────────────────────

    async get_channels() -> list[ChannelInfo]:
        """List all channels with metadata."""
        
        - Query agora_channels table
        - For each channel, get the timestamp of the most recent message
        - Return list of ChannelInfo objects

    async get_channel_info(channel: str) -> Optional[ChannelInfo]:
        """Get info about a specific channel."""

    async create_channel(name: str, description: str) -> ChannelInfo:
        """Create a new non-system channel."""
        
        - Validate name: lowercase, alphanumeric + hyphens only, max 50 chars
        - Cannot create system channels through this method
        - Insert into agora_channels
        - Post a system message to agent-chat: "New channel created: {name}"
        - Return ChannelInfo

    # ──────────────────────────────────────────────
    # SUBSCRIPTIONS (Real-Time via Redis)
    # ──────────────────────────────────────────────

    async subscribe(channel: str, callback: callable):
        """Subscribe to real-time messages on a channel via Redis pub/sub."""
        
        - Subscribe to Redis channel `agora:{channel_name}`
        - When a message arrives, deserialize and call callback(message)
        - This runs in a background task
        - Returns a subscription handle that can be used to unsubscribe

    async unsubscribe(subscription_handle):
        """Unsubscribe from a channel."""
        
        - Unsubscribe from the Redis pub/sub channel
        - Clean up the background task

    async subscribe_multiple(channels: list[str], callback: callable):
        """Subscribe to multiple channels with a single callback."""
        
        - Convenience method that calls subscribe() for each channel
        - Returns list of subscription handles

    # ──────────────────────────────────────────────
    # MAINTENANCE
    # ──────────────────────────────────────────────

    async cleanup_expired_messages() -> int:
        """Delete messages past their expires_at. Run periodically by Genesis."""
        
        - DELETE FROM messages WHERE expires_at IS NOT NULL AND expires_at < NOW()
        - Return count of deleted messages
        - Log the cleanup

    async get_channel_stats() -> dict:
        """Get aggregate stats for monitoring. Used by daily report."""
        
        - Total messages in last 24 hours
        - Messages per channel in last 24 hours
        - Most active agents (by message count)
        - Most active channels
        - Return as dict
```

---

## STEP 6 — Redis Pub/Sub Manager (src/agora/pubsub.py)

The AgoraService needs a clean abstraction over Redis pub/sub. Create a dedicated manager:

```
Class: AgoraPubSub

    __init__(self, redis_client):
        - Store redis client
        - Initialize dict of active subscriptions: {channel: [callbacks]}
        - Initialize structlog logger
        - self._listener_task: Optional asyncio task for the subscription listener

    async publish(channel: str, message: dict):
        """Publish a message to a Redis channel."""
        
        - Serialize message to JSON
        - Publish to Redis channel `agora:{channel}`
        - Log: channel, message_size

    async subscribe(channel: str, callback: callable) -> str:
        """Subscribe to a channel. Returns subscription_id."""
        
        - Generate a unique subscription_id (uuid4)
        - Add to internal subscriptions dict
        - If this is the first subscription for this channel, 
          subscribe the Redis pubsub to `agora:{channel}`
        - If the listener task isn't running, start it
        - Return subscription_id

    async unsubscribe(subscription_id: str):
        """Remove a subscription by id."""
        
        - Remove from internal dict
        - If no more subscriptions for that channel, unsubscribe from Redis

    async _listener_loop(self):
        """Background task that reads Redis pub/sub messages and dispatches to callbacks."""
        
        - Runs in a while True loop
        - Reads messages from the Redis pubsub object
        - When a message arrives on `agora:{channel}`:
          - Deserialize the JSON
          - Look up all callbacks registered for that channel
          - Call each callback with the deserialized message
        - Handle connection errors gracefully (log + reconnect)
        - This is an asyncio task started by subscribe() and stopped by shutdown()

    async shutdown(self):
        """Clean shutdown of all subscriptions."""
        
        - Cancel the listener task
        - Unsubscribe from all Redis channels
        - Clear internal state
```

**Important implementation note:** Redis pub/sub in Python with `redis-py` requires using `redis.asyncio` for async support. The pubsub listener should use `pubsub.get_message()` in a loop with a small sleep, or `pubsub.listen()` as an async generator. Make sure to use the async Redis client, not the sync one.

---

## STEP 7 — Update BaseAgent to Use The Agora (src/common/base_agent.py)

The current BaseAgent has basic `post_to_agora()` and `read_agora()` methods from Phase 0. Replace them with proper AgoraService integration.

**Check what currently exists first**, then modify:

```
Changes to BaseAgent:

    __init__ additions:
        - Accept agora_service: AgoraService as a parameter
        - Store it as self.agora
        - If agora_service is None, create a minimal no-op stub 
          (so agents can still run without Agora in testing)

    Replace post_to_agora() with:
    
    async def post_to_agora(
        self, 
        channel: str, 
        content: str, 
        message_type: MessageType = MessageType.CHAT,
        metadata: dict = None,
        importance: int = 0,
        expires_at: datetime = None,
    ) -> Optional[AgoraMessageResponse]:
        """Post a message to The Agora."""
        
        if self.agora is None:
            self.log.warning("agora_not_available", channel=channel)
            return None
        
        message = AgoraMessage(
            agent_id=self.agent_id,
            agent_name=self.name,
            channel=channel,
            content=content,
            message_type=message_type,
            metadata=metadata or {},
            importance=importance,
            expires_at=expires_at,
        )
        
        return await self.agora.post_message(message)

    Replace read_agora() with:

    async def read_agora(
        self,
        channel: str,
        since: Optional[datetime] = None,
        limit: int = 50,
        message_types: Optional[list[MessageType]] = None,
        only_unread: bool = False,
    ) -> list[AgoraMessageResponse]:
        """Read messages from The Agora."""
        
        if self.agora is None:
            return []
        
        if only_unread:
            return await self.agora.read_channel_since_last_read(
                agent_id=self.agent_id, channel=channel, limit=limit
            )
        
        return await self.agora.read_channel(
            channel=channel, since=since, limit=limit, message_types=message_types
        )

    New method:

    async def mark_agora_read(self, channel: str, up_to_message_id: Optional[int] = None):
        """Mark a channel as read. Call after processing messages."""
        
        if self.agora is None:
            return
        
        await self.agora.mark_read(
            agent_id=self.agent_id, channel=channel, up_to_message_id=up_to_message_id
        )

    New method:

    async def get_agora_unread(self) -> dict[str, int]:
        """Check how many unread messages per channel."""
        
        if self.agora is None:
            return {}
        
        return await self.agora.get_unread_counts(agent_id=self.agent_id)

    New method:

    async def broadcast(self, content: str, importance: int = 1):
        """Post an important message to agent-chat visible to everyone."""
        
        return await self.post_to_agora(
            channel="agent-chat",
            content=content,
            message_type=MessageType.CHAT,
            importance=importance,
        )
```

**CRITICAL:** When updating BaseAgent, make sure ALL existing code that calls the old `post_to_agora()` or `read_agora()` signatures still works. Check:
- `src/genesis/genesis.py` — Genesis calls post_to_agora() in multiple places
- `src/risk/warden.py` — Warden may post alerts
- Any tests that use these methods

Update all callers to use the new signature. If the old calls just pass (channel, content), make sure the new method handles that via defaults.

---

## STEP 8 — Update Genesis to Use AgoraService (src/genesis/genesis.py)

Genesis currently posts to the Agora using the basic Phase 0 methods. Update it to use the full AgoraService:

1. **Initialization:** Genesis should create the AgoraService instance and pass it to its own BaseAgent.__init__()

2. **Replace all post_to_agora calls** with the new typed versions:
   - Cycle logs → `channel="genesis-log"`, `message_type=MessageType.SYSTEM`
   - Spawn notices → `channel="genesis-log"`, `message_type=MessageType.SYSTEM`, `importance=1`
   - Kill notices → `channel="genesis-log"`, `message_type=MessageType.SYSTEM`, `importance=2`
   - Evaluation results → `channel="genesis-log"`, `message_type=MessageType.EVALUATION`
   - Regime changes → `channel="market-intel"`, `message_type=MessageType.SIGNAL`, `importance=1`
   - Capital allocation → `channel="genesis-log"`, `message_type=MessageType.SYSTEM`
   - Daily report → `channel="daily-report"`, `message_type=MessageType.SYSTEM`, `importance=1`

3. **Agora monitoring in run_cycle():** Step 9 of the Genesis cycle reads recent Agora messages. Update this to use:
   ```python
   # Read unread messages from key channels
   unread = await self.get_agora_unread()
   if unread.get("sip-proposals", 0) > 0:
       sips = await self.read_agora("sip-proposals", only_unread=True)
       # Process SIP proposals...
       await self.mark_agora_read("sip-proposals")
   ```

4. **Expired message cleanup:** Add to the Genesis cycle (run once per hour, not every cycle):
   ```python
   # Cleanup expired messages periodically
   if should_run_hourly_maintenance():
       deleted = await self.agora.cleanup_expired_messages()
       if deleted > 0:
           self.log.info("agora_cleanup", expired_messages_deleted=deleted)
   ```

---

## STEP 9 — Update Warden to Use AgoraService (src/risk/warden.py)

**REMINDER: The Warden is the immutable safety layer. Do NOT add any LLM calls or complex logic. Only update how it posts messages.**

1. Accept AgoraService as a parameter (optional — Warden must still function without it)

2. Replace any direct database message inserts with AgoraService calls:
   - Alert escalations → `channel="system-alerts"`, `message_type=MessageType.ALERT`, `importance=2`
   - Trade gate decisions → `channel="system-alerts"`, `message_type=MessageType.SYSTEM`
   - Agent kill notices → `channel="system-alerts"`, `message_type=MessageType.ALERT`, `importance=2`

3. The Warden should POST to the Agora but should NOT READ from it. The Warden makes decisions based on database state and exchange data, never based on agent chatter. This is by design — agents cannot influence the safety layer through messages.

---

## STEP 10 — Agora Integration Helper (src/agora/__init__.py)

Create a clean initialization function that other modules can import:

```python
from src.agora.agora_service import AgoraService
from src.agora.pubsub import AgoraPubSub
from src.agora.schemas import AgoraMessage, MessageType, AgoraMessageResponse

async def create_agora_service(db_session_factory, redis_client) -> AgoraService:
    """Factory function to create a fully initialized AgoraService."""
    pubsub = AgoraPubSub(redis_client)
    service = AgoraService(db_session_factory, redis_client, pubsub)
    return service

__all__ = [
    "AgoraService",
    "AgoraPubSub", 
    "AgoraMessage",
    "MessageType",
    "AgoraMessageResponse",
    "create_agora_service",
]
```

---

## STEP 11 — Tests

Create comprehensive tests for the Agora system:

**tests/test_agora_service.py:**

```
Test posting and reading:
- test_post_message_basic — post a message, verify it's in the database
- test_post_message_all_types — post one of each MessageType, verify all stored correctly
- test_post_message_with_metadata — post with metadata dict, verify JSON stored/retrieved
- test_post_message_with_importance — post with importance levels 0, 1, 2
- test_post_message_with_expiry — post with expires_at in the future, verify it's readable
- test_post_message_expired_rejected — post with expires_at in the past, verify rejection
- test_post_system_message — verify system messages use agent_id=0

Test reading and filtering:
- test_read_channel_basic — post 5 messages to a channel, read them back
- test_read_channel_since — post messages at different times, read only recent ones
- test_read_channel_with_type_filter — post mixed types, filter by specific type
- test_read_channel_with_importance_filter — post mixed importance, filter by min_importance
- test_read_channel_excludes_expired — post an expired message, verify it's excluded by default
- test_read_channel_includes_expired — verify include_expired=True returns them
- test_read_channel_limit — post 20 messages, read with limit=5, verify only 5 returned
- test_read_multiple_channels — post to 3 channels, read all at once

Test search:
- test_search_messages_basic — post messages with known content, search for keyword
- test_search_messages_by_channel — search within a specific channel
- test_search_messages_by_agent — search for messages from a specific agent

Test rate limiting:
- test_rate_limit_enforced — post 11 messages rapidly from same agent, verify 11th is rejected
- test_rate_limit_per_agent — agent A hits limit, agent B can still post
- test_rate_limit_genesis_exempt — Genesis (agent_id=0) can post unlimited messages
- test_rate_limit_resets — post 10 messages, wait for TTL (mock), post again successfully

Test read receipts:
- test_mark_read_creates_receipt — mark a channel read, verify receipt exists
- test_mark_read_updates_receipt — mark read twice, verify timestamp updated
- test_read_since_last_read — post 3 messages, mark read after 2nd, read_since_last_read returns only 3rd
- test_unread_counts — post messages to 3 channels, mark 1 read, verify unread counts

Test channel management:
- test_get_channels — verify all 10 default channels exist
- test_create_channel — create a new channel, verify it appears
- test_create_channel_validation — try invalid names (spaces, uppercase, too long), verify rejection
- test_cannot_create_system_channel — try to create a channel flagged as system, verify rejection

Test maintenance:
- test_cleanup_expired_messages — create expired messages, run cleanup, verify deleted
- test_channel_stats — post various messages, verify stats are accurate
```

**tests/test_agora_pubsub.py:**

```
- test_publish_subscribe — publish a message, verify subscriber callback receives it
- test_multiple_subscribers — two callbacks on same channel, both receive message
- test_unsubscribe — subscribe, unsubscribe, publish, verify callback NOT called
- test_subscribe_multiple_channels — subscribe to 3 channels, publish to each, verify all received
- test_shutdown — subscribe, shutdown, verify clean state
```

**tests/test_agora_integration.py:**

```
- test_genesis_posts_to_agora — run a Genesis cycle, verify messages appear in genesis-log
- test_warden_posts_alerts — trigger a Warden alert condition, verify message in system-alerts
- test_base_agent_post_and_read — create a mock agent, post via BaseAgent methods, read back
- test_base_agent_unread_counts — post messages, check unread, mark read, check again
- test_base_agent_broadcast — broadcast a message, verify it appears in agent-chat
```

Run all tests: `python -m pytest tests/ -v`

---

## STEP 12 — Update Process Runners

Update `scripts/run_genesis.py` and `scripts/run_warden.py` to initialize the AgoraService and pass it to Genesis/Warden:

```python
# In run_genesis.py, before starting the Genesis loop:
import redis.asyncio as aioredis
from src.agora import create_agora_service

redis_client = aioredis.from_url(config.redis_url)
agora = await create_agora_service(db_session_factory, redis_client)

genesis = GenesisAgent(
    db_session_factory=db_session_factory,
    agora_service=agora,
    # ... other params
)
```

Do the same for `run_warden.py`.

Make sure `run_all.py` still works after these changes.

---

## STEP 13 — Live Verification

After all code is written and tests pass:

1. Start the full system: `python scripts/run_all.py`
2. Let it run for 60 seconds
3. Verify in the database:
   - `SELECT COUNT(*) FROM agora_channels;` — should be 10
   - `SELECT channel, message_type, content FROM messages ORDER BY timestamp DESC LIMIT 10;` — should show Genesis cycle logs with proper message_type
   - Verify no errors in console output

4. Stop the system (Ctrl+C)

---

## STEP 14 — Update CLAUDE.md

Add to the Architecture Quick Reference section:
```
### The Agora (Phase 2A)
- Central nervous system — all agent communication flows through here
- AgoraService: post_message(), read_channel(), mark_read(), subscribe()
- 10 channels: market-intel, strategy-proposals, strategy-debate, trade-signals, 
  trade-results, system-alerts, genesis-log, agent-chat, sip-proposals, daily-report
- Real-time: Redis pub/sub for instant delivery
- Persistent: PostgreSQL for history and querying
- Rate limited: 10 messages per 5-minute cycle per agent (Genesis exempt)
- Read receipts: agents track what they've read per channel
```

Update the Phase Roadmap to show Phase 2A as COMPLETE.

---

## STEP 15 — Update CHANGELOG.md and CURRENT_STATUS.md

Log everything built in this session. CURRENT_STATUS.md should note:
- Phase 2A complete
- Next up: Phase 2B (The Library)
- Any issues or decisions made during the build

---

## STEP 16 — Git Commit and Push

```
git add .
git commit -m "Phase 2A: The Agora — central nervous system, pub/sub, read receipts, rate limiting"
git push origin main
```

---

## DESIGN DECISIONS (Reference for Claude Code)

These decisions were made in the War Room (Claude.ai chat) and are final:

1. **Message types:** 9 types (thought, proposal, signal, alert, chat, system, evaluation, trade, economy)
2. **Rate limiting:** 10 messages per 5-minute window per agent, Genesis exempt, enforced via Redis counter with TTL
3. **Read receipts:** Per agent per channel, stored in agora_read_receipts table, agents must explicitly call mark_read()
4. **Search:** Basic PostgreSQL ILIKE for Phase 2. Full-text search upgrade deferred.
5. **Channel creation:** Agents can create non-system channels. System channels are pre-seeded and protected.
6. **Pub/sub:** Redis async pub/sub via redis.asyncio. Messages always persisted to PostgreSQL regardless of pub/sub delivery.
7. **Warden Agora rule:** Warden POSTS to Agora but NEVER READS from it. Safety layer cannot be influenced by agent chatter.
8. **Expired messages:** Messages can have expires_at. Excluded from reads by default. Genesis cleans them up hourly.
9. **BaseAgent integration:** All agents get post_to_agora(), read_agora(), mark_agora_read(), get_agora_unread(), broadcast() — with graceful no-op if AgoraService is None.
10. **Genesis is agent_id=0:** Already fixed in post-Phase-1 bugfix. All system messages use this ID.

---

Before you start, confirm you've read CLAUDE.md and the current project state. Then proceed through each step in order. Ask me if anything is unclear.