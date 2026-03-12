"""Tests for SettlementEngine — signal settlement logic."""

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.common.models import (
    Agent, Base, IntelEndorsement, IntelSignal, Message, Transaction,
)
from src.economy.settlement_engine import SettlementEngine


@pytest.fixture
def db_session_factory():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as session:
        session.add(Agent(id=1, name="Scout-A", type="scout", status="active", reputation_score=100.0))
        session.add(Agent(id=2, name="Trader-B", type="operator", status="active", reputation_score=100.0))
        session.add(Agent(id=3, name="Trader-C", type="operator", status="active", reputation_score=100.0))
        session.add(Message(id=1, agent_id=1, channel="trade-signals", content="test", message_type="signal"))
        session.commit()
    return factory


def _create_expired_signal(factory, scout_id=1, asset="BTC/USDT", direction="bullish",
                           price=50000.0, endorsement_count=0):
    with factory() as session:
        signal = IntelSignal(
            message_id=1, scout_agent_id=scout_id, scout_agent_name=f"Scout-{scout_id}",
            asset=asset, direction=direction, confidence_level=3,
            price_at_creation=price,
            expires_at=datetime.now(timezone.utc) - timedelta(hours=1),
            status="active",
            endorsement_count=endorsement_count,
            total_endorsement_stake=0.0,
        )
        session.add(signal)
        session.commit()
        return signal.id


def _create_endorsement(factory, signal_id, endorser_id=2, stake=10.0, linked_trade_id=None):
    with factory() as session:
        end = IntelEndorsement(
            signal_id=signal_id, endorser_agent_id=endorser_id,
            endorser_agent_name=f"Trader-{endorser_id}", stake_amount=stake,
            settlement_status="pending", linked_trade_id=linked_trade_id,
        )
        session.add(end)
        session.commit()
        return end.id


def _create_trade(factory, agent_id=2, pnl=100.0):
    with factory() as session:
        trade = Transaction(
            agent_id=agent_id, type="spot", symbol="BTC/USDT",
            side="buy", amount=0.1, price=50000.0, pnl=pnl,
        )
        session.add(trade)
        session.commit()
        return trade.id


@pytest.fixture
def economy_mock():
    economy = MagicMock()
    economy.apply_reward = AsyncMock()
    economy.apply_penalty = AsyncMock()
    economy.release_escrow = AsyncMock()
    return economy


@pytest.fixture
def exchange_mock():
    exchange = MagicMock()
    exchange.get_ticker = AsyncMock(return_value={"last": 51000.0})
    return exchange


class TestSettleNoEndorsements:
    def test_settle_signal_no_endorsements(self, db_session_factory, economy_mock):
        sid = _create_expired_signal(db_session_factory, endorsement_count=0)
        engine = SettlementEngine(db_session_factory, economy_mock, exchange_service=None)
        result = asyncio.get_event_loop().run_until_complete(engine.run_settlement_cycle())
        assert result["expired"] == 1
        with db_session_factory() as session:
            sig = session.get(IntelSignal, sid)
            assert sig.status == "expired_no_endorsements"


