"""
Project Syndicate — Agora API Fragment Routes

Returns HTML fragments for HTMX to swap into the Agora page.
"""

__version__ = "0.6.0"

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from sqlalchemy import func, select

from src.common.models import AgoraChannel, Agent, Message

router = APIRouter()


def _message_to_dict(row: Message, agent_type_map: dict) -> dict:
    """Convert a Message row to a template-friendly dict."""
    return {
        "id": row.id,
        "agent_name": row.agent_name or "System",
        "agent_type": agent_type_map.get(row.agent_id, "system"),
        "channel": row.channel,
        "content": row.content,
        "message_type": row.message_type or "chat",
        "importance": row.importance or 0,
        "timestamp": str(row.timestamp) if row.timestamp else "",
    }


def _build_agent_type_map(session, agent_ids: set) -> dict:
    """Bulk-fetch agent types for a set of IDs."""
    if not agent_ids:
        return {}
    rows = session.execute(
        select(Agent.id, Agent.type).where(Agent.id.in_(agent_ids))
    ).all()
    return {r[0]: r[1] for r in rows}


@router.get("/messages", response_class=HTMLResponse)
async def agora_messages(
    request: Request,
    channel: str = "",
    type: str = "",
    importance: int = 0,
    since: str = "",
    limit: int = 50,
):
    templates = request.app.state.templates
    factory = request.app.state.db_session_factory

    with factory() as session:
        stmt = select(Message)

        if channel:
            stmt = stmt.where(Message.channel == channel)

        if type:
            stmt = stmt.where(Message.message_type == type)

        if importance > 0:
            stmt = stmt.where(Message.importance >= importance)

        if since:
            try:
                since_dt = datetime.fromisoformat(since)
                stmt = stmt.where(Message.timestamp > since_dt)
            except ValueError:
                pass

        # Exclude expired
        now = datetime.now(timezone.utc)
        stmt = stmt.where(
            (Message.expires_at.is_(None)) | (Message.expires_at > now)
        )

        stmt = stmt.order_by(Message.timestamp.desc()).limit(limit)
        rows = list(session.execute(stmt).scalars().all())

        agent_ids = {r.agent_id for r in rows if r.agent_id}
        type_map = _build_agent_type_map(session, agent_ids)

        messages = [_message_to_dict(r, type_map) for r in rows]

    return templates.TemplateResponse(
        "fragments/agora_messages.html",
        {"request": request, "messages": messages},
    )


@router.get("/channels", response_class=HTMLResponse)
async def agora_channels(request: Request):
    templates = request.app.state.templates
    factory = request.app.state.db_session_factory

    with factory() as session:
        channels = list(
            session.execute(
                select(AgoraChannel).order_by(AgoraChannel.name)
            ).scalars().all()
        )
        channel_list = [
            {"name": ch.name, "message_count": ch.message_count or 0}
            for ch in channels
        ]

    return templates.TemplateResponse(
        "fragments/agora_channels.html",
        {"request": request, "channels": channel_list, "active_channel": ""},
    )
