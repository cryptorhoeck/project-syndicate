"""Tests for the Command Center — Phase 6A."""

__version__ = "0.1.0"

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.common.models import Agent, AgoraChannel, Base, SystemState
from src.web.app import create_app


@pytest.fixture
def app():
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
        session.commit()

    application = create_app()
    application.state._db_initialized = True
    application.state.db_session_factory = factory
    application.state.engine = engine
    return application


@pytest.fixture
def client(app):
    return TestClient(app)


@pytest.fixture
def app_with_agents(app):
    factory = app.state.db_session_factory
    with factory() as session:
        session.add(Agent(
            id=1, name="Scout-Alpha", type="scout", status="active",
            reputation_score=120.0, generation=1, composite_score=0.72,
            total_true_pnl=12.0,
        ))
        session.add(Agent(
            id=2, name="Operator-3", type="operator", status="active",
            reputation_score=90.0, generation=2, composite_score=0.81,
            total_true_pnl=22.0,
        ))
        session.commit()
    return app


class TestCommandCenter:
    def test_root_returns_200(self, client):
        response = client.get("/")
        assert response.status_code == 200

    def test_root_contains_project_syndicate(self, client):
        response = client.get("/")
        assert "PROJECT SYNDICATE" in response.text

    def test_root_contains_live_badge(self, client):
        response = client.get("/")
        assert "LIVE" in response.text

    def test_root_contains_agent_section(self, client):
        response = client.get("/")
        assert "AGENTS" in response.text

    def test_root_contains_live_feed(self, client):
        response = client.get("/")
        assert "LIVE FEED" in response.text

    def test_root_contains_ecosystem(self, client):
        response = client.get("/")
        assert "ECOSYSTEM" in response.text

    def test_root_empty_state(self, client):
        response = client.get("/")
        assert response.status_code == 200
        assert "No agents spawned" in response.text or "AGENTS" in response.text

    def test_root_with_agents(self, app_with_agents):
        client = TestClient(app_with_agents)
        response = client.get("/")
        assert response.status_code == 200
        assert "Scout-Alpha" in response.text
        assert "Operator-3" in response.text


class TestTopbar:
    def test_topbar_endpoint(self, client):
        response = client.get("/api/system/topbar")
        assert response.status_code == 200

    def test_topbar_contains_treasury(self, client):
        response = client.get("/api/system/topbar")
        assert "TREASURY" in response.text
        assert "$500.00" in response.text

    def test_topbar_contains_alert(self, client):
        response = client.get("/api/system/topbar")
        assert "ALERT" in response.text
        assert "GREEN" in response.text


class TestConstellation:
    def test_constellation_endpoint(self, client):
        response = client.get("/api/system/constellation")
        assert response.status_code == 200

    def test_constellation_returns_json(self, client):
        response = client.get("/api/system/constellation")
        data = response.json()
        assert "agents" in data
        assert "connections" in data
        assert isinstance(data["agents"], list)

    def test_constellation_excludes_genesis(self, client):
        response = client.get("/api/system/constellation")
        data = response.json()
        for a in data["agents"]:
            assert a["id"] != 0

    def test_constellation_with_agents(self, app_with_agents):
        client = TestClient(app_with_agents)
        response = client.get("/api/system/constellation")
        data = response.json()
        assert len(data["agents"]) == 2
