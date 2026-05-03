"""
Dead Man's Switch — Meta-Monitor.

The DMS (`src/risk/heartbeat.py`) advances `system_state.last_heartbeat_at`
each cycle. This module is the FAILSAFE FOR THE FAILSAFE: a separate
component that watches the heartbeat column from the outside and emits a
system-level alert when the DMS itself goes silent.

Design constraints (do not weaken without a documented reason):

  - The meta-monitor MUST NOT live in the same process as the DMS. A dead
    DMS process cannot detect itself going silent. This module is meant to
    be invoked from Genesis's run_cycle() (and any other long-running
    process the colony has).

  - Stale threshold is 2× the DMS's CHECK_INTERVAL by default — i.e., we
    tolerate one missed beat before alerting. This matches the Wire
    DEGRADED_INTERVAL_MULTIPLIER convention.

  - Alerts are de-duplicated: a single transition into "silent" produces
    one alert. Subsequent ticks while still silent do not spam. When the
    heartbeat recovers, the dedup state resets and a recovery alert is
    emitted.

  - Failure mode: if the meta-monitor itself raises (DB down, alert sink
    broken), it logs and returns without crashing the host process. A
    monitoring component must never take down the thing it monitors.

Public surface:
    DmsMetaMonitor        : stateful watcher (holds dedup state)
    HeartbeatStatus        : snapshot dataclass returned by inspect()
    AGORA_EVENT_SILENT     : Agora event class string (`dead_mans_switch.silent_failure`)
    AGORA_EVENT_RECOVERED  : (`dead_mans_switch.recovered`)
"""

from __future__ import annotations

__version__ = "0.1.0"

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Awaitable, Callable, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.common.models import SystemState
from src.risk.heartbeat import CHECK_INTERVAL as DMS_CHECK_INTERVAL_SECONDS

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Tolerate one missed beat: silent-failure threshold = 2 * expected interval.
# Matches the Wire DEGRADED_INTERVAL_MULTIPLIER convention.
SILENT_FAILURE_MULTIPLIER: float = 2.0
DEFAULT_SILENT_THRESHOLD_SECONDS: int = int(
    DMS_CHECK_INTERVAL_SECONDS * SILENT_FAILURE_MULTIPLIER
)

