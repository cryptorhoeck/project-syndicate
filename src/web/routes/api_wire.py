"""
Project Syndicate — Wire dashboard API.

Read-only endpoints for the Wire ticker tape, source health grid,
and treasury spend gauge.
"""

__version__ = "0.1.0"

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.web.dependencies import get_db
from src.wire.models import (
    WireEvent,
    WireRawItem,
    WireSource,
    WireSourceHealth,
    WireTreasuryLedger,
)

logger = logging.getLogger(__name__)
router = APIRouter()


def _serialize_event(e: WireEvent) -> dict:
    return {
        "id": e.id,
        "coin": e.coin,
        "is_macro": bool(e.is_macro),
        "event_type": e.event_type,
        "severity": int(e.severity),
        "direction": e.direction,
        "summary": e.summary,
        "source_url": e.source_url,
        "occurred_at": e.occurred_at.isoformat() if e.occurred_at else None,
    }


@router.get("/api/wire/ticker")
async def get_ticker(
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """Last N severity-3+ ticker events (canonical only)."""
    rows = (
        db.execute(
            select(WireEvent)
            .where(WireEvent.duplicate_of.is_(None))
            .where(WireEvent.severity >= 3)
            .order_by(WireEvent.occurred_at.desc())
            .limit(limit)
        )
        .scalars()
        .all()
    )
    return {"events": [_serialize_event(e) for e in rows], "count": len(rows)}


@router.get("/api/wire/health")
async def get_source_health(db: Session = Depends(get_db)):
    """Per-source health snapshot for the dashboard grid."""
    sources = db.execute(select(WireSource).order_by(WireSource.name)).scalars().all()
    health_rows = {
        h.source_id: h
        for h in db.execute(select(WireSourceHealth)).scalars().all()
    }
    out = []
    for s in sources:
        h = health_rows.get(s.id)
        out.append(
            {
                "name": s.name,
                "display_name": s.display_name,
                "tier": s.tier,
                "enabled": bool(s.enabled),
                "fetch_interval_seconds": s.fetch_interval_seconds,
                "status": h.status if h else "unknown",
                "consecutive_failures": (h.consecutive_failures or 0) if h else 0,
                "last_fetch_success": (
                    h.last_fetch_success.isoformat() if (h and h.last_fetch_success) else None
                ),
                "last_fetch_error": h.last_fetch_error if h else None,
                "items_last_24h": (h.items_last_24h or 0) if h else 0,
            }
        )
    return {"sources": out}


@router.get("/api/wire/treasury")
async def get_treasury_spend(
    lookback_hours: int = Query(default=24, ge=1, le=720),
    db: Session = Depends(get_db),
):
    """Wire infrastructure spend (Haiku digestion etc.) over a rolling window."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)
    total = db.execute(
        select(func.coalesce(func.sum(WireTreasuryLedger.cost_usd), 0))
        .where(WireTreasuryLedger.incurred_at >= cutoff)
    ).scalar_one()
    by_category = db.execute(
        select(
            WireTreasuryLedger.cost_category,
            func.coalesce(func.sum(WireTreasuryLedger.cost_usd), 0),
        )
        .where(WireTreasuryLedger.incurred_at >= cutoff)
        .group_by(WireTreasuryLedger.cost_category)
    ).all()
    return {
        "lookback_hours": lookback_hours,
        "total_cost_usd": float(total or 0),
        "by_category": {row[0]: float(row[1] or 0) for row in by_category},
    }


@router.get("/api/wire/stats")
async def get_stats(db: Session = Depends(get_db)):
    """Roll-up counts for the dashboard header."""
    total_events = db.execute(
        select(func.count(WireEvent.id)).where(WireEvent.duplicate_of.is_(None))
    ).scalar_one()
    pending_raw = db.execute(
        select(func.count(WireRawItem.id)).where(
            WireRawItem.digestion_status == "pending"
        )
    ).scalar_one()
    dead_letter = db.execute(
        select(func.count(WireRawItem.id)).where(
            WireRawItem.digestion_status == "dead_letter"
        )
    ).scalar_one()
    return {
        "total_events": int(total_events),
        "pending_raw_items": int(pending_raw),
        "dead_letter_items": int(dead_letter),
    }
