"""Phase 10: The Wire — seed wire_sources catalog

Revision ID: phase_10_wire_002
Revises: phase_10_wire_001
Create Date: 2026-05-01 00:00:01.000000

Populates wire_sources for all 8 launch sources. Tier 1 sources (Kraken
announcements, CryptoPanic, DefiLlama) are enabled. Tier 2 sources start
disabled and are flipped to enabled when their implementations land in
build Tier 2.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "phase_10_wire_002"
down_revision: Union[str, Sequence[str], None] = "phase_10_wire_001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


SEED_ROWS = [
    # name, display_name, tier, fetch_interval_seconds, enabled, requires_api_key,
    # api_key_env_var, base_url, config_json
    (
        "kraken_announcements",
        "Kraken Announcements",
        "A",
        300,
        True,
        False,
        None,
        "https://blog.kraken.com/category/announcement/feed",
        '{"severity_floor": 3}',
    ),
    (
        "cryptopanic",
        "CryptoPanic (Free)",
        "A",
        600,
        True,
        False,
        None,
        "https://cryptopanic.com/api/v1/posts/",
        '{"public": true}',
    ),
    (
        "defillama",
        "DefiLlama",
        "A",
        1800,
        True,
        False,
        None,
        "https://api.llama.fi",
        '{"tvl_delta_threshold": 0.05}',
    ),
    (
        "etherscan_transfers",
        "Etherscan Large Transfers",
        "A",
        900,
        False,
        True,
        "ETHERSCAN_API_KEY",
        "https://api.etherscan.io/api",
        '{"min_value_eth": 1000}',
    ),
    (
        "funding_rates",
        "Kraken Perp Funding Rates",
        "A",
        300,
        False,
        False,
        None,
        "ccxt://kraken",
        '{"extreme_threshold": 0.001}',
    ),
    (
        "fred",
        "FRED Macro Series",
        "B",
        86400,
        False,
        True,
        "FRED_API_KEY",
        "https://api.stlouisfed.org/fred/",
        '{"series": ["DGS10", "DTWEXBGS", "VIXCLS", "M2SL"]}',
    ),
    (
        "trading_economics",
        "TradingEconomics Calendar",
        "B",
        86400,
        False,
        False,
        None,
        "https://api.tradingeconomics.com/calendar",
        '{"guest_tier": true, "preceded_by_hours": 4}',
    ),
    (
        "fear_greed",
        "Fear & Greed Index",
        "B",
        86400,
        False,
        False,
        None,
        "https://api.alternative.me/fng/",
        "{}",
    ),
]


def upgrade() -> None:
    bind = op.get_bind()
    for (
        name,
        display_name,
        tier,
        fetch_interval_seconds,
        enabled,
        requires_api_key,
        api_key_env_var,
        base_url,
        config_json,
    ) in SEED_ROWS:
        bind.execute(
            sa.text(
                """
                INSERT INTO wire_sources (
                    name, display_name, tier, fetch_interval_seconds,
                    enabled, requires_api_key, api_key_env_var,
                    base_url, config_json
                )
                VALUES (
                    :name, :display_name, :tier, :fetch_interval_seconds,
                    :enabled, :requires_api_key, :api_key_env_var,
                    :base_url, CAST(:config_json AS JSON)
                )
                """
            ),
            {
                "name": name,
                "display_name": display_name,
                "tier": tier,
                "fetch_interval_seconds": fetch_interval_seconds,
                "enabled": enabled,
                "requires_api_key": requires_api_key,
                "api_key_env_var": api_key_env_var,
                "base_url": base_url,
                "config_json": config_json,
            },
        )

        # Initialize a health row per source.
        bind.execute(
            sa.text(
                """
                INSERT INTO wire_source_health (source_id, status)
                SELECT id, 'unknown' FROM wire_sources WHERE name = :name
                """
            ),
            {"name": name},
        )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(sa.text("DELETE FROM wire_source_health"))
    bind.execute(sa.text("DELETE FROM wire_sources"))
