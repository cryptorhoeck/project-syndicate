"""
Volume floor + source diversity checks.

These run on a much slower cadence than per-source heartbeats — every N
minutes is enough. They emit Agora system events when breached:

  - wire.volume_floor_breach : total wire_events in last 6h < 3
  - wire.diversity_breach    : a single source produced > 70% of last 24h events

The check is read-only against the DB; nothing writes back. Alerts are
deduped at the alerts.log_alert layer (Tier 3 will add Agora publication).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.wire.constants import (
    AGORA_EVENT_DIVERSITY_BREACH,
    AGORA_EVENT_VOLUME_FLOOR_BREACH,
    DIVERSITY_MAX_SHARE,
    DIVERSITY_WINDOW_HOURS,
    VOLUME_FLOOR_MIN_EVENTS,
    VOLUME_FLOOR_WINDOW_HOURS,
)
from src.wire.health.alerts import log_alert
from src.wire.models import WireEvent, WireRawItem, WireSource

logger = logging.getLogger(__name__)


@dataclass(slots=True, frozen=True)
class BreachReport:
    """Result of one breach pass. Tests assert against this directly."""

    volume_floor_breached: bool
    volume_count: int
    diversity_breached: bool
    diversity_top_source: Optional[str] = None
    diversity_top_share: float = 0.0


class BreachMonitor:
    """Stateless scanner. Pass a session, get a BreachReport, alerts emit as a side effect."""

    def __init__(self, session: Session, *, now: Optional[datetime] = None) -> None:
        self.session = session
        self._now_override = now

    def now(self) -> datetime:
        return self._now_override or datetime.now(timezone.utc)

    # ----- volume floor -----

    def check_volume_floor(self) -> tuple[bool, int]:
        cutoff = self.now() - timedelta(hours=VOLUME_FLOOR_WINDOW_HOURS)
        # Count canonical events (skip duplicates and dead-lettered raws which
        # never produced an event).
        count = self.session.execute(
            select(func.count(WireEvent.id))
            .where(WireEvent.duplicate_of.is_(None))
            .where(WireEvent.digested_at >= cutoff)
        ).scalar_one()
        breached = count < VOLUME_FLOOR_MIN_EVENTS
        if breached:
            log_alert(
                AGORA_EVENT_VOLUME_FLOOR_BREACH,
                {
                    "window_hours": VOLUME_FLOOR_WINDOW_HOURS,
                    "min_required": VOLUME_FLOOR_MIN_EVENTS,
                    "actual": count,
                },
            )
        return breached, int(count)

    # ----- diversity -----

    def check_diversity(self) -> tuple[bool, Optional[str], float]:
        cutoff = self.now() - timedelta(hours=DIVERSITY_WINDOW_HOURS)
        # Per-source counts via raw_item join.
        rows = self.session.execute(
            select(WireSource.name, func.count(WireEvent.id))
            .select_from(WireEvent)
            .join(WireRawItem, WireRawItem.id == WireEvent.raw_item_id)
            .join(WireSource, WireSource.id == WireRawItem.source_id)
            .where(WireEvent.digested_at >= cutoff)
            .where(WireEvent.duplicate_of.is_(None))
            .group_by(WireSource.name)
        ).all()

        total = sum(int(r[1]) for r in rows)
        if total == 0:
            return False, None, 0.0
        top_name = ""
        top_count = 0
        for name, count in rows:
            count = int(count)
            if count > top_count:
                top_name = name
                top_count = count
        share = top_count / total
        breached = share > DIVERSITY_MAX_SHARE
        if breached:
            log_alert(
                AGORA_EVENT_DIVERSITY_BREACH,
                {
                    "window_hours": DIVERSITY_WINDOW_HOURS,
                    "max_share": DIVERSITY_MAX_SHARE,
                    "top_source": top_name,
                    "top_share": share,
                    "total_events": total,
                },
            )
        return breached, top_name, share

    # ----- combined -----

    def run(self) -> BreachReport:
        vf_breached, count = self.check_volume_floor()
        div_breached, top_name, top_share = self.check_diversity()
        return BreachReport(
            volume_floor_breached=vf_breached,
            volume_count=count,
            diversity_breached=div_breached,
            diversity_top_source=top_name,
            diversity_top_share=top_share,
        )
