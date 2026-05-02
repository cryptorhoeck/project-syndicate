"""
Per-source health monitor.

Updates wire_source_health on every fetch attempt (success or failure) and
adjudicates the source's `status` based on consecutive failure count and
recency of last success. The transition rules are:

  consecutive_failures < FAILING_CONSECUTIVE_FAILURES:
      success -> healthy
      failure -> healthy until 1+ failure, then degraded; last_fetch_success
                 older than DEGRADED_INTERVAL_MULTIPLIER * interval -> degraded

  consecutive_failures >= FAILING_CONSECUTIVE_FAILURES (5): failing
  consecutive_failures >= DISABLED_CONSECUTIVE_FAILURES (20): disabled

The disabled state is what auto-disables a runaway source. The runner reads
wire_sources.enabled AND wire_source_health.status — if either is off/disabled,
the source is skipped that cycle.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.wire.constants import (
    AGORA_EVENT_SOURCE_DISABLED,
    DEGRADED_INTERVAL_MULTIPLIER,
    DISABLED_CONSECUTIVE_FAILURES,
    FAILING_CONSECUTIVE_FAILURES,
    HEALTH_DEGRADED,
    HEALTH_DISABLED,
    HEALTH_FAILING,
    HEALTH_HEALTHY,
    HEALTH_UNKNOWN,
)
from src.wire.health.alerts import log_alert
from src.wire.models import WireSource, WireSourceHealth

logger = logging.getLogger(__name__)


def _ensure_aware_utc(dt: Optional[datetime]) -> Optional[datetime]:
    """Treat naive datetimes coming back from SQLite as UTC. Production
    Postgres rows already arrive aware, so this is a no-op there."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


@dataclass(slots=True, frozen=True)
class HealthSnapshot:
    """A read-only view of one source's health."""

    source_id: int
    source_name: str
    status: str
    consecutive_failures: int
    last_fetch_attempt: Optional[datetime]
    last_fetch_success: Optional[datetime]
    last_fetch_error: Optional[str]
    items_last_24h: int


class HealthMonitor:
    """Encapsulates health row reads/writes for the runner and CLI."""

    def __init__(self, session: Session, *, now: Optional[datetime] = None) -> None:
        self.session = session
        self._now_override = now

    def now(self) -> datetime:
        return self._now_override or datetime.now(timezone.utc)

    def _get_or_create(self, source_id: int) -> WireSourceHealth:
        row = self.session.get(WireSourceHealth, source_id)
        if row is None:
            row = WireSourceHealth(source_id=source_id, status=HEALTH_UNKNOWN)
            self.session.add(row)
            self.session.flush()
        return row

    def record_success(
        self,
        source: WireSource,
        items_added: int,
    ) -> WireSourceHealth:
        row = self._get_or_create(source.id)
        row.last_fetch_attempt = self.now()
        row.last_fetch_success = self.now()
        row.last_fetch_error = None
        row.consecutive_failures = 0
        # items_last_24h is recomputed by refresh_volume_window; this is a hint
        row.items_last_24h = max(0, row.items_last_24h or 0) + max(0, items_added)
        row.status = HEALTH_HEALTHY
        row.updated_at = self.now()
        self.session.add(row)
        return row

    def record_failure(
        self,
        source: WireSource,
        error: str,
    ) -> WireSourceHealth:
        row = self._get_or_create(source.id)
        row.last_fetch_attempt = self.now()
        row.last_fetch_error = (error or "unknown error")[:2000]
        row.consecutive_failures = (row.consecutive_failures or 0) + 1
        row.updated_at = self.now()

        previous_status = row.status
        if row.consecutive_failures >= DISABLED_CONSECUTIVE_FAILURES:
            row.status = HEALTH_DISABLED
        elif row.consecutive_failures >= FAILING_CONSECUTIVE_FAILURES:
            row.status = HEALTH_FAILING
        else:
            row.status = HEALTH_DEGRADED
        self.session.add(row)

        if previous_status != HEALTH_DISABLED and row.status == HEALTH_DISABLED:
            log_alert(
                AGORA_EVENT_SOURCE_DISABLED,
                {
                    "source_id": source.id,
                    "source_name": source.name,
                    "consecutive_failures": row.consecutive_failures,
                    "error": row.last_fetch_error,
                },
            )

        return row

    def refresh_status_from_age(self, source: WireSource) -> WireSourceHealth:
        """Mark a healthy source `degraded` if its last success is older than
        DEGRADED_INTERVAL_MULTIPLIER * fetch_interval_seconds."""
        row = self._get_or_create(source.id)
        if row.status not in (HEALTH_HEALTHY, HEALTH_UNKNOWN):
            return row
        last_success = _ensure_aware_utc(row.last_fetch_success)
        if last_success is None:
            return row
        age = self.now() - last_success
        threshold = timedelta(seconds=int(DEGRADED_INTERVAL_MULTIPLIER * source.fetch_interval_seconds))
        if age > threshold:
            row.status = HEALTH_DEGRADED
            row.updated_at = self.now()
            self.session.add(row)
        return row

    def snapshot_all(self) -> list[HealthSnapshot]:
        sources = self.session.execute(select(WireSource).order_by(WireSource.name)).scalars().all()
        snapshots: list[HealthSnapshot] = []
        for source in sources:
            row = self.session.get(WireSourceHealth, source.id)
            if row is None:
                snapshots.append(
                    HealthSnapshot(
                        source_id=source.id,
                        source_name=source.name,
                        status=HEALTH_UNKNOWN,
                        consecutive_failures=0,
                        last_fetch_attempt=None,
                        last_fetch_success=None,
                        last_fetch_error=None,
                        items_last_24h=0,
                    )
                )
                continue
            snapshots.append(
                HealthSnapshot(
                    source_id=source.id,
                    source_name=source.name,
                    status=row.status,
                    consecutive_failures=row.consecutive_failures or 0,
                    last_fetch_attempt=row.last_fetch_attempt,
                    last_fetch_success=row.last_fetch_success,
                    last_fetch_error=row.last_fetch_error,
                    items_last_24h=row.items_last_24h or 0,
                )
            )
        return snapshots
