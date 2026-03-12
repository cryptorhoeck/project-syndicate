"""
Tests for The Library — Contributions (Agent-Submitted, Peer-Reviewed)

Verifies submission, reviewer assignment, peer review, Genesis solo review,
split decisions, timeouts, and reputation effects.
"""

__version__ = "0.4.0"

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.common.models import Agent, Base, LibraryContribution, LibraryEntry, Lineage, SystemState
from src.library.library_service import LibraryService
from src.library.schemas import ReviewDecision


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_db(agent_count: int = 2):
    """Create test DB with a configurable number of active agents."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)

    with factory() as session:
        session.add(Agent(id=0, name="Genesis", type="genesis", status="active", reputation_score=9999))
        for i in range(1, agent_count + 1):
            session.add(Agent(
                id=i, name=f"Agent-{i}", type="scout", status="active",
                reputation_score=300, parent_id=None, generation=1,
            ))
            session.add(Lineage(agent_id=i, parent_id=None, generation=1, lineage_path=str(i)))
        session.add(SystemState(
            total_treasury=1000.0, peak_treasury=1000.0,
            current_regime="bull", active_agent_count=agent_count, alert_status="green",
        ))
        session.commit()

    return factory


@pytest.fixture
def db_small():
    """DB with 3 active agents (< 8, Genesis solo review)."""
    return _make_db(3)


@pytest.fixture
def db_large():
    """DB with 10 active agents (>= 8, peer review)."""
    return _make_db(10)


@pytest.fixture
def library_small(db_small):
    return LibraryService(db_session_factory=db_small, agora_service=None)


@pytest.fixture
def library_large(db_large):
    return LibraryService(db_session_factory=db_large, agora_service=None)


# ---------------------------------------------------------------------------
# Submission
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_submit_contribution(library_small):
    """Contribution created with pending_review → in_review (Genesis solo)."""
    resp = await library_small.submit_contribution(
        agent_id=1, agent_name="Agent-1", title="My Insight", content="BTC goes up",
    )
    assert resp.status == "in_review"
    assert resp.title == "My Insight"


# ---------------------------------------------------------------------------
# Reviewer assignment
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_assign_genesis_solo(library_small):
    """< 8 agents → Genesis assigned as sole reviewer."""
    resp = await library_small.submit_contribution(
        agent_id=1, agent_name="Agent-1", title="Test", content="Content",
    )
    assert resp.reviewer_1_name == "Genesis"
    assert resp.reviewer_2_name is None


@pytest.mark.asyncio
async def test_assign_peer_reviewers(library_large):
    """>= 8 agents → two peer reviewers assigned."""
    resp = await library_large.submit_contribution(
        agent_id=1, agent_name="Agent-1", title="Test", content="Content",
    )
    assert resp.reviewer_1_name is not None
    assert resp.reviewer_2_name is not None
    assert resp.reviewer_1_name != "Agent-1"
    assert resp.reviewer_2_name != "Agent-1"


@pytest.mark.asyncio
async def test_reviewer_not_self(library_large):
    """Submitter is never their own reviewer."""
    resp = await library_large.submit_contribution(
        agent_id=1, agent_name="Agent-1", title="Test", content="Content",
    )
    assert resp.reviewer_1_name != "Agent-1"
    assert resp.reviewer_2_name != "Agent-1"


@pytest.mark.asyncio
async def test_reviewer_not_same_lineage(db_large):
    """Reviewers from different lineage enforced."""
    # Set agents 2 and 3 to have same parent as agent 1
    with db_large() as session:
        for aid in [1, 2, 3]:
            agent = session.get(Agent, aid)
            agent.parent_id = 99  # Same parent
        session.commit()

    library = LibraryService(db_session_factory=db_large, agora_service=None)
    resp = await library.submit_contribution(
        agent_id=1, agent_name="Agent-1", title="Test", content="Content",
    )
    # Reviewers should NOT be agents 2 or 3 (same lineage)
    if resp.reviewer_1_name:
        assert resp.reviewer_1_name not in ("Agent-1", "Agent-2", "Agent-3") or resp.reviewer_1_name == "Genesis"


# ---------------------------------------------------------------------------
# Review decisions
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_both_approve(library_large):
    """Both approve → contribution published."""
    resp = await library_large.submit_contribution(
        agent_id=1, agent_name="Agent-1", title="Approved", content="Good insight",
    )

    # Get reviewer IDs from DB
    with library_large.db() as session:
        contrib = session.get(LibraryContribution, resp.id)
        r1_id = contrib.reviewer_1_id
        r2_id = contrib.reviewer_2_id

    await library_large.submit_review(resp.id, r1_id, ReviewDecision.APPROVE, "Good work")
    result = await library_large.submit_review(resp.id, r2_id, ReviewDecision.APPROVE, "Agree")

    assert result.final_decision == "approved"
    assert result.final_decision_by == "consensus"

    # Check it was published as a Library entry
    entries = await library_large.search_entries("Approved")
    assert len(entries) == 1


@pytest.mark.asyncio
async def test_both_reject(library_large):
    """Both reject → rejected."""
    resp = await library_large.submit_contribution(
        agent_id=1, agent_name="Agent-1", title="Rejected", content="Bad insight",
    )

    with library_large.db() as session:
        contrib = session.get(LibraryContribution, resp.id)
        r1_id = contrib.reviewer_1_id
        r2_id = contrib.reviewer_2_id

    await library_large.submit_review(resp.id, r1_id, ReviewDecision.REJECT, "Poor quality")
    result = await library_large.submit_review(resp.id, r2_id, ReviewDecision.REJECT, "Agree, reject")

    assert result.final_decision == "rejected"
    assert result.final_decision_by == "consensus"


@pytest.mark.asyncio
async def test_split_decision_without_ai(library_large):
    """Split decision without AI → defaults to reject."""
    resp = await library_large.submit_contribution(
        agent_id=1, agent_name="Agent-1", title="Split", content="Controversial",
    )

    with library_large.db() as session:
        contrib = session.get(LibraryContribution, resp.id)
        r1_id = contrib.reviewer_1_id
        r2_id = contrib.reviewer_2_id

    await library_large.submit_review(resp.id, r1_id, ReviewDecision.APPROVE, "Good")
    result = await library_large.submit_review(resp.id, r2_id, ReviewDecision.REJECT, "Bad")

    assert result.final_decision == "rejected"
    assert result.final_decision_by == "genesis_tiebreaker"


@pytest.mark.asyncio
async def test_genesis_solo_approve(library_small):
    """Genesis solo review approve → published."""
    resp = await library_small.submit_contribution(
        agent_id=1, agent_name="Agent-1", title="Solo Approved", content="Content",
    )

    result = await library_small.submit_review(
        resp.id, 0, ReviewDecision.APPROVE, "Approved by Genesis",
    )
    assert result.final_decision == "approved"
    assert result.final_decision_by == "genesis_solo"


# ---------------------------------------------------------------------------
# Review timeouts
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_review_timeout(db_large):
    """24h timeout handled — single reviewer's decision stands."""
    library = LibraryService(db_session_factory=db_large, agora_service=None)

    resp = await library.submit_contribution(
        agent_id=1, agent_name="Agent-1", title="Timeout Test", content="Content",
    )

    # Simulate one reviewer completing, other timing out
    with db_large() as session:
        contrib = session.get(LibraryContribution, resp.id)
        contrib.reviewer_1_decision = "approve"
        contrib.reviewer_1_reasoning = "Good"
        contrib.reviewer_1_completed_at = datetime.now(timezone.utc)
        # Backdate creation to trigger timeout
        contrib.created_at = datetime.now(timezone.utc) - timedelta(hours=25)
        session.commit()

    await library.handle_review_timeouts()

    with db_large() as session:
        contrib = session.get(LibraryContribution, resp.id)
        assert contrib.final_decision == "approved"
        assert "timed out" in (contrib.genesis_reasoning or "").lower()


# ---------------------------------------------------------------------------
# Reputation effects
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_reputation_effects_logged(library_small, caplog):
    """Reputation changes are logged correctly."""
    resp = await library_small.submit_contribution(
        agent_id=1, agent_name="Agent-1", title="Rep Test", content="Content",
    )

    await library_small.submit_review(
        resp.id, 0, ReviewDecision.APPROVE, "Good",
    )

    # Verify contribution was approved and effects applied
    with library_small.db() as session:
        contrib = session.get(LibraryContribution, resp.id)
        assert contrib.reputation_effects_applied is True
