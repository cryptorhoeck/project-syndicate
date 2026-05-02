"""
Agent context-block builders.

Scout integration is push-only and free: the most-recent N ticker events are
injected into Scout's OODA prompt as a `recent_signals` block. Strategists and
Critics interact with the Archive on a pull-with-cost basis; this module
provides the helper functions they call.

The intent is that the agent's OODA cycle imports these and the
`context_assembler` calls `build_recent_signals_block(session)` once per
Scout cycle. Strategists/Critics call `build_strategist_archive_helper`
to get a pre-bound query function that records cost against their agent_id.
"""

from __future__ import annotations

import logging
from typing import Callable, Optional

from sqlalchemy.orm import Session

from src.wire.publishing.archive import (
    ArchiveQueryParams,
    ArchiveQueryResult,
    WireArchive,
    fetch_recent_ticker_events,
)

logger = logging.getLogger(__name__)


def build_recent_signals_block(
    session: Session,
    *,
    limit: int = 5,
    lookback_hours: int = 24,
) -> dict:
    """Free Scout-side context block.

    Returns a dict suitable for direct inclusion in the agent's context JSON.
    Empty list (not missing) when no events exist — callers should always
    surface "no signals" explicitly so the agent doesn't hallucinate them.
    """
    events = fetch_recent_ticker_events(
        session, limit=limit, lookback_hours=lookback_hours
    )
    return {
        "recent_signals": events,
        "count": len(events),
        "lookback_hours": int(lookback_hours),
    }


def build_strategist_archive_helper(
    session: Session,
    *,
    agent_id: int,
) -> Callable[..., ArchiveQueryResult]:
    """Returns a closure the Strategist can call: helper(coin=..., min_severity=..., ...).
    Each call charges tokens to the agent's thinking budget via wire_query_log."""

    archive = WireArchive(session=session)

    def _query(
        coin: Optional[str] = None,
        lookback_hours: int = 24,
        min_severity: int = 1,
        event_types: Optional[list[str]] = None,
        limit: int = 20,
    ) -> ArchiveQueryResult:
        params = ArchiveQueryParams(
            coin=coin,
            lookback_hours=lookback_hours,
            min_severity=min_severity,
            event_types=event_types,
            limit=limit,
        )
        return archive.query(params, agent_id=agent_id, is_free=False)

    return _query


def build_critic_archive_helper(
    session: Session,
    *,
    agent_id: int,
    free_budget: int,
) -> Callable[..., ArchiveQueryResult]:
    """Critic helper that grants the first `free_budget` calls at zero cost.
    State is closure-local so a fresh helper is built per critique cycle."""

    archive = WireArchive(session=session)
    used_free = {"count": 0}

    def _query(
        coin: Optional[str] = None,
        lookback_hours: int = 24,
        min_severity: int = 1,
        event_types: Optional[list[str]] = None,
        limit: int = 20,
    ) -> ArchiveQueryResult:
        params = ArchiveQueryParams(
            coin=coin,
            lookback_hours=lookback_hours,
            min_severity=min_severity,
            event_types=event_types,
            limit=limit,
        )
        is_free = used_free["count"] < free_budget
        if is_free:
            used_free["count"] += 1
        return archive.query(params, agent_id=agent_id, is_free=is_free)

    return _query
