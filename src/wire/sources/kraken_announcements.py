"""
Kraken announcements source.

Parses the announcement-category RSS feed. Kraken posts listings, delistings,
maintenance windows, deposit/withdrawal pauses. These items have outsized
short-term price impact, so the source enforces a severity floor of 3 and
escalates deterministic-only severity 5 for explicit halt/outage language.
"""

from __future__ import annotations

import hashlib
import logging
import re
from datetime import datetime
from email.utils import parsedate_to_datetime
from typing import Any, Iterable
from xml.etree import ElementTree as ET

import httpx

from src.wire.constants import (
    SEVERITY_CRITICAL,
    SEVERITY_MATERIAL,
    SOURCE_KRAKEN_ANNOUNCEMENTS,
)
from src.wire.sources.base import FetchedItem, SourceFetchError, WireSourceBase

logger = logging.getLogger(__name__)


# Deterministic classification rules. Order matters: first match wins.
_DETERMINISTIC_RULES: list[tuple[re.Pattern[str], dict[str, Any]]] = [
    (
        re.compile(r"\b(withdrawal|deposit)\s+(suspend|halt|pause|disabl)", re.I),
        {
            "deterministic_severity": SEVERITY_CRITICAL,
            "deterministic_event_type": "withdrawal_halt",
            "deterministic_direction": "bearish",
        },
    ),
    (
        re.compile(r"\b(exchange\s+outage|trading\s+halt|all\s+services\s+down)", re.I),
        {
            "deterministic_severity": SEVERITY_CRITICAL,
            "deterministic_event_type": "exchange_outage",
            "deterministic_direction": "bearish",
        },
    ),
    (
        re.compile(r"\bdelisting\b", re.I),
        {
            "deterministic_severity": SEVERITY_MATERIAL,
            "deterministic_event_type": "delisting",
            "deterministic_direction": "bearish",
        },
    ),
    (
        re.compile(r"\b(now\s+listed|new\s+listing|listing\s+of)\b", re.I),
        {
            "deterministic_severity": SEVERITY_MATERIAL,
            "deterministic_event_type": "listing",
            "deterministic_direction": "bullish",
        },
    ),
]


def _classify(title: str) -> dict[str, Any]:
    """Apply deterministic rules to a title; floor severity at 3."""
    for pattern, fields in _DETERMINISTIC_RULES:
        if pattern.search(title):
            return dict(fields)
    # No rule matched, but per kickoff Kraken announcements floor severity at 3.
    return {"deterministic_severity": SEVERITY_MATERIAL}


_TICKER_PATTERN = re.compile(r"\(([A-Z0-9]{2,10})\)")


def _extract_coin(title: str) -> str | None:
    """Best-effort coin extraction from announcement titles like '(SOL)'."""
    match = _TICKER_PATTERN.search(title)
    if match:
        return match.group(1)
    return None


def _build_external_id(guid: str | None, link: str | None, title: str) -> str:
    """Stable external_id. Prefer feed-provided GUID, then link, then a hash."""
    if guid:
        return guid[:256]
    if link:
        return link[:256]
    return hashlib.sha256(title.encode("utf-8")).hexdigest()


class KrakenAnnouncementsSource(WireSourceBase):
    name = SOURCE_KRAKEN_ANNOUNCEMENTS
    display_name = "Kraken Announcements"
    default_interval_seconds = 300
    requires_api_key = False
    api_key_env_var = None

    # Kraken's category-filtered feed (/category/announcement/feed) is gone.
    # The all-blog feed at /feed/ works and exposes per-item <category> tags
    # we filter on client-side. ANNOUNCEMENT_CATEGORIES below is the inclusive
    # set; missing-category items still pass when match_categories is empty.
    DEFAULT_FEED_URL = "https://blog.kraken.com/feed/"

    ANNOUNCEMENT_CATEGORIES_DEFAULT = (
        "Asset Listings",
        "Announcements",
        "Maintenance",
        "API",
        "Security",
        "Support",
    )

    def fetch_raw(self) -> Iterable[FetchedItem]:
        url = self.config.get("base_url") or self.DEFAULT_FEED_URL
        match_categories = self.config.get("match_categories")
        if match_categories is None:
            match_categories = list(self.ANNOUNCEMENT_CATEGORIES_DEFAULT)
        match_lower = {c.lower() for c in match_categories} if match_categories else set()

        client = self.http_client or httpx
        try:
            response = client.get(
                url,
                timeout=15.0,
                follow_redirects=True,
                headers={"User-Agent": "syndicate-wire/1.0"},
            )
            response.raise_for_status()
            body = response.text
        except Exception as exc:  # broad: network, ssl, dns, status
            raise SourceFetchError(f"kraken_announcements fetch failed: {exc}") from exc

        try:
            root = ET.fromstring(body)
        except ET.ParseError as exc:
            raise SourceFetchError(f"kraken_announcements RSS parse failed: {exc}") from exc

        items: list[FetchedItem] = []
        for item in root.iter("item"):
            title_el = item.find("title")
            link_el = item.find("link")
            guid_el = item.find("guid")
            pub_date_el = item.find("pubDate")
            description_el = item.find("description")

            title = (title_el.text or "").strip() if title_el is not None else ""
            link = (link_el.text or "").strip() if link_el is not None else ""
            guid = (guid_el.text or "").strip() if guid_el is not None else ""
            pub_date_str = (pub_date_el.text or "").strip() if pub_date_el is not None else ""
            description = (
                (description_el.text or "").strip() if description_el is not None else ""
            )

            if not title:
                continue

            # Client-side category filter. Skip items that explicitly carry
            # categories which all fall outside the announcement set; if an
            # item lists no categories we keep it and rely on title rules.
            categories = [
                (c.text or "").strip()
                for c in item.findall("category")
                if c is not None and c.text
            ]
            if categories and match_lower:
                cats_lower = {c.lower() for c in categories}
                if not (cats_lower & match_lower):
                    continue

            occurred_at: datetime | None = None
            if pub_date_str:
                try:
                    occurred_at = parsedate_to_datetime(pub_date_str)
                except (TypeError, ValueError):
                    occurred_at = None

            classification = _classify(title)
            coin = _extract_coin(title)

            payload = {
                "title": title,
                "link": link,
                "description": description,
                "pub_date": pub_date_str,
                "categories": categories,
            }
            cat_str = (", ".join(categories[:3]) or "uncategorized")
            haiku_brief = (
                f"Kraken blog ({cat_str}): {title}\n{description}".strip()
            )

            items.append(
                FetchedItem(
                    external_id=_build_external_id(guid, link, title),
                    raw_payload=payload,
                    occurred_at=occurred_at,
                    source_url=link or None,
                    deterministic_coin=coin,
                    haiku_brief=haiku_brief,
                    **classification,
                )
            )

        return items
