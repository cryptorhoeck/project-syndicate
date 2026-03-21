"""Tests for the SSE live feed — Phase 6A."""

__version__ = "0.1.0"

import json
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.common.models import Agent, Base, Message, SystemState
from src.web.app import create_app
from src.web.routes.api_sse import _format_event


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
        session.add(Message(
            id=1,
            agent_id=0,
            agent_name="Genesis",
            channel="genesis-log",
            content="System initialized",
            message_type="system",
        ))
        session.add(Message(
            id=2,
            agent_id=0,
            agent_name="Genesis",
            channel="system-alerts",
            content="Agent SCOUT-ALPHA terminated — budget exhausted",
            message_type="alert",
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


class TestSSEEventFormat:
    """Test the event formatting logic without hitting the streaming endpoint."""

    def test_format_genesis_event(self, app):
        factory = app.state.db_session_factory
        with factory() as session:
            msg = session.get(Message, 1)
            event = _format_event(msg)

        assert event["type"] == "system"
        assert event["icon"] == "◇"
        assert "Genesis" in event["text"]
        assert event["is_major"] is False

    def test_format_death_event(self, app):
        factory = app.state.db_session_factory
        with factory() as session:
            msg = session.get(Message, 2)
            event = _format_event(msg)

        assert event["icon"] == "☠"
        assert event["color"] == "#ff3d3d"
        assert event["is_major"] is True

    def test_event_has_required_fields(self, app):
        factory = app.state.db_session_factory
        with factory() as session:
            msg = session.get(Message, 1)
            event = _format_event(msg)

        assert "id" in event
        assert "type" in event
        assert "icon" in event
        assert "color" in event
        assert "text" in event
        assert "time" in event
        assert "is_major" in event

    def test_sse_endpoint_exists(self, client):
        """Verify the SSE endpoint is registered (don't stream — it blocks)."""
        # Just check the route exists by looking at the app routes
        routes = [r.path for r in client.app.routes if hasattr(r, 'path')]
        assert "/api/events/stream" in routes
