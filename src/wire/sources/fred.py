"""
FRED macro series source.

Pulls the most-recent observation for a configured set of FRED series:
  DGS10   - 10-Year Treasury yield
  DTWEXBGS - Trade-weighted USD index (DXY proxy)
  VIXCLS  - VIX
  M2SL    - M2 money stock

Each series produces one item per calendar day at most (external_id includes
date). Severity is a flat 2 (notable macro context) per the kickoff baseline.

Requires FRED_API_KEY.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Iterable

import httpx

from src.wire.constants import SEVERITY_NOTABLE, SOURCE_FRED
from src.wire.sources.base import FetchedItem, SourceFetchError, WireSourceBase

logger = logging.getLogger(__name__)


DEFAULT_SERIES: list[str] = ["DGS10", "DTWEXBGS", "VIXCLS", "M2SL"]


class FredSource(WireSourceBase):
    name = SOURCE_FRED
    display_name = "FRED Macro Series"
    default_interval_seconds = 86400
    requires_api_key = True
    api_key_env_var = "FRED_API_KEY"

    DEFAULT_BASE_URL = "https://api.stlouisfed.org/fred/"

    def fetch_raw(self) -> Iterable[FetchedItem]:
        if not self.api_key:
            raise SourceFetchError("fred requires FRED_API_KEY")
        base_url = (self.config.get("base_url") or self.DEFAULT_BASE_URL).rstrip("/")
        series_ids: list[str] = self.config.get("series") or list(DEFAULT_SERIES)

        client = self.http_client or httpx
        items: list[FetchedItem] = []
        for series_id in series_ids:
            url = f"{base_url}/series/observations"
            params = {
                "series_id": series_id,
                "api_key": self.api_key,
                "file_type": "json",
                "sort_order": "desc",
                "limit": 1,
            }
            try:
                response = client.get(
                    url,
                    params=params,
                    timeout=15.0,
                    headers={"User-Agent": "syndicate-wire/1.0"},
                )
                response.raise_for_status()
                data = response.json()
            except Exception as exc:
                raise SourceFetchError(
                    f"fred fetch failed for {series_id}: {exc}"
                ) from exc

            if not isinstance(data, dict):
                raise SourceFetchError("fred returned non-object body")
            obs = data.get("observations")
            if not isinstance(obs, list) or not obs:
                continue
            latest = obs[0]
            if not isinstance(latest, dict):
                continue
            obs_date = latest.get("date")
            value_str = latest.get("value")
            if value_str in (None, ".", ""):
                continue

            try:
                value = float(value_str)
            except (TypeError, ValueError):
                continue

            ext_id = f"{series_id}::{obs_date}"
            occurred_at = None
            if obs_date:
                try:
                    occurred_at = datetime.fromisoformat(obs_date).replace(
                        tzinfo=timezone.utc
                    )
                except (TypeError, ValueError):
                    occurred_at = None

            payload_dict: dict[str, Any] = {
                "series_id": series_id,
                "date": obs_date,
                "value": value,
            }
            haiku_brief = (
                f"FRED macro observation: {series_id} = {value} as of {obs_date}."
            )
            items.append(
                FetchedItem(
                    external_id=ext_id,
                    raw_payload=payload_dict,
                    occurred_at=occurred_at,
                    source_url=f"https://fred.stlouisfed.org/series/{series_id}",
                    deterministic_severity=SEVERITY_NOTABLE,
                    deterministic_event_type="macro_data",
                    deterministic_is_macro=True,
                    haiku_brief=haiku_brief,
                )
            )

        return items
