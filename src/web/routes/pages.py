"""
Project Syndicate — Full Page Routes

Each route queries data and renders a complete HTML page (base template + content).
Phase 6A: Command Center as home page.
"""

__version__ = "1.0.0"

import json

import markdown
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from sqlalchemy import func, select
from datetime import datetime, timedelta, timezone

from src.common.models import Agent, AgentCycle, Dynasty, Evaluation, LibraryEntry, Lineage, SystemState
from src.web.dependencies import get_common_context

router = APIRouter()


def _calc_rank_delta(agent, session) -> int:
    """Calculate rank change since last evaluation.

    Positive = improved (moved up), negative = declined.
    """
    try:
        last_eval = session.execute(
            select(Evaluation)
            .where(Evaluation.agent_id == agent.id, Evaluation.role_rank.isnot(None))
            .order_by(Evaluation.evaluated_at.desc())
            .limit(1)
        ).scalar_one_or_none()
        if not last_eval or last_eval.role_rank is None:
            return 0
        current_rank = agent.role_rank if hasattr(agent, "role_rank") and agent.role_rank else None
        if current_rank is None:
            return 0
        return last_eval.role_rank - current_rank  # positive = improved
    except Exception:
        return 0


def _build_agent_card_data(agent, session) -> dict:
    """Build enriched agent card data for Command Center display."""
    # Survival days
    survival_days = 14.0
    max_survival_days = 21
    if agent.survival_clock_end:
        end = agent.survival_clock_end
        if end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)
        remaining = (end - datetime.now(timezone.utc)).total_seconds() / 86400
        survival_days = max(0, remaining)
    if agent.status in ("terminated", "dead"):
        survival_days = 0

    if hasattr(agent, "default_survival_clock_days") and agent.default_survival_clock_days:
        max_survival_days = agent.default_survival_clock_days

    # Sparkline from recent cycle costs (last 20)
    try:
        cycles = list(
            session.execute(
                select(AgentCycle.api_cost_usd)
                .where(AgentCycle.agent_id == agent.id, AgentCycle.api_cost_usd > 0)
                .order_by(AgentCycle.cycle_number.desc())
                .limit(20)
            ).scalars().all()
        )
        sparkline = list(reversed(cycles)) if cycles else [50] * 20
        # Normalize to 0-100 range for display
        if len(sparkline) > 1:
            mn, mx = min(sparkline), max(sparkline)
            rng = mx - mn if mx != mn else 1
            sparkline = [((v - mn) / rng) * 80 + 10 for v in sparkline]
        while len(sparkline) < 20:
            sparkline.insert(0, 50)
    except Exception:
        sparkline = [50] * 20

    # Last cycle info
    try:
        last_cycle = session.execute(
            select(AgentCycle)
            .where(AgentCycle.agent_id == agent.id)
            .order_by(AgentCycle.cycle_number.desc())
            .limit(1)
        ).scalar_one_or_none()
        model_used = (last_cycle.model_used or "—") if last_cycle else "—"
        if "haiku" in model_used.lower():
            model_used = "Haiku"
        elif "sonnet" in model_used.lower():
            model_used = "Sonnet"
        last_cycle_cost = last_cycle.api_cost_usd if last_cycle else 0.0
        last_status = (last_cycle.action_type or "MONITORING").upper() if last_cycle else "IDLE"
        if last_status == "GO_IDLE":
            last_status = "IDLE"
        elif last_status == "BROADCAST_OPPORTUNITY":
            last_status = "HUNTING"
        elif last_status == "EXECUTE_TRADE":
            last_status = "EXECUTING"
        elif last_status == "REFLECTION":
            last_status = "REFLECTING"
        elif last_status in ("APPROVE_PLAN", "REJECT_PLAN", "REQUEST_REVISION"):
            last_status = "REVIEWING"
        elif last_status == "PROPOSE_PLAN":
            last_status = "PLANNING"
    except Exception:
        model_used = "—"
        last_cycle_cost = 0.0
        last_status = "IDLE"

    # Dynasty name
    dynasty_name = "House of Genesis"
    if agent.dynasty_id:
        try:
            dynasty = session.get(Dynasty, agent.dynasty_id)
            if dynasty and dynasty.founder_name:
                dynasty_name = f"House of {dynasty.founder_name}"
        except Exception:
            pass

    return {
        "id": agent.id,
        "name": agent.name,
        "type": agent.type,
        "status": agent.status,
        "generation": agent.generation,
        "prestige_title": agent.prestige_title or "Unproven",
        "total_true_pnl": agent.total_true_pnl or 0.0,
        "sharpe_ratio": getattr(agent, "sharpe_ratio", 0.0) or 0.0,
        "thinking_efficiency": getattr(agent, "thinking_efficiency", 0.0) or 0.0,
        "reputation_score": agent.reputation_score or 0.0,
        "composite_score": agent.composite_score or 0.0,
        "survival_days": survival_days,
        "max_survival_days": max_survival_days,
        "sparkline_data": sparkline,
        "model_used": model_used,
        "last_cycle_cost": last_cycle_cost,
        "last_status": last_status,
        "dynasty": dynasty_name,
        "rank_delta": _calc_rank_delta(agent, session)
    }


