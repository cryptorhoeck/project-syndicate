"""
Project Syndicate — Dynasty & Memorial API Routes (Phase 3F)

JSON endpoints for dynasties, family trees, memorials, and dynasty analytics.
"""

__version__ = "1.2.0"

from fastapi import APIRouter
from fastapi.requests import Request
from fastapi.responses import JSONResponse

from sqlalchemy import select

from src.common.models import Agent, Dynasty, Lineage, Memorial

router = APIRouter()


@router.get("/")
async def list_dynasties(request: Request):
    """Get all dynasties, ordered by status then total P&L."""
    factory = request.app.state.db_session_factory

    with factory() as session:
        dynasties = session.execute(
            select(Dynasty).order_by(Dynasty.status.asc(), Dynasty.total_pnl.desc())
        ).scalars().all()

        return [
            {
                "id": d.id,
                "dynasty_name": d.dynasty_name,
                "founder_name": d.founder_name,
                "founder_role": d.founder_role,
                "status": d.status,
                "founded_at": str(d.founded_at) if d.founded_at else None,
                "extinct_at": str(d.extinct_at) if d.extinct_at else None,
                "total_generations": d.total_generations,
                "total_members": d.total_members,
                "living_members": d.living_members,
                "peak_members": d.peak_members,
                "total_pnl": round(d.total_pnl, 2),
                "avg_lifespan_days": round(d.avg_lifespan_days, 1) if d.avg_lifespan_days else None,
                "best_performer_pnl": round(d.best_performer_pnl, 2),
            }
            for d in dynasties
        ]


@router.get("/{dynasty_id}")
async def dynasty_detail(request: Request, dynasty_id: int):
    """Get dynasty detail with stats."""
    factory = request.app.state.db_session_factory

    with factory() as session:
        dynasty = session.get(Dynasty, dynasty_id)
        if not dynasty:
            return JSONResponse({"error": "Dynasty not found"}, status_code=404)

        return {
            "id": dynasty.id,
            "dynasty_name": dynasty.dynasty_name,
            "founder_name": dynasty.founder_name,
            "founder_role": dynasty.founder_role,
            "status": dynasty.status,
            "founded_at": str(dynasty.founded_at) if dynasty.founded_at else None,
            "extinct_at": str(dynasty.extinct_at) if dynasty.extinct_at else None,
            "total_generations": dynasty.total_generations,
            "total_members": dynasty.total_members,
            "living_members": dynasty.living_members,
            "peak_members": dynasty.peak_members,
            "total_pnl": round(dynasty.total_pnl, 2),
            "avg_lifespan_days": round(dynasty.avg_lifespan_days, 1) if dynasty.avg_lifespan_days else None,
            "best_performer_pnl": round(dynasty.best_performer_pnl, 2),
            "avg_generational_improvement": (
                round(dynasty.avg_generational_improvement, 3)
                if dynasty.avg_generational_improvement else None
            ),
        }


@router.get("/{dynasty_id}/tree")
async def dynasty_tree(request: Request, dynasty_id: int):
    """Get hierarchical tree structure for a dynasty."""
    factory = request.app.state.db_session_factory

    with factory() as session:
        from src.dynasty.lineage_manager import LineageManager
        mgr = LineageManager()
        tree = await mgr.get_family_tree(session, dynasty_id)
        return tree


@router.get("/{dynasty_id}/analytics")
async def dynasty_analytics(request: Request, dynasty_id: int):
    """Get dynasty performance report."""
    factory = request.app.state.db_session_factory

    with factory() as session:
        from src.dynasty.dynasty_analytics import DynastyAnalytics
        analytics = DynastyAnalytics()
        report = await analytics.dynasty_performance(session, dynasty_id)

        if not report:
            return JSONResponse({"error": "Dynasty not found"}, status_code=404)

        return {
            "dynasty_id": report.dynasty_id,
            "dynasty_name": report.dynasty_name,
            "status": report.status,
            "total_pnl": round(report.total_pnl, 2),
            "avg_lifespan_days": round(report.avg_lifespan_days, 1) if report.avg_lifespan_days else None,
            "total_members": report.total_members,
            "living_members": report.living_members,
            "total_generations": report.total_generations,
            "generational_improvement": round(report.generational_improvement, 4),
            "dominant_traits": report.dominant_traits,
            "market_focus": report.market_focus,
        }


@router.get("/memorials/all")
async def list_memorials(request: Request, limit: int = 50, offset: int = 0):
    """Get The Fallen — all memorial records, paginated."""
    factory = request.app.state.db_session_factory

    with factory() as session:
        memorials = session.execute(
            select(Memorial)
            .order_by(Memorial.created_at.desc())
            .offset(offset)
            .limit(limit)
        ).scalars().all()

        return [
            {
                "id": m.id,
                "agent_id": m.agent_id,
                "agent_name": m.agent_name,
                "agent_role": m.agent_role,
                "dynasty_name": m.dynasty_name,
                "generation": m.generation,
                "lifespan_days": round(m.lifespan_days, 1),
                "cause_of_death": m.cause_of_death,
                "total_cycles": m.total_cycles,
                "final_prestige": m.final_prestige,
                "best_metric_name": m.best_metric_name,
                "best_metric_value": round(m.best_metric_value, 3) if m.best_metric_value else None,
                "worst_metric_name": m.worst_metric_name,
                "worst_metric_value": round(m.worst_metric_value, 3) if m.worst_metric_value else None,
                "notable_achievement": m.notable_achievement,
                "final_pnl": round(m.final_pnl, 2),
                "epitaph": m.epitaph,
                "created_at": str(m.created_at) if m.created_at else None,
            }
            for m in memorials
        ]


@router.get("/memorials/{agent_id}")
async def memorial_detail(request: Request, agent_id: int):
    """Get single memorial for an agent."""
    factory = request.app.state.db_session_factory

    with factory() as session:
        memorial = session.execute(
            select(Memorial).where(Memorial.agent_id == agent_id)
        ).scalar_one_or_none()

        if not memorial:
            return JSONResponse({"error": "Memorial not found"}, status_code=404)

        return {
            "id": memorial.id,
            "agent_id": memorial.agent_id,
            "agent_name": memorial.agent_name,
            "agent_role": memorial.agent_role,
            "dynasty_name": memorial.dynasty_name,
            "generation": memorial.generation,
            "lifespan_days": round(memorial.lifespan_days, 1),
            "cause_of_death": memorial.cause_of_death,
            "total_cycles": memorial.total_cycles,
            "final_prestige": memorial.final_prestige,
            "best_metric_name": memorial.best_metric_name,
            "best_metric_value": round(memorial.best_metric_value, 3) if memorial.best_metric_value else None,
            "worst_metric_name": memorial.worst_metric_name,
            "worst_metric_value": round(memorial.worst_metric_value, 3) if memorial.worst_metric_value else None,
            "notable_achievement": memorial.notable_achievement,
            "final_pnl": round(memorial.final_pnl, 2),
            "epitaph": memorial.epitaph,
            "created_at": str(memorial.created_at) if memorial.created_at else None,
        }
