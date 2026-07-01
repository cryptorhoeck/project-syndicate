"""UTC session-timezone guard + connect listener (src.common.db_timezone).

The Postgres session must be UTC so naive timestamps are stored UTC everywhere.
The suite runs on SQLite, which cannot reproduce Postgres's tz-on-insert behaviour,
so a green suite is NOT proof the live session is UTC. The durable guard is a
boot-time assertion (``assert_session_utc``) that fails LOUD if the session drifts
to local time. These tests lock the guard's logic (SQLite-testable) and confirm the
global connect listener doesn't break the non-Postgres test backend.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool

from src.common.db_timezone import SessionTimezoneError, assert_session_utc


def _conn_returning(tz: str):
    conn = MagicMock()
    conn.execute.return_value.scalar.return_value = tz
    return conn


def test_guard_passes_when_session_is_utc():
    assert assert_session_utc(_conn_returning("UTC")) == "UTC"


def test_guard_is_case_insensitive():
    assert assert_session_utc(_conn_returning("utc")) == "utc"


def test_guard_raises_loudly_on_local_timezone():
    with pytest.raises(SessionTimezoneError) as ei:
        assert_session_utc(_conn_returning("America/Halifax"))
    # The error names the offending tz and the required one — actionable at boot.
    msg = str(ei.value)
    assert "America/Halifax" in msg
    assert "UTC" in msg


def test_connect_listener_does_not_break_sqlite():
    """The global connect listener fires for every engine, including SQLite. Its
    psycopg2 guard must skip non-Postgres so the test backend connects cleanly with
    no (unsupported) SET TIME ZONE."""
    import src.common.db_timezone  # noqa: F401 — ensures the listener is registered
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    with engine.connect() as c:
        assert c.execute(text("SELECT 1")).scalar() == 1
    engine.dispose()