@router.get("/", response_class=HTMLResponse)
async def command_center(request: Request):
    """Command Center — the home page."""
    templates = request.app.state.templates
    ctx = get_common_context(request)
    ctx["current_page"] = "command_center"

    factory = request.app.state.db_session_factory

    with factory() as session:
        # Get all agents for cards (including terminated for display)
        rows = list(
            session.execute(
                select(Agent).where(Agent.id != 0)
                .order_by(Agent.composite_score.desc())
            ).scalars().all()
        )

        agents = [_build_agent_card_data(a, session) for a in rows]

        # Leaderboard: active only, sorted by composite
        leaderboard = [a for a in agents if a["status"] not in ("terminated", "dead")]

        # Constellation data
        constellation_agents = [
            {
                "id": a["id"],
                "name": a["name"],
                "role": a["type"],
                "dynasty": a["dynasty"],
                "composite_score": int(a["composite_score"] * 100) if a["composite_score"] <= 1 else int(a["composite_score"]),
                "status": a["status"],
            }
            for a in agents if a["status"] not in ("terminated", "dead")
        ]

        # System stats for status panel
        state = session.execute(select(SystemState)).scalars().first()

        # Cost optimization stats
        today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        try:
            haiku_count = session.execute(
                select(func.count()).select_from(AgentCycle).where(
                    AgentCycle.timestamp >= today_start,
                    AgentCycle.model_used.like("%haiku%"),
                )
            ).scalar() or 0
            sonnet_count = session.execute(
                select(func.count()).select_from(AgentCycle).where(
                    AgentCycle.timestamp >= today_start,
                    AgentCycle.model_used.like("%sonnet%"),
                )
            ).scalar() or 0
            total_cycles = haiku_count + sonnet_count
            haiku_ratio = round(haiku_count / total_cycles * 100) if total_cycles > 0 else 0

            avg_cost = session.execute(
                select(func.avg(AgentCycle.api_cost_usd)).where(
                    AgentCycle.timestamp >= today_start,
                    AgentCycle.api_cost_usd > 0,
                )
            ).scalar() or 0.0
        except Exception:
            haiku_ratio = 0
            avg_cost = 0.0

    ctx["agents"] = agents
    ctx["leaderboard_agents"] = leaderboard
    ctx["constellation_json"] = json.dumps(constellation_agents)
    ctx["haiku_ratio"] = haiku_ratio
    ctx["savings_today"] = 0.0
    ctx["avg_cost_cycle"] = float(avg_cost)

    return templates.TemplateResponse("pages/command_center.html", ctx)


@router.get("/agora", response_class=HTMLResponse)
async def agora_page(request: Request, channel: str = ""):
    templates = request.app.state.templates
    ctx = get_common_context(request)
    ctx["current_page"] = "agora"
    ctx["current_channel"] = channel
    return templates.TemplateResponse("pages/agora.html", ctx)


@router.get("/dashboard/wire", response_class=HTMLResponse)
@router.get("/wire", response_class=HTMLResponse)
async def wire_page(request: Request):
    """The Wire dashboard: live ticker, source health grid, treasury gauge."""
    templates = request.app.state.templates
    ctx = get_common_context(request)
    ctx["current_page"] = "wire"
    return templates.TemplateResponse("pages/wire.html", ctx)


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