AGORA_CHANNEL: str = "system-alerts"
AGORA_EVENT_SILENT: str = "dead_mans_switch.silent_failure"
AGORA_EVENT_RECOVERED: str = "dead_mans_switch.recovered"


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass(slots=True, frozen=True)
class HeartbeatStatus:
    """Snapshot of one inspect() call. Tests + dashboards read this directly."""

    last_heartbeat_at: Optional[datetime]
    age_seconds: Optional[float]
    threshold_seconds: int
    is_silent: bool
    has_ever_beaten: bool


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ensure_aware_utc(dt: Optional[datetime]) -> Optional[datetime]:
    """SQLite (and some Postgres adapter configs) round-trip naive datetimes.
    Treat them as UTC — that is what the DMS writes via `NOW() AT TIME ZONE 'UTC'`."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def inspect_heartbeat(
    session: Session,
    *,
    threshold_seconds: int = DEFAULT_SILENT_THRESHOLD_SECONDS,
    now: Optional[datetime] = None,
) -> HeartbeatStatus:
    """Read system_state.last_heartbeat_at and compute its status.

    Pure read; no writes, no side effects, no logging.
    """
    state = session.execute(select(SystemState).limit(1)).scalar_one_or_none()
    last = _ensure_aware_utc(state.last_heartbeat_at) if state is not None else None
    now_aware = now or datetime.now(timezone.utc)
    if now_aware.tzinfo is None:
        now_aware = now_aware.replace(tzinfo=timezone.utc)

    if last is None:
        # Heartbeat has never beaten. Treat as silent the moment we are past
        # the threshold of the host process's first call (best a cold-start
        # detector can do) — caller decides whether to alert.
        return HeartbeatStatus(
            last_heartbeat_at=None,
            age_seconds=None,
            threshold_seconds=threshold_seconds,
            is_silent=True,
            has_ever_beaten=False,
        )

    age = (now_aware - last).total_seconds()
    return HeartbeatStatus(
        last_heartbeat_at=last,
        age_seconds=age,
        threshold_seconds=threshold_seconds,
        is_silent=age > threshold_seconds,
        has_ever_beaten=True,
    )


# ---------------------------------------------------------------------------
# Stateful monitor with dedup
# ---------------------------------------------------------------------------


# Type alias for the publish callable. Accepts (channel, content, metadata)
# and may be sync or async — the monitor handles both.
PublishCallable = Callable[..., Optional[Awaitable[None]]]


class DmsMetaMonitor:
    """Stateful: keeps `_currently_silent` so a long outage produces ONE alert,
    not one per cycle. Recovery emits a single recovered alert.

    The publish callable receives keyword args:
        channel  : "system-alerts"
        content  : human-readable line for the Agora feed
        metadata : dict with event_class + heartbeat details

    Inject a sync callable for tests; production wires it to Genesis's
    `post_to_agora` via a small adapter.
    """

    def __init__(
        self,
        publish: Optional[PublishCallable] = None,
        *,
        threshold_seconds: int = DEFAULT_SILENT_THRESHOLD_SECONDS,
        now_func: Optional[Callable[[], datetime]] = None,
    ) -> None:
        self.publish = publish
        self.threshold_seconds = int(threshold_seconds)
        self.now_func = now_func or (lambda: datetime.now(timezone.utc))
        self._currently_silent: bool = False
        self._last_alert_status: Optional[HeartbeatStatus] = None

    @property
    def currently_silent(self) -> bool:
        return self._currently_silent

    def reset(self) -> None:
        """Clear in-memory dedup state. Useful for tests and for hard restarts."""
        self._currently_silent = False
        self._last_alert_status = None

    def check(self, session: Session) -> HeartbeatStatus:
        """One inspection pass. Emits an alert iff the silence/recovery state
        has actually changed since the previous check."""
        try:
            status = inspect_heartbeat(
                session,
                threshold_seconds=self.threshold_seconds,
                now=self.now_func(),
            )
        except Exception:  # pragma: no cover — never break the host
            logger.exception("dms_meta_monitor.inspect_failed")
            return HeartbeatStatus(
                last_heartbeat_at=None,
                age_seconds=None,
                threshold_seconds=self.threshold_seconds,
                is_silent=True,
                has_ever_beaten=False,
            )

        if status.is_silent and not self._currently_silent:
            self._emit(AGORA_EVENT_SILENT, status)
            self._currently_silent = True
            self._last_alert_status = status
        elif not status.is_silent and self._currently_silent:
            self._emit(AGORA_EVENT_RECOVERED, status)
            self._currently_silent = False
            self._last_alert_status = status

        return status

    # ------------------------------------------------------------------

    def _emit(self, event_class: str, status: HeartbeatStatus) -> None:
        if self.publish is None:
            logger.warning(
                "dms_meta_monitor.alert_no_publisher",
                extra={"event_class": event_class},
            )
            return

        if event_class == AGORA_EVENT_SILENT:
            human = (
                f"DEAD MAN'S SWITCH SILENT: heartbeat last beat "
                f"{status.last_heartbeat_at.isoformat() if status.last_heartbeat_at else 'NEVER'}"
                f" — age "
                f"{status.age_seconds:.0f}s"
                if status.age_seconds is not None
                else
                "DEAD MAN'S SWITCH SILENT: heartbeat has never beaten since system_state was created"
            )
        else:
            human = (
                f"Dead Man's Switch recovered. Heartbeat current at "
                f"{status.last_heartbeat_at.isoformat()}, age "
                f"{status.age_seconds:.0f}s"
                if status.last_heartbeat_at
                else "Dead Man's Switch recovered."
            )

        metadata = {
            "event_class": event_class,
            "last_heartbeat_at": (
                status.last_heartbeat_at.isoformat()
                if status.last_heartbeat_at
                else None
            ),
            "age_seconds": status.age_seconds,
            "threshold_seconds": status.threshold_seconds,
            "has_ever_beaten": status.has_ever_beaten,
        }

        try:
            result = self.publish(
                channel=AGORA_CHANNEL, content=human, metadata=metadata
            )
            # Tolerate async publishers (e.g., Agora.post_message). If we
            # got back a coroutine, schedule it on the running loop or run
            # it on a fresh dedicated loop. We deliberately avoid
            # `asyncio.run()` because it mutates the default loop policy in
            # a way that breaks tests / callers using the legacy
            # `get_event_loop().run_until_complete(...)` pattern.
            if hasattr(result, "__await__"):
                import asyncio
                running_loop: Optional[asyncio.AbstractEventLoop] = None
                try:
                    running_loop = asyncio.get_running_loop()
                except RuntimeError:
                    running_loop = None
                if running_loop is not None:
                    asyncio.ensure_future(result, loop=running_loop)
                else:
                    new_loop = asyncio.new_event_loop()
                    try:
                        new_loop.run_until_complete(result)
                    finally:
                        new_loop.close()
        except Exception:  # pragma: no cover
            logger.exception("dms_meta_monitor.publish_failed")
