"""
Project Syndicate — Agents API Fragment Routes

Returns HTML fragments for Agent cards and detail views.
"""

__version__ = "0.6.0"

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from sqlalchemy import select

from src.common.models import Agent, Message, ReputationTransaction

router = APIRouter()


@router.get("/cards", response_class=HTMLResponse)
async def agent_cards(request: Request, include_dead: bool = False):
    templates = request.app.state.templates
    factory = request.app.state.db_session_factory

    with factory() as session:
        stmt = select(Agent).where(Agent.id != 0)
        if not include_dead:
            stmt = stmt.where(Agent.status.in_(["active", "hibernating"]))
        stmt = stmt.order_by(Agent.composite_score.desc())
        rows = list(session.execute(stmt).scalars().all())

        agents = [
            {
                "id": a.id,
                "name": a.name,
                "type": a.type,
                "status": a.status,
                "generation": a.generation,
                "prestige_title": a.prestige_title,
                "total_true_pnl": a.total_true_pnl or 0.0,
                "reputation_score": a.reputation_score or 0.0,
                "composite_score": a.composite_score or 0.0,
            }
            for a in rows
        ]

    return templates.TemplateResponse(
        "fragments/agent_cards.html",
        {"request": request, "agents": agents},
    )


@router.get("/{agent_id}/messages", response_class=HTMLResponse)
async def agent_messages(request: Request, agent_id: int, limit: int = 20):
    templates = request.app.state.templates
    factory = request.app.state.db_session_factory

    with factory() as session:
        agent = session.get(Agent, agent_id)
        agent_type = agent.type if agent else "system"

        rows = list(
            session.execute(
                select(Message)
                .where(Message.agent_id == agent_id)
                .order_by(Message.timestamp.desc())
                .limit(limit)
            ).scalars().all()
        )

        messages = [
            {
                "id": r.id,
                "agent_name": r.agent_name or "Unknown",
                "agent_type": agent_type,
                "channel": r.channel,
                "content": r.content,
                "message_type": r.message_type or "chat",
                "importance": r.importance or 0,
                "timestamp": str(r.timestamp) if r.timestamp else "",
            }
            for r in rows
        ]

    return templates.TemplateResponse(
        "fragments/agora_messages.html",
        {"request": request, "messages": messages},
    )


@router.get("/{agent_id}/reputation", response_class=HTMLResponse)
async def agent_reputation(request: Request, agent_id: int, limit: int = 20):
    templates = request.app.state.templates
    factory = request.app.state.db_session_factory

    with factory() as session:
        rows = list(
            session.execute(
                select(ReputationTransaction)
                .where(
                    (ReputationTransaction.from_agent_id == agent_id)
                    | (ReputationTransaction.to_agent_id == agent_id)
                )
                .order_by(ReputationTransaction.timestamp.desc())
                .limit(limit)
            ).scalars().all()
        )

        if not rows:
            return HTMLResponse(
                '<div class="p-4 text-sm text-slate-500">No reputation transactions yet.</div>'
            )

        html_parts = []
        for t in rows:
            is_incoming = t.to_agent_id == agent_id and t.from_agent_id != agent_id
            amount_str = f"+{t.amount:.1f}" if is_incoming else f"-{t.amount:.1f}"
            color = "text-emerald-400" if is_incoming else "text-rose-400"
            html_parts.append(
                f'<div class="flex items-center justify-between px-4 py-2 border-b border-slate-700/50">'
                f'<span class="font-mono text-sm {color}">{amount_str}</span>'
                f'<span class="text-xs text-slate-400 truncate max-w-xs">{t.reason or "—"}</span>'
                f'<span class="font-mono text-xs text-slate-500" data-timestamp="{t.timestamp}">{t.timestamp}</span>'
                f'</div>'
            )

    return HTMLResponse("".join(html_parts))
