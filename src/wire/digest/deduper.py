"""
Wire dedup.

Two layers:

  1. Fetch-time dedup via UNIQUE(source_id, external_id). Already enforced at
     the DB level for raw items.

  2. Cross-source canonical dedup. After digestion, we compute a SHA-256 over
     (coin, event_type, normalized_summary). If a non-duplicate event with the
     same canonical_hash exists within DEDUP_WINDOW_HOURS, the new event
     points its `duplicate_of` at the canonical row.

`canonical_hash` MUST be stable. Same inputs -> same hash, period. Tests rely
on this.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.wire.constants import DEDUP_WINDOW_HOURS
from src.wire.models import WireEvent

_WS_RE = re.compile(r"\s+")


def _normalize_summary(summary: str) -> str:
    """Lowercase + collapse whitespace + strip. Trims trailing punctuation that
    sources commonly disagree on."""
    if summary is None:
        return ""
    text = summary.strip().lower()
    text = _WS_RE.sub(" ", text)
    return text.rstrip(".!? ")


def canonical_hash(
    coin: Optional[str],
    event_type: str,
    summary: str,
) -> str:
    """SHA-256 hex of normalized (coin, event_type, summary) tuple."""
    coin_part = (coin or "").upper()
    type_part = (event_type or "").lower()
    summary_part = _normalize_summary(summary)
    payload = f"{coin_part}|{type_part}|{summary_part}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def find_duplicate(
    session: Session,
    canonical: str,
    *,
    window_hours: int = DEDUP_WINDOW_HOURS,
    now: Optional[datetime] = None,
) -> Optional[WireEvent]:
    """Find an existing canonical (non-duplicate) event matching `canonical`
    within the dedup window. Returns the canonical row or None.
    """
    if now is None:
        now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=window_hours)
    stmt = (
        select(WireEvent)
        .where(WireEvent.canonical_hash == canonical)
        .where(WireEvent.duplicate_of.is_(None))
        .where(WireEvent.occurred_at >= cutoff)
        .order_by(WireEvent.occurred_at.asc())
        .limit(1)
    )
    return session.execute(stmt).scalars().first()


__all__ = ["canonical_hash", "find_duplicate"]
