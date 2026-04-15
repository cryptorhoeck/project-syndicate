"""
Project Syndicate — System API Fragment Routes

Returns HTML fragments for system status page and nav status pill.
"""

__version__ = "0.8.0"

from datetime import datetime, timezone

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from sqlalchemy import func, select

from src.web.dependencies import format_utc_timestamp

from src.common.models import (
    Agent,
    AgentCycle,
    GamingFlag,
    IntelSignal,
    Message,
    ReviewRequest,
    SystemState,
    Transaction,
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
            "last_activity": format_utc_timestamp(genesis_last),
            "last_activity_ago": _ago(genesis_last),
            "interval": "5min",
            "healthy": _healthy(genesis_last, 600),
        },
        {
            "name": "Warden",
            "last_activity": format_utc_timestamp(updated_at),
            "last_activity_ago": _ago(updated_at),
            "interval": "30s",
            "healthy": _healthy(updated_at, 120),
        },
        {
            "name": "Heartbeat",
            "last_activity": format_utc_timestamp(heartbeat_at),
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
                "timestamp": format_utc_timestamp(r.timestamp),
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


@router.get("/cost-optimization", response_class=HTMLResponse)
async def cost_optimization(request: Request):
    """Cost optimization stats panel for the system dashboard."""
    factory = request.app.state.db_session_factory

    with factory() as session:
        now = datetime.now(timezone.utc)
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

        # Model distribution today
        try:
            haiku_count = session.execute(
                select(func.count()).select_from(AgentCycle).where(
                    AgentCycle.timestamp >= today_start,
                    AgentCycle.model_used.like("%haiku%"),
                )
            ).scalar() or 0
        except Exception:
            haiku_count = 0

        try:
            sonnet_count = session.execute(
                select(func.count()).select_from(AgentCycle).where(
                    AgentCycle.timestamp >= today_start,
                    AgentCycle.model_used.like("%sonnet%"),
                )
            ).scalar() or 0
        except Exception:
            sonnet_count = 0

        total_cycles = haiku_count + sonnet_count
        haiku_pct = round(haiku_count / total_cycles * 100, 1) if total_cycles > 0 else 0

        # Average cost per cycle
        try:
            avg_cost = session.execute(
                select(func.avg(AgentCycle.api_cost_usd)).where(
                    AgentCycle.timestamp >= today_start,
                    AgentCycle.api_cost_usd > 0,
                )
            ).scalar() or 0.0
        except Exception:
            avg_cost = 0.0

        # Today's total spend
        today_spend = session.execute(
            select(func.coalesce(func.sum(Transaction.amount), 0.0)).where(
                Transaction.type == "api_cost",
                Transaction.timestamp >= today_start,
            )
        ).scalar() or 0.0

        # Estimated savings (tokens * Sonnet rate - actual spend)
        try:
            total_input = session.execute(
                select(func.coalesce(func.sum(AgentCycle.input_tokens), 0)).where(
                    AgentCycle.timestamp >= today_start,
                )
            ).scalar() or 0
            total_output = session.execute(
                select(func.coalesce(func.sum(AgentCycle.output_tokens), 0)).where(
                    AgentCycle.timestamp >= today_start,
                )
            ).scalar() or 0
        except Exception:
            total_input = 0
            total_output = 0

        sonnet_baseline = (total_input / 1_000_000) * 3.0 + (total_output / 1_000_000) * 15.0
        savings = max(0, round(sonnet_baseline - float(today_spend), 4))

        # All-time savings
        total_spend_all = session.execute(
            select(func.coalesce(func.sum(Transaction.amount), 0.0)).where(
                Transaction.type == "api_cost",
            )
        ).scalar() or 0.0
        try:
            total_input_all = session.execute(
                select(func.coalesce(func.sum(AgentCycle.input_tokens), 0))
            ).scalar() or 0
            total_output_all = session.execute(
                select(func.coalesce(func.sum(AgentCycle.output_tokens), 0))
            ).scalar() or 0
        except Exception:
            total_input_all = 0
            total_output_all = 0

        sonnet_baseline_all = (total_input_all / 1_000_000) * 3.0 + (total_output_all / 1_000_000) * 15.0
        savings_all = max(0, round(sonnet_baseline_all - float(total_spend_all), 4))

    return HTMLResponse(
        f'<div class="grid grid-cols-2 md:grid-cols-4 gap-4 p-4">'
        f'<div class="text-center">'
        f'  <div class="text-xs text-slate-400">Model Distribution</div>'
        f'  <div class="font-mono text-sm text-slate-200">Haiku: {haiku_count} | Sonnet: {sonnet_count}</div>'
        f'  <div class="text-xs text-emerald-400">{haiku_pct}% Haiku</div>'
        f'</div>'
        f'<div class="text-center">'
        f'  <div class="text-xs text-slate-400">Avg Cost/Cycle</div>'
        f'  <div class="font-mono text-lg text-slate-200">C${float(avg_cost):.4f}</div>'
        f'</div>'
        f'<div class="text-center">'
        f'  <div class="text-xs text-slate-400">Savings Today</div>'
        f'  <div class="font-mono text-lg text-emerald-400">C${savings:.4f}</div>'
        f'</div>'
        f'<div class="text-center">'
        f'  <div class="text-xs text-slate-400">Savings All-Time</div>'
        f'  <div class="font-mono text-lg text-emerald-400">C${savings_all:.2f}</div>'
        f'</div>'
        f'</div>'
    )


@router.get("/topbar", response_class=HTMLResponse)
async def topbar_vitals(request: Request):
    """System vitals for the sticky top bar."""
    templates = request.app.state.templates
    factory = request.app.state.db_session_factory

    ctx = {"request": request}
    try:
        with factory() as session:
            state = session.execute(select(SystemState)).scalars().first()
            if state:
                ctx["treasury_balance"] = state.total_treasury or 0.0
                ctx["alert_status"] = state.alert_status or "green"
                ctx["current_regime"] = state.current_regime or "unknown"
                ctx["active_agent_count"] = state.active_agent_count or 0
            else:
                ctx["treasury_balance"] = 0.0
                ctx["alert_status"] = "green"
                ctx["current_regime"] = "unknown"
                ctx["active_agent_count"] = 0
    except Exception:
        ctx["treasury_balance"] = 0.0
        ctx["alert_status"] = "green"
        ctx["current_regime"] = "unknown"
        ctx["active_agent_count"] = 0

    return templates.TemplateResponse(
        "fragments/topbar_vitals.html", ctx
    )


@router.get("/constellation")
async def constellation_data(request: Request):
    """JSON endpoint for the constellation ecosystem view."""
    from fastapi.responses import JSONResponse
    factory = request.app.state.db_session_factory

    with factory() as session:
        agents = list(
            session.execute(
                select(Agent).where(
                    Agent.id != 0,
                    Agent.status.in_(["active", "hibernating"]),
                )
            ).scalars().all()
        )

        agent_list = []
        for a in agents:
            dynasty_name = "House of Genesis"
            if a.dynasty_id:
                try:
                    from src.common.models import Dynasty
                    dynasty = session.get(Dynasty, a.dynasty_id)
                    if dynasty and dynasty.founder_name:
                        dynasty_name = f"House of {dynasty.founder_name}"
                except Exception:
                    pass

            agent_list.append({
                "id": a.id,
                "name": a.name,
                "role": a.type,
                "dynasty": dynasty_name,
                "composite_score": int((a.composite_score or 0) * 100) if (a.composite_score or 0) <= 1 else int(a.composite_score or 0),
                "status": a.status,
            })

        # Dynasty connections
        connections = []
        for i, a1 in enumerate(agent_list):
            for j, a2 in enumerate(agent_list):
                if j <= i:
                    continue
                if a1["dynasty"] == a2["dynasty"] and a1["dynasty"]:
                    connections.append({
                        "from": a1["id"],
                        "to": a2["id"],
                        "type": "dynasty",
                        "strength": 1.0,
                    })

    return JSONResponse({
        "agents": agent_list,
        "connections": connections,
    })
