"""
DefiLlama TVL deltas source.

Pulls /protocols list. Each protocol exposes current TVL plus 1d/7d/30d
percentage change fields. We surface a Wire item for any protocol whose
absolute 1d change exceeds the configured TVL delta threshold (default 5%).

Severity is deterministic based on magnitude:
  >=10%  : severity 3, direction tracks sign
  >= 5%  : severity 2, direction tracks sign
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from typing import Any, Iterable

import httpx

from src.wire.constants import (
    SEVERITY_MATERIAL,
    SEVERITY_NOTABLE,
    SOURCE_DEFILLAMA,
)
from src.wire.sources.base import FetchedItem, SourceFetchError, WireSourceBase

logger = logging.getLogger(__name__)


class DefiLlamaSource(WireSourceBase):
    name = SOURCE_DEFILLAMA
    display_name = "DefiLlama"
    default_interval_seconds = 1800
    requires_api_key = False
    api_key_env_var = None

    DEFAULT_BASE_URL = "https://api.llama.fi"

    def fetch_raw(self) -> Iterable[FetchedItem]:
        base_url = (self.config.get("base_url") or self.DEFAULT_BASE_URL).rstrip("/")
        threshold = float(self.config.get("tvl_delta_threshold", 0.05))
        url = f"{base_url}/protocols"

        client = self.http_client or httpx
        try:
            response = client.get(
                url,
                timeout=20.0,
                headers={"User-Agent": "syndicate-wire/1.0"},
            )
            response.raise_for_status()
            protocols = response.json()
        except Exception as exc:
            raise SourceFetchError(f"defillama fetch failed: {exc}") from exc

        if not isinstance(protocols, list):
            raise SourceFetchError("defillama /protocols did not return a list")

        # Bucket the day so the same significant move on the same day collapses
        # via (source_id, external_id) at fetch time.
        day_bucket = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        items: list[FetchedItem] = []
        for proto in protocols:
            if not isinstance(proto, dict):
                continue
            slug = proto.get("slug") or proto.get("name")
            if not slug:
                continue

            change_1d = proto.get("change_1d")
            try:
                change_pct = float(change_1d) / 100.0 if change_1d is not None else 0.0
            except (TypeError, ValueError):
                continue

            magnitude = abs(change_pct)
            if magnitude < threshold:
                continue

            tvl = proto.get("tvl")
            symbol = proto.get("symbol")
            name = proto.get("name") or slug
            chain = proto.get("chain") or proto.get("chains", [None])[0]

            if magnitude >= 0.10:
                severity = SEVERITY_MATERIAL
            else:
                severity = SEVERITY_NOTABLE

            direction = "bullish" if change_pct > 0 else "bearish"

            external_id = f"{slug}::{day_bucket}"
            link = f"https://defillama.com/protocol/{slug}"

            payload: dict[str, Any] = {
                "slug": slug,
                "name": name,
                "symbol": symbol,
                "chain": chain,
                "tvl": tvl,
                "change_1d_pct": change_pct * 100.0,
            }
            haiku_brief = (
                f"DefiLlama TVL move: {name} ({symbol or 'n/a'}) on {chain or 'multi'} "
                f"changed {change_pct*100:+.1f}% in 24h. Current TVL: {tvl}."
            )

            items.append(
                FetchedItem(
                    external_id=external_id,
                    raw_payload=payload,
                    occurred_at=datetime.now(timezone.utc),
                    source_url=link,
                    deterministic_severity=severity,
                    deterministic_event_type="tvl_change",
                    deterministic_direction=direction,
                    deterministic_coin=symbol if symbol and isinstance(symbol, str) else None,
                    haiku_brief=haiku_brief,
                )
            )

        return items


def _hash_id(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:32]
