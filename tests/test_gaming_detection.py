"""Tests for GamingDetector — wash trading, rubber stamp, intel spam."""

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from unittest.mock import AsyncMock, MagicMock

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.common.models import (
    Agent, Base, CriticAccuracy, GamingFlag, IntelEndorsement, IntelSignal, Message,
)
from src.economy.gaming_detection import GamingDetector


@pytest.fixture
def db_session_factory():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as session:
        session.add(Agent(id=1, name="Scout-A", type="scout", status="active", reputation_score=100.0))
        session.add(Agent(id=2, name="Trader-B", type="operator", status="active", reputation_score=100.0))
        session.add(Agent(id=3, name="Critic-C", type="critic", status="active", reputation_score=100.0))
        session.add(Agent(id=4, name="Scout-D", type="scout", status="active", reputation_score=100.0))
        session.add(Message(id=1, agent_id=1, channel="trade-signals", content="test", message_type="signal"))
        session.commit()
    return factory


@pytest.fixture
def detector(db_session_factory):
    return GamingDetector(db_session_factory, agora_service=None)


def _seed_signals_and_endorsements(factory, scout_id, endorser_id, count, extra_endorsers=None):
    """Seed 'count' signals from scout, all endorsed by endorser."""
    now = datetime.now(timezone.utc)
    with factory() as session:
        for i in range(count):
            signal = IntelSignal(
                message_id=1, scout_agent_id=scout_id,
                scout_agent_name=f"Scout-{scout_id}", asset="BTC/USDT",
                direction="bullish", confidence_level=3, price_at_creation=50000.0,
                expires_at=now + timedelta(hours=48), status="active",
                endorsement_count=1,
            )
            session.add(signal)
            session.flush()
            end = IntelEndorsement(
                signal_id=signal.id, endorser_agent_id=endorser_id,
                endorser_agent_name=f"Trader-{endorser_id}",
                stake_amount=10.0, settlement_status="pending",
                created_at=now - timedelta(days=1),
            )
            session.add(end)

            if extra_endorsers:
                for eid in extra_endorsers:
                    end2 = IntelEndorsement(
                        signal_id=signal.id, endorser_agent_id=eid,
                        endorser_agent_name=f"Agent-{eid}",
                        stake_amount=10.0, settlement_status="pending",
                        created_at=now - timedelta(days=1),
                    )
                    session.add(end2)
        session.commit()


class TestWashTrading:
    def test_wash_trading_detection(self, db_session_factory, detector):
        # Agent 2 endorses agent 1's signals 5 times (100% of endorsements)
        _seed_signals_and_endorsements(db_session_factory, scout_id=1, endorser_id=2, count=5)
        flags = asyncio.get_event_loop().run_until_complete(
            detector.check_wash_trading(lookback_days=7)
        )
        assert len(flags) >= 1
        assert flags[0].flag_type == "wash_trading"
        assert set(flags[0].agent_ids) == {1, 2}

    def test_wash_trading_below_threshold(self, db_session_factory, detector):
        # Agent 2 endorses agent 1 twice, but also endorses agent 4 many times
        _seed_signals_and_endorsements(db_session_factory, scout_id=1, endorser_id=2, count=2)
        _seed_signals_and_endorsements(db_session_factory, scout_id=4, endorser_id=2, count=10)
        flags = asyncio.get_event_loop().run_until_complete(
            detector.check_wash_trading(lookback_days=7)
        )
        # The pair (1, 2) has 2 endorsements — won't be flagged (threshold is >2 AND >50%)
        wash_flags_for_pair = [f for f in flags if set(f.agent_ids) == {1, 2}]
        assert len(wash_flags_for_pair) == 0


