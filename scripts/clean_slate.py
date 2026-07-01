"""Clean-slate reset for a Project Syndicate colony — self-maintaining.

Wipes ALL operational data and resets the colony to a fresh, bootable state, while
PRESERVING seed/config/reference data. The wipe set is DERIVED from the live schema
(every table minus an explicit preserve/reset allow-list), so it can never silently
drift out of sync with the model — the failure mode that repeatedly left operational
tables un-wiped (agent_genomes orphans) or accidentally destroyed seeds
(parameter_registry, via a CASCADE from system_improvement_proposals).

Categories (only the two allow-lists below are hand-maintained; a new SEED table is the
only thing that requires editing them — a new *operational* table is wiped automatically):

  PRESERVE_ENTIRELY : kept intact — seed / config / reference.
  RESET_IN_PLACE    : row(s) kept, values reset (system_state; agora_channels counters).
  agents            : Genesis (id=0) kept + reset; all others deleted.
  WIPE              : everything else (the derived remainder).

CASCADE safety: a naive `TRUNCATE <wipe> CASCADE` drags in any table with an FK into the
wipe set — including protected ones (parameter_registry → SIPs; agents → dynasties, which
would take Genesis). So protected→wipe FKs are NULLed, and the wipe tables they reference
are excluded from the CASCADE truncate and DELETEd separately (after their dependents are
gone). All of this is derived from information_schema, so new such FKs are handled
automatically.

A boot then re-registers Genesis and re-creates singletons (colony_maturity → nascent),
so the result boots clean with ZERO manual re-seeding.
"""

import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from dotenv import load_dotenv

load_dotenv(os.path.join(PROJECT_ROOT, ".env"), override=True)

import psycopg2

from src.common.config import config

# --- The ONLY hand-maintained lists. Adding a SEED table => add it here. ---
PRESERVE_ENTIRELY = {
    "wire_sources",        # seeded external intel feeds
    "parameter_registry",  # governance parameter definitions (config)
    "alembic_version",     # migration head — wiping it makes the DB forget which
                           # migrations it's on (a landmine atop the broken-chain issue).
                           # DO NOT WIPE.
    "library_entries",     # persistent knowledge base (accumulates across runs by design)
}
RESET_IN_PLACE = {"system_state", "agora_channels"}
GENESIS_ID = 0


def _all_public_tables(cur) -> set[str]:
    cur.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
    return {r[0] for r in cur.fetchall()}


def _foreign_keys(cur) -> list[tuple[str, str, str]]:
    """(referencing_table, referencing_column, referenced_table) for every public FK."""
    cur.execute(
        """
        SELECT tc.table_name, kcu.column_name, ccu.table_name
        FROM information_schema.table_constraints tc
        JOIN information_schema.key_column_usage kcu
             ON tc.constraint_name = kcu.constraint_name AND tc.table_schema = kcu.table_schema
        JOIN information_schema.constraint_column_usage ccu
             ON tc.constraint_name = ccu.constraint_name AND tc.table_schema = ccu.table_schema
        WHERE tc.constraint_type = 'FOREIGN KEY' AND tc.table_schema = 'public'
        """
    )
    return [(r[0], r[1], r[2]) for r in cur.fetchall()]


def clean_slate(database_url: str) -> dict:
    """Reset the colony to a fresh, bootable state. Returns a summary dict."""
    starting = config.starting_treasury
    conn = psycopg2.connect(database_url)
    conn.autocommit = True
    cur = conn.cursor()

    # Terminate other (non-idle) backends so TRUNCATE isn't blocked by a stuck lock.
    cur.execute(
        """SELECT pg_terminate_backend(pid) FROM pg_stat_activity
           WHERE datname = current_database() AND pid <> pg_backend_pid() AND state <> 'idle'"""
    )

    all_tables = _all_public_tables(cur)
    special = {"agents"}
    protected = PRESERVE_ENTIRELY | RESET_IN_PLACE | special
    wipe = all_tables - protected

    # Guard against silent drift: every allow-listed name must be a real table.
    bad = protected - all_tables
    if bad:
        raise RuntimeError(
            f"clean_slate allow-lists name tables that don't exist: {sorted(bad)}. "
            f"Fix the lists (PRESERVE_ENTIRELY / RESET_IN_PLACE) before running."
        )

    # Protect the protected tables from CASCADE: NULL every protected->wipe FK, and hold
    # the referenced wipe tables out of the CASCADE truncate (TRUNCATE CASCADE drags a
    # referencing table in regardless of the nulled values). Derived, so new FKs are safe.
    referenced_by_protected: set[str] = set()
    for tbl, col, ref in _foreign_keys(cur):
        if tbl in protected and ref in wipe:
            cur.execute(f'UPDATE "{tbl}" SET "{col}" = NULL')
            referenced_by_protected.add(ref)

    # Wipe the bulk with one CASCADE truncate; DELETE the held-out tables (their dependents
    # are now truncated, their protected referrers nulled — so no FK violation).
    truncatable = sorted(wipe - referenced_by_protected)
    if truncatable:
        cur.execute(
            "TRUNCATE " + ", ".join(f'"{t}"' for t in truncatable) + " RESTART IDENTITY CASCADE"
        )
    for t in sorted(referenced_by_protected):
        cur.execute(f'DELETE FROM "{t}"')

    # agents: delete all but Genesis, then reset Genesis's treasury.
    cur.execute(f"DELETE FROM agents WHERE id <> {GENESIS_ID}")
    cur.execute(
        f"""UPDATE agents SET capital_allocated = {starting}, capital_current = {starting}
            WHERE id = {GENESIS_ID}"""
    )

    # Reset-in-place singletons.
    cur.execute(
        f"""UPDATE system_state SET total_treasury = {starting}, peak_treasury = {starting},
            treasury_currency = 'CAD', active_agent_count = 0, last_heartbeat_at = NULL,
            current_regime = 'unknown'"""
    )
    cur.execute("UPDATE agora_channels SET message_count = 0")  # the ticker counter bug

    cur.close()
    conn.close()
    return {
        "wiped": len(truncatable) + len(referenced_by_protected),
        "wiped_tables": sorted(wipe),
        "preserved": sorted(PRESERVE_ENTIRELY),
        "reset_in_place": sorted(RESET_IN_PLACE),
        "delete_wiped": sorted(referenced_by_protected),
    }


if __name__ == "__main__":
    result = clean_slate(config.database_url)
    print(f"Clean slate complete. Wiped {result['wiped']} operational tables.")
    print(f"  Preserved      : {', '.join(result['preserved'])}")
    print(f"  Reset in place : {', '.join(result['reset_in_place'])}")
    if result["delete_wiped"]:
        print(f"  (DELETE-wiped to shield seeds from CASCADE: {', '.join(result['delete_wiped'])})")
