"""Tests for IntelMarket — signal creation and endorsement."""

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.common.models import Agent, Base, IntelSignal, IntelEndorsement, Message
from src.economy.intel_market import IntelMarket


@pytest.fixture
def db_session_factory():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as session:
        session.add(Agent(id=1, name="Scout-A", type="scout", status="active", reputation_score=100.0))
        session.add(Agent(id=2, name="Trader-B", type="operator", status="active", reputation_score=100.0))
        session.add(Agent(id=3, name="Broke", type="scout", status="active", reputation_score=10.0))
        session.add(Agent(id=4, name="LowRep", type="scout", status="active", reputation_score=40.0))
        # Seed a message for FK
        session.add(Message(id=1, agent_id=1, channel="trade-signals", content="test", message_type="signal"))
        session.commit()
    return factory


@pytest.fixture
def economy_mock(db_session_factory):
    economy = MagicMock()
    economy.db = db_session_factory
    economy.MIN_REPUTATION_FOR_INTEL = 50.0
    economy.MIN_REPUTATION_FOR_ENDORSEMENT = 25.0
    economy.MIN_ENDORSEMENT_STAKE = 5.0
    economy.MAX_ENDORSEMENT_STAKE = 25.0

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

    return economy


@pytest.fixture
def intel_market(db_session_factory, economy_mock):
    return IntelMarket(db_session_factory, economy_mock, agora_service=None)


def _future_dt(hours=48):
    return datetime.now(timezone.utc) + timedelta(hours=hours)


class TestCreateSignal:
    def test_create_signal(self, intel_market, db_session_factory):
        signal = asyncio.get_event_loop().run_until_complete(
            intel_market.create_signal(
                scout_agent_id=1, scout_agent_name="Scout-A",
                message_id=1, asset="BTC/USDT", direction="bullish",
                confidence_level=4, price_at_creation=50000.0, expires_at=_future_dt(),
            )
        )
        assert signal is not None
        assert signal.asset == "BTC/USDT"
        assert signal.direction == "bullish"
        assert signal.status == "active"

    def test_create_signal_low_reputation(self, intel_market):
        signal = asyncio.get_event_loop().run_until_complete(
            intel_market.create_signal(
                scout_agent_id=4, scout_agent_name="LowRep",
                message_id=1, asset="BTC/USDT", direction="bullish",
                confidence_level=3, price_at_creation=50000.0, expires_at=_future_dt(),
            )
        )
        assert signal is None

    def test_create_signal_invalid_asset(self, intel_market):
        signal = asyncio.get_event_loop().run_until_complete(
            intel_market.create_signal(
                scout_agent_id=1, scout_agent_name="Scout-A",
                message_id=1, asset="BTCUSDT", direction="bullish",
                confidence_level=3, price_at_creation=50000.0, expires_at=_future_dt(),
            )
        )
        assert signal is None

    def test_create_signal_past_expiry(self, intel_market):
        signal = asyncio.get_event_loop().run_until_complete(
            intel_market.create_signal(
                scout_agent_id=1, scout_agent_name="Scout-A",
                message_id=1, asset="BTC/USDT", direction="bearish",
                confidence_level=3, price_at_creation=50000.0,
                expires_at=datetime.now(timezone.utc) - timedelta(hours=1),
            )
        )
        assert signal is None


