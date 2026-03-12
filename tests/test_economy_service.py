"""Tests for EconomyService — reputation management."""

import asyncio
import pytest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.common.models import Agent, Base, ReputationTransaction
from src.economy.economy_service import EconomyService


@pytest.fixture
def db_session_factory():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as session:
        session.add(Agent(id=0, name="Genesis", type="genesis", status="active", reputation_score=0.0))
        session.add(Agent(id=1, name="Scout-A", type="scout", status="active", reputation_score=100.0))
        session.add(Agent(id=2, name="Scout-B", type="scout", status="active", reputation_score=100.0))
        session.add(Agent(id=3, name="BrokeAgent", type="scout", status="active", reputation_score=5.0))
        session.commit()
    return factory


@pytest.fixture
def economy(db_session_factory):
    return EconomyService(db_session_factory=db_session_factory)


class TestInitializeReputation:
    def test_initialize_reputation(self, economy, db_session_factory):
        asyncio.get_event_loop().run_until_complete(
            economy.initialize_agent_reputation(agent_id=1)
        )
        with db_session_factory() as session:
            agent = session.get(Agent, 1)
            assert agent.reputation_score == 100.0


class TestTransferReputation:
    def test_transfer_reputation(self, economy, db_session_factory):
        result = asyncio.get_event_loop().run_until_complete(
            economy.transfer_reputation(
                from_agent_id=1, to_agent_id=2, amount=30.0, reason="test_transfer"
            )
        )
        assert result is True
        with db_session_factory() as session:
            a1 = session.get(Agent, 1)
            a2 = session.get(Agent, 2)
            assert a1.reputation_score == 70.0
            assert a2.reputation_score == 130.0

    def test_transfer_insufficient_balance(self, economy):
        result = asyncio.get_event_loop().run_until_complete(
            economy.transfer_reputation(
                from_agent_id=3, to_agent_id=2, amount=50.0, reason="too_much"
            )
        )
        assert result is False


class TestRewardsAndPenalties:
    def test_apply_reward(self, economy, db_session_factory):
        asyncio.get_event_loop().run_until_complete(
            economy.apply_reward(agent_id=1, amount=20.0, reason="bonus")
        )
        with db_session_factory() as session:
            agent = session.get(Agent, 1)
            assert agent.reputation_score == 120.0

    def test_apply_penalty(self, economy, db_session_factory):
        asyncio.get_event_loop().run_until_complete(
            economy.apply_penalty(agent_id=1, amount=10.0, reason="violation")
        )
        with db_session_factory() as session:
            agent = session.get(Agent, 1)
            assert agent.reputation_score == 90.0

    def test_negative_reputation_detection(self, economy, db_session_factory):
        asyncio.get_event_loop().run_until_complete(
            economy.apply_penalty(agent_id=3, amount=60.0, reason="massive_penalty")
        )
        neg = asyncio.get_event_loop().run_until_complete(
            economy.check_negative_reputation_agents()
        )
        assert 3 in neg


class TestEscrow:
    def test_escrow_and_release(self, economy, db_session_factory):
        escrowed = asyncio.get_event_loop().run_until_complete(
            economy.escrow_reputation(agent_id=1, amount=30.0, reason="test")
        )
        assert escrowed is True
        with db_session_factory() as session:
            assert session.get(Agent, 1).reputation_score == 70.0

        asyncio.get_event_loop().run_until_complete(
            economy.release_escrow(agent_id=1, amount=30.0, reason="test")
        )
        with db_session_factory() as session:
            assert session.get(Agent, 1).reputation_score == 100.0

    def test_escrow_insufficient(self, economy):
        result = asyncio.get_event_loop().run_until_complete(
            economy.escrow_reputation(agent_id=3, amount=50.0, reason="too_much")
        )
        assert result is False


class TestTransactionHistory:
    def test_transaction_history(self, economy):
        asyncio.get_event_loop().run_until_complete(
            economy.apply_reward(agent_id=1, amount=10.0, reason="r1")
        )
        asyncio.get_event_loop().run_until_complete(
            economy.apply_reward(agent_id=1, amount=5.0, reason="r2")
        )
        history = asyncio.get_event_loop().run_until_complete(
            economy.get_transaction_history(agent_id=1)
        )
        assert len(history) >= 2
