"""
Kraken perp funding rates source.

Pulls current funding rates for major perp pairs via ccxt. Emits an item only
when the absolute funding rate exceeds the configured extreme threshold
(default 0.001 i.e. 0.1% per 8h interval). Severity is deterministic:

  >= 0.001 (0.1%) : severity 2
  >= 0.003 (0.3%) : severity 3 (rare extreme — crowded trade marker)

Direction tracks sign: positive funding -> longs paying shorts -> crowded long
-> bearish bias (mean reversion). Negative -> bullish bias.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Iterable

from src.wire.constants import (
    SEVERITY_MATERIAL,
    SEVERITY_NOTABLE,
    SOURCE_FUNDING_RATES,
)
from src.wire.sources.base import FetchedItem, SourceFetchError, WireSourceBase

logger = logging.getLogger(__name__)


DEFAULT_PAIRS: list[str] = ["BTC/USD:USD", "ETH/USD:USD"]


class FundingRatesSource(WireSourceBase):
    name = SOURCE_FUNDING_RATES
    display_name = "Kraken Perp Funding Rates"
    default_interval_seconds = 300
    requires_api_key = False
    api_key_env_var = None

    def fetch_raw(self) -> Iterable[FetchedItem]:
        threshold = float(self.config.get("extreme_threshold", 0.001))
        pairs: list[str] = self.config.get("pairs") or list(DEFAULT_PAIRS)

        # The ccxt client is injectable for tests; in production we lazy-import
        # to avoid pulling ccxt at module load.
        client = self.http_client
        if client is None:
            try:
                import ccxt  # noqa: WPS433
            except ImportError as exc:
                raise SourceFetchError(f"ccxt unavailable: {exc}") from exc
            client = ccxt.kraken({"enableRateLimit": True})

        items: list[FetchedItem] = []
        now = datetime.now(timezone.utc)
        for symbol in pairs:
            try:
                rate_obj = client.fetch_funding_rate(symbol)
            except Exception as exc:
                raise SourceFetchError(
                    f"funding_rates fetch failed for {symbol}: {exc}"
                ) from exc

            if not isinstance(rate_obj, dict):
                continue
            funding_rate = rate_obj.get("fundingRate")
            try:
                rate = float(funding_rate)
            except (TypeError, ValueError):
                continue

            magnitude = abs(rate)
            if magnitude < threshold:
                continue

            severity = SEVERITY_MATERIAL if magnitude >= 0.003 else SEVERITY_NOTABLE
            direction = "bearish" if rate > 0 else "bullish"

            coin = symbol.split("/")[0]
            day_bucket = now.strftime("%Y-%m-%d-%H")  # one item per pair per hour
            ext_id = f"{symbol}::{day_bucket}"
            payload_dict: dict[str, Any] = {
                "symbol": symbol,
                "funding_rate": rate,
                "interval": rate_obj.get("interval"),
                "timestamp": rate_obj.get("timestamp"),
            }
            haiku_brief = (
                f"Kraken perp funding extreme: {symbol} funding rate {rate*100:+.3f}% "
                f"(threshold {threshold*100:.3f}%)."
            )
            items.append(
                FetchedItem(
                    external_id=ext_id,
                    raw_payload=payload_dict,
                    occurred_at=now,
                    source_url=None,
                    deterministic_severity=severity,
                    deterministic_event_type="funding_extreme",
                    deterministic_coin=coin,
                    deterministic_direction=direction,
                    haiku_brief=haiku_brief,
                )
            )

        return items
