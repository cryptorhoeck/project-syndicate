"""Tests for the Opportunities Manager."""

__version__ = "0.8.0"

import pytest
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.common.models import Agent, Base, Opportunity
from src.agents.opportunities import OpportunityManager


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

    # Seed agents
    session.add(Agent(
        id=1, name="Scout-Alpha", type="scout", status="active",
        generation=1, capital_allocated=100.0, capital_current=100.0,
        thinking_budget_daily=0.50, thinking_budget_used_today=0.0,
    ))
    session.add(Agent(
        id=2, name="Strategist-Prime", type="strategist", status="active",
        generation=1, capital_allocated=100.0, capital_current=100.0,
        thinking_budget_daily=0.50, thinking_budget_used_today=0.0,
    ))
    session.commit()
    yield session
    session.close()


class TestCreateOpportunity:
    def test_creates_opportunity(self, db_session):
        scout = db_session.query(Agent).get(1)
        mgr = OpportunityManager(db_session)
        opp = mgr.create_opportunity(
            scout=scout,
            market="SOL/USDT",
            signal_type="volume_breakout",
            details="High volume spike on SOL",
            urgency="high",
            confidence=8,
        )
        assert opp.id is not None
        assert opp.status == "new"
        assert opp.market == "SOL/USDT"
        assert opp.scout_agent_id == 1
        assert opp.confidence == 8

    def test_sets_expiry(self, db_session):
        scout = db_session.query(Agent).get(1)
        mgr = OpportunityManager(db_session)
        opp = mgr.create_opportunity(
            scout=scout,
            market="BTC/USDT",
            signal_type="trend_reversal",
            details="BTC reversing from support",
            ttl_hours=2,
        )
        assert opp.expires_at is not None


class TestClaimOpportunity:
    def test_claim_success(self, db_session):
        scout = db_session.query(Agent).get(1)
        strat = db_session.query(Agent).get(2)
        mgr = OpportunityManager(db_session)

        opp = mgr.create_opportunity(scout=scout, market="SOL/USDT",
                                     signal_type="breakout", details="test")
        claimed = mgr.claim_opportunity(opp.id, strat)
        assert claimed is not None
        assert claimed.status == "claimed"
        assert claimed.claimed_by_agent_id == 2

    def test_cannot_claim_already_claimed(self, db_session):
        scout = db_session.query(Agent).get(1)
        strat = db_session.query(Agent).get(2)
        mgr = OpportunityManager(db_session)

        opp = mgr.create_opportunity(scout=scout, market="SOL/USDT",
                                     signal_type="breakout", details="test")
        mgr.claim_opportunity(opp.id, strat)
        result = mgr.claim_opportunity(opp.id, strat)
        assert result is None

    def test_cannot_claim_nonexistent(self, db_session):
        strat = db_session.query(Agent).get(2)
        mgr = OpportunityManager(db_session)
        result = mgr.claim_opportunity(9999, strat)
        assert result is None


class TestGetUnclaimed:
    def test_returns_new_opportunities(self, db_session):
        scout = db_session.query(Agent).get(1)
        mgr = OpportunityManager(db_session)

        mgr.create_opportunity(scout=scout, market="SOL/USDT",
                               signal_type="breakout", details="a")
        mgr.create_opportunity(scout=scout, market="ETH/USDT",
                               signal_type="support", details="b")

        unclaimed = mgr.get_unclaimed()
        assert len(unclaimed) == 2

    def test_filters_by_market(self, db_session):
        scout = db_session.query(Agent).get(1)
        mgr = OpportunityManager(db_session)

        mgr.create_opportunity(scout=scout, market="SOL/USDT",
                               signal_type="breakout", details="a")
        mgr.create_opportunity(scout=scout, market="ETH/USDT",
                               signal_type="support", details="b")

        sol_only = mgr.get_unclaimed(market="SOL/USDT")
        assert len(sol_only) == 1
        assert sol_only[0].market == "SOL/USDT"

    def test_excludes_expired(self, db_session):
        scout = db_session.query(Agent).get(1)
        mgr = OpportunityManager(db_session)

        opp = mgr.create_opportunity(scout=scout, market="SOL/USDT",
                                     signal_type="breakout", details="a",
                                     ttl_hours=0)
        # Manually expire it
        opp.expires_at = datetime.now(timezone.utc) - timedelta(hours=1)
        db_session.add(opp)
        db_session.commit()

        unclaimed = mgr.get_unclaimed()
        assert len(unclaimed) == 0


class TestExpireStale:
    def test_expires_past_ttl(self, db_session):
        scout = db_session.query(Agent).get(1)
        mgr = OpportunityManager(db_session)

        opp = mgr.create_opportunity(scout=scout, market="SOL/USDT",
                                     signal_type="breakout", details="a")
        opp.expires_at = datetime.now(timezone.utc) - timedelta(hours=1)
        db_session.add(opp)
        db_session.commit()

        count = mgr.expire_stale()
        assert count == 1

        refreshed = db_session.query(Opportunity).get(opp.id)
        assert refreshed.status == "expired"

    def test_does_not_expire_fresh(self, db_session):
        scout = db_session.query(Agent).get(1)
        mgr = OpportunityManager(db_session)

        mgr.create_opportunity(scout=scout, market="SOL/USDT",
                               signal_type="breakout", details="a")

        count = mgr.expire_stale()
        assert count == 0


class TestConvertToPlan:
    def test_marks_converted(self, db_session):
        scout = db_session.query(Agent).get(1)
        mgr = OpportunityManager(db_session)

        opp = mgr.create_opportunity(scout=scout, market="SOL/USDT",
                                     signal_type="breakout", details="a")
        mgr.convert_to_plan(opp.id, plan_id=42)

        refreshed = db_session.query(Opportunity).get(opp.id)
        assert refreshed.status == "converted"
        assert refreshed.converted_to_plan_id == 42


class TestFormatForContext:
    def test_format_with_opportunities(self, db_session):
        scout = db_session.query(Agent).get(1)
        mgr = OpportunityManager(db_session)

        mgr.create_opportunity(scout=scout, market="SOL/USDT",
                               signal_type="breakout", details="Test opp")

        opps = mgr.get_unclaimed()
        text = mgr.format_for_context(opps)
        assert "ACTIVE OPPORTUNITIES" in text
        assert "SOL/USDT" in text

    def test_format_empty(self, db_session):
        mgr = OpportunityManager(db_session)
        text = mgr.format_for_context([])
        assert "No active opportunities" in text
