"""
Project Syndicate — Full Page Routes

Each route queries data and renders a complete HTML page (base template + content).
"""

__version__ = "0.6.0"

import markdown
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from sqlalchemy import func, select

from src.common.models import Agent, LibraryEntry, SystemState
from src.web.dependencies import get_common_context

router = APIRouter()


@router.get("/agora", response_class=HTMLResponse)
async def agora_page(request: Request, channel: str = ""):
    templates = request.app.state.templates
    ctx = get_common_context(request)
    ctx["current_page"] = "agora"
    ctx["current_channel"] = channel
    return templates.TemplateResponse("pages/agora.html", ctx)


@router.get("/leaderboard", response_class=HTMLResponse)
async def leaderboard_page(request: Request):
    templates = request.app.state.templates
    ctx = get_common_context(request)
    ctx["current_page"] = "leaderboard"
    return templates.TemplateResponse("pages/leaderboard.html", ctx)


@router.get("/library", response_class=HTMLResponse)
async def library_page(request: Request, category: str = "textbook"):
    templates = request.app.state.templates
    ctx = get_common_context(request)
    ctx["current_page"] = "library"
    ctx["active_category"] = category
    return templates.TemplateResponse("pages/library.html", ctx)


@router.get("/library/{entry_id}", response_class=HTMLResponse)
async def library_entry_page(request: Request, entry_id: int):
    templates = request.app.state.templates
    ctx = get_common_context(request)
    ctx["current_page"] = "library"

    factory = request.app.state.db_session_factory
    with factory() as session:
        entry = session.get(LibraryEntry, entry_id)
        if entry is None:
            from fastapi.responses import Response
            return Response(status_code=404, content="Entry not found")

        ctx["entry"] = {
            "id": entry.id,
            "title": entry.title,
            "category": entry.category,
            "content_html": markdown.markdown(entry.content or ""),
            "summary": entry.summary,
            "source_agent_name": entry.source_agent_name,
            "created_at": str(entry.created_at) if entry.created_at else "",
            "market_regime_at_creation": entry.market_regime_at_creation,
            "view_count": entry.view_count or 0,
            "tags": entry.tags if isinstance(entry.tags, list) else [],
        }

    return templates.TemplateResponse("pages/library_entry.html", ctx)


@router.get("/agents", response_class=HTMLResponse)
async def agents_page(request: Request):
    templates = request.app.state.templates
    ctx = get_common_context(request)
    ctx["current_page"] = "agents"

    factory = request.app.state.db_session_factory
    with factory() as session:
        active = session.execute(
            select(func.count()).select_from(Agent).where(Agent.status == "active", Agent.id != 0)
        ).scalar() or 0
        hibernating = session.execute(
            select(func.count()).select_from(Agent).where(Agent.status == "hibernating")
        ).scalar() or 0
        deceased = session.execute(
            select(func.count()).select_from(Agent).where(Agent.status == "terminated")
        ).scalar() or 0
        max_gen = session.execute(
            select(func.max(Agent.generation)).where(Agent.id != 0)
        ).scalar()

    ctx["stats"] = {
        "active": active,
        "hibernating": hibernating,
        "deceased": deceased,
        "generation_range": f"1-{max_gen}" if max_gen and max_gen > 1 else (str(max_gen) if max_gen else "--"),
    }

    return templates.TemplateResponse("pages/agents.html", ctx)


@router.get("/agents/{agent_id}", response_class=HTMLResponse)
async def agent_detail_page(request: Request, agent_id: int):
    templates = request.app.state.templates
    ctx = get_common_context(request)
    ctx["current_page"] = "agents"

    factory = request.app.state.db_session_factory
    with factory() as session:
        agent = session.get(Agent, agent_id)
        if agent is None:
            from fastapi.responses import Response
            return Response(status_code=404, content="Agent not found")

        # Build lineage text
        lineage_text = ""
        if agent.parent_id:
            parent = session.get(Agent, agent.parent_id)
            if parent:
                lineage_text = f"{parent.name} (Gen {parent.generation}) [{parent.status.upper()}]\n"
                lineage_text += f"  └── {agent.name} (Gen {agent.generation}) [{agent.status.upper()}] ← THIS AGENT"
        else:
            lineage_text = f"{agent.name} (Gen {agent.generation}) [{agent.status.upper()}]"

        # Build children
        children = session.execute(
            select(Agent).where(Agent.parent_id == agent_id)
        ).scalars().all()
        for child in children:
            lineage_text += f"\n      {'├' if child != children[-1] else '└'}── {child.name} (Gen {child.generation}) [{child.status.upper()}]"

        ctx["agent"] = {
            "id": agent.id,
            "name": agent.name,
            "type": agent.type,
            "status": agent.status,
            "generation": agent.generation,
            "prestige_title": agent.prestige_title,
            "total_true_pnl": agent.total_true_pnl or 0.0,
            "sharpe_ratio": getattr(agent, "sharpe_ratio", 0.0) or 0.0,
            "reputation_score": agent.reputation_score or 0.0,
            "composite_score": agent.composite_score or 0.0,
        }
        ctx["lineage"] = lineage_text

    return templates.TemplateResponse("pages/agent_detail.html", ctx)


@router.get("/system", response_class=HTMLResponse)
async def system_page(request: Request):
    templates = request.app.state.templates
    ctx = get_common_context(request)
    ctx["current_page"] = "system"

    factory = request.app.state.db_session_factory
    with factory() as session:
        state = session.execute(select(SystemState)).scalars().first()
        if state:
            peak = state.peak_treasury or 1.0
            current = state.total_treasury or 0.0
            ctx["reserve_pct"] = (current / peak * 100) if peak > 0 else 0
            ctx["circuit_breaker_pct"] = ((peak - current) / peak * 100) if peak > 0 else 0
        else:
            ctx["reserve_pct"] = 0
            ctx["circuit_breaker_pct"] = 0

    return templates.TemplateResponse("pages/system.html", ctx)
