"""
Wire scheduler.

Simple time-based loop. Each enabled source has a per-source `next_due` clock;
on every tick, the scheduler runs sources whose clock has elapsed, then sleeps
to the nearest next due time.

Why not APScheduler? The whole job is "every N seconds run a function." Adding
APScheduler buys background-thread complexity and persistence for ~30 lines we
already have. If we ever need cron-style scheduling or distributed work
distribution we can swap.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.wire.digest.haiku_digester import HaikuClientProto, HaikuDigester
from src.wire.health.breach_monitor import BreachMonitor
from src.wire.ingestors.runner import SourceRunner
from src.wire.models import WireSource

logger = logging.getLogger(__name__)


# How often the volume-floor + diversity scan runs (separate from per-source cadence).
DEFAULT_BREACH_CHECK_INTERVAL_SECONDS = 30 * 60


@dataclass
class IngestorScheduler:
    """Drives the runner + digester on a loop.

    Tier 1 builds this as a single-threaded sync loop; Tier 3 adds Agora
    publishing in the digest path. The scheduler is process-isolated; running
    multiple instances would double-fetch unless a Redis lock is added.
    """

    session_factory: Callable[[], Session]
    haiku_client: Optional[HaikuClientProto] = None
    sleep_func: Callable[[float], None] = time.sleep
    now_func: Callable[[], datetime] = field(
        default_factory=lambda: lambda: datetime.now(timezone.utc)
    )
    min_loop_interval_seconds: float = 5.0
    breach_check_interval_seconds: float = DEFAULT_BREACH_CHECK_INTERVAL_SECONDS

    _next_due: dict[int, datetime] = field(default_factory=dict, init=False)
    _last_breach_check: Optional[datetime] = field(default=None, init=False)

    def _due_sources(self, session: Session) -> list[WireSource]:
        sources = (
            session.execute(
                select(WireSource).where(WireSource.enabled.is_(True)).order_by(WireSource.name)
            )
            .scalars()
            .all()
        )
        now = self.now_func()
        due: list[WireSource] = []
        for src in sources:
            next_due = self._next_due.get(src.id)
            if next_due is None or next_due <= now:
                due.append(src)
        return due

    def tick(self) -> dict:
        """Run one tick: fetch any due sources, then digest pending items.

        Returns a small dict summary so callers / tests can assert behavior.
        """
        session = self.session_factory()
        try:
            due = self._due_sources(session)
            run_results = []
            runner = SourceRunner(session=session)
            for src in due:
                result = runner.run_source(src)
                run_results.append(result)
                # schedule next due regardless of success
                self._next_due[src.id] = self.now_func() + timedelta(
                    seconds=src.fetch_interval_seconds
                )

            digest_results: list = []
            if self.haiku_client is not None:
                digester = HaikuDigester(haiku_client=self.haiku_client, session=session)
                digest_results = digester.digest_pending(limit=200)

            breach_report = None
            now = self.now_func()
            if (
                self._last_breach_check is None
                or (now - self._last_breach_check).total_seconds()
                >= self.breach_check_interval_seconds
            ):
                breach_monitor = BreachMonitor(session, now=now)
                breach_report = breach_monitor.run()
                self._last_breach_check = now

            return {
                "runs": run_results,
                "digests": digest_results,
                "breach": breach_report,
            }
        finally:
            session.close()

    def run_forever(self, max_ticks: Optional[int] = None) -> None:
        """Loop indefinitely (or for max_ticks). Sleeps to the nearest next-due."""
        ticks = 0
        while True:
            self.tick()
            ticks += 1
            if max_ticks is not None and ticks >= max_ticks:
                return
            self.sleep_func(self._sleep_seconds())

    def _sleep_seconds(self) -> float:
        if not self._next_due:
            return self.min_loop_interval_seconds
        now = self.now_func()
        upcoming = [d for d in self._next_due.values() if d > now]
        if not upcoming:
            return self.min_loop_interval_seconds
        delta = (min(upcoming) - now).total_seconds()
        return max(self.min_loop_interval_seconds, min(60.0, delta))
