"""
TradingEconomics economic-calendar source.

Free guest tier (`c=guest:guest`) returns a calendar of upcoming events. We
emit one item per scheduled high-importance event. If the event is within the
configured `preceded_by_hours` (default 4h) of the current time, severity is
escalated to 3 ('FOMC in 4h, reduce size').

No paid key required; we still flag requires_api_key=False in the seed.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

import httpx

from src.wire.constants import (
    SEVERITY_MATERIAL,
    SEVERITY_NOTABLE,
    SOURCE_TRADING_ECONOMICS,
)
from src.wire.sources.base import FetchedItem, SourceFetchError, WireSourceBase

logger = logging.getLogger(__name__)


class TradingEconomicsSource(WireSourceBase):
    name = SOURCE_TRADING_ECONOMICS
    display_name = "TradingEconomics Calendar"
    default_interval_seconds = 86400
    requires_api_key = False
    api_key_env_var = None

    DEFAULT_BASE_URL = "https://api.tradingeconomics.com/calendar"

    def fetch_raw(self) -> Iterable[FetchedItem]:
        base_url = self.config.get("base_url") or self.DEFAULT_BASE_URL
        preceded_by = float(self.config.get("preceded_by_hours", 4.0))
        params = {"c": "guest:guest", "f": "json"}
        client = self.http_client or httpx
        try:
            response = client.get(
                base_url,
                params=params,
                timeout=15.0,
                headers={"User-Agent": "syndicate-wire/1.0"},
            )
            response.raise_for_status()
            data = response.json()
        except Exception as exc:
            raise SourceFetchError(f"trading_economics fetch failed: {exc}") from exc

        if not isinstance(data, list):
            raise SourceFetchError("trading_economics did not return a list")

        now = datetime.now(timezone.utc)
        threshold = now + timedelta(hours=preceded_by)
        items: list[FetchedItem] = []
        for event in data:
            if not isinstance(event, dict):
                continue
            importance = event.get("Importance")
            try:
                importance_int = int(importance) if importance is not None else 0
            except (TypeError, ValueError):
                importance_int = 0
            if importance_int < 2:  # only Medium+ importance
                continue
            calendar_id = event.get("CalendarId") or event.get("Date")
            if not calendar_id:
                continue
            occurred_at_raw = event.get("Date")
            occurred_at = self._coerce_iso(occurred_at_raw)

            severity = SEVERITY_NOTABLE
            if occurred_at is not None:
                if occurred_at.tzinfo is None:
                    occurred_at = occurred_at.replace(tzinfo=timezone.utc)
                if now <= occurred_at <= threshold:
                    severity = SEVERITY_MATERIAL

            country = event.get("Country") or "?"
            ev_name = event.get("Event") or "calendar event"
            ext_id = str(calendar_id)
            payload_dict: dict[str, Any] = {
                "calendar_id": calendar_id,
                "country": country,
                "event": ev_name,
                "importance": importance_int,
                "date": occurred_at_raw,
            }
            haiku_brief = (
                f"TradingEconomics calendar: {country} — {ev_name} at {occurred_at_raw} "
                f"(importance={importance_int})"
            )
            items.append(
                FetchedItem(
                    external_id=ext_id,
                    raw_payload=payload_dict,
                    occurred_at=occurred_at,
                    source_url=None,
                    deterministic_severity=severity,
                    deterministic_event_type="macro_calendar",
                    deterministic_is_macro=True,
                    haiku_brief=haiku_brief,
                )
            )

        return items
