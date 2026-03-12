"""Tests for the Orientation Protocol."""

__version__ = "0.8.0"

import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from dataclasses import dataclass

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.common.models import Agent, Base
from src.agents.orientation import OrientationProtocol, OrientationResult, ROLE_SUMMARIES, DEFAULT_WATCHLISTS


@dataclass
class MockAPIResponse:
    content: str
    input_tokens: int = 100
    output_tokens: int = 50
    cost_usd: float = 0.005
    latency_ms: int = 500
    model: str = "claude-sonnet-4"
    stop_reason: str = "end_turn"


def _valid_output():
    return '{"situation": "First cycle", "confidence": {"score": 6, "reasoning": "New agent"}, "recent_pattern": "none", "action": {"type": "update_watchlist", "params": {"add_markets": ["BTC/USDT", "ETH/USDT", "SOL/USDT"], "remove_markets": [], "reason": "Initial setup"}}, "reasoning": "Setting up initial watchlist", "self_note": "First cycle complete"}'


def _invalid_output():
    return "This is not JSON at all"


@pytest.fixture
def db_session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    session = factory()

    session.add(Agent(
        id=1, name="Scout-Alpha", type="scout", status="initializing",
        generation=1, capital_allocated=100.0, capital_current=100.0,
        thinking_budget_daily=0.50, thinking_budget_used_today=0.0,
    ))
    session.add(Agent(
        id=2, name="Strategist-Prime", type="strategist", status="initializing",
        generation=1, capital_allocated=100.0, capital_current=100.0,
        thinking_budget_daily=0.50, thinking_budget_used_today=0.0,
    ))
    session.commit()
    yield session
    session.close()


@pytest.fixture
def mock_claude():
    client = MagicMock()
    client.call = AsyncMock(return_value=MockAPIResponse(content=_valid_output()))
    return client


class TestOrientationSuccess:
    @pytest.mark.asyncio
    async def test_scout_orientation(self, db_session, mock_claude):
        protocol = OrientationProtocol(db_session, claude_client=mock_claude)
        agent = db_session.query(Agent).get(1)

        result = await protocol.orient_agent(agent)
        assert result.success
        assert result.agent_name == "Scout-Alpha"
        assert len(result.initial_watchlist) > 0

        # Agent should be marked as oriented
        refreshed = db_session.query(Agent).get(1)
        assert refreshed.orientation_completed
        assert not refreshed.orientation_failed
        assert refreshed.status == "active"

    @pytest.mark.asyncio
    async def test_strategist_orientation(self, db_session, mock_claude):
        # Strategist going idle
        idle_output = '{"situation": "First cycle", "confidence": {"score": 5, "reasoning": "New"}, "recent_pattern": "none", "action": {"type": "go_idle", "params": {"reason": "Waiting for scout intel"}}, "reasoning": "No data yet", "self_note": "Ready"}'
        mock_claude.call = AsyncMock(return_value=MockAPIResponse(content=idle_output))

        protocol = OrientationProtocol(db_session, claude_client=mock_claude)
        agent = db_session.query(Agent).get(2)

        result = await protocol.orient_agent(agent)
        assert result.success
        # Strategist defaults to default watchlist since go_idle doesn't specify markets
        assert isinstance(result.initial_watchlist, list)


class TestOrientationFailure:
    @pytest.mark.asyncio
    async def test_invalid_output_fails(self, db_session, mock_claude):
        mock_claude.call = AsyncMock(
            return_value=MockAPIResponse(content=_invalid_output())
        )
        protocol = OrientationProtocol(db_session, claude_client=mock_claude)
        agent = db_session.query(Agent).get(1)

        result = await protocol.orient_agent(agent)
        assert not result.success
        assert "validation_failed" in result.failure_reason

        refreshed = db_session.query(Agent).get(1)
        assert refreshed.orientation_failed
        assert not refreshed.orientation_completed

    @pytest.mark.asyncio
    async def test_api_error_fails(self, db_session, mock_claude):
        mock_claude.call = AsyncMock(side_effect=Exception("API timeout"))
        protocol = OrientationProtocol(db_session, claude_client=mock_claude)
        agent = db_session.query(Agent).get(1)

        result = await protocol.orient_agent(agent)
        assert not result.success
        assert "api_error" in result.failure_reason

    @pytest.mark.asyncio
    async def test_no_claude_client(self, db_session):
        protocol = OrientationProtocol(db_session, claude_client=None)
        agent = db_session.query(Agent).get(1)

        result = await protocol.orient_agent(agent)
        assert not result.success
        assert "no_claude_client" in result.failure_reason


class TestSummaryLoading:
    def test_load_summaries_for_scout(self, db_session):
        protocol = OrientationProtocol(db_session)
        summaries = protocol._load_summaries("scout")
        # Should find the summaries we created
        assert len(summaries) > 0

    def test_role_summary_mapping(self):
        assert "scout" in ROLE_SUMMARIES
        assert "strategist" in ROLE_SUMMARIES
        assert "critic" in ROLE_SUMMARIES
        assert "operator" in ROLE_SUMMARIES

    def test_all_roles_have_thinking_efficiently(self):
        for role, summaries in ROLE_SUMMARIES.items():
            assert "thinking_efficiently" in summaries, f"{role} missing thinking_efficiently"


class TestWatchlistExtraction:
    def test_extract_from_update_watchlist(self, db_session):
        protocol = OrientationProtocol(db_session)
        parsed = {
            "action": {
                "type": "update_watchlist",
                "params": {"add_markets": ["BTC/USDT", "SOL/USDT"]}
            }
        }
        watchlist = protocol._extract_watchlist(parsed, "scout")
        assert watchlist == ["BTC/USDT", "SOL/USDT"]

    def test_extract_from_market_param(self, db_session):
        protocol = OrientationProtocol(db_session)
        parsed = {
            "action": {
                "type": "broadcast_opportunity",
                "params": {"market": "ETH/USDT"}
            }
        }
        watchlist = protocol._extract_watchlist(parsed, "scout")
        assert watchlist == ["ETH/USDT"]

    def test_fallback_to_defaults(self, db_session):
        protocol = OrientationProtocol(db_session)
        parsed = {
            "action": {
                "type": "go_idle",
                "params": {"reason": "nothing to do"}
            }
        }
        watchlist = protocol._extract_watchlist(parsed, "scout")
        assert watchlist == DEFAULT_WATCHLISTS["scout"]


class TestPromptBuilding:
    def test_system_prompt_contains_role(self, db_session):
        protocol = OrientationProtocol(db_session)
        agent = db_session.query(Agent).get(1)
        from src.agents.roles import get_role
        role_def = get_role(agent.type)
        prompt = protocol._build_system_prompt(agent, role_def)
        assert "Scout-Alpha" in prompt
        assert "FIRST CYCLE" in prompt
        assert "WARDEN LIMITS" in prompt

    def test_user_prompt_contains_training(self, db_session):
        protocol = OrientationProtocol(db_session)
        agent = db_session.query(Agent).get(1)
        from src.agents.roles import get_role
        role_def = get_role(agent.type)
        summaries = protocol._load_summaries(agent.type)
        if summaries:
            prompt = protocol._build_user_prompt(agent, role_def, summaries)
            assert "TRAINING MATERIALS" in prompt
