"""
Wire source abstract base.

Each concrete source implements `fetch_raw()`. The runner persists items into
wire_raw_items, updates wire_source_health, and hands pending items to the
digester. Sources are stateless and pure where possible — no DB writes from
inside `fetch_raw()`.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable

logger = logging.getLogger(__name__)


class SourceFetchError(Exception):
    """Raised when a source fetch fails (network, parse, rate limit)."""


@dataclass(slots=True)
class FetchedItem:
    """One raw item produced by a source's fetch_raw().

    The runner uses (source_id, external_id) as the dedup key when persisting,
    so external_id MUST be stable for the same logical item across fetches.

    Deterministic fields (set when the source can pre-classify with certainty)
    bypass Haiku judgment for that aspect. Use sparingly — Haiku does the
    bulk of classification.
    """

    external_id: str
    raw_payload: dict[str, Any]
    occurred_at: datetime | None = None
    source_url: str | None = None

    # Source-side deterministic classification overrides. None = let Haiku decide.
    deterministic_severity: int | None = None
    deterministic_event_type: str | None = None
    deterministic_coin: str | None = None
    deterministic_direction: str | None = None
    deterministic_is_macro: bool | None = None

    # Free-form text the digester can hand to Haiku verbatim. If None, the
    # digester serializes raw_payload as JSON.
    haiku_brief: str | None = None

    metadata: dict[str, Any] = field(default_factory=dict)


class WireSourceBase(ABC):
    """Abstract base. Subclasses must define class attrs and implement fetch_raw().

    Class attributes:
      name              : canonical source key, must match wire_sources.name
      display_name      : human-readable
      default_interval_seconds : default cadence (the DB row's value wins at runtime)
      requires_api_key  : whether API key is mandatory to call this source
      api_key_env_var   : the env var name (None when not required)
    """

    name: str = ""
    display_name: str = ""
    default_interval_seconds: int = 600
    requires_api_key: bool = False
    api_key_env_var: str | None = None

    def __init__(
        self,
        api_key: str | None = None,
        http_client: Any | None = None,
        config: dict[str, Any] | None = None,
    ) -> None:
        self.api_key = api_key
        self.http_client = http_client  # injected; tests pass a fake
        self.config: dict[str, Any] = config or {}

    @abstractmethod
    def fetch_raw(self) -> Iterable[FetchedItem]:
        """Fetch items from the upstream source and return FetchedItems.

        Implementations MUST raise SourceFetchError for transient failures so
        the runner can mark health appropriately. They MUST NOT raise on
        empty results — return an empty iterable instead.
        """
        raise NotImplementedError

    # ----- helpers shared by subclasses -----

    def _coerce_iso(self, value: Any) -> datetime | None:
        """Best-effort ISO-8601 parser. Returns None when unparseable."""
        if value is None:
            return None
        if isinstance(value, datetime):
            return value
        if isinstance(value, (int, float)):
            try:
                return datetime.fromtimestamp(float(value))
            except (OverflowError, OSError, ValueError):
                return None
        if isinstance(value, str):
            # Strip trailing Z; fromisoformat doesn't accept it before 3.11 in all forms.
            v = value.rstrip("Z").replace("Z", "")
            try:
                return datetime.fromisoformat(v)
            except ValueError:
                return None
        return None
