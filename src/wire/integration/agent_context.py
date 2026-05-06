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
    Each call charges tokens to the agent's thinking budget via wire_query_log.

    EXTENSION (subsystems F+G hotfix, 2026-05-04): the returned closure
    has a ``.prefetch(watched_markets, ...)`` attribute that returns a
    role-aware system-initiated read used by ContextAssembler to build
    the priority-context Wire slice. The prefetch path is FREE
    (`is_free=True` on `archive.query`) — its cost is absorbed in the
    cycle's overall context budget, same model as Scout's
    `recent_signals` block. The agent-initiated query path (the
    closure itself) is unchanged: every call still charges. Existing
    callers (and tests) that invoke `helper(...)` directly are not
    affected.
    """

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

    def _prefetch(
        watched_markets: Optional[list[str]] = None,
        lookback_hours: int = 24,
        min_severity: int = 3,
        limit: int = 5,
    ) -> ArchiveQueryResult:
        # System-initiated read; uses is_free=True so the agent's
        # thinking budget is unaffected. The DB query is broad
        # (no coin filter) and the post-filter narrows to coins
        # in `watched_markets` PLUS macro events (coin is None).
        # We over-fetch (limit*4, capped) and post-filter in Python
        # so the resulting top-N matches the role-aware filter.
        return _system_prefetch(
            archive,
            agent_id=agent_id,
            watched_markets=watched_markets,
            lookback_hours=lookback_hours,
            min_severity=min_severity,
            limit=limit,
        )

    _query.prefetch = _prefetch
    return _query


def build_critic_archive_helper(
    session: Session,
    *,
    agent_id: int,
    free_budget: int,
) -> Callable[..., ArchiveQueryResult]:
    """Critic helper that grants the first `free_budget` calls at zero cost.
    State is closure-local so a fresh helper is built per critique cycle.

    EXTENSION (subsystems F+G hotfix, 2026-05-04): the returned closure
    has a ``.prefetch(watched_markets, ...)`` attribute mirroring the
    Strategist helper. The prefetch path is system-initiated and does
    NOT consume the Critic's `free_budget` counter — only
    agent-initiated queries (calls to the closure itself) decrement
    it. This is the documented contract:

      - prefetch (system, role-aware Wire slice): FREE,
        free_budget unaffected.
      - first `free_budget` agent-initiated queries: FREE, counter
        increments.
      - subsequent agent-initiated queries: charged.

    Test `test_pre_fetch_does_not_consume_critic_free_budget` locks
    this in.
    """

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

    def _prefetch(
        watched_markets: Optional[list[str]] = None,
        lookback_hours: int = 24,
        min_severity: int = 3,
        limit: int = 5,
    ) -> ArchiveQueryResult:
        # System-initiated read; uses is_free=True AND does NOT
        # decrement used_free — it's not an agent-initiated query.
        return _system_prefetch(
            archive,
            agent_id=agent_id,
            watched_markets=watched_markets,
            lookback_hours=lookback_hours,
            min_severity=min_severity,
            limit=limit,
        )

    _query.prefetch = _prefetch
    return _query


# ---------------------------------------------------------------------------
# Shared prefetch implementation (role-aware Wire slice, system-initiated)
# ---------------------------------------------------------------------------


def _system_prefetch(
    archive: WireArchive,
    *,
    agent_id: int,
    watched_markets: Optional[list[str]],
    lookback_hours: int,
    min_severity: int,
    limit: int,
) -> ArchiveQueryResult:
    """Run a broad Archive query then post-filter to the role-aware
    set: events whose `coin` is in `watched_markets`, OR events with
    no coin attribution (macro events — fear/greed, calendar,
    cross-chain). System-initiated, always free.

    Over-fetches (4x the requested limit, capped at 50) so the
    role-aware top-N is dense even when the broader window is mixed
    with off-watch coins. The DB ORDER BY (severity desc, occurred_at
    desc) is preserved on the broader set; we just trim after filter.
    """
    over_fetch = min(50, max(int(limit) * 4, int(limit)))
    params = ArchiveQueryParams(
        coin=None,  # broad — post-filter handles role-aware selection
        lookback_hours=lookback_hours,
        min_severity=min_severity,
        event_types=None,
        limit=over_fetch,
    )
    raw = archive.query(params, agent_id=agent_id, is_free=True)

    watched_set = (
        {str(c).upper() for c in watched_markets}
        if watched_markets else set()
    )
    filtered: list[dict] = []
    for ev in raw.events:
        ev_coin = ev.get("coin")
        if ev_coin is None:
            # Macro event — always include.
            filtered.append(ev)
        elif str(ev_coin).upper() in watched_set:
            filtered.append(ev)
        if len(filtered) >= int(limit):
            break

    return ArchiveQueryResult(
        events=filtered,
        token_cost=0,
        query_log_id=raw.query_log_id,
        free_query=True,
        metadata={
            "system_prefetch": True,
            "watched_markets": list(watched_set),
            "raw_count": len(raw.events),
            "filtered_count": len(filtered),
        },
    )
