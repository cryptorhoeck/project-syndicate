"""
Tests for The Library — Integration with BaseAgent and Genesis

Verifies end-to-end flows: agent death → post-mortem, survival → strategy record,
BaseAgent Library methods, Agora notifications.
"""

__version__ = "0.4.0"

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from src.common.models import (
    Agent,
    AgoraChannel,
    Base,
    Evaluation,
    LibraryContribution,
    LibraryEntry,
    Lineage,
    SystemState,
)
from src.common.base_agent import BaseAgent
from src.library.library_service import LibraryService


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def db():
    """In-memory SQLite database for integration tests."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)

    with factory() as session:
        session.add(Agent(id=0, name="Genesis", type="genesis", status="active"))
        session.add(Agent(
            id=1, name="TestAgent", type="scout", status="active",
            generation=1, strategy_summary="Test strategy",
            reputation_score=300,
        ))
        session.add(Agent(
            id=2, name="DeadAgent", type="operator", status="terminated",
            generation=1, strategy_summary="Failed approach",
            termination_reason="Unprofitable",
            terminated_at=datetime(2026, 3, 12, tzinfo=timezone.utc),
            created_at=datetime(2026, 3, 1, tzinfo=timezone.utc),
            total_gross_pnl=-100.0, total_api_cost=10.0, total_true_pnl=-110.0,
            evaluation_count=1, profitable_evaluations=0,
        ))
        session.add(Lineage(agent_id=1, parent_id=None, generation=1, lineage_path="1"))
        session.add(Lineage(agent_id=2, parent_id=None, generation=1, lineage_path="2"))

        for ch_name, desc, is_sys in [
            ("market-intel", "Market discoveries", False),
            ("genesis-log", "Genesis decisions", True),
            ("agent-chat", "Discussion", False),
        ]:
            session.add(AgoraChannel(name=ch_name, description=desc, is_system=is_sys, message_count=0))

        session.add(SystemState(
            total_treasury=1000.0, peak_treasury=1000.0,
            current_regime="bull", active_agent_count=2, alert_status="green",
        ))
        session.add(Evaluation(
            id=1, agent_id=1, evaluation_type="survival_check",
            pnl_gross=50.0, pnl_net=40.0, api_cost=10.0,
            sharpe_ratio=1.2, result="survived",
        ))
        session.commit()

    return factory


@pytest.fixture
def mock_agora():
    agora = AsyncMock()
    agora.post_message = AsyncMock()
    return agora


@pytest.fixture
def library(db, mock_agora):
    return LibraryService(db_session_factory=db, agora_service=mock_agora)


# ---------------------------------------------------------------------------
# Concrete test agent
# ---------------------------------------------------------------------------

class MockAgent(BaseAgent):
    async def initialize(self): pass
    async def run(self): pass
    async def evaluate(self): return {"status": "test"}


# ---------------------------------------------------------------------------
# Integration tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_agent_death_creates_post_mortem(library, db):
    """Terminated agent → post-mortem in Library."""
    entry = await library.create_post_mortem(agent_id=2)
    assert entry.category == "post_mortem"
    assert "DeadAgent" in entry.title
    assert entry.is_published is True


@pytest.mark.asyncio
async def test_agent_survival_creates_strategy_record(library, db):
    """Profitable survivor → delayed strategy record."""
    entry = await library.create_strategy_record(agent_id=1, evaluation_id=1)
    assert entry.category == "strategy_record"
    assert entry.is_published is False
    assert "TestAgent" in entry.title


@pytest.mark.asyncio
async def test_base_agent_read_textbook(db):
    """BaseAgent.read_textbook() works."""
    library = LibraryService(db_session_factory=db, agora_service=None)
    agent = MockAgent(
        agent_id=1, name="TestAgent", agent_type="scout",
        db_session_factory=db, library_service=library,
    )
    content = agent.read_textbook("market")
    assert content is not None
    assert "Market Mechanics" in content


@pytest.mark.asyncio
async def test_base_agent_read_textbook_no_library(db):
    """read_textbook returns None when no library."""
    agent = MockAgent(
        agent_id=1, name="TestAgent", agent_type="scout",
        db_session_factory=db, library_service=None,
    )
    assert agent.read_textbook("market") is None


@pytest.mark.asyncio
async def test_base_agent_submit_to_library(db):
    """BaseAgent.submit_to_library() creates contribution."""
    library = LibraryService(db_session_factory=db, agora_service=None)
    agent = MockAgent(
        agent_id=1, name="TestAgent", agent_type="scout",
        db_session_factory=db, library_service=library,
    )
    resp = await agent.submit_to_library("My Discovery", "BTC pattern observed")
    assert resp is not None
    assert resp.title == "My Discovery"
    assert resp.status == "in_review"


@pytest.mark.asyncio
async def test_base_agent_get_pending_reviews(db):
    """BaseAgent.get_my_pending_reviews() returns assignments."""
    library = LibraryService(db_session_factory=db, agora_service=None)
    agent = MockAgent(
        agent_id=1, name="TestAgent", agent_type="scout",
        db_session_factory=db, library_service=library,
    )

    # Submit from another agent — Genesis is assigned as reviewer (small pop)
    await library.submit_contribution(
        agent_id=1, agent_name="TestAgent",
        title="Test", content="Content",
    )

    # Genesis should have a pending review
    genesis_agent = MockAgent(
        agent_id=0, name="Genesis", agent_type="genesis",
        db_session_factory=db, library_service=library,
    )
    pending = await genesis_agent.get_my_pending_reviews()
    assert len(pending) >= 1


@pytest.mark.asyncio
async def test_agora_notifications(library, mock_agora, db):
    """Library events post to correct Agora channels."""
    await library.create_post_mortem(agent_id=2)
    assert mock_agora.post_message.called

    # Check that at least one message was posted
    calls = mock_agora.post_message.call_args_list
    channels = [c[0][0].channel for c in calls]
    assert "genesis-log" in channels


@pytest.mark.asyncio
async def test_library_stats(library, db):
    """get_library_stats returns correct counts."""
    await library.create_post_mortem(agent_id=2)

    stats = await library.get_library_stats()
    assert stats["total_entries"] >= 1
    assert stats["entries_by_category"]["post_mortem"] >= 1
    assert "pending_reviews" in stats
    assert "top_viewed" in stats
