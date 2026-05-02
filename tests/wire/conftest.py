"""Wire test fixtures.

Importing src.wire.models here ensures the 6 Wire tables are registered on
Base.metadata before the in-memory SQLite engine creates schema. The runner
also depends on agents existing for FK targets in wire_query_log; we seed a
test agent in `wire_seeded_session`.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable, Optional

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

# Importing wire.models registers Wire tables on Base.metadata.
import src.wire.models  # noqa: F401
from src.common.models import Agent, Base, SystemState
from src.wire.models import WireSource, WireSourceHealth


def _seed_sources(session: Session) -> dict[str, WireSource]:
    """Seed wire_sources to mirror the alembic seed migration."""
    seeds = [
        ("kraken_announcements", "Kraken Announcements", "A", 300, True, False, None,
         "https://blog.kraken.com/category/announcement/feed", {"severity_floor": 3}),
        ("cryptopanic", "CryptoPanic (Free)", "A", 600, True, False, None,
         "https://cryptopanic.com/api/v1/posts/", {"public": True}),
        ("defillama", "DefiLlama", "A", 1800, True, False, None,
         "https://api.llama.fi", {"tvl_delta_threshold": 0.05}),
        ("etherscan_transfers", "Etherscan Large Transfers", "A", 900, False, True,
         "ETHERSCAN_API_KEY", "https://api.etherscan.io/api", {"min_value_eth": 1000}),
        ("funding_rates", "Kraken Perp Funding Rates", "A", 300, False, False, None,
         "ccxt://kraken", {"extreme_threshold": 0.001}),
        ("fred", "FRED Macro Series", "B", 86400, False, True, "FRED_API_KEY",
         "https://api.stlouisfed.org/fred/", {"series": ["DGS10"]}),
        ("trading_economics", "TradingEconomics Calendar", "B", 86400, False, False, None,
         "https://api.tradingeconomics.com/calendar", {"guest_tier": True}),
        ("fear_greed", "Fear & Greed Index", "B", 86400, False, False, None,
         "https://api.alternative.me/fng/", {}),
    ]
    by_name: dict[str, WireSource] = {}
    for (
        name, display, tier, interval, enabled, requires_key, env_var, base_url, cfg
    ) in seeds:
        src = WireSource(
            name=name,
            display_name=display,
            tier=tier,
            fetch_interval_seconds=interval,
            enabled=enabled,
            requires_api_key=requires_key,
            api_key_env_var=env_var,
            base_url=base_url,
            config_json=cfg,
        )
        session.add(src)
        by_name[name] = src
    session.flush()
    for src in by_name.values():
        session.add(WireSourceHealth(source_id=src.id, status="unknown"))
    session.commit()
    return by_name


@pytest.fixture
def wire_engine():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine


@pytest.fixture
def wire_session_factory(wire_engine):
    return sessionmaker(bind=wire_engine)


@pytest.fixture
def wire_session(wire_session_factory):
    session = wire_session_factory()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def wire_seeded_session(wire_session_factory):
    """Seeds wire_sources + a single test agent for FK references."""
    session = wire_session_factory()
    try:
        # System state row not strictly required for Wire tests.
        agent = Agent(
            name="Wire-Test-Scout",
            type="scout",
            status="active",
            capital_allocated=100.0,
            capital_current=100.0,
        )
        session.add(agent)
        session.flush()
        _seed_sources(session)
        yield session
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Reusable HTTP fakes
# ---------------------------------------------------------------------------


class FakeResponse:
    """Minimal stand-in for httpx.Response."""

    def __init__(
        self,
        *,
        text: str = "",
        json_data=None,
        status_code: int = 200,
        raise_exc: Optional[Exception] = None,
    ) -> None:
        self.text = text
        self._json = json_data
        self.status_code = status_code
        self._raise_exc = raise_exc

    def raise_for_status(self) -> None:
        if self._raise_exc is not None:
            raise self._raise_exc
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        if self._json is None:
            raise ValueError("no JSON body configured")
        return self._json


class FakeHttpClient:
    """Records the last call and returns a queued response."""

    def __init__(self, response: FakeResponse | Exception) -> None:
        self.response = response
        self.last_url: Optional[str] = None
        self.last_params: Optional[dict] = None
        self.last_kwargs: Optional[dict] = None

    def get(self, url, params=None, **kwargs):
        self.last_url = url
        self.last_params = params
        self.last_kwargs = kwargs
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


# ---------------------------------------------------------------------------
# Reusable Haiku fake
# ---------------------------------------------------------------------------


def make_fake_haiku_client(
    responses: list[str],
    *,
    cost_per_call: float = 0.000123,
) -> Callable[[str, str], "object"]:
    """Build a fake haiku client returning queued response strings."""
    from src.wire.digest.haiku_digester import HaikuCallResult

    iter_responses = iter(responses)

    def _client(system_prompt: str, user_prompt: str) -> HaikuCallResult:
        try:
            text = next(iter_responses)
        except StopIteration as exc:
            raise AssertionError("no more fake Haiku responses queued") from exc
        return HaikuCallResult(
            text=text, cost_usd=cost_per_call, input_tokens=100, output_tokens=50
        )

    return _client


@pytest.fixture
def fixed_now():
    """Return a fixed reference datetime."""
    return datetime(2026, 5, 1, 12, 0, 0, tzinfo=timezone.utc)
