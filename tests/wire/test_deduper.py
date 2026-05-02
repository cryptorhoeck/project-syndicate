"""Dedup tests."""

from datetime import datetime, timedelta, timezone

from src.wire.digest.deduper import canonical_hash, find_duplicate
from src.wire.models import WireEvent


class TestCanonicalHash:
    def test_stable_same_inputs(self) -> None:
        h1 = canonical_hash("BTC", "listing", "BTC listed on Kraken")
        h2 = canonical_hash("BTC", "listing", "BTC listed on Kraken")
        assert h1 == h2
        assert len(h1) == 64

    def test_normalizes_whitespace_and_case(self) -> None:
        a = canonical_hash("BTC", "listing", "BTC  Listed on  Kraken.")
        b = canonical_hash("btc", "LISTING", "btc listed on kraken")
        assert a == b

    def test_differs_on_coin(self) -> None:
        a = canonical_hash("BTC", "listing", "X")
        b = canonical_hash("ETH", "listing", "X")
        assert a != b

    def test_differs_on_event_type(self) -> None:
        a = canonical_hash("BTC", "listing", "X")
        b = canonical_hash("BTC", "delisting", "X")
        assert a != b

    def test_handles_none_coin(self) -> None:
        h = canonical_hash(None, "macro_calendar", "FOMC at 2pm")
        assert isinstance(h, str)
        assert len(h) == 64


class TestFindDuplicate:
    def test_returns_existing_within_window(self, wire_seeded_session, fixed_now) -> None:
        canonical = canonical_hash("BTC", "listing", "BTC listed")
        canon_event = WireEvent(
            canonical_hash=canonical,
            coin="BTC",
            event_type="listing",
            severity=3,
            summary="BTC listed",
            occurred_at=fixed_now - timedelta(hours=1),
        )
        wire_seeded_session.add(canon_event)
        wire_seeded_session.commit()

        found = find_duplicate(wire_seeded_session, canonical, now=fixed_now)
        assert found is not None
        assert found.id == canon_event.id

    def test_returns_none_outside_window(self, wire_seeded_session, fixed_now) -> None:
        canonical = canonical_hash("BTC", "listing", "BTC listed")
        old_event = WireEvent(
            canonical_hash=canonical,
            coin="BTC",
            event_type="listing",
            severity=3,
            summary="BTC listed",
            occurred_at=fixed_now - timedelta(hours=48),
        )
        wire_seeded_session.add(old_event)
        wire_seeded_session.commit()

        found = find_duplicate(wire_seeded_session, canonical, now=fixed_now)
        assert found is None

    def test_skips_events_already_marked_duplicate(self, wire_seeded_session, fixed_now) -> None:
        canonical = canonical_hash("BTC", "listing", "BTC listed")
        canon_event = WireEvent(
            canonical_hash=canonical,
            coin="BTC",
            event_type="listing",
            severity=3,
            summary="BTC listed",
            occurred_at=fixed_now - timedelta(hours=1),
        )
        wire_seeded_session.add(canon_event)
        wire_seeded_session.flush()

        dup = WireEvent(
            canonical_hash=canonical,
            coin="BTC",
            event_type="listing",
            severity=3,
            summary="BTC listed",
            occurred_at=fixed_now - timedelta(minutes=30),
            duplicate_of=canon_event.id,
        )
        wire_seeded_session.add(dup)
        wire_seeded_session.commit()

        # find_duplicate must return the canonical row, not the duplicate.
        found = find_duplicate(wire_seeded_session, canonical, now=fixed_now)
        assert found is not None
        assert found.id == canon_event.id
