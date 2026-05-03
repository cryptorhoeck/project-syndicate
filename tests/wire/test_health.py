"""Health monitor tests."""

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from src.wire.constants import (
    DISABLED_CONSECUTIVE_FAILURES,
    FAILING_CONSECUTIVE_FAILURES,
    HEALTH_DEGRADED,
    HEALTH_DISABLED,
    HEALTH_FAILING,
    HEALTH_HEALTHY,
    HEALTH_UNKNOWN,
)
from src.wire.health.monitor import HealthMonitor
from src.wire.models import WireSource, WireSourceHealth


def _src(session, name="cryptopanic") -> WireSource:
    return session.execute(
        select(WireSource).where(WireSource.name == name)
    ).scalar_one()


class TestSuccessTransitions:
    def test_first_success_marks_healthy(self, wire_seeded_session) -> None:
        m = HealthMonitor(wire_seeded_session)
        s = _src(wire_seeded_session)
        m.record_success(s, items_added=2)
        wire_seeded_session.commit()
        h = wire_seeded_session.get(WireSourceHealth, s.id)
        assert h.status == HEALTH_HEALTHY
        assert h.consecutive_failures == 0
        assert h.last_fetch_success is not None

    def test_success_after_failure_resets_failures(self, wire_seeded_session) -> None:
        m = HealthMonitor(wire_seeded_session)
        s = _src(wire_seeded_session)
        m.record_failure(s, "boom")
        m.record_failure(s, "boom2")
        wire_seeded_session.commit()
        m.record_success(s, items_added=0)
        wire_seeded_session.commit()
        h = wire_seeded_session.get(WireSourceHealth, s.id)
        assert h.status == HEALTH_HEALTHY
        assert h.consecutive_failures == 0


class TestFailureTransitions:
    def test_one_failure_is_degraded(self, wire_seeded_session) -> None:
        m = HealthMonitor(wire_seeded_session)
        s = _src(wire_seeded_session)
        m.record_failure(s, "boom")
        wire_seeded_session.commit()
        h = wire_seeded_session.get(WireSourceHealth, s.id)
        assert h.status == HEALTH_DEGRADED

    def test_threshold_failures_is_failing(self, wire_seeded_session) -> None:
        m = HealthMonitor(wire_seeded_session)
        s = _src(wire_seeded_session)
        for _ in range(FAILING_CONSECUTIVE_FAILURES):
            m.record_failure(s, "boom")
        wire_seeded_session.commit()
        h = wire_seeded_session.get(WireSourceHealth, s.id)
        assert h.status == HEALTH_FAILING
        assert h.consecutive_failures == FAILING_CONSECUTIVE_FAILURES

    def test_disabled_threshold(self, wire_seeded_session) -> None:
        m = HealthMonitor(wire_seeded_session)
        s = _src(wire_seeded_session)
        for _ in range(DISABLED_CONSECUTIVE_FAILURES):
            m.record_failure(s, "boom")
        wire_seeded_session.commit()
        h = wire_seeded_session.get(WireSourceHealth, s.id)
        assert h.status == HEALTH_DISABLED


class TestAgeBasedDegrade:
    def test_old_success_flips_to_degraded(self, wire_seeded_session) -> None:
        s = _src(wire_seeded_session)
        m = HealthMonitor(wire_seeded_session)
        m.record_success(s, items_added=1)
        wire_seeded_session.commit()
        # Manually push last_fetch_success way back.
        h = wire_seeded_session.get(WireSourceHealth, s.id)
        h.last_fetch_success = datetime.now(timezone.utc) - timedelta(
            seconds=s.fetch_interval_seconds * 5
        )
        wire_seeded_session.commit()

        m.refresh_status_from_age(s)
        wire_seeded_session.commit()
        h = wire_seeded_session.get(WireSourceHealth, s.id)
        assert h.status == HEALTH_DEGRADED


class TestSnapshot:
    def test_snapshot_returns_one_row_per_source(self, wire_seeded_session) -> None:
        m = HealthMonitor(wire_seeded_session)
        snaps = m.snapshot_all()
        # Seeded conftest has 8 sources.
        assert len(snaps) == 8
        # All start at unknown (or healthy after seed wrote 'unknown').
        statuses = {s.status for s in snaps}
        assert statuses <= {HEALTH_UNKNOWN, HEALTH_HEALTHY, HEALTH_DEGRADED, HEALTH_FAILING, HEALTH_DISABLED}
