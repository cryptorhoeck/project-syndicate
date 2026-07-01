"""Build a complete, bootable, UTC-born Project Syndicate database from nothing.

============================ THIS IS THE BUILD PATH ============================
The ORM models (``Base.metadata``) are the schema source of truth. This script creates
every table directly via ``create_all``.

The Alembic migration chain is HISTORICAL and does NOT build a database from base: it
never creates 7 live tables (agent_genomes, system_improvement_proposals, agent_alliances,
agent_tools, intel_accuracy_tracking, intel_challenges, sandbox_executions), so
``alembic upgrade head`` on an empty DB dies at Phase 9A ("relation
system_improvement_proposals does not exist"). Do NOT expect it to work — use this script.
The chain is *stamped* to head afterward (not run) so the DB reports the latest revision
and stays open to any FUTURE incremental migration.

Seed ownership (confirmed in the bytes — the two owners must never collide):
  * init_fresh_db OWNS first-creation of the data-migration seeds — ``create_all`` cannot
    run data migrations, so these would otherwise be empty:
      - parameter_registry              (via scripts.seed_parameter_registry.seed)
      - wire_sources + wire_source_health (the 8 launch sources)
      - agora_channels                  (the 10 default channels; boot's
        AgoraService._ensure_channel_exists RAISES for a missing *system* channel, so the
        3 system channels MUST pre-exist)
  * boot OWNS get-or-create of the runtime singletons (idempotent), so this script does
    NOT touch them:
      - system_state         (genesis.py get-or-creates)
      - Genesis agent (id=0) (genesis.py get-or-creates)

Usage:  python scripts/init_fresh_db.py [--force]
"""

from __future__ import annotations

import argparse
import os
import sys
from urllib.parse import urlparse

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from dotenv import load_dotenv

load_dotenv(os.path.join(PROJECT_ROOT, ".env"), override=True)

from sqlalchemy import create_engine, inspect, text

from src.common.config import config
from src.common.models import Base
import src.wire.models  # noqa: F401 — registers wire tables on Base.metadata


# --- Seed data. Source of truth for a fresh build; the historical migrations carry copies. ---

# The 8 launch sources (mirrors phase_10_wire_002). Tier A / no-key sources start enabled.
WIRE_SOURCES = [
    # name, display_name, tier, interval_s, enabled, needs_key, key_env, base_url, config_json
    ("kraken_announcements", "Kraken Announcements", "A", 300, True, False, None, "https://blog.kraken.com/category/announcement/feed", '{"severity_floor": 3}'),
    ("cryptopanic", "CryptoPanic (Free)", "A", 600, True, False, None, "https://cryptopanic.com/api/v1/posts/", '{"public": true}'),
    ("defillama", "DefiLlama", "A", 1800, True, False, None, "https://api.llama.fi", '{"tvl_delta_threshold": 0.05}'),
    ("etherscan_transfers", "Etherscan Large Transfers", "A", 900, False, True, "ETHERSCAN_API_KEY", "https://api.etherscan.io/api", '{"min_value_eth": 1000}'),
    ("funding_rates", "Kraken Perp Funding Rates", "A", 300, False, False, None, "ccxt://kraken", '{"extreme_threshold": 0.001}'),
    ("fred", "FRED Macro Series", "B", 86400, False, True, "FRED_API_KEY", "https://api.stlouisfed.org/fred/", '{"series": ["DGS10", "DTWEXBGS", "VIXCLS", "M2SL"]}'),
    ("trading_economics", "TradingEconomics Calendar", "B", 86400, False, False, None, "https://api.tradingeconomics.com/calendar", '{"guest_tier": true, "preceded_by_hours": 4}'),
    ("fear_greed", "Fear & Greed Index", "B", 86400, False, False, None, "https://api.alternative.me/fng/", "{}"),
]

# The 10 default channels (mirrors fdc6e51f4c04). The 3 is_system channels are REQUIRED by boot.
AGORA_CHANNELS = [
    ("market-intel", "Market discoveries, price movements, opportunities", False),
    ("strategy-proposals", "Formal strategy proposals for debate", False),
    ("strategy-debate", "Critiques, counter-arguments, stress tests", False),
    ("trade-signals", "Pre-trade announcements: I'm about to trade X because Y", False),
    ("trade-results", "Post-trade outcomes, P&L updates", False),
    ("system-alerts", "Warden alerts, Dead Man's Switch, circuit breaker events", True),
    ("genesis-log", "Genesis spawn/kill/evaluate decisions, capital allocation", True),
    ("agent-chat", "Free-form agent discussion, ideas, collaboration", False),
    ("sip-proposals", "System Improvement Proposals", False),
    ("daily-report", "Genesis daily narrative report", True),
]


