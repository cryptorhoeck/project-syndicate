"""
Project Syndicate — The Agora Package

Central nervous system of the Syndicate.
All agent communication flows through here.
"""

__version__ = "0.3.0"

from src.agora.agora_service import AgoraService
from src.agora.pubsub import AgoraPubSub
from src.agora.schemas import (
    AgoraMessage,
    AgoraMessageResponse,
    ChannelInfo,
    MessageType,
    ReadReceipt,
)


async def create_agora_service(db_session_factory, redis_client) -> AgoraService:
    """Factory function to create a fully initialized AgoraService."""
    pubsub = AgoraPubSub(redis_client)
    service = AgoraService(db_session_factory, redis_client, pubsub)
    return service


__all__ = [
    "AgoraService",
    "AgoraPubSub",
    "AgoraMessage",
    "AgoraMessageResponse",
    "ChannelInfo",
    "MessageType",
    "ReadReceipt",
    "create_agora_service",
]
