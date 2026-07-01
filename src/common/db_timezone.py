"""Pin every Postgres connection to a UTC session timezone.

Root cause of the maiden-launch timestamp skew: the Postgres server session
timezone defaulted to the OS locale (``America/Halifax``, UTC-3). Naive
``DateTime`` columns therefore stored LOCAL wall-time — and psycopg2 even
localises tz-aware ``datetime.now(timezone.utc)`` values to the session timezone
on insert into a naive column, stripping the tzinfo — so code that reasons in UTC
read every age as ~3h too old (e.g. a 2-minute-old signal looked 3 hours stale).

The systemic fix is to make naive == UTC everywhere by pinning the session to UTC:
``func.now()`` then returns UTC and psycopg2 stops localising on insert. This is
enforced two ways (belt-and-suspenders):

1. A global SQLAlchemy ``connect`` listener (below) that runs ``SET TIME ZONE 'UTC'``
   on every new Postgres connection. Registered globally because runtime engines
   are created scattered across many modules (genesis_runner, warden, treasury,
   web/app, wire/cli, ...) — a global listener covers every engine, current and
   future, and can't be missed by an edit. Non-Postgres backends (SQLite in tests)
   are skipped.
2. A one-time DB default: ``ALTER DATABASE <db> SET timezone TO 'UTC';`` — so even
   connections that somehow bypass the listener inherit UTC.

And a boot-time guard (``assert_session_utc``) that fails LOUD if the live session
is not UTC — because the test suite runs on SQLite and structurally cannot
reproduce the Postgres tz-on-insert behaviour, so a green suite is not proof.
"""

from __future__ import annotations

import logging

from sqlalchemy import event, text
from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)


@event.listens_for(Engine, "connect")
def _pin_session_timezone_utc(dbapi_connection, connection_record) -> None:
    """Set the session TIME ZONE to UTC on every new Postgres (psycopg2) connection.

    SQLite (used by the test suite) has no ``SET TIME ZONE`` and needs no pin — its
    CURRENT_TIMESTAMP is already UTC and it does no tz-conversion on insert — so it
    is skipped by the driver-module guard.
    """
    if "psycopg2" not in type(dbapi_connection).__module__:
        return
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("SET TIME ZONE 'UTC'")
    finally:
        cursor.close()


class SessionTimezoneError(RuntimeError):
    """Raised at boot when the live DB session timezone is not UTC."""


def assert_session_utc(connection) -> str:
    """Boot guard — assert the live DB session timezone is UTC, else refuse to boot.

    The suite runs on SQLite, which cannot reproduce the Postgres tz-on-insert bug,
    so this structural check at startup is the durable guard against silent 3-hour
    drift: a wrong session timezone becomes a loud, obvious boot failure instead of
    quietly skewing every age by the UTC offset.

    Args:
        connection: a live SQLAlchemy Connection (e.g. ``session.connection()`` or
            ``engine.connect()``).

    Returns:
        The confirmed timezone string (``"UTC"``).

    Raises:
        SessionTimezoneError: if the session timezone is not UTC.
    """
    tz = connection.execute(text("SELECT current_setting('TimeZone')")).scalar()
    if str(tz).upper() != "UTC":
        raise SessionTimezoneError(
            f"DB session timezone is {tz!r}, not 'UTC'. Naive timestamps would be "
            f"stored in local time and every age would skew by the UTC offset "
            f"(the maiden-launch bug). Fix: ALTER DATABASE <db> SET timezone TO "
            f"'UTC'; and ensure src.common.db_timezone is imported so the connect-"
            f"time UTC pin is registered. Refusing to boot."
        )
    return tz