def _alembic_head() -> str:
    """The chain's head revision, resolved dynamically (never hardcoded)."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    cfg = Config(os.path.join(PROJECT_ROOT, "alembic.ini"))
    return ScriptDirectory.from_config(cfg).get_current_head()


def init_fresh_db(database_url: str, *, force: bool = False, stamp: bool = True) -> dict:
    """Create + seed a fresh database. Returns a summary dict.

    Raises RuntimeError if the target already has tables and ``force`` is False (no-clobber).
    """
    engine = create_engine(database_url)

    existing = [t for t in inspect(engine).get_table_names() if t != "alembic_version"]
    if existing and not force:
        engine.dispose()
        raise RuntimeError(
            f"Target DB already has {len(existing)} tables — refusing to clobber. Pass "
            f"force=True only if you intend to build over an existing schema."
        )

    # 1. Schema: every model table, straight from the source of truth.
    Base.metadata.create_all(engine)

    # 2. Born on UTC (closes the clock-fix loop; pairs with the connect-time listener).
    #    ALTER DATABASE cannot run inside a transaction, so use an AUTOCOMMIT connection.
    db_name = urlparse(database_url).path.lstrip("/")
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        conn.execute(text(f'ALTER DATABASE "{db_name}" SET timezone TO \'UTC\''))

    # 3. Seeds init_fresh_db owns (create_all cannot run the data migrations that seed these).
    from scripts.seed_parameter_registry import seed as seed_parameter_registry

    seed_parameter_registry(database_url)

    with engine.begin() as conn:
        for (name, display, tier, interval, enabled, needs_key, key_env, url, cfg_json) in WIRE_SOURCES:
            conn.execute(
                text(
                    """INSERT INTO wire_sources
                       (name, display_name, tier, fetch_interval_seconds, enabled,
                        requires_api_key, api_key_env_var, base_url, config_json)
                       VALUES (:name, :display, :tier, :interval, :enabled,
                               :needs_key, :key_env, :url, CAST(:cfg AS JSON))"""
                ),
                {"name": name, "display": display, "tier": tier, "interval": interval,
                 "enabled": enabled, "needs_key": needs_key, "key_env": key_env,
                 "url": url, "cfg": cfg_json},
            )
            conn.execute(
                text(
                    """INSERT INTO wire_source_health (source_id, status)
                       SELECT id, 'unknown' FROM wire_sources WHERE name = :name"""
                ),
                {"name": name},
            )
        for (name, description, is_system) in AGORA_CHANNELS:
            conn.execute(
                text(
                    """INSERT INTO agora_channels (name, description, is_system, message_count)
                       VALUES (:name, :description, :is_system, 0)"""
                ),
                {"name": name, "description": description, "is_system": is_system},
            )

    # 4. Stamp the chain to head (NOT run) so the DB reports the latest revision and stays
    #    open to future incremental migrations. Written directly on THIS engine because
    #    alembic's own `stamp` forces the app DATABASE_URL via env.py (wrong DB for a temp
    #    build). Head is resolved from the script graph, never hardcoded.
    if stamp:
        head = _alembic_head()
        with engine.begin() as conn:
            conn.execute(
                text(
                    "CREATE TABLE IF NOT EXISTS alembic_version ("
                    "version_num VARCHAR(32) NOT NULL, "
                    "CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num))"
                )
            )
            conn.execute(text("DELETE FROM alembic_version"))
            conn.execute(text("INSERT INTO alembic_version (version_num) VALUES (:v)"), {"v": head})

    engine.dispose()
    return {
        "tables_created": len(Base.metadata.tables),
        "wire_sources": len(WIRE_SOURCES),
        "agora_channels": len(AGORA_CHANNELS),
        "seeded": ["parameter_registry", "wire_sources", "wire_source_health", "agora_channels"],
        "boot_owned_not_touched": ["system_state", "agents(Genesis id=0)"],
        "stamped_head": _alembic_head() if stamp else None,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Build a fresh Project Syndicate database.")
    parser.add_argument("--force", action="store_true", help="build even if the DB already has tables")
    args = parser.parse_args()
    result = init_fresh_db(config.database_url, force=args.force)
    print("Fresh DB built (THIS is the build path; migrations are historical, stamped to head).")
    print(f"  Tables created : {result['tables_created']}")
    print(f"  Seeded         : {', '.join(result['seeded'])}")
    print(f"  Wire sources   : {result['wire_sources']} | Agora channels: {result['agora_channels']}")
    print(f"  Stamped head   : {result['stamped_head']}")
    print(f"  Boot get-or-creates (not touched here): {', '.join(result['boot_owned_not_touched'])}")
