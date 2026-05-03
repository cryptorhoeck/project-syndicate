"""Haiku digester tests — schema validation, severity capping, dedup, dead-letter."""

from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from src.wire.constants import (
    DIGESTION_STATUS_DEAD_LETTER,
    DIGESTION_STATUS_DIGESTED,
)
from src.wire.digest.haiku_digester import HaikuDigester
from src.wire.models import WireEvent, WireRawItem, WireTreasuryLedger

from tests.wire.conftest import make_fake_haiku_client


def _make_raw(session, *, source_name="cryptopanic", external_id="ext-1",
              haiku_brief="Headline X", payload_extras=None,
              occurred_at=None) -> WireRawItem:
    from src.wire.models import WireSource
    src = session.execute(
        select(WireSource).where(WireSource.name == source_name)
    ).scalar_one()
    envelope = {
        "payload": {"foo": "bar"},
        "haiku_brief": haiku_brief,
        "source_url": "https://example.com/x",
        "deterministic_severity": None,
        "deterministic_event_type": None,
        "deterministic_coin": None,
        "deterministic_direction": None,
        "deterministic_is_macro": None,
    }
    if payload_extras:
        envelope.update(payload_extras)
    raw = WireRawItem(
        source_id=src.id,
        external_id=external_id,
        raw_payload=envelope,
        occurred_at=occurred_at or datetime(2026, 4, 15, 10, 0, tzinfo=timezone.utc),
    )
    session.add(raw)
    session.commit()
    return raw


class TestHappyPath:
    def test_creates_event_and_marks_digested(self, wire_seeded_session) -> None:
        _make_raw(wire_seeded_session, external_id="cp-1")
        haiku = make_fake_haiku_client([
            '{"coin":"BTC","is_macro":false,"event_type":"listing","severity":3,'
            '"direction":"bullish","summary":"BTC listing announced"}'
        ])

        digester = HaikuDigester(haiku_client=haiku, session=wire_seeded_session)
        results = digester.digest_pending()

        assert len(results) == 1
        assert results[0].status == DIGESTION_STATUS_DIGESTED

        events = wire_seeded_session.execute(select(WireEvent)).scalars().all()
        assert len(events) == 1
        assert events[0].event_type == "listing"
        assert events[0].severity == 3
        assert events[0].coin == "BTC"
        assert events[0].direction == "bullish"

    def test_records_treasury_ledger_entry(self, wire_seeded_session) -> None:
        _make_raw(wire_seeded_session, external_id="cp-1")
        haiku = make_fake_haiku_client(
            ['{"coin":"BTC","is_macro":false,"event_type":"other","severity":1,'
             '"direction":"neutral","summary":"trivial"}'],
            cost_per_call=0.0005,
        )
        digester = HaikuDigester(haiku_client=haiku, session=wire_seeded_session)
        digester.digest_pending()

        ledgers = wire_seeded_session.execute(select(WireTreasuryLedger)).scalars().all()
        assert len(ledgers) == 1
        assert float(ledgers[0].cost_usd) == pytest.approx(0.0005)
        assert ledgers[0].cost_category == "haiku_digestion"


class TestSeverityCapping:
    def test_haiku_assigning_5_is_downgraded_to_4(self, wire_seeded_session) -> None:
        _make_raw(wire_seeded_session, external_id="cp-1")
        captured: list[dict] = []
        haiku = make_fake_haiku_client([
            '{"coin":"BTC","is_macro":false,"event_type":"hack","severity":5,'
            '"direction":"bearish","summary":"some hack"}'
        ])

        digester = HaikuDigester(
            haiku_client=haiku,
            session=wire_seeded_session,
            on_severity_capped=lambda payload: captured.append(payload),
        )
        results = digester.digest_pending()
        assert results[0].severity == 4
        assert results[0].severity_capped is True
        assert captured and captured[0]["haiku_attempted"] == 5

    def test_deterministic_5_overrides_haiku(self, wire_seeded_session) -> None:
        _make_raw(
            wire_seeded_session,
            external_id="kr-1",
            source_name="kraken_announcements",
            payload_extras={
                "deterministic_severity": 5,
                "deterministic_event_type": "withdrawal_halt",
            },
        )
        haiku = make_fake_haiku_client([
            '{"coin":"ETH","is_macro":false,"event_type":"hack","severity":2,'
            '"direction":"bearish","summary":"low impact"}'
        ])
        digester = HaikuDigester(haiku_client=haiku, session=wire_seeded_session)
        results = digester.digest_pending()
        assert results[0].severity == 5

        evt = wire_seeded_session.execute(select(WireEvent)).scalar_one()
        # Deterministic event_type wins over Haiku's.
        assert evt.event_type == "withdrawal_halt"


