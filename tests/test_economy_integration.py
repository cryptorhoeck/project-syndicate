"""Integration tests for the Economy system."""

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.common.models import (
    Agent, Base, IntelEndorsement, IntelSignal, Message, Transaction,
)
from src.economy.economy_service import EconomyService


@pytest.fixture
def db_session_factory():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as session:
        session.add(Agent(id=0, name="Genesis", type="genesis", status="active", reputation_score=0.0))
        session.add(Agent(id=1, name="Scout-A", type="scout", status="active", reputation_score=100.0))
        session.add(Agent(id=2, name="Trader-B", type="operator", status="active", reputation_score=100.0))
        session.add(Agent(id=3, name="Critic-C", type="critic", status="active", reputation_score=100.0))
        session.add(Agent(id=4, name="Strategist-D", type="strategist", status="active", reputation_score=100.0))
        session.add(Message(id=1, agent_id=1, channel="trade-signals", content="test", message_type="signal"))
        session.add(Message(id=2, agent_id=4, channel="strategy-debate", content="proposal", message_type="proposal"))
        session.commit()
    return factory


@pytest.fixture
def economy(db_session_factory):
    return EconomyService(db_session_factory=db_session_factory)


class TestGenesisInitializesReputation:
    def test_genesis_initializes_reputation_on_spawn(self, economy, db_session_factory):
        with db_session_factory() as session:
            session.add(Agent(id=10, name="Newbie", type="scout", status="active", reputation_score=0.0))
            session.commit()

        asyncio.get_event_loop().run_until_complete(
            economy.initialize_agent_reputation(agent_id=10)
        )
        with db_session_factory() as session:
            agent = session.get(Agent, 10)
            assert agent.reputation_score == 100.0


class TestNegativeReputationTrigger:
    def test_negative_rep_triggers_evaluation(self, economy, db_session_factory):
        asyncio.get_event_loop().run_until_complete(
            economy.apply_penalty(agent_id=2, amount=160.0, reason="massive_loss")
        )
        neg = asyncio.get_event_loop().run_until_complete(
            economy.check_negative_reputation_agents()
        )
        assert 2 in neg


class TestFullIntelLifecycle:
    def test_full_intel_lifecycle(self, economy, db_session_factory):
        """Scout creates signal -> Trader endorses -> settlement."""
        signal = asyncio.get_event_loop().run_until_complete(
            economy.intel_market.create_signal(
                scout_agent_id=1, scout_agent_name="Scout-A",
                message_id=1, asset="BTC/USDT", direction="bullish",
                confidence_level=4, price_at_creation=50000.0,
                expires_at=datetime.now(timezone.utc) + timedelta(hours=48),
            )
        )
        assert signal is not None

        endorsement = asyncio.get_event_loop().run_until_complete(
            economy.intel_market.endorse_signal(
                signal_id=signal.id, endorser_agent_id=2,
                endorser_agent_name="Trader-B", stake_amount=10.0,
            )
        )
        assert endorsement is not None

        balance = asyncio.get_event_loop().run_until_complete(
            economy.get_balance(agent_id=2)
        )
        assert balance == 90.0


class TestFullReviewLifecycle:
    def test_full_review_lifecycle(self, economy, db_session_factory):
        """Strategist requests -> Critic accepts -> Critic reviews -> payment."""
        request = asyncio.get_event_loop().run_until_complete(
            economy.review_market.request_review(
                requester_agent_id=4, requester_agent_name="Strategist-D",
                proposal_message_id=2, proposal_summary="BTC momentum strategy",
                budget_reputation=15.0,
            )
        )
        assert request is not None
        assert request.status == "open"

        balance = asyncio.get_event_loop().run_until_complete(
            economy.get_balance(agent_id=4)
        )
        assert balance == 85.0

        assignment = asyncio.get_event_loop().run_until_complete(
            economy.review_market.accept_review(
                request_id=request.id, critic_agent_id=3, critic_agent_name="Critic-C",
            )
        )
        assert assignment is not None

        result = asyncio.get_event_loop().run_until_complete(
            economy.review_market.submit_review(
                assignment_id=assignment.id, verdict="approve",
                reasoning="Sound strategy with good risk management", risk_score=3,
            )
        )
        assert result is not None
        assert result.verdict == "approve"

        critic_balance = asyncio.get_event_loop().run_until_complete(
            economy.get_balance(agent_id=3)
        )
        assert critic_balance == 115.0  # 100 + 15 review payment
