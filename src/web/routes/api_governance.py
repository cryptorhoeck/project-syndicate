"""
Project Syndicate — Governance API

Dashboard endpoints for SIP tracking, colony maturity, and parameter registry.
"""

__version__ = "0.1.0"

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import select, func
from sqlalchemy.orm import Session

from src.common.models import (
    ColonyMaturity, SystemImprovementProposal, SIPDebate,
    ParameterRegistryEntry, ParameterChangeLog,
)
from src.web.dependencies import get_db

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/api/governance/sips")
async def get_sip_status(db: Session = Depends(get_db)):
    """Returns governance data for the dashboard."""
    now = datetime.now(timezone.utc)

    # Colony maturity
    maturity_row = db.execute(select(ColonyMaturity).limit(1)).scalar_one_or_none()
    maturity = {
        "stage": maturity_row.stage if maturity_row else "nascent",
        "age_days": maturity_row.colony_age_days if maturity_row else 0,
        "max_generation": maturity_row.max_generation if maturity_row else 1,
        "sips_passed": maturity_row.total_sips_passed if maturity_row else 0,
        "active_agents": maturity_row.active_agent_count if maturity_row else 0,
    }

    # Active SIPs (in debate, voting, tallied, genesis_review, owner_review)
    active_statuses = ["debate", "voting", "tallied", "genesis_review", "owner_review", "implementing"]
    active_sips_rows = db.execute(
        select(SystemImprovementProposal).where(
            SystemImprovementProposal.lifecycle_status.in_(active_statuses)
        ).order_by(SystemImprovementProposal.proposed_at.desc())
    ).scalars().all()

    active_sips = []
    for sip in active_sips_rows:
        # Calculate time remaining
        time_remaining = None
        if sip.lifecycle_status == "debate" and sip.debate_ends_at:
            delta = sip.debate_ends_at.replace(tzinfo=timezone.utc) - now
            if delta.total_seconds() > 0:
                hours = int(delta.total_seconds() // 3600)
                mins = int((delta.total_seconds() % 3600) // 60)
                time_remaining = f"{hours}h {mins}m"
            else:
                time_remaining = "advancing..."
        elif sip.lifecycle_status == "voting" and sip.voting_ends_at:
            delta = sip.voting_ends_at.replace(tzinfo=timezone.utc) - now
            if delta.total_seconds() > 0:
                hours = int(delta.total_seconds() // 3600)
                mins = int((delta.total_seconds() % 3600) // 60)
                time_remaining = f"{hours}h {mins}m"
            else:
                time_remaining = "tallying..."

        # Debate count
        debate_count = db.execute(
            select(func.count()).select_from(SIPDebate).where(
                SIPDebate.sip_id == sip.id
            )
        ).scalar() or 0

        vote_pct = None
        if sip.vote_pass_percentage is not None:
            vote_pct = round(sip.vote_pass_percentage * 100, 1)

        active_sips.append({
            "id": sip.id,
            "title": sip.title,
            "proposer": sip.proposer_agent_name,
            "lifecycle_status": sip.lifecycle_status,
            "time_remaining": time_remaining,
            "target_parameter": sip.target_parameter_key,
            "proposed_value": sip.proposed_value,
            "debate_count": debate_count,
            "support_count": sip.support_count or 0,
            "oppose_count": sip.oppose_count or 0,
            "vote_pct": vote_pct,
            "parameter_tier": sip.parameter_tier,
        })

    # Recent outcomes (last 10 resolved)
    recent_rows = db.execute(
        select(SystemImprovementProposal).where(
            SystemImprovementProposal.lifecycle_status.in_([
                "implemented", "rejected_by_vote", "vetoed_by_genesis",
                "rejected_by_owner", "expired",
            ])
        ).order_by(SystemImprovementProposal.resolved_at.desc()).limit(10)
    ).scalars().all()

    recent_outcomes = [
        {
            "id": sip.id,
            "title": sip.title,
            "outcome": sip.lifecycle_status,
            "vote_pct": round(sip.vote_pass_percentage * 100, 1) if sip.vote_pass_percentage else None,
            "resolved_at": sip.resolved_at.isoformat() if sip.resolved_at else None,
        }
        for sip in recent_rows
    ]

    # Drift summary (last 30 days)
    from datetime import timedelta
    cutoff = now - timedelta(days=30)
    changes = db.execute(
        select(ParameterChangeLog).where(
            ParameterChangeLog.changed_at >= cutoff
        )
    ).scalars().all()

    softer = sum(1 for c in changes if c.drift_direction == "softer")
    harder = sum(1 for c in changes if c.drift_direction == "harder")

    return {
        "maturity": maturity,
        "active_sips": active_sips,
        "recent_outcomes": recent_outcomes,
        "drift": {
            "softer": softer,
            "harder": harder,
            "alert": softer > harder + 2,
        },
    }


@router.get("/api/governance/parameters")
async def get_parameters(db: Session = Depends(get_db)):
    """Returns all parameters in the registry."""
    rows = db.execute(
        select(ParameterRegistryEntry).order_by(
            ParameterRegistryEntry.tier,
            ParameterRegistryEntry.category,
            ParameterRegistryEntry.parameter_key,
        )
    ).scalars().all()

    return [
        {
            "key": r.parameter_key,
            "name": r.display_name,
            "description": r.description,
            "category": r.category,
            "value": r.current_value,
            "default": r.default_value,
            "min": r.min_value,
            "max": r.max_value,
            "tier": r.tier,
            "unit": r.unit,
        }
        for r in rows
    ]
