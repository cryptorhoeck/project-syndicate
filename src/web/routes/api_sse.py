"""
Project Syndicate — Server-Sent Events for Live Feed

Streams Agora messages as real-time SSE events to the Command Center.
"""

__version__ = "0.1.0"

import asyncio
import json
from datetime import datetime, timezone

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from sqlalchemy import select, func

from src.common.models import Message

router = APIRouter()

# Event type mapping: channel + message_type → icon + color + is_major
EVENT_MAP = {
    ("trade-signals", "signal"): ("⚡", "#00e676", False),
    ("trade-signals", "trade"): ("⚡", "#00e676", False),
    ("trade-results", "trade"): ("⚡", "#00e676", False),
    ("market-intel", "signal"): ("◎", "#00e5ff", False),
    ("market-intel", "intel"): ("◎", "#00e5ff", False),
    ("strategy-proposals", "proposal"): ("◈", "#ffb300", False),
    ("strategy-debate", "evaluation"): ("◆", "#ff3d3d", False),
    ("system-alerts", "alert"): ("⚠", "#ff3d3d", True),
    ("genesis-log", "evaluation"): ("◇", "#8892b0", False),
    ("genesis-log", "system"): ("◇", "#8892b0", False),
    ("agent-chat", "chat"): ("◌", "#8892b0", False),
    ("agent-activity", "system"): ("◐", "#b388ff", False),
}

# Defaults by channel
CHANNEL_DEFAULTS = {
    "trade-signals": ("⚡", "#00e676", False),
    "trade-results": ("⚡", "#00e676", False),
    "market-intel": ("◎", "#00e5ff", False),
    "strategy-proposals": ("◈", "#ffb300", False),
    "strategy-debate": ("◆", "#ff3d3d", False),
    "system-alerts": ("⚠", "#ffb300", True),
    "genesis-log": ("◇", "#8892b0", False),
    "agent-chat": ("◌", "#8892b0", False),
    "agent-activity": ("◐", "#b388ff", False),
    "sip-proposals": ("◈", "#b388ff", False),
    "daily-report": ("◇", "#8892b0", False),
}


def _format_event(msg) -> dict:
    """Format a database Message row as an SSE event dict."""
    channel = msg.channel or ""
    msg_type = msg.message_type or "system"

    icon, color, is_major = EVENT_MAP.get(
        (channel, msg_type),
        CHANNEL_DEFAULTS.get(channel, ("◇", "#8892b0", False))
    )

    # Detect major events from content
    content = msg.content or ""
    content_lower = content.lower()
    if "terminated" in content_lower or "death" in content_lower:
        icon, color, is_major = "☠", "#ff3d3d", True
    elif "spawned" in content_lower or "offspring" in content_lower or "reproduction" in content_lower:
        icon, color, is_major = "✦", "#b388ff", True
    elif "circuit breaker" in content_lower:
        icon, color, is_major = "⚠", "#ff3d3d", True
    elif "black swan" in content_lower:
        icon, color, is_major = "⚠", "#ff3d3d", True

    # Time ago
    now = datetime.now(timezone.utc)
    ts = msg.timestamp
    if ts and ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    if ts:
        delta = (now - ts).total_seconds()
        if delta < 60:
            time_str = f"{int(delta)}s ago"
        elif delta < 3600:
            time_str = f"{int(delta // 60)}m ago"
        else:
            time_str = f"{int(delta // 3600)}h ago"
    else:
        time_str = "now"

    return {
        "id": msg.id,
        "type": msg_type,
        "icon": icon,
        "color": color,
        "text": f"{msg.agent_name or 'System'}: {content[:200]}",
        "time": time_str,
        "timestamp": str(ts) if ts else "",
        "is_major": is_major,
    }


@router.get("/stream")
async def event_stream(request: Request):
    """SSE endpoint for the live activity feed."""
    factory = request.app.state.db_session_factory

    async def generate():
        last_id = 0

        # Seed with recent messages
        try:
            with factory() as session:
                recent = list(
                    session.execute(
                        select(Message)
                        .order_by(Message.id.desc())
                        .limit(20)
                    ).scalars().all()
                )
                if recent:
                    last_id = recent[0].id
                    for msg in reversed(recent):
                        event = _format_event(msg)
                        yield f"data: {json.dumps(event)}\n\n"
        except Exception:
            pass

        # Poll for new messages
        while True:
            await asyncio.sleep(2)
            try:
                with factory() as session:
                    new_msgs = list(
                        session.execute(
                            select(Message)
                            .where(Message.id > last_id)
                            .order_by(Message.id.asc())
                            .limit(10)
                        ).scalars().all()
                    )
                    for msg in new_msgs:
                        last_id = msg.id
                        event = _format_event(msg)
                        yield f"data: {json.dumps(event)}\n\n"
            except Exception:
                pass

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
