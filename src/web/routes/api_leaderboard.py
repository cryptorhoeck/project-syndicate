"""
Project Syndicate — Leaderboard API Fragment Routes

Returns HTML fragments for HTMX leaderboard tabs.
"""

__version__ = "0.6.0"

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from sqlalchemy import func, select

from src.common.models import Agent, CriticAccuracy, IntelSignal

router = APIRouter()


@router.get("/agents", response_class=HTMLResponse)
async def leaderboard_agents(
    request: Request,
    sort: str = "composite",
    order: str = "desc",
):
    templates = request.app.state.templates
    factory = request.app.state.db_session_factory

    sort_columns = {
        "composite": Agent.composite_score,
        "pnl": Agent.total_true_pnl,
        "reputation": Agent.reputation_score,
    }
    col = sort_columns.get(sort, Agent.composite_score)
    order_func = col.desc() if order == "desc" else col.asc()

    with factory() as session:
        rows = list(
            session.execute(
                select(Agent)
                .where(Agent.id != 0, Agent.status.in_(["active", "hibernating"]))
                .order_by(order_func)
                .limit(50)
            ).scalars().all()
        )

        agents = [
            {
                "id": a.id,
                "name": a.name,
                "type": a.type,
                "generation": a.generation,
                "prestige_title": a.prestige_title,
                "total_true_pnl": a.total_true_pnl or 0.0,
                "sharpe_ratio": getattr(a, "sharpe_ratio", 0.0) or 0.0,
                "reputation_score": a.reputation_score or 0.0,
                "composite_score": a.composite_score or 0.0,
                "status": a.status,
            }
            for a in rows
        ]

    return templates.TemplateResponse(
        "fragments/leaderboard_table.html",
        {"request": request, "agents": agents},
    )


@router.get("/intel", response_class=HTMLResponse)
async def leaderboard_intel(request: Request):
    templates = request.app.state.templates
    factory = request.app.state.db_session_factory

    with factory() as session:
        rows = session.execute(
            select(
                IntelSignal.scout_agent_id,
                IntelSignal.scout_agent_name,
                func.count().label("total"),
                func.sum(IntelSignal.endorsement_count).label("endorsed"),
            )
            .group_by(IntelSignal.scout_agent_id, IntelSignal.scout_agent_name)
            .order_by(func.count().desc())
            .limit(20)
        ).all()

        scouts = []
        for scout_id, name, total, endorsed in rows:
            # Count profitable
            profitable = session.execute(
                select(func.count()).select_from(IntelSignal).where(
                    IntelSignal.scout_agent_id == scout_id,
                    IntelSignal.status == "settled_profitable",
                )
            ).scalar() or 0
            scouts.append({
                "name": name or f"Agent-{scout_id}",
                "total_signals": total,
                "endorsed_count": endorsed or 0,
                "profitable_pct": (profitable / total * 100) if total > 0 else 0,
            })

    return templates.TemplateResponse(
        "fragments/intel_leaderboard.html",
        {"request": request, "scouts": scouts},
    )


@router.get("/critics", response_class=HTMLResponse)
async def leaderboard_critics(request: Request):
    templates = request.app.state.templates
    factory = request.app.state.db_session_factory

    with factory() as session:
        rows = list(
            session.execute(
                select(CriticAccuracy)
                .where(CriticAccuracy.total_reviews > 0)
                .order_by(CriticAccuracy.total_reviews.desc())
                .limit(20)
            ).scalars().all()
        )

        critics = []
        for c in rows:
            agent = session.get(Agent, c.critic_agent_id)
            name = agent.name if agent else f"Agent-{c.critic_agent_id}"
            total = c.total_reviews or 0
            approves = c.approve_count or 0
            critics.append({
                "name": name,
                "total_reviews": total,
                "approve_pct": (approves / total * 100) if total > 0 else 0,
            })

    return templates.TemplateResponse(
        "fragments/critic_leaderboard.html",
        {"request": request, "critics": critics},
    )


@router.get("/reputation", response_class=HTMLResponse)
async def leaderboard_reputation(request: Request):
    """Reputation rankings — reuses leaderboard_table with sort by reputation."""
    templates = request.app.state.templates
    factory = request.app.state.db_session_factory

    with factory() as session:
        rows = list(
            session.execute(
                select(Agent)
                .where(Agent.id != 0, Agent.status.in_(["active", "hibernating"]))
                .order_by(Agent.reputation_score.desc())
                .limit(50)
            ).scalars().all()
        )
        agents = [
            {
                "id": a.id, "name": a.name, "type": a.type,
                "generation": a.generation, "prestige_title": a.prestige_title,
                "total_true_pnl": a.total_true_pnl or 0.0,
                "sharpe_ratio": getattr(a, "sharpe_ratio", 0.0) or 0.0,
                "reputation_score": a.reputation_score or 0.0,
                "composite_score": a.composite_score or 0.0,
                "status": a.status,
            }
            for a in rows
        ]

    return templates.TemplateResponse(
        "fragments/leaderboard_table.html",
        {"request": request, "agents": agents},
    )


@router.get("/dynasties", response_class=HTMLResponse)
async def leaderboard_dynasties(request: Request):
    """Dynasty rankings — lineage performance."""
    templates = request.app.state.templates
    factory = request.app.state.db_session_factory

    with factory() as session:
        # Get root agents (generation 0 or 1, no parent)
        founders = list(
            session.execute(
                select(Agent)
                .where(Agent.parent_id.is_(None), Agent.id != 0)
                .order_by(Agent.composite_score.desc())
                .limit(20)
            ).scalars().all()
        )

        agents = [
            {
                "id": a.id, "name": a.name, "type": a.type,
                "generation": a.generation, "prestige_title": a.prestige_title,
                "total_true_pnl": a.total_true_pnl or 0.0,
                "sharpe_ratio": getattr(a, "sharpe_ratio", 0.0) or 0.0,
                "reputation_score": a.reputation_score or 0.0,
                "composite_score": a.composite_score or 0.0,
                "status": a.status,
            }
            for a in founders
        ]

    return templates.TemplateResponse(
        "fragments/leaderboard_table.html",
        {"request": request, "agents": agents},
    )
