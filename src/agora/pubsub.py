"""
Project Syndicate — Agora Pub/Sub Manager

Clean abstraction over Redis pub/sub for real-time Agora message delivery.
Uses redis.asyncio for non-blocking operation.
"""

__version__ = "0.3.0"

import asyncio
import json
import uuid
from typing import Any, Callable, Optional

import redis.asyncio as aioredis
import structlog

logger = structlog.get_logger()


class AgoraPubSub:
    """Manages Redis pub/sub subscriptions for The Agora."""

    CHANNEL_PREFIX = "agora:"

    def __init__(self, redis_client: aioredis.Redis) -> None:
        self.redis = redis_client
        self.pubsub: Optional[aioredis.client.PubSub] = None
        self._subscriptions: dict[str, dict[str, Callable]] = {}  # {channel: {sub_id: callback}}
        self._listener_task: Optional[asyncio.Task] = None
        self.log = logger.bind(component="agora_pubsub")

    async def publish(self, channel: str, message: dict) -> None:
        """Publish a message to a Redis channel."""
        redis_channel = f"{self.CHANNEL_PREFIX}{channel}"
        payload = json.dumps(message, default=str)
        await self.redis.publish(redis_channel, payload)
        self.log.debug("pubsub_published", channel=channel, size=len(payload))

    async def subscribe(self, channel: str, callback: Callable) -> str:
        """Subscribe to a channel. Returns subscription_id."""
        sub_id = str(uuid.uuid4())
        redis_channel = f"{self.CHANNEL_PREFIX}{channel}"

        if channel not in self._subscriptions:
            self._subscriptions[channel] = {}
            # Subscribe to the Redis channel
            if self.pubsub is None:
                self.pubsub = self.redis.pubsub()
            await self.pubsub.subscribe(redis_channel)
            self.log.info("pubsub_subscribed", channel=channel)

        self._subscriptions[channel][sub_id] = callback

        # Start listener if not running
        if self._listener_task is None or self._listener_task.done():
            self._listener_task = asyncio.create_task(self._listener_loop())

        return sub_id

    async def unsubscribe(self, subscription_id: str) -> None:
        """Remove a subscription by id."""
        for channel, subs in list(self._subscriptions.items()):
            if subscription_id in subs:
                del subs[subscription_id]
                # If no more subscriptions for this channel, unsubscribe from Redis
                if not subs:
                    del self._subscriptions[channel]
                    if self.pubsub is not None:
                        redis_channel = f"{self.CHANNEL_PREFIX}{channel}"
                        await self.pubsub.unsubscribe(redis_channel)
                        self.log.info("pubsub_unsubscribed", channel=channel)
                return

    async def subscribe_multiple(self, channels: list[str], callback: Callable) -> list[str]:
        """Subscribe to multiple channels with a single callback."""
        sub_ids = []
        for ch in channels:
            sub_id = await self.subscribe(ch, callback)
            sub_ids.append(sub_id)
        return sub_ids

    async def _listener_loop(self) -> None:
        """Background task that reads Redis pub/sub and dispatches to callbacks."""
        if self.pubsub is None:
            return

        self.log.info("pubsub_listener_started")
        try:
            while self._subscriptions:
                try:
                    message = await self.pubsub.get_message(
                        ignore_subscribe_messages=True, timeout=1.0
                    )
                    if message is None:
                        await asyncio.sleep(0.1)
                        continue

                    if message["type"] != "message":
                        continue

                    redis_channel = message["channel"]
                    if isinstance(redis_channel, bytes):
                        redis_channel = redis_channel.decode("utf-8")

                    # Strip prefix to get Agora channel name
                    if redis_channel.startswith(self.CHANNEL_PREFIX):
                        channel = redis_channel[len(self.CHANNEL_PREFIX):]
                    else:
                        continue

                    # Deserialize payload
                    data = message["data"]
                    if isinstance(data, bytes):
                        data = data.decode("utf-8")
                    payload = json.loads(data)

                    # Dispatch to all callbacks for this channel
                    callbacks = self._subscriptions.get(channel, {})
                    for callback in callbacks.values():
                        try:
                            result = callback(payload)
                            if asyncio.iscoroutine(result):
                                await result
                        except Exception as exc:
                            self.log.error("pubsub_callback_error", channel=channel, error=str(exc))

                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    self.log.error("pubsub_listener_error", error=str(exc))
                    await asyncio.sleep(1)
        except asyncio.CancelledError:
            pass
        finally:
            self.log.info("pubsub_listener_stopped")

    async def shutdown(self) -> None:
        """Clean shutdown of all subscriptions."""
        if self._listener_task is not None and not self._listener_task.done():
            self._listener_task.cancel()
            try:
                await self._listener_task
            except asyncio.CancelledError:
                pass

        if self.pubsub is not None:
            await self.pubsub.unsubscribe()
            await self.pubsub.close()
            self.pubsub = None

        self._subscriptions.clear()
        self._listener_task = None
        self.log.info("pubsub_shutdown_complete")
