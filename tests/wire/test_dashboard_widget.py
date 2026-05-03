"""Smoke tests for the Wire dashboard API endpoints."""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

# Importing wire.models here ensures Wire tables register on the shared Base.
import src.wire.models  # noqa: F401
from src.common.models import Agent, Base, SystemState
from src.wire.models import WireEvent, WireRawItem, WireSource, WireTreasuryLedger


def _seed_thread_safe_session():
    """Build a SQLite engine TestClient threads can share."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as session:
        agent = Agent(
            name="DashboardTestScout",
            type="scout",
            status="active",
            capital_allocated=100.0,
            capital_current=100.0,
        )
        session.add(agent)
        session.flush()
        for tier1 in ("kraken_announcements", "cryptopanic", "defillama"):
            session.add(
                WireSource(
                    name=tier1,
                    display_name=tier1,
                    tier="A",
                    fetch_interval_seconds=300,
                    enabled=True,
                    requires_api_key=False,
                    base_url="http://x",
                    config_json={},
                )
            )
        for tier2 in (
            "etherscan_transfers", "funding_rates", "fred",
            "trading_economics", "fear_greed",
        ):
            session.add(
                WireSource(
                    name=tier2,
                    display_name=tier2,
                    tier="A" if tier2 in ("etherscan_transfers", "funding_rates") else "B",
                    fetch_interval_seconds=900,
                    enabled=False,
                    requires_api_key=tier2 in ("etherscan_transfers", "fred"),
                    base_url="http://x",
                    config_json={},
                )
            )
        session.commit()
    return engine, factory


@pytest.fixture
def thread_safe_factory():
    engine, factory = _seed_thread_safe_session()
    yield factory
    engine.dispose()


def _build_app(factory):
    """Build a FastAPI app wired to the thread-safe session factory."""
    from fastapi import FastAPI

    from src.web.dependencies import get_db
    from src.web.routes.api_wire import router as wire_router

    app = FastAPI()
    app.include_router(wire_router)

    def _get_db_override():
        session = factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = _get_db_override
    return app


class TestWireApi:
    def test_ticker_endpoint_returns_json(self, thread_safe_factory, fixed_now) -> None:
        with thread_safe_factory() as session:
            evt = WireEvent(
                canonical_hash="h1",
                coin="BTC",
                event_type="listing",
                severity=3,
                summary="BTC listed",
                occurred_at=fixed_now,
                digested_at=fixed_now,
                published_to_ticker=True,
            )
            session.add(evt)
            session.commit()

        app = _build_app(thread_safe_factory)
        with TestClient(app) as client:
            r = client.get("/api/wire/ticker?limit=10")
        assert r.status_code == 200
        data = r.json()
        assert data["count"] == 1
        assert data["events"][0]["severity"] == 3

    def test_health_endpoint_lists_all_sources(self, thread_safe_factory) -> None:
        app = _build_app(thread_safe_factory)
        with TestClient(app) as client:
            r = client.get("/api/wire/health")
        assert r.status_code == 200
        data = r.json()
        assert len(data["sources"]) == 8
        names = {s["name"] for s in data["sources"]}
        assert "cryptopanic" in names

    def test_treasury_endpoint(self, thread_safe_factory, fixed_now) -> None:
        with thread_safe_factory() as session:
            session.add(
                WireTreasuryLedger(
                    cost_category="haiku_digestion",
                    cost_usd=0.001,
                    incurred_at=fixed_now - timedelta(minutes=10),
                )
            )
            session.commit()
        app = _build_app(thread_safe_factory)
        with TestClient(app) as client:
            r = client.get("/api/wire/treasury?lookback_hours=24")
        assert r.status_code == 200
        data = r.json()
        assert data["total_cost_usd"] >= 0.001

    def test_stats_endpoint(self, thread_safe_factory) -> None:
        app = _build_app(thread_safe_factory)
        with TestClient(app) as client:
            r = client.get("/api/wire/stats")
        assert r.status_code == 200
        data = r.json()
        assert "total_events" in data
        assert "pending_raw_items" in data
        assert "dead_letter_items" in data
