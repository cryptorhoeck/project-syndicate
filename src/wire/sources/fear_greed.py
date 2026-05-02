"""
Fear & Greed Index source.

Crude but useful regime tag. The /fng/ endpoint returns the current numeric
score (0-100) and a classification ('Extreme Fear', 'Fear', 'Neutral',
'Greed', 'Extreme Greed').

Severity is deterministic 2 — notable macro signal. We emit one item per day
(external_id includes the score's reported timestamp date).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Iterable

import httpx

from src.wire.constants import SEVERITY_NOTABLE, SOURCE_FEAR_GREED
from src.wire.sources.base import FetchedItem, SourceFetchError, WireSourceBase

logger = logging.getLogger(__name__)


class FearGreedSource(WireSourceBase):
    name = SOURCE_FEAR_GREED
    display_name = "Fear & Greed Index"
    default_interval_seconds = 86400
    requires_api_key = False
    api_key_env_var = None

    DEFAULT_BASE_URL = "https://api.alternative.me/fng/"

    def fetch_raw(self) -> Iterable[FetchedItem]:
        base_url = self.config.get("base_url") or self.DEFAULT_BASE_URL
        client = self.http_client or httpx
        try:
            response = client.get(
                base_url,
                timeout=15.0,
                headers={"User-Agent": "syndicate-wire/1.0"},
            )
            response.raise_for_status()
            data = response.json()
        except Exception as exc:
            raise SourceFetchError(f"fear_greed fetch failed: {exc}") from exc

        if not isinstance(data, dict):
            raise SourceFetchError("fear_greed did not return an object")
        results = data.get("data")
        if not isinstance(results, list) or not results:
            return []

        latest = results[0]
        if not isinstance(latest, dict):
            return []
        try:
            value = int(latest.get("value", 0))
        except (TypeError, ValueError):
            value = 0
        classification = latest.get("value_classification") or "Unknown"
        ts = latest.get("timestamp")
        try:
            occurred_at = datetime.fromtimestamp(int(ts), tz=timezone.utc)
        except (TypeError, ValueError):
            occurred_at = datetime.now(timezone.utc)

        ext_id = f"fng::{occurred_at.strftime('%Y-%m-%d')}"
        # Direction: extreme fear -> contrarian bullish; extreme greed -> contrarian bearish.
        if value <= 25:
            direction = "bullish"
        elif value >= 75:
            direction = "bearish"
        else:
            direction = "neutral"
        payload_dict: dict[str, Any] = {
            "value": value,
            "classification": classification,
            "timestamp": ts,
        }
        haiku_brief = (
            f"Fear & Greed index: {value} ({classification}) as of "
            f"{occurred_at.isoformat(timespec='seconds')}."
        )
        return [
            FetchedItem(
                external_id=ext_id,
                raw_payload=payload_dict,
                occurred_at=occurred_at,
                source_url="https://alternative.me/crypto/fear-and-greed-index/",
                deterministic_severity=SEVERITY_NOTABLE,
                deterministic_event_type="macro_data",
                deterministic_is_macro=True,
                deterministic_direction=direction,
                haiku_brief=haiku_brief,
            )
        ]
