"""
Tests for The Agora — AgoraService

Tests posting, reading, filtering, rate limiting, read receipts,
channel management, search, and maintenance.
"""

__version__ = "0.3.0"

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.agora.agora_service import AgoraService
from src.agora.schemas import AgoraMessage, AgoraMessageResponse, MessageType
from src.common.models import (
    Agent,
    AgoraChannel,
    AgoraReadReceipt,
    Base,
    Message,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


@pytest.fixture
def db():
    """In-memory SQLite database for testing."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)

    # Seed: create Genesis agent (id=0) and a test agent (id=1)
    with factory() as session:
        session.add(Agent(id=0, name="Genesis", type="genesis", status="active"))
        session.add(Agent(id=1, name="Scout-Alpha", type="scout", status="active"))
        session.add(Agent(id=2, name="Scout-Beta", type="scout", status="active"))
        # Seed default channels
        for ch_name, desc, is_sys in [
            ("market-intel", "Market discoveries", False),
            ("strategy-proposals", "Strategy proposals", False),
            ("strategy-debate", "Critiques and stress tests", False),
            ("trade-signals", "Pre-trade announcements", False),
            ("trade-results", "Post-trade outcomes", False),
            ("system-alerts", "Warden alerts", True),
            ("genesis-log", "Genesis decisions", True),
            ("agent-chat", "Free-form discussion", False),
            ("sip-proposals", "System Improvement Proposals", False),
            ("daily-report", "Daily narrative report", True),
        ]:
            session.add(AgoraChannel(name=ch_name, description=desc, is_system=is_sys, message_count=0))
        session.commit()

    return factory


@pytest.fixture
def mock_redis():
    """Mock async Redis client."""
    r = AsyncMock()
    r.get = AsyncMock(return_value=None)
    r.incr = AsyncMock(return_value=1)
    r.expire = AsyncMock()
    r.publish = AsyncMock()

    # Pipeline mock
    pipe = AsyncMock()
    pipe.incr = AsyncMock()
    pipe.expire = AsyncMock()
    pipe.execute = AsyncMock(return_value=[1, True])
    r.pipeline = MagicMock(return_value=pipe)

    return r


@pytest.fixture
def agora(db, mock_redis):
    """AgoraService with in-memory DB and mock Redis."""
    from src.agora.pubsub import AgoraPubSub
    pubsub = AgoraPubSub(mock_redis)
    return AgoraService(db, mock_redis, pubsub)


def _msg(agent_id=1, agent_name="Scout-Alpha", channel="agent-chat",
         content="Hello Agora", message_type=MessageType.CHAT,
         importance=0, metadata=None, expires_at=None, parent_message_id=None):
    """Helper to build AgoraMessage."""
    return AgoraMessage(
        agent_id=agent_id,
        agent_name=agent_name,
        channel=channel,
        content=content,
        message_type=message_type,
        metadata=metadata or {},
        importance=importance,
        expires_at=expires_at,
        parent_message_id=parent_message_id,
    )


# ---------------------------------------------------------------------------
# Posting tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_post_message_basic(agora):
    resp = await agora.post_message(_msg())
    assert resp.id is not None
    assert resp.channel == "agent-chat"
    assert resp.content == "Hello Agora"
    assert resp.agent_name == "Scout-Alpha"


@pytest.mark.asyncio
async def test_post_message_all_types(agora):
    for mt in MessageType:
        resp = await agora.post_message(_msg(message_type=mt, content=f"Type: {mt.value}"))
        assert resp.message_type == mt.value


@pytest.mark.asyncio
async def test_post_message_with_metadata(agora):
    meta = {"strategy": "momentum", "confidence": 0.85}
    resp = await agora.post_message(_msg(metadata=meta))
    assert resp.metadata == meta


@pytest.mark.asyncio
async def test_post_message_with_importance(agora):
    for imp in [0, 1, 2]:
        resp = await agora.post_message(_msg(importance=imp, content=f"Importance {imp}"))
        assert resp.importance == imp


@pytest.mark.asyncio
async def test_post_message_with_future_expiry(agora):
    future = datetime.now(timezone.utc) + timedelta(hours=1)
    resp = await agora.post_message(_msg(expires_at=future))
    assert resp.expires_at is not None


@pytest.mark.asyncio
async def test_post_message_expired_rejected(agora):
    past = datetime.now(timezone.utc) - timedelta(hours=1)
    with pytest.raises(ValueError, match="expires_at is in the past"):
        await agora.post_message(_msg(expires_at=past))


@pytest.mark.asyncio
async def test_post_system_message(agora):
    resp = await agora.post_system_message("system-alerts", "Test system alert")
    assert resp.agent_id == 0
    assert resp.agent_name == "System"
    assert resp.message_type == "system"


# ---------------------------------------------------------------------------
# Reading and filtering tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_read_channel_basic(agora):
    for i in range(5):
        await agora.post_message(_msg(content=f"Message {i}"))

    messages = await agora.read_channel("agent-chat")
    assert len(messages) == 5


@pytest.mark.asyncio
async def test_read_channel_since(agora):
    # Insert old message with explicit past timestamp
    with agora.db_session_factory() as session:
        session.add(Message(
            agent_id=1, channel="agent-chat", content="Old message",
            message_type="chat", agent_name="Scout-Alpha", importance=0,
            timestamp=datetime(2020, 1, 1, tzinfo=timezone.utc),
        ))
        session.commit()

    cutoff = datetime(2025, 1, 1, tzinfo=timezone.utc)
    await agora.post_message(_msg(content="New message"))

    messages = await agora.read_channel("agent-chat", since=cutoff)
    assert len(messages) == 1
    assert messages[0].content == "New message"


@pytest.mark.asyncio
async def test_read_channel_with_type_filter(agora):
    await agora.post_message(_msg(message_type=MessageType.CHAT, content="Chat"))
    await agora.post_message(_msg(message_type=MessageType.SIGNAL, content="Signal"))
    await agora.post_message(_msg(message_type=MessageType.ALERT, content="Alert"))

    signals = await agora.read_channel("agent-chat", message_types=[MessageType.SIGNAL])
    assert len(signals) == 1
    assert signals[0].content == "Signal"


@pytest.mark.asyncio
async def test_read_channel_with_importance_filter(agora):
    await agora.post_message(_msg(importance=0, content="Normal"))
    await agora.post_message(_msg(importance=1, content="Important"))
    await agora.post_message(_msg(importance=2, content="Critical"))

    important = await agora.read_channel("agent-chat", min_importance=1)
    assert len(important) == 2


@pytest.mark.asyncio
async def test_read_channel_excludes_expired(agora):
    # Post a message that's already expired (bypass the posting check by writing directly)
    with agora.db_session_factory() as session:
        msg = Message(
            agent_id=1, channel="agent-chat", content="Expired",
            message_type="chat", agent_name="Scout-Alpha", importance=0,
            expires_at=datetime.now(timezone.utc) - timedelta(hours=1),
        )
        session.add(msg)
        session.commit()

    await agora.post_message(_msg(content="Fresh"))

    messages = await agora.read_channel("agent-chat")
    assert len(messages) == 1
    assert messages[0].content == "Fresh"


@pytest.mark.asyncio
async def test_read_channel_includes_expired(agora):
    with agora.db_session_factory() as session:
        msg = Message(
            agent_id=1, channel="agent-chat", content="Expired",
            message_type="chat", agent_name="Scout-Alpha", importance=0,
            expires_at=datetime.now(timezone.utc) - timedelta(hours=1),
        )
        session.add(msg)
        session.commit()

    await agora.post_message(_msg(content="Fresh"))

    messages = await agora.read_channel("agent-chat", include_expired=True)
    assert len(messages) == 2


@pytest.mark.asyncio
async def test_read_channel_limit(agora):
    for i in range(20):
        await agora.post_message(_msg(content=f"Msg {i}"))

    messages = await agora.read_channel("agent-chat", limit=5)
    assert len(messages) == 5


@pytest.mark.asyncio
async def test_read_multiple_channels(agora):
    await agora.post_message(_msg(channel="agent-chat", content="Chat msg"))
    await agora.post_message(_msg(channel="market-intel", content="Intel msg"))
    await agora.post_message(_msg(channel="trade-signals", content="Signal msg"))

    result = await agora.read_multiple_channels(["agent-chat", "market-intel", "trade-signals"])
    assert len(result) == 3
    assert len(result["agent-chat"]) == 1
    assert len(result["market-intel"]) == 1
    assert len(result["trade-signals"]) == 1


# ---------------------------------------------------------------------------
# Search tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_search_messages_basic(agora):
    await agora.post_message(_msg(content="BTC looks bullish today"))
    await agora.post_message(_msg(content="ETH is crabbing"))

    results = await agora.search_messages("bullish")
    assert len(results) == 1
    assert "bullish" in results[0].content


@pytest.mark.asyncio
async def test_search_messages_by_channel(agora):
    await agora.post_message(_msg(channel="agent-chat", content="BTC signal"))
    await agora.post_message(_msg(channel="market-intel", content="BTC discovery"))

    results = await agora.search_messages("BTC", channel="market-intel")
    assert len(results) == 1
    assert results[0].channel == "market-intel"


@pytest.mark.asyncio
async def test_search_messages_by_agent(agora):
    await agora.post_message(_msg(agent_id=1, content="Scout found something"))
    await agora.post_message(_msg(agent_id=2, agent_name="Scout-Beta", content="Beta found something"))

    results = await agora.search_messages("found", agent_id=1)
    assert len(results) == 1
    assert results[0].agent_id == 1


# ---------------------------------------------------------------------------
# Rate limiting tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_rate_limit_enforced(agora, mock_redis):
    # Simulate counter at limit
    mock_redis.get = AsyncMock(return_value="10")

    with pytest.raises(ValueError, match="Rate limit exceeded"):
        await agora.post_message(_msg())


@pytest.mark.asyncio
async def test_rate_limit_per_agent(agora, mock_redis):
    # Agent 1 is at limit
    async def side_effect(key):
        if key == "agora:rate:1":
            return "10"
        return None
    mock_redis.get = AsyncMock(side_effect=side_effect)

    # Agent 1 should be blocked
    with pytest.raises(ValueError, match="Rate limit exceeded"):
        await agora.post_message(_msg(agent_id=1))

    # Agent 2 should succeed
    resp = await agora.post_message(_msg(agent_id=2, agent_name="Scout-Beta"))
    assert resp.id is not None


@pytest.mark.asyncio
async def test_rate_limit_genesis_exempt(agora, mock_redis):
    # Counter at limit — but Genesis (agent_id=0) is exempt
    mock_redis.get = AsyncMock(return_value="100")

    resp = await agora.post_message(_msg(agent_id=0, agent_name="Genesis"))
    assert resp.id is not None


@pytest.mark.asyncio
async def test_rate_limit_resets(agora, mock_redis):
    # First call: counter at 9 (under limit)
    mock_redis.get = AsyncMock(return_value="9")
    resp = await agora.post_message(_msg())
    assert resp.id is not None


# ---------------------------------------------------------------------------
# Read receipt tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_mark_read_creates_receipt(agora):
    receipt = await agora.mark_read(agent_id=1, channel="agent-chat")
    assert receipt.agent_id == 1
    assert receipt.channel == "agent-chat"
    assert receipt.last_read_at is not None


@pytest.mark.asyncio
async def test_mark_read_updates_receipt(agora):
    r1 = await agora.mark_read(agent_id=1, channel="agent-chat")
    r2 = await agora.mark_read(agent_id=1, channel="agent-chat", up_to_message_id=5)
    assert r2.last_read_at >= r1.last_read_at
    assert r2.last_read_message_id == 5


@pytest.mark.asyncio
async def test_read_since_last_read(agora):
    # Post 2 old messages and 1 new message with controlled timestamps
    old_time = datetime(2020, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    mid_time = datetime(2025, 6, 1, 0, 0, 0, tzinfo=timezone.utc)
    new_time = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)

    with agora.db_session_factory() as session:
        session.add(Message(
            agent_id=1, channel="agent-chat", content="Msg 1",
            message_type="chat", agent_name="Scout-Alpha", importance=0,
            timestamp=old_time,
        ))
        session.add(Message(
            agent_id=1, channel="agent-chat", content="Msg 2",
            message_type="chat", agent_name="Scout-Alpha", importance=0,
            timestamp=old_time,
        ))
        session.add(Message(
            agent_id=1, channel="agent-chat", content="Msg 3",
            message_type="chat", agent_name="Scout-Alpha", importance=0,
            timestamp=new_time,
        ))
        session.commit()

    # Set read receipt to mid_time (between old and new)
    from src.common.models import AgoraReadReceipt
    with agora.db_session_factory() as session:
        receipt = AgoraReadReceipt(
            agent_id=1, channel="agent-chat", last_read_at=mid_time,
        )
        session.add(receipt)
        session.commit()

    unread = await agora.read_channel_since_last_read(agent_id=1, channel="agent-chat")
    assert len(unread) == 1
    assert unread[0].content == "Msg 3"


@pytest.mark.asyncio
async def test_unread_counts(agora):
    old_time = datetime(2020, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    mid_time = datetime(2025, 6, 1, 0, 0, 0, tzinfo=timezone.utc)
    new_time = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)

    with agora.db_session_factory() as session:
        # Old chat messages (will be marked as read)
        session.add(Message(
            agent_id=1, channel="agent-chat", content="Chat 1",
            message_type="chat", agent_name="Scout-Alpha", importance=0,
            timestamp=old_time,
        ))
        session.add(Message(
            agent_id=1, channel="agent-chat", content="Chat 2",
            message_type="chat", agent_name="Scout-Alpha", importance=0,
            timestamp=old_time,
        ))
        # New chat message (posted after mark_read)
        session.add(Message(
            agent_id=1, channel="agent-chat", content="Chat 3",
            message_type="chat", agent_name="Scout-Alpha", importance=0,
            timestamp=new_time,
        ))
        # Market intel (never read)
        session.add(Message(
            agent_id=1, channel="market-intel", content="Intel 1",
            message_type="chat", agent_name="Scout-Alpha", importance=0,
            timestamp=old_time,
        ))
        session.commit()

    # Set agent-chat read receipt to mid_time
    from src.common.models import AgoraReadReceipt
    with agora.db_session_factory() as session:
        session.add(AgoraReadReceipt(
            agent_id=1, channel="agent-chat", last_read_at=mid_time,
        ))
        session.commit()

    counts = await agora.get_unread_counts(agent_id=1)
    assert counts.get("agent-chat", 0) == 1  # Only Chat 3
    assert counts.get("market-intel", 0) == 1  # Never read


# ---------------------------------------------------------------------------
# Channel management tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_channels(agora):
    channels = await agora.get_channels()
    assert len(channels) == 10
    names = {ch.name for ch in channels}
    assert "agent-chat" in names
    assert "system-alerts" in names


@pytest.mark.asyncio
async def test_create_channel(agora):
    ch = await agora.create_channel("custom-signals", "Custom trading signals")
    assert ch.name == "custom-signals"
    assert ch.is_system is False

    channels = await agora.get_channels()
    # 10 default + 1 new + 1 auto-created agent-chat announcement
    names = {c.name for c in channels}
    assert "custom-signals" in names


@pytest.mark.asyncio
async def test_create_channel_validation(agora):
    with pytest.raises(ValueError, match="Invalid channel name"):
        await agora.create_channel("UPPERCASE", "Bad name")

    with pytest.raises(ValueError, match="Invalid channel name"):
        await agora.create_channel("has spaces", "Bad name")

    with pytest.raises(ValueError, match="Invalid channel name"):
        await agora.create_channel("x" * 60, "Too long")


@pytest.mark.asyncio
async def test_cannot_create_system_channel(agora):
    with pytest.raises(ValueError, match="Cannot create system channel"):
        await agora.create_channel("system-alerts", "Trying to overwrite")


# ---------------------------------------------------------------------------
# Maintenance tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cleanup_expired_messages(agora):
    # Insert expired messages directly
    with agora.db_session_factory() as session:
        for i in range(3):
            session.add(Message(
                agent_id=1, channel="agent-chat", content=f"Expired {i}",
                message_type="signal", agent_name="Scout-Alpha", importance=0,
                expires_at=datetime.now(timezone.utc) - timedelta(hours=1),
            ))
        session.add(Message(
            agent_id=1, channel="agent-chat", content="Still valid",
            message_type="chat", agent_name="Scout-Alpha", importance=0,
        ))
        session.commit()

    deleted = await agora.cleanup_expired_messages()
    assert deleted == 3

    # Verify only the valid message remains
    messages = await agora.read_channel("agent-chat", include_expired=True)
    assert len(messages) == 1
    assert messages[0].content == "Still valid"


@pytest.mark.asyncio
async def test_channel_stats(agora):
    await agora.post_message(_msg(channel="agent-chat", content="Chat 1"))
    await agora.post_message(_msg(channel="agent-chat", content="Chat 2"))
    await agora.post_message(_msg(channel="market-intel", content="Intel 1"))

    stats = await agora.get_channel_stats()
    assert stats["total_messages_24h"] == 3
    assert stats["per_channel"]["agent-chat"] == 2
    assert stats["per_channel"]["market-intel"] == 1