class TestEndorseSignal:
    def _create_signal(self, intel_market):
        return asyncio.get_event_loop().run_until_complete(
            intel_market.create_signal(
                scout_agent_id=1, scout_agent_name="Scout-A",
                message_id=1, asset="BTC/USDT", direction="bullish",
                confidence_level=4, price_at_creation=50000.0, expires_at=_future_dt(),
            )
        )

    def test_endorse_signal(self, intel_market, db_session_factory):
        signal = self._create_signal(intel_market)
        endorsement = asyncio.get_event_loop().run_until_complete(
            intel_market.endorse_signal(
                signal_id=signal.id, endorser_agent_id=2,
                endorser_agent_name="Trader-B", stake_amount=10.0,
            )
        )
        assert endorsement is not None
        assert endorsement.stake_amount == 10.0
        # Verify stake was escrowed
        with db_session_factory() as session:
            agent = session.get(Agent, 2)
            assert agent.reputation_score == 90.0

    def test_endorse_own_signal(self, intel_market):
        signal = self._create_signal(intel_market)
        result = asyncio.get_event_loop().run_until_complete(
            intel_market.endorse_signal(
                signal_id=signal.id, endorser_agent_id=1,
                endorser_agent_name="Scout-A", stake_amount=10.0,
            )
        )
        assert result is None

    def test_endorse_duplicate(self, intel_market):
        signal = self._create_signal(intel_market)
        asyncio.get_event_loop().run_until_complete(
            intel_market.endorse_signal(
                signal_id=signal.id, endorser_agent_id=2,
                endorser_agent_name="Trader-B", stake_amount=10.0,
            )
        )
        dup = asyncio.get_event_loop().run_until_complete(
            intel_market.endorse_signal(
                signal_id=signal.id, endorser_agent_id=2,
                endorser_agent_name="Trader-B", stake_amount=10.0,
            )
        )
        assert dup is None

    def test_endorse_expired_signal(self, intel_market, db_session_factory):
        # Create a signal that's already expired
        with db_session_factory() as session:
            signal = IntelSignal(
                message_id=1, scout_agent_id=1, scout_agent_name="Scout-A",
                asset="BTC/USDT", direction="bullish", confidence_level=3,
                price_at_creation=50000.0,
                expires_at=datetime.now(timezone.utc) - timedelta(hours=1),
                status="active",
            )
            session.add(signal)
            session.commit()
            sid = signal.id

        result = asyncio.get_event_loop().run_until_complete(
            intel_market.endorse_signal(
                signal_id=sid, endorser_agent_id=2,
                endorser_agent_name="Trader-B", stake_amount=10.0,
            )
        )
        assert result is None

    def test_endorse_below_min_stake(self, intel_market):
        signal = self._create_signal(intel_market)
        result = asyncio.get_event_loop().run_until_complete(
            intel_market.endorse_signal(
                signal_id=signal.id, endorser_agent_id=2,
                endorser_agent_name="Trader-B", stake_amount=2.0,
            )
        )
        assert result is None

    def test_endorse_above_max_stake(self, intel_market):
        signal = self._create_signal(intel_market)
        result = asyncio.get_event_loop().run_until_complete(
            intel_market.endorse_signal(
                signal_id=signal.id, endorser_agent_id=2,
                endorser_agent_name="Trader-B", stake_amount=30.0,
            )
        )
        assert result is None

    def test_endorse_insufficient_reputation(self, intel_market):
        signal = self._create_signal(intel_market)
        result = asyncio.get_event_loop().run_until_complete(
            intel_market.endorse_signal(
                signal_id=signal.id, endorser_agent_id=3,
                endorser_agent_name="Broke", stake_amount=10.0,
            )
        )
        assert result is None

    def test_link_trade_to_endorsement(self, intel_market, db_session_factory):
        signal = self._create_signal(intel_market)
        endorsement = asyncio.get_event_loop().run_until_complete(
            intel_market.endorse_signal(
                signal_id=signal.id, endorser_agent_id=2,
                endorser_agent_name="Trader-B", stake_amount=10.0,
            )
        )
        result = asyncio.get_event_loop().run_until_complete(
            intel_market.link_trade_to_endorsement(
                endorser_agent_id=2, signal_id=signal.id, trade_id=42,
            )
        )
        assert result is True


class TestQueries:
    def test_get_active_signals(self, intel_market):
        asyncio.get_event_loop().run_until_complete(
            intel_market.create_signal(
                scout_agent_id=1, scout_agent_name="Scout-A",
                message_id=1, asset="BTC/USDT", direction="bullish",
                confidence_level=3, price_at_creation=50000.0, expires_at=_future_dt(),
            )
        )
        signals = asyncio.get_event_loop().run_until_complete(
            intel_market.get_active_signals()
        )
        assert len(signals) >= 1

    def test_get_active_signals_by_asset(self, intel_market):
        asyncio.get_event_loop().run_until_complete(
            intel_market.create_signal(
                scout_agent_id=1, scout_agent_name="Scout-A",
                message_id=1, asset="ETH/USDT", direction="bearish",
                confidence_level=3, price_at_creation=3000.0, expires_at=_future_dt(),
            )
        )
        signals = asyncio.get_event_loop().run_until_complete(
            intel_market.get_active_signals(asset="ETH/USDT")
        )
        assert all(s.asset == "ETH/USDT" for s in signals)

    def test_get_signals_ready_for_settlement(self, intel_market, db_session_factory):
        with db_session_factory() as session:
            signal = IntelSignal(
                message_id=1, scout_agent_id=1, scout_agent_name="Scout-A",
                asset="BTC/USDT", direction="bullish", confidence_level=3,
                price_at_creation=50000.0,
                expires_at=datetime.now(timezone.utc) - timedelta(hours=1),
                status="active",
            )
            session.add(signal)
            session.commit()

        ready = asyncio.get_event_loop().run_until_complete(
            intel_market.get_signals_ready_for_settlement()
        )
        assert len(ready) >= 1

    def test_agent_signal_stats(self, intel_market):
        asyncio.get_event_loop().run_until_complete(
            intel_market.create_signal(
                scout_agent_id=1, scout_agent_name="Scout-A",
                message_id=1, asset="BTC/USDT", direction="bullish",
                confidence_level=3, price_at_creation=50000.0, expires_at=_future_dt(),
            )
        )
        stats = asyncio.get_event_loop().run_until_complete(
            intel_market.get_agent_signal_stats(agent_id=1)
        )
        assert stats["total_signals"] >= 1
