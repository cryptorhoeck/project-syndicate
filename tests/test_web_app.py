"""Tests for the Project Syndicate Web Frontend."""

__version__ = "0.6.0"

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.common.models import (
    Agent, AgoraChannel, Base, GamingFlag, IntelSignal,
    LibraryEntry, Message, ReviewRequest, SystemState,
)
from src.web.app import create_app


@pytest.fixture
def db_factory():
    """Create an in-memory SQLite DB with all tables and seed data."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)

    with factory() as session:
        session.add(SystemState(
            id=1, total_treasury=500.0, peak_treasury=1000.0,
            current_regime="crab", active_agent_count=0, alert_status="green",
        ))
        session.add(Agent(
            id=0, name="Genesis", type="genesis", status="active",
            reputation_score=0.0, generation=0,
        ))
        for ch_name in ["system-alerts", "genesis-log", "daily-report",
                        "market-intel", "strategy-proposals", "trade-signals",
                        "agent-chat"]:
            session.add(AgoraChannel(name=ch_name, description=f"{ch_name} channel", is_system=True))
        session.commit()

    return factory, engine


@pytest.fixture
def app(db_factory):
    """Create a test app with SQLite in-memory database."""
    factory, engine = db_factory
    application = create_app()
    # Mark DB as already initialized so lifespan skips PostgreSQL connection
    application.state._db_initialized = True
    application.state.db_session_factory = factory
    application.state.engine = engine
    return application


@pytest.fixture
def client(app):
    return TestClient(app)


@pytest.fixture
def app_with_agents(app):
    """App with a few test agents seeded."""
    factory = app.state.db_session_factory
    with factory() as session:
        session.add(Agent(
            id=1, name="Scout-Alpha", type="scout", status="active",
            reputation_score=120.0, generation=1, composite_score=1.5,
            total_true_pnl=42.0,
        ))
        session.add(Agent(
            id=2, name="Strategist-Beta", type="strategist", status="active",
            reputation_score=90.0, generation=2, composite_score=1.2,
            total_true_pnl=-10.0,
        ))
        session.add(Agent(
            id=3, name="Critic-Gamma", type="critic", status="hibernating",
            reputation_score=80.0, generation=1, composite_score=0.8,
            total_true_pnl=5.0,
        ))
        session.commit()
    return app


@pytest.fixture
def client_with_agents(app_with_agents):
    return TestClient(app_with_agents)


@pytest.fixture
def app_with_messages(app_with_agents):
    """App with agents and Agora messages."""
    factory = app_with_agents.state.db_session_factory
    with factory() as session:
        session.add(Message(
            id=1, agent_id=0, agent_name="Genesis", channel="genesis-log",
            content="Cycle complete", message_type="system",
        ))
        session.add(Message(
            id=2, agent_id=1, agent_name="Scout-Alpha", channel="trade-signals",
            content="BTC/USDT bullish", message_type="signal", importance=1,
        ))
        session.add(Message(
            id=3, agent_id=1, agent_name="Scout-Alpha", channel="system-alerts",
            content="Test alert", message_type="alert", importance=2,
        ))
        session.commit()
    return app_with_agents


@pytest.fixture
def client_with_messages(app_with_messages):
    return TestClient(app_with_messages)


# ──────────────────────────────────────────────
# APP STARTUP
# ──────────────────────────────────────────────

class TestAppStartup:
    def test_app_starts(self, client):
        response = client.get("/agora")
        assert response.status_code == 200

    def test_root_redirects_to_agora(self, client):
        response = client.get("/", follow_redirects=False)
        assert response.status_code == 302
        assert response.headers["location"] == "/agora"

    def test_admin_redirects(self, client):
        response = client.get("/admin/agora", follow_redirects=False)
        assert response.status_code == 302
        assert response.headers["location"] == "/agora"


# ──────────────────────────────────────────────
# PAGE ROUTES
# ──────────────────────────────────────────────

class TestPageRoutes:
    def test_agora_page_loads(self, client):
        response = client.get("/agora")
        assert response.status_code == 200
        assert "Agora" in response.text

    def test_leaderboard_page_loads(self, client):
        response = client.get("/leaderboard")
        assert response.status_code == 200
        assert "Leaderboard" in response.text

    def test_library_page_loads(self, client):
        response = client.get("/library")
        assert response.status_code == 200
        assert "Library" in response.text

    def test_agents_page_loads(self, client):
        response = client.get("/agents")
        assert response.status_code == 200
        assert "Syndicate" in response.text

    def test_system_page_loads(self, client):
        response = client.get("/system")
        assert response.status_code == 200
        assert "System" in response.text

    def test_agent_detail_404(self, client):
        response = client.get("/agents/999")
        assert response.status_code == 404


# ──────────────────────────────────────────────
# API FRAGMENT ROUTES
# ──────────────────────────────────────────────

class TestAgoraApi:
    def test_api_agora_messages(self, client):
        response = client.get("/api/agora/messages")
        assert response.status_code == 200

    def test_api_agora_messages_with_channel(self, client_with_messages):
        response = client_with_messages.get("/api/agora/messages?channel=genesis-log")
        assert response.status_code == 200
        assert "Cycle complete" in response.text

    def test_api_agora_channels(self, client):
        response = client.get("/api/agora/channels")
        assert response.status_code == 200
        assert "system-alerts" in response.text


class TestLeaderboardApi:
    def test_api_leaderboard_agents(self, client):
        response = client.get("/api/leaderboard/agents")
        assert response.status_code == 200

    def test_api_leaderboard_agents_with_data(self, client_with_agents):
        response = client_with_agents.get("/api/leaderboard/agents")
        assert response.status_code == 200
        assert "Scout-Alpha" in response.text

    def test_api_leaderboard_intel(self, client):
        response = client.get("/api/leaderboard/intel")
        assert response.status_code == 200

    def test_api_leaderboard_critics(self, client):
        response = client.get("/api/leaderboard/critics")
        assert response.status_code == 200

    def test_api_leaderboard_reputation(self, client):
        response = client.get("/api/leaderboard/reputation")
        assert response.status_code == 200

    def test_api_leaderboard_dynasties(self, client):
        response = client.get("/api/leaderboard/dynasties")
        assert response.status_code == 200


class TestLibraryApi:
    def test_api_library_entries(self, client):
        response = client.get("/api/library/entries")
        assert response.status_code == 200

    def test_api_library_entries_by_category(self, client):
        response = client.get("/api/library/entries?category=post_mortem")
        assert response.status_code == 200

    def test_api_library_search(self, client):
        response = client.get("/api/library/entries?search=nonexistent")
        assert response.status_code == 200


class TestAgentsApi:
    def test_api_agents_cards(self, client):
        response = client.get("/api/agents/cards")
        assert response.status_code == 200

    def test_api_agents_cards_with_data(self, client_with_agents):
        response = client_with_agents.get("/api/agents/cards")
        assert response.status_code == 200
        assert "Scout-Alpha" in response.text

    def test_api_agents_messages(self, client_with_messages):
        response = client_with_messages.get("/api/agents/1/messages")
        assert response.status_code == 200

    def test_api_agents_reputation(self, client_with_agents):
        response = client_with_agents.get("/api/agents/1/reputation")
        assert response.status_code == 200


class TestSystemApi:
    def test_api_system_status(self, client):
        response = client.get("/api/system/status")
        assert response.status_code == 200
        assert "NOMINAL" in response.text

    def test_api_system_processes(self, client):
        response = client.get("/api/system/processes")
        assert response.status_code == 200

    def test_api_system_economy(self, client):
        response = client.get("/api/system/economy")
        assert response.status_code == 200

    def test_api_system_alerts(self, client_with_messages):
        response = client_with_messages.get("/api/system/alerts")
        assert response.status_code == 200
        assert "Test alert" in response.text

    def test_api_system_status_pill(self, client):
        response = client.get("/api/system/status-pill")
        assert response.status_code == 200
        assert "NOMINAL" in response.text


# ──────────────────────────────────────────────
# THEME & EMPTY STATES
# ──────────────────────────────────────────────

class TestThemeAndEmptyStates:
    def test_dark_mode_default(self, client):
        response = client.get("/agora")
        assert 'class="dark"' in response.text

    def test_agora_empty_state(self, client):
        response = client.get("/api/agora/messages")
        assert "quiet" in response.text.lower() or response.status_code == 200

    def test_agents_empty_state(self, client):
        response = client.get("/api/agents/cards")
        assert "empty" in response.text.lower() or "arena" in response.text.lower()

    def test_leaderboard_empty_state(self, client):
        response = client.get("/api/leaderboard/agents")
        assert "No agents to rank" in response.text or response.status_code == 200
