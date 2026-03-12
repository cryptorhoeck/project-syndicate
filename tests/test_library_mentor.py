"""
Tests for The Library — Mentor System (Knowledge Inheritance)

Verifies mentor package building, storage, retrieval, and condensation.
"""

__version__ = "0.4.0"

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.common.models import (
    Agent,
    Base,
    LibraryEntry,
    Lineage,
    Message,
    SystemState,
    Transaction,
)
from src.library.library_service import LibraryService


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def db():
    """In-memory SQLite database for mentor tests."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)

    with factory() as session:
        session.add(Agent(id=0, name="Genesis", type="genesis", status="active"))

        # Gen 1 parent
        session.add(Agent(
            id=1, name="Scout-Alpha", type="scout", status="active",
            generation=1, strategy_summary="BTC momentum trading",
        ))
        session.add(Lineage(agent_id=1, parent_id=None, generation=1, lineage_path="1"))

        # Gen 2 child (has parent)
        session.add(Agent(
            id=2, name="Scout-Beta", type="scout", status="active",
            generation=2, parent_id=1, strategy_summary="ETH mean reversion",
        ))
        session.add(Lineage(agent_id=2, parent_id=1, generation=2, lineage_path="1/2"))

        # Gen 5 agent (needs condensation)
        session.add(Agent(
            id=5, name="Scout-Epsilon", type="scout", status="active",
            generation=5, parent_id=2, strategy_summary="Multi-strategy",
        ))
        session.add(Lineage(
            agent_id=5, parent_id=2, generation=5, lineage_path="1/2/5",
            mentor_package_json=json.dumps({
                "parent_agent_id": 2,
                "parent_agent_name": "Scout-Beta",
                "parent_generation": 2,
                "strategy_template": "ETH mean reversion",
                "top_trades": [],
                "failures": [],
                "market_assessment": "",
                "grandparent_package": {"strategy_template": "BTC momentum"},
                "recommended_library_entries": [],
                "condensed_heritage": None,
            }),
        ))

        # Some trades for parent
        session.add(Transaction(
            agent_id=1, type="spot", exchange="kraken", symbol="BTC/USD",
            side="buy", amount=0.1, price=50000, pnl=500,
        ))
        session.add(Transaction(
            agent_id=1, type="spot", exchange="kraken", symbol="BTC/USD",
            side="sell", amount=0.05, price=48000, pnl=-200,
        ))

        # Market intel message from parent
        session.add(Message(
            agent_id=1, channel="market-intel",
            content="BTC showing bullish divergence on 4h",
            agent_name="Scout-Alpha", message_type="signal",
        ))

        session.add(SystemState(
            total_treasury=1000.0, peak_treasury=1000.0,
            current_regime="bull", active_agent_count=3, alert_status="green",
        ))
        session.commit()

    return factory


@pytest.fixture
def library(db):
    return LibraryService(db_session_factory=db, agora_service=None)


# ---------------------------------------------------------------------------
# Mentor package tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_build_mentor_package_gen1(library):
    """Basic package for Gen 1 — no grandparent."""
    package = await library.build_mentor_package(parent_agent_id=1)
    assert package.parent_agent_name == "Scout-Alpha"
    assert package.parent_generation == 1
    assert package.strategy_template == "BTC momentum trading"
    assert len(package.top_trades) >= 1
    assert len(package.failures) >= 1
    assert package.grandparent_package is None


@pytest.mark.asyncio
async def test_build_mentor_package_with_grandparent(db):
    """Gen 2+ includes grandparent data if available."""
    # Store a mentor package on the parent's lineage
    with db() as session:
        lineage = session.execute(
            __import__("sqlalchemy").select(Lineage).where(Lineage.agent_id == 1)
        ).scalar_one()
        lineage.mentor_package_json = json.dumps({
            "parent_agent_id": 0,
            "parent_agent_name": "Genesis",
            "parent_generation": 0,
            "strategy_template": "Original strategy",
            "top_trades": [],
            "failures": [],
        })
        session.commit()

    library = LibraryService(db_session_factory=db, agora_service=None)
    package = await library.build_mentor_package(parent_agent_id=2)
    assert package.parent_agent_name == "Scout-Beta"
    assert package.grandparent_package is not None


@pytest.mark.asyncio
async def test_build_mentor_package_no_ai(library):
    """No anthropic_client → no condensation, raw packages preserved."""
    assert library.anthropic is None
    package = await library.build_mentor_package(parent_agent_id=5)
    assert package.condensed_heritage is None


@pytest.mark.asyncio
async def test_get_mentor_package(library):
    """Store and retrieve mentor package."""
    await library.build_mentor_package(parent_agent_id=1)

    package = await library.get_mentor_package(agent_id=1)
    assert package is not None
    assert package.parent_agent_name == "Scout-Alpha"


@pytest.mark.asyncio
async def test_get_mentor_package_gen1_no_prior(library):
    """Gen 1 with no stored package returns None initially (then build stores it)."""
    # Before building, there's no stored package for agent 1
    with library.db() as session:
        lineage = session.execute(
            __import__("sqlalchemy").select(Lineage).where(Lineage.agent_id == 1)
        ).scalar_one()
        assert lineage.mentor_package_json is None

    package = await library.get_mentor_package(agent_id=1)
    assert package is None


@pytest.mark.asyncio
async def test_recommended_library_entries(db):
    """Relevant entries selected by view count."""
    with db() as session:
        now = datetime.now(timezone.utc)
        session.add(LibraryEntry(
            category="pattern", title="BTC Pattern",
            content="BTC momentum pattern", tags=["btc"],
            is_published=True, published_at=now, view_count=10,
        ))
        session.add(LibraryEntry(
            category="pattern", title="ETH Pattern",
            content="ETH mean reversion pattern", tags=["eth"],
            is_published=True, published_at=now, view_count=5,
        ))
        session.commit()

    library = LibraryService(db_session_factory=db, agora_service=None)
    package = await library.build_mentor_package(parent_agent_id=1)
    assert len(package.recommended_library_entries) > 0
