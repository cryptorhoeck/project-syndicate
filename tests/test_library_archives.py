"""
Tests for The Library — Archives (Dynamic Knowledge)

Verifies post-mortems, strategy records, pattern summaries,
delayed publication, view tracking, and search.
"""

__version__ = "0.4.0"

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.common.models import (
    Agent,
    Base,
    Evaluation,
    LibraryEntry,
    LibraryView,
    Lineage,
    SystemState,
    Transaction,
)
from src.library.library_service import LibraryService
from src.library.schemas import LibraryCategory


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def db():
    """In-memory SQLite database for archive tests."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)

    with factory() as session:
        session.add(Agent(id=0, name="Genesis", type="genesis", status="active"))
        session.add(Agent(
            id=1, name="Scout-Alpha", type="scout", status="terminated",
            generation=1, strategy_summary="BTC momentum",
            termination_reason="Unprofitable: True P&L -15%",
            terminated_at=datetime(2026, 3, 12, tzinfo=timezone.utc),
            created_at=datetime(2026, 3, 1, tzinfo=timezone.utc),
            total_gross_pnl=-50.0, total_api_cost=5.0, total_true_pnl=-55.0,
            evaluation_count=2, profitable_evaluations=0,
        ))
        session.add(Agent(
            id=2, name="Strategist-Prime", type="strategist", status="active",
            generation=1, strategy_summary="Mean reversion ETH",
            composite_score=0.65, total_true_pnl=100.0,
        ))
        session.add(Lineage(agent_id=1, parent_id=None, generation=1, lineage_path="1"))
        session.add(SystemState(
            total_treasury=1000.0, peak_treasury=1000.0,
            current_regime="bull", active_agent_count=1, alert_status="green",
        ))
        session.add(Evaluation(
            id=1, agent_id=2, evaluation_type="survival_check",
            pnl_gross=120.0, pnl_net=100.0, api_cost=20.0,
            sharpe_ratio=1.5, result="survived",
        ))
        session.commit()

    return factory


@pytest.fixture
def library(db):
    return LibraryService(db_session_factory=db, agora_service=None)


@pytest.fixture
def library_with_agora(db):
    """Library with a mock Agora service."""
    agora = AsyncMock()
    agora.post_message = AsyncMock()
    return LibraryService(db_session_factory=db, agora_service=agora)


# ---------------------------------------------------------------------------
# Post-mortems
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_post_mortem(library):
    """Create post-mortem for terminated agent."""
    entry = await library.create_post_mortem(agent_id=1)
    assert entry is not None
    assert entry.category == "post_mortem"
    assert "Scout-Alpha" in entry.title
    assert entry.is_published is True
    assert entry.published_at is not None


@pytest.mark.asyncio
async def test_create_post_mortem_without_ai(library):
    """Template fallback works when no anthropic_client."""
    assert library.anthropic is None
    entry = await library.create_post_mortem(agent_id=1)
    assert "Gross P&L" in entry.content
    assert "review raw data" in entry.summary.lower()


@pytest.mark.asyncio
async def test_create_post_mortem_tags(library):
    """Post-mortem has correct tags."""
    entry = await library.create_post_mortem(agent_id=1)
    assert "scout" in entry.tags
    assert "bull" in entry.tags


# ---------------------------------------------------------------------------
# Strategy records
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_strategy_record_delayed(library):
    """Strategy record is created but NOT published."""
    entry = await library.create_strategy_record(agent_id=2, evaluation_id=1)
    assert entry is not None
    assert entry.category == "strategy_record"
    assert entry.is_published is False


@pytest.mark.asyncio
async def test_publish_delayed_entries(db):
    """Advance time past 48h, verify publication."""
    library = LibraryService(db_session_factory=db, agora_service=None)

    # Create a delayed entry with publish_after in the past
    past = datetime.now(timezone.utc) - timedelta(hours=49)
    with db() as session:
        entry = LibraryEntry(
            category="strategy_record",
            title="Delayed Test",
            content="Test content",
            is_published=False,
            publish_after=past,
        )
        session.add(entry)
        session.commit()

    published = await library.publish_delayed_entries()
    assert len(published) == 1
    assert published[0].is_published is True
    assert published[0].title == "Delayed Test"


@pytest.mark.asyncio
async def test_publish_delayed_entries_not_yet(db):
    """Entries with future publish_after stay unpublished."""
    library = LibraryService(db_session_factory=db, agora_service=None)

    future = datetime.now(timezone.utc) + timedelta(hours=24)
    with db() as session:
        entry = LibraryEntry(
            category="strategy_record",
            title="Future Test",
            content="Test content",
            is_published=False,
            publish_after=future,
        )
        session.add(entry)
        session.commit()

    published = await library.publish_delayed_entries()
    assert len(published) == 0


# ---------------------------------------------------------------------------
# Pattern summaries
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_pattern_summary(library):
    """Immediate publication."""
    entry = await library.create_pattern_summary(
        title="BTC breakout pattern",
        content="Observed bullish engulfing across 4h timeframe",
        tags=["btc", "pattern", "bull"],
    )
    assert entry is not None
    assert entry.category == "pattern"
    assert entry.is_published is True
    assert "Genesis" in (entry.source_agent_name or "")


# ---------------------------------------------------------------------------
# View tracking
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_record_view(db):
    """view_count increments on first view."""
    library = LibraryService(db_session_factory=db, agora_service=None)

    with db() as session:
        entry = LibraryEntry(
            category="pattern", title="Test", content="Content",
            is_published=True, published_at=datetime.now(timezone.utc),
        )
        session.add(entry)
        session.commit()
        entry_id = entry.id

    await library.record_view(entry_id, agent_id=1)

    resp = await library.get_entry(entry_id)
    assert resp.view_count == 1


@pytest.mark.asyncio
async def test_record_view_idempotent(db):
    """Same agent viewing twice only increments once."""
    library = LibraryService(db_session_factory=db, agora_service=None)

    with db() as session:
        entry = LibraryEntry(
            category="pattern", title="Test", content="Content",
            is_published=True, published_at=datetime.now(timezone.utc),
        )
        session.add(entry)
        session.commit()
        entry_id = entry.id

    await library.record_view(entry_id, agent_id=1)
    await library.record_view(entry_id, agent_id=1)

    resp = await library.get_entry(entry_id)
    assert resp.view_count == 1


# ---------------------------------------------------------------------------
# Get entries and search
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_entries_by_category(db):
    """Filter entries by category."""
    library = LibraryService(db_session_factory=db, agora_service=None)

    now = datetime.now(timezone.utc)
    with db() as session:
        session.add(LibraryEntry(
            category="post_mortem", title="PM 1", content="C1",
            is_published=True, published_at=now,
        ))
        session.add(LibraryEntry(
            category="pattern", title="Pattern 1", content="C2",
            is_published=True, published_at=now,
        ))
        session.commit()

    results = await library.get_entries(category=LibraryCategory.POST_MORTEM)
    assert len(results) == 1
    assert results[0].category == "post_mortem"


@pytest.mark.asyncio
async def test_get_entries_published_only(db):
    """Unpublished entries excluded by default."""
    library = LibraryService(db_session_factory=db, agora_service=None)

    now = datetime.now(timezone.utc)
    with db() as session:
        session.add(LibraryEntry(
            category="strategy_record", title="Published", content="C1",
            is_published=True, published_at=now,
        ))
        session.add(LibraryEntry(
            category="strategy_record", title="Unpublished", content="C2",
            is_published=False,
        ))
        session.commit()

    results = await library.get_entries()
    titles = [r.title for r in results]
    assert "Published" in titles
    assert "Unpublished" not in titles


@pytest.mark.asyncio
async def test_search_entries(db):
    """Keyword search finds matching entries."""
    library = LibraryService(db_session_factory=db, agora_service=None)

    now = datetime.now(timezone.utc)
    with db() as session:
        session.add(LibraryEntry(
            category="pattern", title="BTC Breakout", content="Bullish engulfing",
            is_published=True, published_at=now,
        ))
        session.add(LibraryEntry(
            category="pattern", title="ETH Range", content="Sideways movement",
            is_published=True, published_at=now,
        ))
        session.commit()

    results = await library.search_entries("Breakout")
    assert len(results) == 1
    assert results[0].title == "BTC Breakout"
