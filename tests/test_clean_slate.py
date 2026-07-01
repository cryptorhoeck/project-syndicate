"""clean_slate — is a clean slate actually clean? (guard + Postgres integration)

The CASCADE behaviour that makes this hard only exists on Postgres, and the suite runs
on SQLite — so a green SQLite suite is NOT proof (the exact trap from the timezone bug).
The real proof is the opt-in Postgres integration test below, run against the configured
DB. It seeds the previously-MISSED tables + dirties the ticker counter, then asserts
clean_slate leaves every operational table empty, the seeds intact (asserted against the
`PRESERVE_ENTIRELY` constant itself, so the test and the code can't drift apart), the
counter zeroed, and Genesis preserved.

The Postgres test is guarded — it WIPES the target DB — so it's skipped unless
RUN_CLEAN_SLATE_PG=1.
"""

import os

import pytest

pg_only = pytest.mark.skipif(
    os.getenv("RUN_CLEAN_SLATE_PG") != "1",
    reason="destructive Postgres integration — set RUN_CLEAN_SLATE_PG=1 to run",
)


# --- SQLite-safe: lock the allow-list invariants (no DB needed) ---

def test_allow_lists_are_sane():
    from scripts.clean_slate import PRESERVE_ENTIRELY, RESET_IN_PLACE
    assert PRESERVE_ENTIRELY.isdisjoint(RESET_IN_PLACE)
    # alembic_version MUST be preserved — wiping it makes the DB forget its migration head.
    assert "alembic_version" in PRESERVE_ENTIRELY
    # agora_channels resets in place (its message_count is the ticker bug), never fully wiped.
    assert "agora_channels" in RESET_IN_PLACE
    assert "agents" not in PRESERVE_ENTIRELY  # agents is handled specially, not preserved wholesale


# --- Postgres integration: the real proof (opt-in, destructive) ---

@pg_only
def test_clean_slate_is_actually_clean():
    from sqlalchemy import create_engine, text
    from src.common.config import config
    from scripts.clean_slate import clean_slate, PRESERVE_ENTIRELY, RESET_IN_PLACE

    eng = create_engine(config.database_url)
    with eng.begin() as c:
        c.execute(text("UPDATE agora_channels SET message_count = 77"))  # dirty the ticker

    res = clean_slate(config.database_url)

    with eng.connect() as c:
        # 1. Every operational (wiped) table is empty.
        for t in res["wiped_tables"]:
            assert c.execute(text(f'SELECT count(*) FROM "{t}"')).scalar() == 0, f"{t} not wiped"
        # 2. No PRESERVE/RESET table was in the wipe set (asserted against the constants).
        assert PRESERVE_ENTIRELY.isdisjoint(res["wiped_tables"])
        assert RESET_IN_PLACE.isdisjoint(res["wiped_tables"])
        # 3. Seeds survived the CASCADE that used to drop parameter_registry + forget migrations.
        assert c.execute(text("SELECT count(*) FROM parameter_registry")).scalar() > 0
        assert c.execute(text("SELECT count(*) FROM wire_sources")).scalar() > 0
        assert c.execute(text("SELECT count(*) FROM alembic_version")).scalar() == 1
        # 4. The ticker counter is zeroed.
        assert c.execute(text("SELECT COALESCE(sum(message_count), 0) FROM agora_channels")).scalar() == 0
        # 5. Genesis kept; every other agent gone.
        assert c.execute(text("SELECT count(*) FROM agents WHERE id = 0")).scalar() == 1
        assert c.execute(text("SELECT count(*) FROM agents WHERE id != 0")).scalar() == 0
    eng.dispose()


@pg_only
def test_guard_fails_loud_on_bad_allow_list(monkeypatch):
    """The silent-drift guard: a preserve-list name that isn't a real table must raise,
    not silently proceed."""
    import scripts.clean_slate as cs
    from src.common.config import config
    monkeypatch.setattr(cs, "PRESERVE_ENTIRELY", cs.PRESERVE_ENTIRELY | {"not_a_real_table_xyz"})
    with pytest.raises(RuntimeError, match="don't exist"):
        cs.clean_slate(config.database_url)
