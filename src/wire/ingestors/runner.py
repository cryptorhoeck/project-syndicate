"""
Per-source runner.

Responsibilities:
  1. Resolve the concrete WireSourceBase implementation for a given DB row.
  2. Invoke fetch_raw().
  3. Persist FetchedItem -> wire_raw_items, deduping at (source_id, external_id).
  4. Update wire_source_health via HealthMonitor.

The runner does NOT call Haiku — that's the digester's job. The runner is the
write side; the digester is the read-pending-and-process side. Keeping the
two decoupled lets the runner stay fast and lets digestion fail loudly without
also losing the raw record.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Iterable, Optional

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.wire.constants import HEALTH_DISABLED
from src.wire.health.monitor import HealthMonitor
from src.wire.models import WireRawItem, WireSource, WireSourceHealth
from src.wire.sources.base import FetchedItem, SourceFetchError, WireSourceBase
from src.wire.sources.cryptopanic import CryptoPanicSource
from src.wire.sources.defillama import DefiLlamaSource
from src.wire.sources.kraken_announcements import KrakenAnnouncementsSource

logger = logging.getLogger(__name__)


# Class registry: source.name -> concrete WireSourceBase subclass.
# Tier 2 sources will be appended here as they're implemented.
SOURCE_REGISTRY: dict[str, type[WireSourceBase]] = {
    KrakenAnnouncementsSource.name: KrakenAnnouncementsSource,
    CryptoPanicSource.name: CryptoPanicSource,
    DefiLlamaSource.name: DefiLlamaSource,
}


def register_source(cls: type[WireSourceBase]) -> type[WireSourceBase]:
    """Register a WireSourceBase subclass. Decorator-friendly."""
    if not getattr(cls, "name", None):
        raise ValueError(f"{cls.__name__} has no `name` attribute")
    SOURCE_REGISTRY[cls.name] = cls
    return cls


def resolve_source(
    db_row: WireSource,
    *,
    http_client=None,
    api_key: Optional[str] = None,
) -> WireSourceBase:
    """Build a concrete WireSourceBase for the given DB row.

    api_key: explicit override. If None and the source declares an
        api_key_env_var, the value is read from os.environ.
    """
    cls = SOURCE_REGISTRY.get(db_row.name)
    if cls is None:
        raise KeyError(f"no implementation registered for source {db_row.name!r}")

    effective_key = api_key
    if effective_key is None and db_row.api_key_env_var:
        effective_key = os.environ.get(db_row.api_key_env_var)

    cfg = dict(db_row.config_json or {})
    cfg.setdefault("base_url", db_row.base_url)
    return cls(api_key=effective_key, http_client=http_client, config=cfg)


@dataclass(slots=True)
class SourceRunResult:
    """Outcome of one runner cycle for one source."""

    source_id: int
    source_name: str
    success: bool
    items_seen: int = 0
    items_inserted: int = 0
    error: Optional[str] = None


@dataclass
class SourceRunner:
    """Stateless runner. One instance can drive many sources sequentially."""

    session: Session
    monitor: HealthMonitor = field(init=False)

    def __post_init__(self) -> None:
        self.monitor = HealthMonitor(self.session)

    # ------------------------------------------------------------------

    def run_source_by_name(self, source_name: str, **resolve_kwargs) -> SourceRunResult:
        source = self.session.execute(
            select(WireSource).where(WireSource.name == source_name)
        ).scalar_one_or_none()
        if source is None:
            return SourceRunResult(
                source_id=0,
                source_name=source_name,
                success=False,
                error=f"no source row with name {source_name!r}",
            )
        return self.run_source(source, **resolve_kwargs)

    def run_enabled_sources(self, **resolve_kwargs) -> list[SourceRunResult]:
        sources = (
            self.session.execute(
                select(WireSource).where(WireSource.enabled.is_(True)).order_by(WireSource.name)
            )
            .scalars()
            .all()
        )
        results: list[SourceRunResult] = []
        for src in sources:
            health = self.session.get(WireSourceHealth, src.id)
            if health and health.status == HEALTH_DISABLED:
                logger.info(
                    "wire.runner.skip_disabled",
                    extra={"source_name": src.name, "source_id": src.id},
                )
                continue
            results.append(self.run_source(src, **resolve_kwargs))
        return results

    def run_source(
        self,
        source: WireSource,
        *,
        http_client=None,
        api_key: Optional[str] = None,
        instance: Optional[WireSourceBase] = None,
    ) -> SourceRunResult:
        """Run one source: fetch + persist + update health."""
        try:
            impl = instance or resolve_source(
                source, http_client=http_client, api_key=api_key
            )
        except KeyError as exc:
            error = f"resolve failed: {exc}"
            self.monitor.record_failure(source, error)
            self.session.commit()
            return SourceRunResult(
                source_id=source.id,
                source_name=source.name,
                success=False,
                error=error,
            )

        try:
            items = list(impl.fetch_raw() or [])
        except SourceFetchError as exc:
            self.monitor.record_failure(source, str(exc))
            self.session.commit()
            return SourceRunResult(
                source_id=source.id,
                source_name=source.name,
                success=False,
                error=str(exc),
            )
        except Exception as exc:  # belt-and-suspenders
            logger.exception("wire.runner.unexpected_failure")
            self.monitor.record_failure(source, f"unexpected: {exc}")
            self.session.commit()
            return SourceRunResult(
                source_id=source.id,
                source_name=source.name,
                success=False,
                error=str(exc),
            )

        inserted = self._persist_items(source, items)
        self.monitor.record_success(source, items_added=inserted)
        self.session.commit()
        return SourceRunResult(
            source_id=source.id,
            source_name=source.name,
            success=True,
            items_seen=len(items),
            items_inserted=inserted,
        )

    # ------------------------------------------------------------------

    def _persist_items(self, source: WireSource, items: Iterable[FetchedItem]) -> int:
        inserted = 0
        for item in items:
            envelope = self._envelope_for(item)
            row = WireRawItem(
                source_id=source.id,
                external_id=item.external_id[:256],
                occurred_at=item.occurred_at,
                raw_payload=envelope,
            )
            self.session.add(row)
            try:
                self.session.flush()
            except IntegrityError:
                # Duplicate (source_id, external_id) — already seen.
                self.session.rollback()
                continue
            inserted += 1
        return inserted

    @staticmethod
    def _envelope_for(item: FetchedItem) -> dict:
        """Serialize a FetchedItem into the raw_payload envelope used by the
        digester. Convention is documented in src/wire/digest/haiku_digester.py."""
        return {
            "payload": item.raw_payload,
            "haiku_brief": item.haiku_brief,
            "source_url": item.source_url,
            "deterministic_severity": item.deterministic_severity,
            "deterministic_event_type": item.deterministic_event_type,
            "deterministic_coin": item.deterministic_coin,
            "deterministic_direction": item.deterministic_direction,
            "deterministic_is_macro": item.deterministic_is_macro,
            "metadata": item.metadata,
        }