class TestDirectionalSettlement:
    def test_bullish_correct(self, db_session_factory, economy_mock, exchange_mock):
        # Price went up 2% — bullish was correct
        exchange_mock.get_ticker = AsyncMock(return_value={"last": 51000.0})
        sid = _create_expired_signal(db_session_factory, direction="bullish", price=50000.0, endorsement_count=1)
        _create_endorsement(db_session_factory, sid)
        engine = SettlementEngine(db_session_factory, economy_mock, exchange_mock)
        result = asyncio.get_event_loop().run_until_complete(engine.run_settlement_cycle())
        assert result["profitable"] == 1
        with db_session_factory() as session:
            sig = session.get(IntelSignal, sid)
            assert sig.status == "settled_profitable"

    def test_bullish_incorrect(self, db_session_factory, economy_mock, exchange_mock):
        # Price went down — bullish was wrong
        exchange_mock.get_ticker = AsyncMock(return_value={"last": 49000.0})
        sid = _create_expired_signal(db_session_factory, direction="bullish", price=50000.0, endorsement_count=1)
        _create_endorsement(db_session_factory, sid)
        engine = SettlementEngine(db_session_factory, economy_mock, exchange_mock)
        result = asyncio.get_event_loop().run_until_complete(engine.run_settlement_cycle())
        assert result["unprofitable"] == 1

    def test_bearish_correct(self, db_session_factory, economy_mock, exchange_mock):
        exchange_mock.get_ticker = AsyncMock(return_value={"last": 49000.0})
        sid = _create_expired_signal(db_session_factory, direction="bearish", price=50000.0, endorsement_count=1)
        _create_endorsement(db_session_factory, sid)
        engine = SettlementEngine(db_session_factory, economy_mock, exchange_mock)
        result = asyncio.get_event_loop().run_until_complete(engine.run_settlement_cycle())
        assert result["profitable"] == 1

    def test_neutral_correct(self, db_session_factory, economy_mock, exchange_mock):
        exchange_mock.get_ticker = AsyncMock(return_value={"last": 50100.0})  # 0.2% move < 0.5%
        sid = _create_expired_signal(db_session_factory, direction="neutral", price=50000.0, endorsement_count=1)
        _create_endorsement(db_session_factory, sid)
        engine = SettlementEngine(db_session_factory, economy_mock, exchange_mock)
        result = asyncio.get_event_loop().run_until_complete(engine.run_settlement_cycle())
        assert result["profitable"] == 1

    def test_direction_threshold(self, db_session_factory, economy_mock, exchange_mock):
        # Price moved only 0.2% — below 0.5% threshold, bullish should fail
        exchange_mock.get_ticker = AsyncMock(return_value={"last": 50100.0})
        sid = _create_expired_signal(db_session_factory, direction="bullish", price=50000.0, endorsement_count=1)
        _create_endorsement(db_session_factory, sid)
        engine = SettlementEngine(db_session_factory, economy_mock, exchange_mock)
        result = asyncio.get_event_loop().run_until_complete(engine.run_settlement_cycle())
        assert result["unprofitable"] == 1


class TestTradeLinkedSettlement:
    def test_trade_linked_profitable(self, db_session_factory, economy_mock, exchange_mock):
        exchange_mock.get_ticker = AsyncMock(return_value={"last": 51000.0})
        sid = _create_expired_signal(db_session_factory, direction="bullish", price=50000.0, endorsement_count=1)
        trade_id = _create_trade(db_session_factory, agent_id=2, pnl=100.0)
        _create_endorsement(db_session_factory, sid, endorser_id=2, stake=10.0, linked_trade_id=trade_id)
        engine = SettlementEngine(db_session_factory, economy_mock, exchange_mock)
        asyncio.get_event_loop().run_until_complete(engine.run_settlement_cycle())
        # Scout should be rewarded
        economy_mock.apply_reward.assert_any_call(1, 10.0, "intel_signal_win")
        # Endorser gets stake back + bonus
        economy_mock.release_escrow.assert_any_call(2, 10.0, "endorsement_win")

    def test_trade_linked_unprofitable(self, db_session_factory, economy_mock, exchange_mock):
        exchange_mock.get_ticker = AsyncMock(return_value={"last": 51000.0})
        sid = _create_expired_signal(db_session_factory, direction="bullish", price=50000.0, endorsement_count=1)
        trade_id = _create_trade(db_session_factory, agent_id=2, pnl=-50.0)
        _create_endorsement(db_session_factory, sid, endorser_id=2, stake=10.0, linked_trade_id=trade_id)
        engine = SettlementEngine(db_session_factory, economy_mock, exchange_mock)
        asyncio.get_event_loop().run_until_complete(engine.run_settlement_cycle())
        # Scout penalized
        economy_mock.apply_penalty.assert_any_call(1, 10.0, "intel_signal_loss")
        # Endorser loses stake — no release_escrow for win
        # But release_escrow might still be called for other reasons; just check penalty was applied