class TestDeadLetter:
    def test_invalid_json_then_invalid_json_dead_letters(self, wire_seeded_session) -> None:
        _make_raw(wire_seeded_session, external_id="cp-1")
        haiku = make_fake_haiku_client(["not json", "still {bad"])

        digester = HaikuDigester(haiku_client=haiku, session=wire_seeded_session)
        results = digester.digest_pending()
        assert results[0].status == DIGESTION_STATUS_DEAD_LETTER
        assert results[0].event_id is None

        raws = wire_seeded_session.execute(select(WireRawItem)).scalars().all()
        assert raws[0].digestion_status == DIGESTION_STATUS_DEAD_LETTER

        events = wire_seeded_session.execute(select(WireEvent)).scalars().all()
        assert events == []

    def test_invalid_then_valid_succeeds(self, wire_seeded_session) -> None:
        _make_raw(wire_seeded_session, external_id="cp-1")
        haiku = make_fake_haiku_client([
            "garbage",
            '{"coin":"BTC","is_macro":false,"event_type":"other","severity":1,'
            '"direction":"neutral","summary":"ok"}',
        ])
        digester = HaikuDigester(haiku_client=haiku, session=wire_seeded_session)
        results = digester.digest_pending()
        assert results[0].status == DIGESTION_STATUS_DIGESTED

    def test_unknown_event_type_dead_letters(self, wire_seeded_session) -> None:
        _make_raw(wire_seeded_session, external_id="cp-1")
        haiku = make_fake_haiku_client([
            '{"coin":"BTC","is_macro":false,"event_type":"alien_invasion","severity":3,'
            '"direction":"bearish","summary":"x"}',
            '{"coin":"BTC","is_macro":false,"event_type":"alien_invasion","severity":3,'
            '"direction":"bearish","summary":"x"}',
        ])
        digester = HaikuDigester(haiku_client=haiku, session=wire_seeded_session)
        results = digester.digest_pending()
        assert results[0].status == DIGESTION_STATUS_DEAD_LETTER

    def test_empty_summary_dead_letters(self, wire_seeded_session) -> None:
        _make_raw(wire_seeded_session, external_id="cp-1")
        haiku = make_fake_haiku_client([
            '{"coin":"BTC","is_macro":false,"event_type":"other","severity":1,'
            '"direction":"neutral","summary":""}',
            '{"coin":"BTC","is_macro":false,"event_type":"other","severity":1,'
            '"direction":"neutral","summary":""}',
        ])
        digester = HaikuDigester(haiku_client=haiku, session=wire_seeded_session)
        results = digester.digest_pending()
        assert results[0].status == DIGESTION_STATUS_DEAD_LETTER


class TestDedup:
    def test_second_event_same_canonical_hash_marked_duplicate(self, wire_seeded_session) -> None:
        _make_raw(wire_seeded_session, external_id="cp-1")
        _make_raw(wire_seeded_session, external_id="cp-2")
        same_payload = (
            '{"coin":"BTC","is_macro":false,"event_type":"listing","severity":3,'
            '"direction":"bullish","summary":"BTC listed on Kraken"}'
        )
        haiku = make_fake_haiku_client([same_payload, same_payload])
        digester = HaikuDigester(haiku_client=haiku, session=wire_seeded_session)
        results = digester.digest_pending()

        assert results[0].duplicate_of is None
        assert results[1].duplicate_of == results[0].event_id


class TestCodeFenceTolerance:
    def test_strips_markdown_code_fences(self, wire_seeded_session) -> None:
        _make_raw(wire_seeded_session, external_id="cp-1")
        haiku = make_fake_haiku_client([
            '```json\n{"coin":"BTC","is_macro":false,"event_type":"other","severity":1,'
            '"direction":"neutral","summary":"x"}\n```'
        ])
        digester = HaikuDigester(haiku_client=haiku, session=wire_seeded_session)
        results = digester.digest_pending()
        assert results[0].status == DIGESTION_STATUS_DIGESTED


class TestSummaryCap:
    def test_long_summary_truncated_to_200(self, wire_seeded_session) -> None:
        _make_raw(wire_seeded_session, external_id="cp-1")
        long = "x" * 500
        haiku = make_fake_haiku_client([
            f'{{"coin":"BTC","is_macro":false,"event_type":"other","severity":1,'
            f'"direction":"neutral","summary":"{long}"}}'
        ])
        digester = HaikuDigester(haiku_client=haiku, session=wire_seeded_session)
        digester.digest_pending()
        evt = wire_seeded_session.execute(select(WireEvent)).scalar_one()
        assert len(evt.summary) <= 200
