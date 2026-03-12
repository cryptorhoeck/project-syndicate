"""
Tests for The Agora — Integration with Genesis, Warden, and BaseAgent

Verifies that the Agora service integrates properly with existing components.
"""

__version__ = "0.3.0"

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from src.agora.agora_service import AgoraService
from src.agora.pubsub import AgoraPubSub
from src.agora.schemas import AgoraMessage, MessageType
from src.common.base_agent import BaseAgent, AgentStatus
from src.common.models import (
    Agent,
    AgoraChannel,
    Base,
    Message,
    SystemState,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def db():
    """In-memory SQLite database for integration tests."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)

    with factory() as session:
        session.add(Agent(id=0, name="Genesis", type="genesis", status="active"))
        session.add(Agent(id=1, name="TestAgent", type="scout", status="active"))
        for ch_name, desc, is_sys in [
            ("market-intel", "Market discoveries", False),
            ("strategy-proposals", "Strategy proposals", False),
            ("strategy-debate", "Critiques", False),
            ("trade-signals", "Pre-trade announcements", False),
            ("trade-results", "Post-trade outcomes", False),
            ("system-alerts", "Warden alerts", True),
            ("genesis-log", "Genesis decisions", True),
            ("agent-chat", "Free-form discussion", False),
            ("sip-proposals", "SIPs", False),
            ("daily-report", "Daily report", True),
        ]:
            session.add(AgoraChannel(name=ch_name, description=desc, is_system=is_sys, message_count=0))
        session.add(SystemState(
            total_treasury=1000.0, peak_treasury=1000.0,
            current_regime="unknown", active_agent_count=1, alert_status="green",
        ))
        session.commit()

    return factory


@pytest.fixture
def mock_redis():
    r = AsyncMock()
    r.get = AsyncMock(return_value=None)
    pipe = AsyncMock()
    pipe.incr = AsyncMock()
    pipe.expire = AsyncMock()
    pipe.execute = AsyncMock(return_value=[1, True])
    r.pipeline = MagicMock(return_value=pipe)
    r.publish = AsyncMock()
    return r


@pytest.fixture
def agora(db, mock_redis):
    pubsub = AgoraPubSub(mock_redis)
    return AgoraService(db, mock_redis, pubsub)


# ---------------------------------------------------------------------------
# Concrete test agent (BaseAgent is abstract)
# ---------------------------------------------------------------------------

class MockAgent(BaseAgent):
    async def initialize(self): pass
    async def run(self): pass
    async def evaluate(self): return {"status": "test"}


# ---------------------------------------------------------------------------
# BaseAgent integration tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_base_agent_post_and_read(db, agora):
    agent = MockAgent(
        agent_id=1, name="TestAgent", agent_type="scout",
        db_session_factory=db, agora_service=agora,
    )

    resp = await agent.post_to_agora("agent-chat", "Test message from agent")
    assert resp is not None
    assert resp.content == "Test message from agent"

    messages = await agent.read_agora("agent-chat")
    assert len(messages) == 1
    assert messages[0].content == "Test message from agent"


@pytest.mark.asyncio
async def test_base_agent_unread_counts(db, agora):
    agent = MockAgent(
        agent_id=1, name="TestAgent", agent_type="scout",
        db_session_factory=db, agora_service=agora,
    )

    await agent.post_to_agora("agent-chat", "Msg 1")
    await agent.post_to_agora("agent-chat", "Msg 2")

    unread = await agent.get_agora_unread()
    assert unread.get("agent-chat", 0) == 2

    await agent.mark_agora_read("agent-chat")

    unread = await agent.get_agora_unread()
    assert unread.get("agent-chat", 0) == 0


@pytest.mark.asyncio
async def test_base_agent_broadcast(db, agora):
    agent = MockAgent(
        agent_id=1, name="TestAgent", agent_type="scout",
        db_session_factory=db, agora_service=agora,
    )

    resp = await agent.broadcast("Important announcement!")
    assert resp is not None
    assert resp.channel == "agent-chat"
    assert resp.importance == 1


@pytest.mark.asyncio
async def test_base_agent_post_with_message_type(db, agora):
    agent = MockAgent(
        agent_id=1, name="TestAgent", agent_type="scout",
        db_session_factory=db, agora_service=agora,
    )

    resp = await agent.post_to_agora(
        "market-intel",
        "BTC breakout detected",
        message_type=MessageType.SIGNAL,
        importance=1,
    )
    assert resp.message_type == "signal"
    assert resp.importance == 1


@pytest.mark.asyncio
async def test_base_agent_fallback_without_agora(db):
    """When agora_service is None, post falls back to direct DB insert."""
    agent = MockAgent(
        agent_id=1, name="TestAgent", agent_type="scout",
        db_session_factory=db, agora_service=None,
    )

    result = await agent.post_to_agora("agent-chat", "Fallback message")
    assert result is None  # Returns None without AgoraService

    # Verify message was written directly to DB
    with db() as session:
        msgs = session.execute(
            select(Message).where(Message.channel == "agent-chat")
        ).scalars().all()
        assert len(msgs) == 1
        assert msgs[0].content == "Fallback message"
        assert msgs[0].agent_name == "TestAgent"


@pytest.mark.asyncio
async def test_base_agent_agora_none_graceful(db):
    """All Agora methods are no-ops when agora_service is None."""
    agent = MockAgent(
        agent_id=1, name="TestAgent", agent_type="scout",
        db_session_factory=db, agora_service=None,
    )

    messages = await agent.read_agora("agent-chat")
    assert messages == []  # Fallback returns empty from DB (no messages yet)

    await agent.mark_agora_read("agent-chat")  # Should not raise

    unread = await agent.get_agora_unread()
    assert unread == {}
