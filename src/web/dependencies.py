"""
Project Syndicate — Web Dependencies

Shared dependencies for web routes. Provides DB sessions and service access.
All web usage is READ-ONLY — the frontend never modifies data.
"""

__version__ = "0.6.0"

from datetime import datetime, timezone

from fastapi import Request
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.common.models import Agent, SystemState


def format_utc_timestamp(ts) -> str:
    """Format a datetime as ISO 8601 with Z suffix for correct JS parsing.

    Database timestamps are stored as naive UTC. This ensures the browser's
    ``new Date(isoStr)`` interprets them as UTC rather than local time.
    """
    if ts is None:
        return ""
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.strftime("%Y-%m-%dT%H:%M:%SZ")


def get_db(request: Request):
    """Get a database session from the app state.

    Yields a session that is automatically closed after use.
    Use as a FastAPI dependency: ``db = Depends(get_db)``.
    """
    factory = request.app.state.db_session_factory
    session = factory()
    try:
        yield session
    finally:
        session.close()


def get_common_context(request: Request) -> dict:
    """Build the common template context shared across all pages."""
    ctx = {"request": request}
    factory = getattr(request.app.state, "db_session_factory", None)
    if factory is None:
        ctx.update(
            treasury_balance=0.0,
            active_agent_count=0,
            current_regime="unknown",
            alert_status="green",
        )
        return ctx

    try:
        with factory() as session:
            state = session.execute(select(SystemState)).scalars().first()
            if state:
                ctx["treasury_balance"] = state.total_treasury or 0.0
                ctx["active_agent_count"] = state.active_agent_count or 0
                ctx["current_regime"] = state.current_regime or "unknown"
                ctx["alert_status"] = state.alert_status or "green"
            else:
                ctx["treasury_balance"] = 0.0
                ctx["active_agent_count"] = 0
                ctx["current_regime"] = "unknown"
                ctx["alert_status"] = "green"
    except Exception:
        ctx.update(
            treasury_balance=0.0,
            active_agent_count=0,
            current_regime="unknown",
            alert_status="green",
        )

    return ctx


def get_agent_type(agent_id: int, request: Request) -> str:
    """Look up agent type for color coding."""
    factory = getattr(request.app.state, "db_session_factory", None)
    if factory is None:
        return "system"
    try:
        with factory() as session:
            agent = session.get(Agent, agent_id)
            return agent.type if agent else "system"
    except Exception:
        return "system"
