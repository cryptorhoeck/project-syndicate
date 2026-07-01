"""init_fresh_db — does a from-nothing build produce a complete, bootable, UTC-born DB?

The migration chain can't build from base (it never creates 7 live tables), so
``create_all`` is the official build path. This proves it end-to-end: create a genuinely
SEPARATE temp database, build it from empty, and assert it is complete (all model tables),
seeded (the data-migration seeds init_fresh_db owns), born on UTC at the *database* level,
and stamped at head — while the boot-owned singletons are deliberately untouched. The temp
DB is dropped in a ``finally`` so a failed run can't orphan it, and the live DB is never
touched.

Guarded — it CREATEs and DROPs a database — so it's skipped unless RUN_INIT_FRESH_PG=1.
"""

import os
from urllib.parse import urlparse

import pytest

pg_only = pytest.mark.skipif(
    os.getenv("RUN_INIT_FRESH_PG") != "1",
    reason="creates/drops a temp database — set RUN_INIT_FRESH_PG=1 to run",
)

TMP_DB = "syndicate_initfresh_test"


def _admin_exec(admin_url: str, sql: str) -> None:
    import psycopg2

    conn = psycopg2.connect(admin_url)
    conn.autocommit = True  # CREATE/DROP DATABASE can't run in a transaction
    try:
        conn.cursor().execute(sql)
    finally:
        conn.close()


@pg_only
def test_init_fresh_db_builds_bootable_db_from_nothing():
    from sqlalchemy import create_engine, inspect, text

    from src.common.config import config
    from src.common.models import Base
    import src.wire.models  # noqa: F401 — complete Base.metadata
    from scripts.init_fresh_db import AGORA_CHANNELS, WIRE_SOURCES, _alembic_head, init_fresh_db

    parsed = urlparse(config.database_url)
    admin_url = config.database_url.replace(parsed.path, "/postgres")  # neutral DB to admin from
    tmp_url = config.database_url.replace(parsed.path, "/" + TMP_DB)

    _admin_exec(admin_url, f"DROP DATABASE IF EXISTS {TMP_DB}")
    _admin_exec(admin_url, f"CREATE DATABASE {TMP_DB}")
    try:
        init_fresh_db(tmp_url)

        eng = create_engine(tmp_url)
        try:
            tables = set(inspect(eng).get_table_names())
            # 1. Complete schema built from nothing — every model table present.
            missing = set(Base.metadata.tables.keys()) - tables
            assert not missing, f"init_fresh_db left tables uncreated: {sorted(missing)}"

            with eng.connect() as c:
                # 2. The seeds init_fresh_db owns are populated.
                assert c.execute(text("SELECT count(*) FROM parameter_registry")).scalar() > 0
                assert c.execute(text("SELECT count(*) FROM wire_sources")).scalar() == len(WIRE_SOURCES)
                assert c.execute(text("SELECT count(*) FROM wire_source_health")).scalar() == len(WIRE_SOURCES)
                assert c.execute(text("SELECT count(*) FROM agora_channels")).scalar() == len(AGORA_CHANNELS)
                # 3. The 3 system channels exist — boot raises without them.
                assert c.execute(text("SELECT count(*) FROM agora_channels WHERE is_system = true")).scalar() == 3

                # 4. Boot-owned singletons are NOT seeded here (disjoint ownership — no collision).
                assert c.execute(text("SELECT count(*) FROM system_state")).scalar() == 0
                assert c.execute(text("SELECT count(*) FROM agents")).scalar() == 0

                # 5. Born on UTC at the DATABASE level (not just the session listener).
                datcfg = c.execute(
                    text(
                        "SELECT string_agg(o, ',') FROM ("
                        "  SELECT unnest(setconfig) o FROM pg_db_role_setting "
                        "  WHERE setdatabase = (SELECT oid FROM pg_database WHERE datname = :n)"
                        ") x"
                    ),
                    {"n": TMP_DB},
                ).scalar() or ""
                assert "timezone=utc" in datcfg.lower(), f"DB not born on UTC: {datcfg!r}"

                # 6. Stamped at head — reports latest revision, open to future migrations.
                assert c.execute(text("SELECT version_num FROM alembic_version")).scalar() == _alembic_head()
        finally:
            eng.dispose()
    finally:
        _admin_exec(admin_url, f"DROP DATABASE IF EXISTS {TMP_DB}")  # teardown, always
