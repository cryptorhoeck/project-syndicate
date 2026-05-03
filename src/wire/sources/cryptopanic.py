"""
CryptoPanic free-tier news aggregator.

Pulls the public posts feed. Each post is fed to Haiku for severity, event type,
direction, and summary. Coin is extracted deterministically from the post's
`currencies` array when present (CryptoPanic provides this).
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Iterable

import httpx

from src.wire.constants import SOURCE_CRYPTOPANIC
from src.wire.sources.base import FetchedItem, SourceFetchError, WireSourceBase

logger = logging.getLogger(__name__)


class CryptoPanicSource(WireSourceBase):
    name = SOURCE_CRYPTOPANIC
    display_name = "CryptoPanic"
    default_interval_seconds = 600
    requires_api_key = True
    api_key_env_var = "CRYPTOPANIC_API_KEY"

    DEFAULT_BASE_URL = "https://cryptopanic.com/api/v1/posts/"

    def fetch_raw(self) -> Iterable[FetchedItem]:
        if not self.api_key:
            # CryptoPanic now returns 404 for unauthenticated calls — even on
            # the public posts endpoint. Fail fast like FRED/Etherscan rather
            # than emit empty results silently.
            raise SourceFetchError(
                "cryptopanic requires CRYPTOPANIC_API_KEY"
            )
        url = self.config.get("base_url") or self.DEFAULT_BASE_URL
        params = {"public": "true", "auth_token": self.api_key}

        client = self.http_client or httpx
        try:
            response = client.get(
                url,
                params=params,
                timeout=15.0,
                follow_redirects=True,
                headers={"User-Agent": "syndicate-wire/1.0"},
            )
            response.raise_for_status()
            data = response.json()
        except Exception as exc:
            raise SourceFetchError(f"cryptopanic fetch failed: {exc}") from exc

        if not isinstance(data, dict):
            raise SourceFetchError("cryptopanic returned non-object body")

        results = data.get("results")
        if results is None:
            return []
        if not isinstance(results, list):
            raise SourceFetchError("cryptopanic 'results' is not a list")

        items: list[FetchedItem] = []
        for post in results:
            if not isinstance(post, dict):
                continue
            external_id = post.get("id")
            if external_id is None:
                continue
            external_id = str(external_id)
            title = post.get("title") or ""
            published_at_raw = post.get("published_at") or post.get("created_at")
            occurred_at = self._coerce_iso(published_at_raw)
            link = post.get("url") or post.get("source", {}).get("url")
            currencies = post.get("currencies") or []
            coin = None
            if isinstance(currencies, list) and currencies:
                first = currencies[0]
                if isinstance(first, dict):
                    coin = first.get("code") or first.get("symbol")

            payload: dict[str, Any] = {
                "id": external_id,
                "title": title,
                "url": link,
                "published_at": published_at_raw,
                "currencies": currencies,
                "domain": post.get("domain"),
                "kind": post.get("kind"),
            }
            haiku_brief = (
                f"CryptoPanic headline: {title}\n"
                f"source_domain={post.get('domain')} kind={post.get('kind')} coins={coin or 'none'}"
            )

            items.append(
                FetchedItem(
                    external_id=external_id,
                    raw_payload=payload,
                    occurred_at=occurred_at,
                    source_url=link,
                    deterministic_coin=coin,
                    haiku_brief=haiku_brief,
                )
            )

        return items
