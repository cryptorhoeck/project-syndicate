"""
Dead Man's Switch — meta-monitor tests.

These cover the failsafe-of-the-failsafe: an external observer that detects
when the DMS process has stopped beating the heartbeat. The most critical
test is `test_dms_process_death_fires_silent_failure`, which is the analogue
of the Wire silent-failure callback test for the Library reflection bug.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from src.common.models import SystemState
from src.risk.dms_meta_monitor import (
    AGORA_CHANNEL,
    AGORA_EVENT_RECOVERED,
    AGORA_EVENT_SILENT,
    DEFAULT_SILENT_THRESHOLD_SECONDS,
    DmsMetaMonitor,
    HeartbeatStatus,
    SILENT_FAILURE_MULTIPLIER,
    inspect_heartbeat,
)


def _put_heartbeat(session, *, age_seconds: float | None, now: datetime) -> None:
    """Set system_state.last_heartbeat_at to `now - age_seconds`. None -> NULL."""
    state = session.execute(select(SystemState).limit(1)).scalar_one_or_none()
    if state is None:
        state = SystemState(
            total_treasury=0.0,
            peak_treasury=0.0,
            current_regime="unknown",
            active_agent_count=0,
            alert_status="green",
        )
        session.add(state)
    if age_seconds is None:
        state.last_heartbeat_at = None
    else:
        state.last_heartbeat_at = now - timedelta(seconds=age_seconds)
    session.commit()


@pytest.fixture
def now() -> datetime:
    return datetime(2026, 5, 3, 2, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def captured() -> list:
    return []


@pytest.fixture
def publisher(captured):
    """Capturing publisher: records (channel, content, metadata) per call."""
    def _pub(channel, content, metadata):
        captured.append((channel, content, metadata))
        return None  # sync publisher — meta-monitor handles both
    return _pub


# ---------------------------------------------------------------------------
# inspect_heartbeat — pure read tests
# ---------------------------------------------------------------------------


class TestInspectHeartbeat:
    def test_fresh_returns_not_silent(self, db_session_factory, now) -> None:
        with db_session_factory() as session:
            _put_heartbeat(session, age_seconds=10, now=now)
            status = inspect_heartbeat(session, now=now)
        assert status.is_silent is False
        assert status.has_ever_beaten is True
        assert status.age_seconds == pytest.approx(10, abs=1)

    def test_at_threshold_not_silent(self, db_session_factory, now) -> None:
        with db_session_factory() as session:
            _put_heartbeat(session, age_seconds=DEFAULT_SILENT_THRESHOLD_SECONDS, now=now)
            status = inspect_heartbeat(session, now=now)
        assert status.is_silent is False  # equality is "still in tolerance"

    def test_past_threshold_is_silent(self, db_session_factory, now) -> None:
        with db_session_factory() as session:
            _put_heartbeat(
                session,
                age_seconds=DEFAULT_SILENT_THRESHOLD_SECONDS + 1,
                now=now,
            )
            status = inspect_heartbeat(session, now=now)
        assert status.is_silent is True
        assert status.age_seconds > status.threshold_seconds

    def test_never_beaten_is_silent(self, db_session_factory, now) -> None:
        with db_session_factory() as session:
            _put_heartbeat(session, age_seconds=None, now=now)
            status = inspect_heartbeat(session, now=now)
        assert status.is_silent is True
        assert status.has_ever_beaten is False
        assert status.last_heartbeat_at is None


# ---------------------------------------------------------------------------
# DmsMetaMonitor — alert + dedup behaviour
# ---------------------------------------------------------------------------


class TestSilentFailureAlert:
    def test_dms_process_death_fires_silent_failure(
        self, db_session_factory, publisher, captured, now
    ) -> None:
        """The kickoff-equivalent test: simulate a DMS process death (heartbeat
        stale beyond 2x interval) and confirm a single silent_failure alert
        fires at the next meta-monitor check."""
        with db_session_factory() as session:
            # Heartbeat is stale by exactly 2*interval + 1s — past threshold.
            _put_heartbeat(
                session,
                age_seconds=DEFAULT_SILENT_THRESHOLD_SECONDS + 1,
                now=now,
            )

        monitor = DmsMetaMonitor(publish=publisher, now_func=lambda: now)
        with db_session_factory() as session:
            status = monitor.check(session)

        assert status.is_silent is True
        assert len(captured) == 1
        channel, content, metadata = captured[0]
        assert channel == AGORA_CHANNEL
        assert metadata["event_class"] == AGORA_EVENT_SILENT
        assert metadata["age_seconds"] > metadata["threshold_seconds"]
        assert "DEAD MAN'S SWITCH SILENT" in content
        assert monitor.currently_silent is True

    def test_silent_failure_within_2x_interval(
        self, db_session_factory, publisher, captured, now
    ) -> None:
        """Document the 2x-interval contract from the directive: no alert at
        1.5x, alert at 2x+1s."""
        from src.risk.heartbeat import CHECK_INTERVAL

        # 1.5x — not yet silent
        with db_session_factory() as session:
            _put_heartbeat(session, age_seconds=int(1.5 * CHECK_INTERVAL), now=now)
        monitor = DmsMetaMonitor(publish=publisher, now_func=lambda: now)
        with db_session_factory() as session:
            status = monitor.check(session)
        assert status.is_silent is False
        assert captured == []

        # 2x + 1s — must fire
        with db_session_factory() as session:
            _put_heartbeat(
                session,
                age_seconds=int(SILENT_FAILURE_MULTIPLIER * CHECK_INTERVAL) + 1,
                now=now,
            )
        with db_session_factory() as session:
            monitor.check(session)
        assert len(captured) == 1


class TestAlertDedup:
    def test_repeated_check_while_silent_does_not_spam(
        self, db_session_factory, publisher, captured, now
    ) -> None:
        with db_session_factory() as session:
            _put_heartbeat(
                session,
                age_seconds=DEFAULT_SILENT_THRESHOLD_SECONDS + 60,
                now=now,
            )
        monitor = DmsMetaMonitor(publish=publisher, now_func=lambda: now)
        for _ in range(5):
            with db_session_factory() as session:
                monitor.check(session)
        assert len(captured) == 1  # one alert, not five

    def test_recovery_emits_single_recovered_alert(
        self, db_session_factory, publisher, captured, now
    ) -> None:
        # Start silent.
        with db_session_factory() as session:
            _put_heartbeat(
                session,
                age_seconds=DEFAULT_SILENT_THRESHOLD_SECONDS + 60,
                now=now,
            )
        monitor = DmsMetaMonitor(publish=publisher, now_func=lambda: now)
        with db_session_factory() as session:
            monitor.check(session)
        assert captured[-1][2]["event_class"] == AGORA_EVENT_SILENT

        # DMS recovers — heartbeat fresh again.
        with db_session_factory() as session:
            _put_heartbeat(session, age_seconds=5, now=now)
        with db_session_factory() as session:
            monitor.check(session)
        assert len(captured) == 2
        assert captured[-1][2]["event_class"] == AGORA_EVENT_RECOVERED

        # Subsequent checks while still healthy do not spam.
        with db_session_factory() as session:
            monitor.check(session)
        assert len(captured) == 2


class TestFreshHeartbeatNoAlert:
    def test_fresh_check_emits_nothing(
        self, db_session_factory, publisher, captured, now
    ) -> None:
        with db_session_factory() as session:
            _put_heartbeat(session, age_seconds=10, now=now)
        monitor = DmsMetaMonitor(publish=publisher, now_func=lambda: now)
        with db_session_factory() as session:
            monitor.check(session)
        assert captured == []
        assert monitor.currently_silent is False


class TestMonitorRobustness:
    def test_publish_failure_does_not_propagate(
        self, db_session_factory, now
    ) -> None:
        """A broken alert sink must not take down the host process."""
        def _bad_publish(*args, **kwargs):
            raise RuntimeError("alert sink down")

        with db_session_factory() as session:
            _put_heartbeat(
                session,
                age_seconds=DEFAULT_SILENT_THRESHOLD_SECONDS + 1,
                now=now,
            )
        monitor = DmsMetaMonitor(publish=_bad_publish, now_func=lambda: now)
        with db_session_factory() as session:
            status = monitor.check(session)
        assert status.is_silent is True

    def test_async_publisher_supported(
        self, db_session_factory, now
    ) -> None:
        """post_to_agora is async; the meta-monitor must accept coroutines."""
        captured: list = []

        async def _async_pub(channel, content, metadata):
            captured.append((channel, content, metadata))

        with db_session_factory() as session:
            _put_heartbeat(
                session,
                age_seconds=DEFAULT_SILENT_THRESHOLD_SECONDS + 1,
                now=now,
            )
        monitor = DmsMetaMonitor(publish=_async_pub, now_func=lambda: now)
        with db_session_factory() as session:
            monitor.check(session)

        assert len(captured) == 1
        assert captured[0][2]["event_class"] == AGORA_EVENT_SILENT


# ---------------------------------------------------------------------------
# Heartbeat process-side fix: writes are no longer self-gated
# ---------------------------------------------------------------------------


class TestHeartbeatWritesUnconditionally:
    """The original bug: check_heartbeat_freshness gated _update_heartbeat,
    so a stale heartbeat blocked recovery. The fix removes that self-check
    from _run_checks() — the DMS now writes whenever Postgres + Redis pass.

    These tests assert the surface that other components rely on:
        1. `_run_checks` does NOT call any freshness check that could
           self-defeat the writer.
        2. `consecutive_failures` no longer tracks a `stale_heartbeat` key.
    """

    def test_run_checks_only_tracks_external_dependencies(self) -> None:
        from src.risk import heartbeat as hb
        # Internal dict must not include the self-defeating key.
        assert set(hb.consecutive_failures.keys()) == {"postgres", "redis"}

    def test_run_checks_function_does_not_invoke_freshness(self) -> None:
        # The freshness logic moved out of the live-write path. The
        # surviving function in heartbeat.py used to be called
        # `check_heartbeat_freshness` — we removed it entirely so future
        # readers don't accidentally re-introduce the loop.
        from src.risk import heartbeat as hb
        assert not hasattr(hb, "check_heartbeat_freshness"), (
            "check_heartbeat_freshness must not exist on heartbeat module — "
            "it was the self-defeating self-check that left the colony "
            "without a heartbeat for 39 days. If you re-introduce a "
            "freshness check, put it in dms_meta_monitor.py, not here."
        )