class TestTimeBasedSettlement:
    def test_time_based_correct(self, db_session_factory, economy_mock, exchange_mock):
        exchange_mock.get_ticker = AsyncMock(return_value={"last": 51000.0})
        sid = _create_expired_signal(db_session_factory, direction="bullish", price=50000.0, endorsement_count=1)
        _create_endorsement(db_session_factory, sid, endorser_id=2, stake=10.0, linked_trade_id=None)
        engine = SettlementEngine(db_session_factory, economy_mock, exchange_mock)
        asyncio.get_event_loop().run_until_complete(engine.run_settlement_cycle())
        # Scout gets half reward
        economy_mock.apply_reward.assert_any_call(1, 5.0, "intel_signal_time_win")
        # Endorser refunded
        economy_mock.release_escrow.assert_any_call(2, 10.0, "endorsement_time_refund")

    def test_time_based_incorrect(self, db_session_factory, economy_mock, exchange_mock):
        exchange_mock.get_ticker = AsyncMock(return_value={"last": 49000.0})
        sid = _create_expired_signal(db_session_factory, direction="bullish", price=50000.0, endorsement_count=1)
        _create_endorsement(db_session_factory, sid, endorser_id=2, stake=10.0, linked_trade_id=None)
        engine = SettlementEngine(db_session_factory, economy_mock, exchange_mock)
        asyncio.get_event_loop().run_until_complete(engine.run_settlement_cycle())
        # Scout penalized half
        economy_mock.apply_penalty.assert_any_call(1, 5.0, "intel_signal_time_loss")
        # Endorser still refunded (didn't trade)
        economy_mock.release_escrow.assert_any_call(2, 10.0, "endorsement_time_refund")


class TestMixedSettlement:
    def test_mixed_settlement(self, db_session_factory, economy_mock, exchange_mock):
        exchange_mock.get_ticker = AsyncMock(return_value={"last": 51000.0})
        sid = _create_expired_signal(db_session_factory, direction="bullish", price=50000.0, endorsement_count=2)
        trade_id = _create_trade(db_session_factory, agent_id=2, pnl=100.0)
        _create_endorsement(db_session_factory, sid, endorser_id=2, stake=10.0, linked_trade_id=trade_id)
        _create_endorsement(db_session_factory, sid, endorser_id=3, stake=15.0, linked_trade_id=None)
        engine = SettlementEngine(db_session_factory, economy_mock, exchange_mock)
        result = asyncio.get_event_loop().run_until_complete(engine.run_settlement_cycle())
        assert result["settled"] == 1
        assert result["profitable"] == 1


class TestErrorHandling:
    def test_settlement_no_exchange(self, db_session_factory, economy_mock):
        sid = _create_expired_signal(db_session_factory, direction="bullish", price=50000.0, endorsement_count=1)
        _create_endorsement(db_session_factory, sid)
        engine = SettlementEngine(db_session_factory, economy_mock, exchange_service=None)
        result = asyncio.get_event_loop().run_until_complete(engine.run_settlement_cycle())
        # Signal should not be settled — deferred
        with db_session_factory() as session:
            sig = session.get(IntelSignal, sid)
            assert sig.status == "active"  # Still active, expiry extended

    def test_settlement_exchange_error(self, db_session_factory, economy_mock):
        exchange = MagicMock()
        exchange.get_ticker = AsyncMock(side_effect=Exception("Connection failed"))
        sid = _create_expired_signal(db_session_factory, direction="bullish", price=50000.0, endorsement_count=1)
        _create_endorsement(db_session_factory, sid)
        engine = SettlementEngine(db_session_factory, economy_mock, exchange)
        result = asyncio.get_event_loop().run_until_complete(engine.run_settlement_cycle())
        with db_session_factory() as session:
            sig = session.get(IntelSignal, sid)
            assert sig.status == "active"


class TestFullCycle:
    def test_run_settlement_cycle(self, db_session_factory, economy_mock, exchange_mock):
        exchange_mock.get_ticker = AsyncMock(return_value={"last": 51000.0})
        # Create multiple signals
        s1 = _create_expired_signal(db_session_factory, direction="bullish", price=50000.0, endorsement_count=1)
        _create_endorsement(db_session_factory, s1)
        s2 = _create_expired_signal(db_session_factory, direction="bearish", price=50000.0, endorsement_count=0)

        engine = SettlementEngine(db_session_factory, economy_mock, exchange_mock)
        result = asyncio.get_event_loop().run_until_complete(engine.run_settlement_cycle())
        assert result["settled"] >= 1
        assert result["expired"] >= 1
