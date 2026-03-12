"""
Project Syndicate — The Agora Service

Central nervous system of the Syndicate. Every agent thought, trade decision,
debate, and evaluation flows through The Agora.

- Real-time delivery via Redis pub/sub
- Persistent history via PostgreSQL
- Rate limiting per agent (Genesis exempt)
- Read receipts per agent per channel
- Channel management with system channel protection
"""

__version__ = "0.3.0"

import json
import re
from datetime import datetime, timezone
from typing import Optional

import redis.asyncio as aioredis
import structlog
from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import sessionmaker

from src.agora.pubsub import AgoraPubSub
from src.agora.schemas import (
    AgoraMessage,
    AgoraMessageResponse,
    ChannelInfo,
    MessageType,
    ReadReceipt,
)
from src.common.models import (
    AgoraChannel,
    AgoraReadReceipt,
    Message,
)

logger = structlog.get_logger()

# Valid channel name pattern: lowercase, alphanumeric, hyphens, max 50 chars
_CHANNEL_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9\-]{0,48}[a-z0-9]$")


class AgoraService:
    """The Agora — central communication service for all agents."""

    RATE_LIMIT = 10            # messages per window
    RATE_LIMIT_WINDOW = 300    # 5-minute window in seconds
    RATE_LIMIT_EXEMPT = [0]    # Genesis agent_id is exempt

    SYSTEM_CHANNELS = [
        "system-alerts",
        "genesis-log",
        "daily-report",
    ]

    def __init__(
        self,
        db_session_factory: sessionmaker,
        redis_client: aioredis.Redis,
        pubsub: Optional[AgoraPubSub] = None,
    ) -> None:
        self.db_session_factory = db_session_factory
        self.redis = redis_client
        self.pubsub = pubsub or AgoraPubSub(redis_client)
        self.log = logger.bind(component="agora")

    # ------------------------------------------------------------------
    # POSTING MESSAGES
    # ------------------------------------------------------------------

    async def post_message(self, message: AgoraMessage) -> AgoraMessageResponse:
        """Post a message to The Agora. Primary method for all communication."""

        # 1. Validate channel
        await self._ensure_channel_exists(message.channel)

        # 2. Rate limit check
        if message.agent_id not in self.RATE_LIMIT_EXEMPT:
            allowed = await self._check_rate_limit(message.agent_id)
            if not allowed:
                raise ValueError(
                    f"Rate limit exceeded for agent {message.agent_id}: "
                    f"max {self.RATE_LIMIT} messages per {self.RATE_LIMIT_WINDOW}s"
                )

        # 3. Check expiry
        if message.expires_at is not None:
            now = datetime.now(timezone.utc)
            expires = message.expires_at
            if expires.tzinfo is None:
                expires = expires.replace(tzinfo=timezone.utc)
            if expires <= now:
                raise ValueError("Message expires_at is in the past")

        # 4. Write to PostgreSQL
        with self.db_session_factory() as session:
            db_message = Message(
                agent_id=message.agent_id,
                channel=message.channel,
                content=message.content,
                metadata_json=message.metadata if message.metadata else None,
                message_type=message.message_type.value,
                agent_name=message.agent_name,
                parent_message_id=message.parent_message_id,
                importance=message.importance,
                expires_at=message.expires_at,
            )
            session.add(db_message)

            # Increment channel message count
            session.execute(
                update(AgoraChannel)
                .where(AgoraChannel.name == message.channel)
                .values(message_count=AgoraChannel.message_count + 1)
            )
            session.commit()

            response = AgoraMessageResponse(
                id=db_message.id,
                agent_id=db_message.agent_id or 0,
                agent_name=db_message.agent_name or "",
                channel=db_message.channel,
                content=db_message.content,
                message_type=db_message.message_type,
                metadata=db_message.metadata_json or {},
                importance=db_message.importance,
                parent_message_id=db_message.parent_message_id,
                timestamp=db_message.timestamp,
                expires_at=db_message.expires_at,
            )

        # 5. Publish to Redis pub/sub (fire-and-forget)
        try:
            await self.pubsub.publish(message.channel, response.model_dump(mode="json"))
        except Exception as exc:
            self.log.warning("pubsub_publish_failed", channel=message.channel, error=str(exc))

        # 6. Log
        self.log.info(
            "message_posted",
            agent_id=message.agent_id,
            agent_name=message.agent_name,
            channel=message.channel,
            message_type=message.message_type.value,
            content_length=len(message.content),
        )

        return response

    async def post_system_message(
        self,
        channel: str,
        content: str,
        metadata: Optional[dict] = None,
    ) -> AgoraMessageResponse:
        """Convenience method for system-level messages."""
        msg = AgoraMessage(
            agent_id=0,
            agent_name="System",
            channel=channel,
            content=content,
            message_type=MessageType.SYSTEM,
            metadata=metadata or {},
        )
        return await self.post_message(msg)

    # ------------------------------------------------------------------
    # READING MESSAGES
    # ------------------------------------------------------------------

    async def read_channel(
        self,
        channel: str,
        since: Optional[datetime] = None,
        limit: int = 50,
        message_types: Optional[list[MessageType]] = None,
        min_importance: int = 0,
        include_expired: bool = False,
    ) -> list[AgoraMessageResponse]:
        """Read messages from a channel with filtering."""
        with self.db_session_factory() as session:
            stmt = select(Message).where(Message.channel == channel)

            if since is not None:
                stmt = stmt.where(Message.timestamp > since)

            if message_types:
                type_values = [mt.value for mt in message_types]
                stmt = stmt.where(Message.message_type.in_(type_values))

            if min_importance > 0:
                stmt = stmt.where(Message.importance >= min_importance)

            if not include_expired:
                now = datetime.now(timezone.utc)
                stmt = stmt.where(
                    (Message.expires_at.is_(None)) | (Message.expires_at > now)
                )

            stmt = stmt.order_by(Message.timestamp.desc()).limit(limit)
            rows = session.execute(stmt).scalars().all()

            return [self._row_to_response(r) for r in rows]

    async def read_channel_since_last_read(
        self,
        agent_id: int,
        channel: str,
        limit: int = 50,
    ) -> list[AgoraMessageResponse]:
        """Read only NEW messages since this agent last read this channel."""
        with self.db_session_factory() as session:
            receipt = session.execute(
                select(AgoraReadReceipt).where(
                    AgoraReadReceipt.agent_id == agent_id,
                    AgoraReadReceipt.channel == channel,
                )
            ).scalar_one_or_none()

        since = receipt.last_read_at if receipt else None
        return await self.read_channel(channel=channel, since=since, limit=limit)

    async def read_multiple_channels(
        self,
        channels: list[str],
        since: Optional[datetime] = None,
        limit: int = 100,
    ) -> dict[str, list[AgoraMessageResponse]]:
        """Read from multiple channels at once."""
        result = {}
        for ch in channels:
            result[ch] = await self.read_channel(channel=ch, since=since, limit=limit)
        return result

    async def get_recent_activity(
        self,
        limit: int = 20,
        min_importance: int = 0,
    ) -> list[AgoraMessageResponse]:
        """Get the most recent messages across ALL channels."""
        with self.db_session_factory() as session:
            stmt = select(Message)
            now = datetime.now(timezone.utc)
            stmt = stmt.where(
                (Message.expires_at.is_(None)) | (Message.expires_at > now)
            )

            if min_importance > 0:
                stmt = stmt.where(Message.importance >= min_importance)

            stmt = stmt.order_by(Message.timestamp.desc()).limit(limit)
            rows = session.execute(stmt).scalars().all()
            return [self._row_to_response(r) for r in rows]

    async def search_messages(
        self,
        query: str,
        channel: Optional[str] = None,
        agent_id: Optional[int] = None,
        limit: int = 20,
    ) -> list[AgoraMessageResponse]:
        """Full-text search across Agora messages (basic ILIKE)."""
        with self.db_session_factory() as session:
            stmt = select(Message).where(Message.content.ilike(f"%{query}%"))

            if channel is not None:
                stmt = stmt.where(Message.channel == channel)
            if agent_id is not None:
                stmt = stmt.where(Message.agent_id == agent_id)

            stmt = stmt.order_by(Message.timestamp.desc()).limit(limit)
            rows = session.execute(stmt).scalars().all()
            return [self._row_to_response(r) for r in rows]

    # ------------------------------------------------------------------
    # READ RECEIPTS
    # ------------------------------------------------------------------

    async def mark_read(
        self,
        agent_id: int,
        channel: str,
        up_to_message_id: Optional[int] = None,
    ) -> ReadReceipt:
        """Mark a channel as read up to a specific message (or now)."""
        now = datetime.now(timezone.utc)

        with self.db_session_factory() as session:
            existing = session.execute(
                select(AgoraReadReceipt).where(
                    AgoraReadReceipt.agent_id == agent_id,
                    AgoraReadReceipt.channel == channel,
                )
            ).scalar_one_or_none()

            if existing:
                existing.last_read_at = now
                if up_to_message_id is not None:
                    existing.last_read_message_id = up_to_message_id
                session.commit()
                return ReadReceipt(
                    agent_id=existing.agent_id,
                    channel=existing.channel,
                    last_read_at=existing.last_read_at,
                    last_read_message_id=existing.last_read_message_id,
                )
            else:
                receipt = AgoraReadReceipt(
                    agent_id=agent_id,
                    channel=channel,
                    last_read_at=now,
                    last_read_message_id=up_to_message_id,
                )
                session.add(receipt)
                session.commit()
                return ReadReceipt(
                    agent_id=receipt.agent_id,
                    channel=receipt.channel,
                    last_read_at=receipt.last_read_at,
                    last_read_message_id=receipt.last_read_message_id,
                )

    async def get_unread_counts(self, agent_id: int) -> dict[str, int]:
        """Get count of unread messages per channel for an agent."""
        result = {}

        with self.db_session_factory() as session:
            channels = session.execute(select(AgoraChannel)).scalars().all()

            for ch in channels:
                receipt = session.execute(
                    select(AgoraReadReceipt).where(
                        AgoraReadReceipt.agent_id == agent_id,
                        AgoraReadReceipt.channel == ch.name,
                    )
                ).scalar_one_or_none()

                stmt = select(func.count()).select_from(Message).where(
                    Message.channel == ch.name
                )
                if receipt:
                    stmt = stmt.where(Message.timestamp > receipt.last_read_at)

                count = session.execute(stmt).scalar() or 0
                if count > 0:
                    result[ch.name] = count

        return result

    # ------------------------------------------------------------------
    # CHANNEL MANAGEMENT
    # ------------------------------------------------------------------

    async def get_channels(self) -> list[ChannelInfo]:
        """List all channels with metadata."""
        with self.db_session_factory() as session:
            channels = session.execute(
                select(AgoraChannel).order_by(AgoraChannel.name)
            ).scalars().all()

            result = []
            for ch in channels:
                # Get latest message timestamp
                latest_ts = session.execute(
                    select(func.max(Message.timestamp)).where(
                        Message.channel == ch.name
                    )
                ).scalar()

                result.append(ChannelInfo(
                    name=ch.name,
                    description=ch.description,
                    is_system=ch.is_system,
                    message_count=ch.message_count,
                    latest_message_at=latest_ts,
                ))

            return result

    async def get_channel_info(self, channel: str) -> Optional[ChannelInfo]:
        """Get info about a specific channel."""
        with self.db_session_factory() as session:
            ch = session.get(AgoraChannel, channel)
            if ch is None:
                return None

            latest_ts = session.execute(
                select(func.max(Message.timestamp)).where(
                    Message.channel == ch.name
                )
            ).scalar()

            return ChannelInfo(
                name=ch.name,
                description=ch.description,
                is_system=ch.is_system,
                message_count=ch.message_count,
                latest_message_at=latest_ts,
            )

    async def create_channel(self, name: str, description: str) -> ChannelInfo:
        """Create a new non-system channel."""
        if not _CHANNEL_NAME_RE.match(name):
            raise ValueError(
                f"Invalid channel name '{name}': must be lowercase alphanumeric + hyphens, 2-50 chars"
            )

        if name in self.SYSTEM_CHANNELS:
            raise ValueError(f"Cannot create system channel '{name}' through this method")

        with self.db_session_factory() as session:
            existing = session.get(AgoraChannel, name)
            if existing:
                raise ValueError(f"Channel '{name}' already exists")

            channel = AgoraChannel(
                name=name,
                description=description,
                is_system=False,
                message_count=0,
            )
            session.add(channel)
            session.commit()

        # Announce creation
        await self.post_system_message(
            "agent-chat",
            f"New channel created: {name} — {description}",
        )

        self.log.info("channel_created", name=name)
        return ChannelInfo(
            name=name,
            description=description,
            is_system=False,
            message_count=0,
            latest_message_at=None,
        )

    # ------------------------------------------------------------------
    # SUBSCRIPTIONS (delegate to PubSub)
    # ------------------------------------------------------------------

    async def subscribe(self, channel: str, callback) -> str:
        """Subscribe to real-time messages on a channel via Redis pub/sub."""
        return await self.pubsub.subscribe(channel, callback)

    async def unsubscribe(self, subscription_id: str) -> None:
        """Unsubscribe from a channel."""
        await self.pubsub.unsubscribe(subscription_id)

    async def subscribe_multiple(self, channels: list[str], callback) -> list[str]:
        """Subscribe to multiple channels with a single callback."""
        return await self.pubsub.subscribe_multiple(channels, callback)

    # ------------------------------------------------------------------
    # MAINTENANCE
    # ------------------------------------------------------------------

    async def cleanup_expired_messages(self) -> int:
        """Delete messages past their expires_at. Run periodically by Genesis."""
        now = datetime.now(timezone.utc)
        with self.db_session_factory() as session:
            result = session.execute(
                delete(Message).where(
                    Message.expires_at.is_not(None),
                    Message.expires_at < now,
                )
            )
            count = result.rowcount
            session.commit()

        if count > 0:
            self.log.info("expired_messages_cleaned", count=count)
        return count

    async def get_channel_stats(self) -> dict:
        """Get aggregate stats for monitoring. Used by daily report."""
        from datetime import timedelta
        cutoff = datetime.now(timezone.utc) - timedelta(hours=24)

        with self.db_session_factory() as session:
            # Total messages in last 24h
            total_24h = session.execute(
                select(func.count()).select_from(Message).where(
                    Message.timestamp >= cutoff
                )
            ).scalar() or 0

            # Messages per channel in last 24h
            per_channel = session.execute(
                select(Message.channel, func.count().label("count"))
                .where(Message.timestamp >= cutoff)
                .group_by(Message.channel)
                .order_by(func.count().desc())
            ).all()

            # Most active agents in last 24h
            active_agents = session.execute(
                select(Message.agent_name, func.count().label("count"))
                .where(Message.timestamp >= cutoff, Message.agent_name.is_not(None))
                .group_by(Message.agent_name)
                .order_by(func.count().desc())
                .limit(10)
            ).all()

        return {
            "total_messages_24h": total_24h,
            "per_channel": {row[0]: row[1] for row in per_channel},
            "most_active_agents": {row[0]: row[1] for row in active_agents},
        }

    # ------------------------------------------------------------------
    # INTERNAL HELPERS
    # ------------------------------------------------------------------

    async def _ensure_channel_exists(self, channel: str) -> None:
        """Auto-create non-system channels if they don't exist."""
        with self.db_session_factory() as session:
            existing = session.get(AgoraChannel, channel)
            if existing:
                return

        # Channel doesn't exist
        if channel in self.SYSTEM_CHANNELS:
            raise ValueError(f"System channel '{channel}' does not exist in database")

        # Auto-create non-system channel
        with self.db_session_factory() as session:
            ch = AgoraChannel(
                name=channel,
                description=f"Auto-created channel: {channel}",
                is_system=False,
                message_count=0,
            )
            session.add(ch)
            session.commit()
        self.log.info("channel_auto_created", channel=channel)

    async def _check_rate_limit(self, agent_id: int) -> bool:
        """Check and increment rate limit counter. Returns True if allowed."""
        key = f"agora:rate:{agent_id}"
        try:
            current = await self.redis.get(key)
            if current is not None and int(current) >= self.RATE_LIMIT:
                self.log.warning("rate_limit_hit", agent_id=agent_id)
                return False

            pipe = self.redis.pipeline()
            pipe.incr(key)
            pipe.expire(key, self.RATE_LIMIT_WINDOW)
            await pipe.execute()
            return True
        except Exception as exc:
            # If Redis is down, allow the message (PostgreSQL is the source of truth)
            self.log.warning("rate_limit_check_failed", error=str(exc))
            return True

    @staticmethod
    def _row_to_response(row: Message) -> AgoraMessageResponse:
        """Convert a SQLAlchemy Message row to an AgoraMessageResponse."""
        return AgoraMessageResponse(
            id=row.id,
            agent_id=row.agent_id or 0,
            agent_name=row.agent_name or "",
            channel=row.channel,
            content=row.content,
            message_type=row.message_type or "chat",
            metadata=row.metadata_json or {},
            importance=row.importance or 0,
            parent_message_id=row.parent_message_id,
            timestamp=row.timestamp,
            expires_at=row.expires_at,
        )
