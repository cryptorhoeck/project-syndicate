"""
Project Syndicate — Personality API Routes (Phase 3E)

JSON endpoints for behavioral profiles, relationships, divergence, and temperature history.
"""

__version__ = "1.1.0"

from fastapi import APIRouter
from fastapi.requests import Request
from fastapi.responses import JSONResponse

from sqlalchemy import select

from src.common.models import (
    Agent, AgentRelationship, BehavioralProfile, DivergenceScore,
)

router = APIRouter()


@router.get("/{agent_id}/profile")
async def agent_profile(request: Request, agent_id: int):
    """Get latest behavioral profile for an agent."""
    factory = request.app.state.db_session_factory

    with factory() as session:
        profile = session.execute(
            select(BehavioralProfile)
            .where(BehavioralProfile.agent_id == agent_id)
            .order_by(BehavioralProfile.created_at.desc())
            .limit(1)
        ).scalar_one_or_none()

        if not profile:
            return JSONResponse({"error": "No profile found"}, status_code=404)

        return {
            "agent_id": agent_id,
            "risk_appetite": {"score": profile.risk_appetite_score, "label": profile.risk_appetite_label},
            "market_focus": {"entropy": profile.market_focus_entropy, "data": profile.market_focus_data},
            "timing": {"heatmap": profile.timing_heatmap},
            "decision_style": {"score": profile.decision_style_score, "label": profile.decision_style_label},
            "collaboration": {"score": profile.collaboration_score, "label": profile.collaboration_label},
            "learning_velocity": {"score": profile.learning_velocity_score, "label": profile.learning_velocity_label},
            "resilience": {"score": profile.resilience_score, "label": profile.resilience_label},
            "is_complete": profile.is_complete,
            "dominant_regime": profile.dominant_regime,
            "regime_distribution": profile.regime_distribution,
            "created_at": str(profile.created_at) if profile.created_at else None,
        }


@router.get("/{agent_id}/relationships")
async def agent_relationships(request: Request, agent_id: int):
    """Get trust relationships for an agent."""
    factory = request.app.state.db_session_factory

    with factory() as session:
        rels = session.execute(
            select(AgentRelationship)
            .where(
                AgentRelationship.agent_id == agent_id,
                AgentRelationship.archived == False,
            )
            .order_by(AgentRelationship.trust_score.desc())
        ).scalars().all()

        return [
            {
                "target_agent_id": r.target_agent_id,
                "target_agent_name": r.target_agent_name,
                "trust_score": round(r.trust_score, 3),
                "interaction_count": r.interaction_count,
                "positive_outcomes": r.positive_outcomes,
                "negative_outcomes": r.negative_outcomes,
                "last_interaction_at": str(r.last_interaction_at) if r.last_interaction_at else None,
            }
            for r in rels
        ]


@router.get("/{agent_id}/temperature-history")
async def agent_temperature_history(request: Request, agent_id: int):
    """Get temperature evolution history for an agent."""
    factory = request.app.state.db_session_factory

    with factory() as session:
        agent = session.get(Agent, agent_id)
        if not agent:
            return JSONResponse({"error": "Agent not found"}, status_code=404)

        return {
            "agent_id": agent_id,
            "current_temperature": agent.api_temperature,
            "last_signal": agent.last_temperature_signal,
            "history": agent.temperature_history or [],
        }


@router.get("/divergence")
async def divergence_scores(request: Request, role: str | None = None, limit: int = 50):
    """Get latest divergence scores, optionally filtered by role."""
    factory = request.app.state.db_session_factory

    with factory() as session:
        stmt = select(DivergenceScore).order_by(DivergenceScore.computed_at.desc())
        if role:
            stmt = stmt.where(DivergenceScore.agent_a_role == role)
        stmt = stmt.limit(limit)

        scores = session.execute(stmt).scalars().all()

        return [
            {
                "agent_a_id": s.agent_a_id,
                "agent_b_id": s.agent_b_id,
                "role": s.agent_a_role,
                "divergence_score": round(s.divergence_score, 4),
                "comparable_metrics": s.comparable_metrics,
                "computed_at": str(s.computed_at) if s.computed_at else None,
            }
            for s in scores
        ]
