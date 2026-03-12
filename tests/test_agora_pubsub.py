"""
Tests for The Agora — PubSub Manager

Tests publish, subscribe, unsubscribe, multiple channels, and shutdown.
"""

__version__ = "0.3.0"

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agora.pubsub import AgoraPubSub


@pytest.fixture
def mock_redis():
    """Mock async Redis client for pub/sub testing."""
    r = AsyncMock()
    r.publish = AsyncMock(return_value=1)

    # Mock pubsub object
    ps = AsyncMock()
    ps.subscribe = AsyncMock()
    ps.unsubscribe = AsyncMock()
    ps.close = AsyncMock()
    ps.get_message = AsyncMock(return_value=None)
    r.pubsub = MagicMock(return_value=ps)

    return r


@pytest.fixture
def pubsub(mock_redis):
    return AgoraPubSub(mock_redis)


@pytest.mark.asyncio
async def test_publish(pubsub, mock_redis):
    await pubsub.publish("agent-chat", {"content": "Hello"})
    mock_redis.publish.assert_called_once()
    args = mock_redis.publish.call_args
    assert args[0][0] == "agora:agent-chat"
    payload = json.loads(args[0][1])
    assert payload["content"] == "Hello"


@pytest.mark.asyncio
async def test_subscribe_returns_id(pubsub):
    callback = AsyncMock()
    sub_id = await pubsub.subscribe("agent-chat", callback)
    assert sub_id is not None
    assert len(sub_id) == 36  # UUID4 format


@pytest.mark.asyncio
async def test_multiple_subscribers(pubsub):
    cb1 = AsyncMock()
    cb2 = AsyncMock()
    id1 = await pubsub.subscribe("agent-chat", cb1)
    id2 = await pubsub.subscribe("agent-chat", cb2)
    assert id1 != id2
    assert len(pubsub._subscriptions.get("agent-chat", {})) == 2


@pytest.mark.asyncio
async def test_unsubscribe(pubsub):
    callback = AsyncMock()
    sub_id = await pubsub.subscribe("agent-chat", callback)
    await pubsub.unsubscribe(sub_id)
    assert "agent-chat" not in pubsub._subscriptions


@pytest.mark.asyncio
async def test_subscribe_multiple_channels(pubsub):
    callback = AsyncMock()
    ids = await pubsub.subscribe_multiple(["agent-chat", "market-intel", "trade-signals"], callback)
    assert len(ids) == 3
    assert len(pubsub._subscriptions) == 3


@pytest.mark.asyncio
async def test_shutdown(pubsub):
    callback = AsyncMock()
    await pubsub.subscribe("agent-chat", callback)
    await pubsub.subscribe("market-intel", callback)

    await pubsub.shutdown()
    assert len(pubsub._subscriptions) == 0
    assert pubsub.pubsub is None
    assert pubsub._listener_task is None
