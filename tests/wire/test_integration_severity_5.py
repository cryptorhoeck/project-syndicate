"""Operator halt + Genesis regime review hook tests."""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from src.wire.constants import (
    DIGESTION_STATUS_DIGESTED,
    SEVERITY_CRITICAL,
)
from src.wire.digest.haiku_digester import HaikuDigester
from src.wire.integration.genesis_regime import (
    register_severity_5_review_hook,
    reset_hooks,
)
from src.wire.integration.operator_halt import (
    OperatorHaltSignal,
    list_active,
    publish_halt_for_event,
    reset_registry,
)
from src.wire.models import WireRawItem, WireEvent

from tests.wire.conftest import make_fake_haiku_client


@pytest.fixture(autouse=True)
def _clean_module_state():
    reset_hooks()
    reset_registry()
    yield
    reset_hooks()
    reset_registry()


class TestOperatorHaltDirect:
    def test_severity_5_exchange_outage_publishes(self) -> None:
        signal = publish_halt_for_event(
            event_id=1,
            coin="BTC",
            event_type="exchange_outage",
            severity=SEVERITY_CRITICAL,
            summary="Kraken outage",
        )
        assert signal is not None
        assert signal.coin == "BTC"
        active = list_active()
        assert len(active) == 1

    def test_severity_4_does_not_publish(self) -> None:
        signal = publish_halt_for_event(
            event_id=1,
            coin="BTC",
            event_type="exchange_outage",
            severity=4,
            summary="x",
        )
        assert signal is None
        assert list_active() == []

    def test_non_halt_event_type_does_not_publish(self) -> None:
        signal = publish_halt_for_event(
            event_id=1,
            coin="BTC",
            event_type="hack",  # not in OPERATOR_HALT_EVENT_TYPES
            severity=SEVERITY_CRITICAL,
            summary="x",
        )
        assert signal is None
        assert list_active() == []

    def test_signal_expires(self) -> None:
        signal = publish_halt_for_event(
            event_id=1,
            coin="BTC",
            event_type="withdrawal_halt",
            severity=SEVERITY_CRITICAL,
            summary="x",
            auto_expire_minutes=1,
        )
        assert signal is not None
        # 2 minutes later it should not be active.
        future = datetime.now(timezone.utc) + timedelta(minutes=2)
        active = list_active(now=future)
        assert active == []


class TestGenesisHook:
    def test_hook_fires_on_severity_5(self) -> None:
        captured: list = []
        register_severity_5_review_hook(lambda payload: captured.append(payload))
        from src.wire.integration.genesis_regime import maybe_dispatch
        fired = maybe_dispatch(
            event_id=42,
            severity=SEVERITY_CRITICAL,
            coin="BTC",
            event_type="chain_halt",
            summary="ETH chain halt",
            occurred_at_iso="2026-05-01T12:00:00",
        )
        assert fired
        assert len(captured) == 1
        assert captured[0]["event_id"] == 42

    def test_hook_does_not_fire_below_5(self) -> None:
        captured: list = []
        register_severity_5_review_hook(lambda payload: captured.append(payload))
        from src.wire.integration.genesis_regime import maybe_dispatch
        fired = maybe_dispatch(
            event_id=42,
            severity=4,
            coin="BTC",
            event_type="hack",
            summary="x",
            occurred_at_iso=None,
        )
        assert not fired
        assert captured == []

    def test_failing_hook_does_not_break_others(self) -> None:
        captured: list = []
        def boom(p):
            raise RuntimeError("boom")
        register_severity_5_review_hook(boom)
        register_severity_5_review_hook(lambda p: captured.append(p))
        from src.wire.integration.genesis_regime import maybe_dispatch
        fired = maybe_dispatch(
            event_id=1,
            severity=SEVERITY_CRITICAL,
            coin="BTC",
            event_type="chain_halt",
            summary="x",
            occurred_at_iso=None,
        )
        assert fired
        assert len(captured) == 1


class TestDigesterTriggersHooks:
    def test_severity_5_event_via_digester_fires_both_hooks(
        self, wire_seeded_session, fixed_now
    ) -> None:
        # Seed a raw item flagged with deterministic severity 5 and an op-halt event_type.
        from src.wire.models import WireSource
        src = wire_seeded_session.execute(
            select(WireSource).where(WireSource.name == "kraken_announcements")
        ).scalar_one()
        envelope = {
            "payload": {},
            "haiku_brief": "outage",
            "deterministic_severity": 5,
            "deterministic_event_type": "exchange_outage",
            "deterministic_coin": "ETH",
            "deterministic_direction": "bearish",
        }
        raw = WireRawItem(
            source_id=src.id,
            external_id="kr-outage-1",
            raw_payload=envelope,
            occurred_at=fixed_now,
        )
        wire_seeded_session.add(raw)
        wire_seeded_session.commit()

        captured: list = []
        register_severity_5_review_hook(lambda p: captured.append(p))

        haiku = make_fake_haiku_client([
            '{"coin":"ETH","is_macro":false,"event_type":"hack","severity":2,'
            '"direction":"bearish","summary":"low impact note"}'
        ])
        digester = HaikuDigester(haiku_client=haiku, session=wire_seeded_session)
        results = digester.digest_pending()

        assert results[0].status == DIGESTION_STATUS_DIGESTED
        assert results[0].severity == SEVERITY_CRITICAL  # deterministic 5 wins
        assert len(captured) == 1
        assert list_active() != []  # halt registered