class TestRubberStamp:
    def test_rubber_stamp_detection(self, db_session_factory, detector):
        with db_session_factory() as session:
            session.add(CriticAccuracy(
                critic_agent_id=3, total_reviews=12, accurate_reviews=10,
                accuracy_score=0.83, approve_count=11, reject_count=1,
            ))
            session.commit()

        flags = asyncio.get_event_loop().run_until_complete(
            detector.check_rubber_stamp_critics()
        )
        assert len(flags) >= 1
        assert flags[0].flag_type == "rubber_stamp"

    def test_rubber_stamp_below_threshold(self, db_session_factory, detector):
        with db_session_factory() as session:
            session.add(CriticAccuracy(
                critic_agent_id=3, total_reviews=10, accurate_reviews=8,
                accuracy_score=0.8, approve_count=8, reject_count=2,
            ))
            session.commit()

        flags = asyncio.get_event_loop().run_until_complete(
            detector.check_rubber_stamp_critics()
        )
        assert len(flags) == 0

    def test_rubber_stamp_insufficient_reviews(self, db_session_factory, detector):
        with db_session_factory() as session:
            session.add(CriticAccuracy(
                critic_agent_id=3, total_reviews=5, accurate_reviews=5,
                accuracy_score=1.0, approve_count=5,
            ))
            session.commit()

        flags = asyncio.get_event_loop().run_until_complete(
            detector.check_rubber_stamp_critics()
        )
        assert len(flags) == 0


class TestIntelSpam:
    def test_intel_spam_detection(self, db_session_factory, detector):
        now = datetime.now(timezone.utc)
        with db_session_factory() as session:
            for i in range(25):
                signal = IntelSignal(
                    message_id=1, scout_agent_id=1, scout_agent_name="Scout-A",
                    asset="BTC/USDT", direction="bullish", confidence_level=3,
                    price_at_creation=50000.0,
                    expires_at=now + timedelta(hours=48), status="active",
                    endorsement_count=0,  # No endorsements
                    created_at=now - timedelta(days=i % 30),
                )
                session.add(signal)
            # Give 1 signal an endorsement (4% rate < 10%)
            session.flush()
            first_signal = session.query(IntelSignal).filter_by(scout_agent_id=1).first()
            first_signal.endorsement_count = 1
            session.commit()

        flags = asyncio.get_event_loop().run_until_complete(
            detector.check_intel_spam()
        )
        assert len(flags) >= 1
        assert flags[0].flag_type == "intel_spam"

    def test_intel_spam_below_threshold(self, db_session_factory, detector):
        now = datetime.now(timezone.utc)
        with db_session_factory() as session:
            for i in range(25):
                signal = IntelSignal(
                    message_id=1, scout_agent_id=1, scout_agent_name="Scout-A",
                    asset="BTC/USDT", direction="bullish", confidence_level=3,
                    price_at_creation=50000.0,
                    expires_at=now + timedelta(hours=48), status="active",
                    endorsement_count=1 if i < 5 else 0,  # 20% rate > 10%
                    created_at=now - timedelta(days=i % 30),
                )
                session.add(signal)
            session.commit()

        flags = asyncio.get_event_loop().run_until_complete(
            detector.check_intel_spam()
        )
        assert len(flags) == 0


class TestFlagManagement:
    def test_resolve_flag(self, db_session_factory, detector):
        with db_session_factory() as session:
            flag = GamingFlag(
                flag_type="wash_trading", agent_ids=[1, 2],
                evidence="Test evidence", severity="warning",
            )
            session.add(flag)
            session.commit()
            flag_id = flag.id

        result = asyncio.get_event_loop().run_until_complete(
            detector.resolve_flag(flag_id=flag_id, reviewed_by="genesis")
        )
        assert result is True
        with db_session_factory() as session:
            f = session.get(GamingFlag, flag_id)
            assert f.resolved is True

    def test_resolve_flag_with_penalty(self, db_session_factory, detector):
        with db_session_factory() as session:
            flag = GamingFlag(
                flag_type="wash_trading", agent_ids=[1, 2],
                evidence="Test evidence", severity="penalty",
            )
            session.add(flag)
            session.commit()
            flag_id = flag.id

        result = asyncio.get_event_loop().run_until_complete(
            detector.resolve_flag(flag_id=flag_id, reviewed_by="genesis", penalty=10.0)
        )
        assert result is True
        with db_session_factory() as session:
            f = session.get(GamingFlag, flag_id)
            assert f.penalty_applied == 10.0

    def test_full_detection_cycle(self, db_session_factory, detector):
        # Just verify it runs without errors
        flags = asyncio.get_event_loop().run_until_complete(
            detector.run_full_detection(lookback_days=7)
        )
        assert isinstance(flags, list)
