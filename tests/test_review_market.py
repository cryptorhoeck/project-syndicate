"""Tests for ReviewMarket — review requests and critic assignments."""

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from unittest.mock import AsyncMock, MagicMock

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.common.models import (
    Agent, Base, CriticAccuracy, Message, ReviewAssignment, ReviewRequest,
)
from src.economy.review_market import ReviewMarket


@pytest.fixture
def db_session_factory():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as session:
        session.add(Agent(id=1, name="Strategist-A", type="strategist", status="active", reputation_score=100.0))
        session.add(Agent(id=2, name="Critic-B", type="critic", status="active", reputation_score=100.0))
        session.add(Agent(id=3, name="Critic-C", type="critic", status="active", reputation_score=100.0))
        session.add(Agent(id=4, name="Broke", type="strategist", status="active", reputation_score=5.0))
        session.add(Message(id=1, agent_id=1, channel="strategy-debate", content="proposal", message_type="proposal"))
        session.commit()
    return factory


@pytest.fixture
def economy_mock(db_session_factory):
    economy = MagicMock()
    economy.MIN_REVIEW_BUDGET = 10.0
    economy.MAX_REVIEW_BUDGET = 25.0

    async def get_balance(agent_id):
        with db_session_factory() as session:
            agent = session.get(Agent, agent_id)
            return agent.reputation_score if agent else 0.0
    economy.get_balance = get_balance

    async def escrow_reputation(agent_id, amount, reason):
        with db_session_factory() as session:
            agent = session.get(Agent, agent_id)
            if agent and agent.reputation_score >= amount:
                agent.reputation_score -= amount
                session.commit()
                return True
            return False
    economy.escrow_reputation = escrow_reputation

    economy.apply_reward = AsyncMock()
    economy.release_escrow = AsyncMock()
    return economy


@pytest.fixture
def review_market(db_session_factory, economy_mock):
    return ReviewMarket(db_session_factory, economy_mock, agora_service=None)


class TestRequestReview:
    def test_request_review(self, review_market, db_session_factory):
        req = asyncio.get_event_loop().run_until_complete(
            review_market.request_review(
                requester_agent_id=1, requester_agent_name="Strategist-A",
                proposal_message_id=1, proposal_summary="Buy BTC strategy",
                budget_reputation=15.0,
            )
        )
        assert req is not None
        assert req.status == "open"
        assert req.budget_reputation == 15.0

    def test_request_review_two_required(self, review_market):
        req = asyncio.get_event_loop().run_until_complete(
            review_market.request_review(
                requester_agent_id=1, requester_agent_name="Strategist-A",
                proposal_message_id=1, proposal_summary="Big strategy",
                budget_reputation=15.0, capital_percentage=0.30,
            )
        )
        assert req is not None
        assert req.requires_two_reviews is True

    def test_request_review_insufficient_reputation(self, review_market):
        req = asyncio.get_event_loop().run_until_complete(
            review_market.request_review(
                requester_agent_id=4, requester_agent_name="Broke",
                proposal_message_id=1, proposal_summary="Strategy",
                budget_reputation=15.0,
            )
        )
        assert req is None


