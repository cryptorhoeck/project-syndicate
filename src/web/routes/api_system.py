"""
Project Syndicate — System API Fragment Routes

Returns HTML fragments for system status page and nav status pill.
"""

__version__ = "0.6.0"

from datetime import datetime, timezone

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from sqlalchemy import func, select

from src.common.models import (
    Agent,
    GamingFlag,
    IntelSignal,
    Message,
    ReviewRequest,
    SystemState,
)

router = APIRouter()


@router.get("/status-pill", response_class=HTMLResponse)
async def status_pill(request: Request):
    """Tiny status badge for the nav sidebar."""
    factory = request.app.state.db_session_factory

    with factory() as session:
        state = session.execute(select(SystemState)).scalars().first()
        alert = state.alert_status if state else "green"

    config = {
        "green": ("NOMINAL", "bg-emerald-500/20 text-emerald-400"),
        "yellow": ("YELLOW", "bg-amber-500/20 text-amber-400"),
        "red": ("RED", "bg-rose-500/20 text-rose-400"),
        "circuit_breaker": ("CIRCUIT BREAKER", "bg-rose-500/30 text-rose-400 flash-circuit"),
    }
    label, classes = config.get(alert, config["green"])
    return HTMLResponse(
        f'<span class="inline-flex items-center px-2 py-0.5 rounded text-xs font-mono font-medium {classes}">{label}</span>'
    )


@router.get("/status", response_class=HTMLResponse)
async def system_status(request: Request):
    templates = request.app.state.templates
    factory = request.app.state.db_session_factory

    with factory() as session:
        state = session.execute(select(SystemState)).scalars().first()
        alert = state.alert_status if state else "green"

    return templates.TemplateResponse(
        "fragments/system_status.html",
        {"request": request, "alert_status": alert},
    )


@router.get("/processes", response_class=HTMLResponse)
async def system_processes(request: Request):
    templates = request.app.state.templates
    factory = request.app.state.db_session_factory

    with factory() as session:
        state = session.execute(select(SystemState)).scalars().first()
        heartbeat_at = state.last_heartbeat_at if state else None
        updated_at = state.updated_at if state else None

        # Genesis: last message from agent 0
        genesis_last = session.execute(
            select(func.max(Message.timestamp)).where(Message.agent_id == 0)
        ).scalar()

    now = datetime.now(timezone.utc)

    def _ago(dt):
        if dt is None:
            return "never"
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        diff = now - dt
        secs = int(diff.total_seconds())
        if secs < 60:
            return f"{secs}s ago"
        if secs < 3600:
            return f"{secs // 60}m ago"
        return f"{secs // 3600}h ago"

    def _healthy(dt, max_seconds):
        if dt is None:
            return False
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (now - dt).total_seconds() < max_seconds

    processes = [
        {
            "name": "Genesis",
            "last_activity": str(genesis_last) if genesis_last else "",
            "last_activity_ago": _ago(genesis_last),
            "interval": "5min",
            "healthy": _healthy(genesis_last, 600),
        },
        {
            "name": "Warden",
            "last_activity": str(updated_at) if updated_at else "",
            "last_activity_ago": _ago(updated_at),
            "interval": "30s",
            "healthy": _healthy(updated_at, 120),
        },
        {
            "name": "Heartbeat",
            "last_activity": str(heartbeat_at) if heartbeat_at else "",
            "last_activity_ago": _ago(heartbeat_at),
            "interval": "10s",
            "healthy": _healthy(heartbeat_at, 30),
        },
    ]

    return templates.TemplateResponse(
        "fragments/process_health.html",
        {"request": request, "processes": processes},
    )


@router.get("/economy", response_class=HTMLResponse)
async def system_economy(request: Request):
    factory = request.app.state.db_session_factory

    with factory() as session:
        active_signals = session.execute(
            select(func.count()).select_from(IntelSignal).where(IntelSignal.status == "active")
        ).scalar() or 0

        open_reviews = session.execute(
            select(func.count()).select_from(ReviewRequest).where(ReviewRequest.status == "open")
        ).scalar() or 0

        gaming_flags = session.execute(
            select(func.count()).select_from(GamingFlag).where(GamingFlag.resolved == False)
        ).scalar() or 0

    flags_color = "text-rose-400 font-semibold" if gaming_flags > 0 else "text-slate-300"

    return HTMLResponse(
        f'<div class="grid grid-cols-3 gap-4 p-4">'
        f'<div class="text-center">'
        f'  <div class="text-xs text-slate-400">Active Signals</div>'
        f'  <div class="font-mono text-lg text-slate-200">{active_signals}</div>'
        f'</div>'
        f'<div class="text-center">'
        f'  <div class="text-xs text-slate-400">Open Reviews</div>'
        f'  <div class="font-mono text-lg text-slate-200">{open_reviews}</div>'
        f'</div>'
        f'<div class="text-center">'
        f'  <div class="text-xs text-slate-400">Gaming Flags</div>'
        f'  <div class="font-mono text-lg {flags_color}">{gaming_flags}</div>'
        f'</div>'
        f'</div>'
    )


@router.get("/alerts", response_class=HTMLResponse)
async def system_alerts(request: Request):
    templates = request.app.state.templates
    factory = request.app.state.db_session_factory

    with factory() as session:
        rows = list(
            session.execute(
                select(Message)
                .where(Message.channel == "system-alerts")
                .order_by(Message.timestamp.desc())
                .limit(20)
            ).scalars().all()
        )

        # Build agent type map
        agent_ids = {r.agent_id for r in rows if r.agent_id}
        type_map = {}
        if agent_ids:
            type_rows = session.execute(
                select(Agent.id, Agent.type).where(Agent.id.in_(agent_ids))
            ).all()
            type_map = {r[0]: r[1] for r in type_rows}

        messages = [
            {
                "id": r.id,
                "agent_name": r.agent_name or "System",
                "agent_type": type_map.get(r.agent_id, "system"),
                "channel": r.channel,
                "content": r.content,
                "message_type": r.message_type or "alert",
                "importance": r.importance or 0,
                "timestamp": str(r.timestamp) if r.timestamp else "",
            }
            for r in rows
        ]

    if not messages:
        return HTMLResponse(
            '<div class="p-4 text-sm text-slate-500">No alerts. System is running normally.</div>'
        )

    return templates.TemplateResponse(
        "fragments/agora_messages.html",
        {"request": request, "messages": messages},
    )
