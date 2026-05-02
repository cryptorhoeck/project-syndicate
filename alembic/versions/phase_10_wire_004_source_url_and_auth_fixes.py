"""Phase 10 hotfix: source URL + auth corrections from live validation

Revision ID: phase_10_wire_004
Revises: phase_10_wire_003
Create Date: 2026-05-02 00:00:00.000000

Live Step B run found:
  - kraken_announcements: /category/announcement/feed is 404; switched to /feed/
  - cryptopanic: now requires auth_token even for public posts
  - trading_economics: guest tier returns 410 Gone; requires paid key

This migration aligns wire_sources rows with the code changes shipped in
the same commit. After upgrade, sources gated on absent keys will mark
themselves degraded (intended) until the corresponding env var is set.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "phase_10_wire_004"
down_revision: Union[str, Sequence[str], None] = "phase_10_wire_003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()

    # Kraken announcements: switch URL to the all-blog feed.
    bind.execute(
        sa.text(
            "UPDATE wire_sources SET base_url = :url "
            "WHERE name = 'kraken_announcements'"
        ),
        {"url": "https://blog.kraken.com/feed/"},
    )

    # CryptoPanic now requires an auth token.
    bind.execute(
        sa.text(
            "UPDATE wire_sources "
            "SET requires_api_key = TRUE, api_key_env_var = :var, "
            "    display_name = 'CryptoPanic' "
            "WHERE name = 'cryptopanic'"
        ),
        {"var": "CRYPTOPANIC_API_KEY"},
    )

    # TradingEconomics guest tier deprecated.
    bind.execute(
        sa.text(
            "UPDATE wire_sources "
            "SET requires_api_key = TRUE, api_key_env_var = :var "
            "WHERE name = 'trading_economics'"
        ),
        {"var": "TRADINGECONOMICS_API_KEY"},
    )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text(
            "UPDATE wire_sources SET base_url = :url "
            "WHERE name = 'kraken_announcements'"
        ),
        {"url": "https://blog.kraken.com/category/announcement/feed"},
    )
    bind.execute(
        sa.text(
            "UPDATE wire_sources "
            "SET requires_api_key = FALSE, api_key_env_var = NULL, "
            "    display_name = 'CryptoPanic (Free)' "
            "WHERE name = 'cryptopanic'"
        )
    )
    bind.execute(
        sa.text(
            "UPDATE wire_sources "
            "SET requires_api_key = FALSE, api_key_env_var = NULL "
            "WHERE name = 'trading_economics'"
        )
    )