class TestReviewFlow:
    def _create_request(self, review_market, requires_two=False):
        cap = 0.30 if requires_two else 0.0
        return asyncio.get_event_loop().run_until_complete(
            review_market.request_review(
                requester_agent_id=1, requester_agent_name="Strategist-A",
                proposal_message_id=1, proposal_summary="Strategy proposal",
                budget_reputation=15.0, capital_percentage=cap,
            )
        )

    def test_accept_review(self, review_market):
        req = self._create_request(review_market)
        assignment = asyncio.get_event_loop().run_until_complete(
            review_market.accept_review(
                request_id=req.id, critic_agent_id=2, critic_agent_name="Critic-B",
            )
        )
        assert assignment is not None
        assert assignment.critic_agent_id == 2

    def test_accept_own_request(self, review_market):
        req = self._create_request(review_market)
        assignment = asyncio.get_event_loop().run_until_complete(
            review_market.accept_review(
                request_id=req.id, critic_agent_id=1, critic_agent_name="Strategist-A",
            )
        )
        assert assignment is None

    def test_accept_already_full(self, review_market):
        req = self._create_request(review_market, requires_two=False)
        asyncio.get_event_loop().run_until_complete(
            review_market.accept_review(
                request_id=req.id, critic_agent_id=2, critic_agent_name="Critic-B",
            )
        )
        second = asyncio.get_event_loop().run_until_complete(
            review_market.accept_review(
                request_id=req.id, critic_agent_id=3, critic_agent_name="Critic-C",
            )
        )
        assert second is None

    def test_accept_second_reviewer(self, review_market):
        req = self._create_request(review_market, requires_two=True)
        a1 = asyncio.get_event_loop().run_until_complete(
            review_market.accept_review(
                request_id=req.id, critic_agent_id=2, critic_agent_name="Critic-B",
            )
        )
        a2 = asyncio.get_event_loop().run_until_complete(
            review_market.accept_review(
                request_id=req.id, critic_agent_id=3, critic_agent_name="Critic-C",
            )
        )
        assert a1 is not None
        assert a2 is not None

    def test_submit_review(self, review_market, economy_mock):
        req = self._create_request(review_market)
        assignment = asyncio.get_event_loop().run_until_complete(
            review_market.accept_review(
                request_id=req.id, critic_agent_id=2, critic_agent_name="Critic-B",
            )
        )
        result = asyncio.get_event_loop().run_until_complete(
            review_market.submit_review(
                assignment_id=assignment.id, verdict="approve",
                reasoning="Good strategy", risk_score=3,
            )
        )
        assert result is not None
        assert result.verdict == "approve"
        assert result.completed_at is not None
        # Critic should be paid
        economy_mock.apply_reward.assert_called()

    def test_submit_review_two_critics(self, review_market, economy_mock):
        req = self._create_request(review_market, requires_two=True)
        a1 = asyncio.get_event_loop().run_until_complete(
            review_market.accept_review(
                request_id=req.id, critic_agent_id=2, critic_agent_name="Critic-B",
            )
        )
        a2 = asyncio.get_event_loop().run_until_complete(
            review_market.accept_review(
                request_id=req.id, critic_agent_id=3, critic_agent_name="Critic-C",
            )
        )
        asyncio.get_event_loop().run_until_complete(
            review_market.submit_review(
                assignment_id=a1.id, verdict="approve", reasoning="OK", risk_score=2,
            )
        )
        asyncio.get_event_loop().run_until_complete(
            review_market.submit_review(
                assignment_id=a2.id, verdict="reject", reasoning="Too risky", risk_score=8,
            )
        )
        # Both should be paid (half each)
        assert economy_mock.apply_reward.call_count >= 2

    def test_expire_stale_requests(self, review_market, db_session_factory, economy_mock):
        # Create an expired request directly
        with db_session_factory() as session:
            req = ReviewRequest(
                requester_agent_id=1, requester_agent_name="Strategist-A",
                proposal_message_id=1, proposal_summary="Old request",
                budget_reputation=15.0, requires_two_reviews=False,
                status="open",
                expires_at=datetime.now(timezone.utc) - timedelta(hours=1),
            )
            session.add(req)
            session.commit()

        count = asyncio.get_event_loop().run_until_complete(
            review_market.expire_stale_requests()
        )
        assert count >= 1
        economy_mock.release_escrow.assert_called()

    def test_overdue_assignment(self, review_market, db_session_factory):
        with db_session_factory() as session:
            req = ReviewRequest(
                requester_agent_id=1, requester_agent_name="Strategist-A",
                proposal_message_id=1, proposal_summary="Test",
                budget_reputation=15.0, status="assigned",
                expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
            )
            session.add(req)
            session.flush()
            assignment = ReviewAssignment(
                review_request_id=req.id, critic_agent_id=2,
                critic_agent_name="Critic-B",
                deadline_at=datetime.now(timezone.utc) - timedelta(hours=2),
            )
            session.add(assignment)
            session.commit()

        count = asyncio.get_event_loop().run_until_complete(
            review_market.check_overdue_assignments()
        )
        assert count >= 1


class TestCriticAccuracy:
    def test_update_accuracy(self, review_market, db_session_factory):
        # First seed a critic_accuracy record
        with db_session_factory() as session:
            session.add(CriticAccuracy(
                critic_agent_id=2, total_reviews=5, accurate_reviews=3,
                accuracy_score=0.6,
            ))
            session.commit()

        asyncio.get_event_loop().run_until_complete(
            review_market.update_critic_accuracy(critic_agent_id=2, was_accurate=True)
        )
        with db_session_factory() as session:
            record = session.get(CriticAccuracy, 2)
            assert record.accurate_reviews == 4

    def test_get_critic_stats(self, review_market, db_session_factory):
        with db_session_factory() as session:
            session.add(CriticAccuracy(
                critic_agent_id=2, total_reviews=10, accurate_reviews=7,
                accuracy_score=0.7, approve_count=5, reject_count=3,
                conditional_count=2, avg_risk_score=4.5,
            ))
            session.commit()

        stats = asyncio.get_event_loop().run_until_complete(
            review_market.get_critic_stats(critic_agent_id=2)
        )
        assert stats is not None
        assert stats.total_reviews == 10
