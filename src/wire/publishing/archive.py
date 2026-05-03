"""
Wire Archive — pull side.

Synchronous query API. Strategists/Critics call WireArchive.query(...) during
plan formulation / critique. Each call:

  1. Computes a token cost (base + per-result + lookback penalty).
  2. Records the query + cost into wire_query_log.
  3. Returns a list of structured event dicts the agent can read in their
     thinking cycle.

Critics get a small free baseline (CRITIC_FREE_QUERIES_PER_CRITIQUE) so they
aren't pressured into shallow critiques on cost grounds.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.wire.constants import (
    ARCHIVE_QUERY_BASE_TOKENS,
    ARCHIVE_QUERY_LOOKBACK_PENALTY_THRESHOLD_HOURS,
    ARCHIVE_QUERY_LOOKBACK_PENALTY_TOKENS,
    ARCHIVE_QUERY_PER_RESULT_TOKENS,
    CRITIC_FREE_QUERIES_PER_CRITIQUE,
)
from src.wire.models import WireEvent, WireQueryLog

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Query params + result shapes
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ArchiveQueryParams:
    """Inputs for a single Archive query."""

    coin: Optional[str] = None
    lookback_hours: int = 24
    min_severity: int = 1
    event_types: Optional[list[str]] = None
    limit: int = 20

    def to_dict(self) -> dict:
        return {
            "coin": self.coin,
            "lookback_hours": int(self.lookback_hours),
            "min_severity": int(self.min_severity),
            "event_types": list(self.event_types) if self.event_types else None,
            "limit": int(self.limit),
        }


@dataclass(slots=True)
class ArchiveQueryResult:
    """Output of an Archive query."""

    events: list[dict]
    token_cost: int
    query_log_id: Optional[int] = None
    free_query: bool = False
    metadata: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Token cost
# ---------------------------------------------------------------------------


def calculate_query_cost(
    params: ArchiveQueryParams,
    *,
    results_count: int,
) -> int:
    """Token cost for one Archive query."""
    cost = ARCHIVE_QUERY_BASE_TOKENS
    cost += ARCHIVE_QUERY_PER_RESULT_TOKENS * max(0, int(results_count))
    if int(params.lookback_hours) > ARCHIVE_QUERY_LOOKBACK_PENALTY_THRESHOLD_HOURS:
        cost += ARCHIVE_QUERY_LOOKBACK_PENALTY_TOKENS
    return cost


# ---------------------------------------------------------------------------
# Archive
# ---------------------------------------------------------------------------


def _serialize_event(event: WireEvent) -> dict:
    return {
        "id": event.id,
        "coin": event.coin,
        "is_macro": bool(event.is_macro),
        "event_type": event.event_type,
        "severity": int(event.severity),
        "direction": event.direction,
        "summary": event.summary,
        "source_url": event.source_url,
        "occurred_at": event.occurred_at.isoformat() if event.occurred_at else None,
    }


@dataclass
class WireArchive:
    """Stateless archive query handler.

    Note on free queries: tracking per-critique baseline state isn't this
    module's job. Callers (Critic OODA path) pass `is_free=True` for the first
    N calls per critique cycle and switch to False thereafter. The Archive
    will record both kinds in wire_query_log with `token_cost = 0` for free
    queries so they're still auditable.
    """

    session: Session
    now_func: Optional[callable] = None  # injectable for tests

    def _now(self) -> datetime:
        if self.now_func:
            return self.now_func()
        return datetime.now(timezone.utc)

    def query(
        self,
        params: ArchiveQueryParams,
        *,
        agent_id: int,
        is_free: bool = False,
    ) -> ArchiveQueryResult:
        # Build SELECT.
        cutoff = self._now() - timedelta(hours=int(params.lookback_hours))
        stmt = (
            select(WireEvent)
            .where(WireEvent.duplicate_of.is_(None))
            .where(WireEvent.occurred_at >= cutoff)
            .where(WireEvent.severity >= int(params.min_severity))
        )
        if params.coin:
            stmt = stmt.where(WireEvent.coin == params.coin)
        if params.event_types:
            stmt = stmt.where(WireEvent.event_type.in_(list(params.event_types)))
        stmt = stmt.order_by(
            WireEvent.severity.desc(),
            WireEvent.occurred_at.desc(),
        ).limit(int(params.limit))

        rows = list(self.session.execute(stmt).scalars().all())
        events = [_serialize_event(e) for e in rows]
        cost = 0 if is_free else calculate_query_cost(params, results_count=len(events))

        log = WireQueryLog(
            agent_id=int(agent_id),
            query_params=params.to_dict(),
            results_count=len(events),
            token_cost=int(cost),
        )
        self.session.add(log)
        self.session.flush()
        # We deliberately don't commit here — the caller's session manages tx.
        return ArchiveQueryResult(
            events=events,
            token_cost=cost,
            query_log_id=log.id,
            free_query=is_free,
        )

    def critic_free_budget(self) -> int:
        """Baseline number of free queries per critique."""
        return CRITIC_FREE_QUERIES_PER_CRITIQUE


# ---------------------------------------------------------------------------
# Recent ticker fetch (for Scout context — free)
# ---------------------------------------------------------------------------


def fetch_recent_ticker_events(
    session: Session,
    *,
    limit: int = 5,
    now: Optional[datetime] = None,
    lookback_hours: int = 24,
) -> list[dict]:
    """Used by Scout context block. Free: no wire_query_log entry, no cost."""
    if now is None:
        now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=lookback_hours)
    stmt = (
        select(WireEvent)
        .where(WireEvent.duplicate_of.is_(None))
        .where(WireEvent.published_to_ticker.is_(True))
        .where(WireEvent.occurred_at >= cutoff)
        .order_by(WireEvent.occurred_at.desc())
        .limit(int(limit))
    )
    return [_serialize_event(e) for e in session.execute(stmt).scalars().all()]
