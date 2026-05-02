"""Kraken announcements source tests against fixture RSS."""

from src.wire.constants import (
    SEVERITY_CRITICAL,
    SEVERITY_MATERIAL,
)
from src.wire.sources.base import SourceFetchError
from src.wire.sources.kraken_announcements import KrakenAnnouncementsSource

from tests.wire.conftest import FakeHttpClient, FakeResponse


_RSS_FIXTURE = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
<channel>
  <title>Kraken Blog</title>
  <item>
    <title>New Listing of Solana (SOL)</title>
    <link>https://blog.kraken.com/post/sol-listing</link>
    <guid>https://blog.kraken.com/post/sol-listing</guid>
    <pubDate>Mon, 15 Apr 2026 10:00:00 +0000</pubDate>
    <description>Kraken is pleased to announce the new listing of SOL.</description>
  </item>
  <item>
    <title>Withdrawal halt for ETH due to network upgrade</title>
    <link>https://blog.kraken.com/post/eth-halt</link>
    <guid>https://blog.kraken.com/post/eth-halt</guid>
    <pubDate>Mon, 15 Apr 2026 11:00:00 +0000</pubDate>
    <description>ETH withdrawals temporarily suspended.</description>
  </item>
  <item>
    <title>Routine maintenance window scheduled</title>
    <link>https://blog.kraken.com/post/maint</link>
    <guid>https://blog.kraken.com/post/maint</guid>
    <pubDate>Mon, 15 Apr 2026 12:00:00 +0000</pubDate>
    <description>Brief maintenance window.</description>
  </item>
</channel>
</rss>
"""


class TestKrakenParse:
    def test_returns_three_items(self) -> None:
        client = FakeHttpClient(FakeResponse(text=_RSS_FIXTURE))
        source = KrakenAnnouncementsSource(http_client=client)
        items = list(source.fetch_raw())
        assert len(items) == 3

    def test_listing_gets_severity_3_and_bullish(self) -> None:
        client = FakeHttpClient(FakeResponse(text=_RSS_FIXTURE))
        source = KrakenAnnouncementsSource(http_client=client)
        items = list(source.fetch_raw())
        listing = next(i for i in items if "Solana" in i.raw_payload["title"])
        assert listing.deterministic_severity == SEVERITY_MATERIAL
        assert listing.deterministic_event_type == "listing"
        assert listing.deterministic_direction == "bullish"

    def test_withdrawal_halt_gets_severity_5(self) -> None:
        client = FakeHttpClient(FakeResponse(text=_RSS_FIXTURE))
        source = KrakenAnnouncementsSource(http_client=client)
        items = list(source.fetch_raw())
        halt = next(i for i in items if "Withdrawal halt" in i.raw_payload["title"])
        assert halt.deterministic_severity == SEVERITY_CRITICAL
        assert halt.deterministic_event_type == "withdrawal_halt"

    def test_unmatched_title_floors_at_severity_3(self) -> None:
        client = FakeHttpClient(FakeResponse(text=_RSS_FIXTURE))
        source = KrakenAnnouncementsSource(http_client=client)
        items = list(source.fetch_raw())
        maint = next(i for i in items if "maintenance" in i.raw_payload["title"])
        assert maint.deterministic_severity == SEVERITY_MATERIAL

    def test_coin_extracted_from_parens(self) -> None:
        client = FakeHttpClient(FakeResponse(text=_RSS_FIXTURE))
        source = KrakenAnnouncementsSource(http_client=client)
        items = list(source.fetch_raw())
        listing = next(i for i in items if "Solana" in i.raw_payload["title"])
        assert listing.deterministic_coin == "SOL"

    def test_external_id_uses_guid(self) -> None:
        client = FakeHttpClient(FakeResponse(text=_RSS_FIXTURE))
        source = KrakenAnnouncementsSource(http_client=client)
        items = list(source.fetch_raw())
        for i in items:
            assert i.external_id.startswith("https://blog.kraken.com/post/")

    def test_network_failure_raises_source_fetch_error(self) -> None:
        client = FakeHttpClient(RuntimeError("boom"))
        source = KrakenAnnouncementsSource(http_client=client)
        try:
            list(source.fetch_raw())
        except SourceFetchError as exc:
            assert "kraken_announcements" in str(exc)
        else:
            raise AssertionError("expected SourceFetchError")

    def test_malformed_rss_raises_source_fetch_error(self) -> None:
        client = FakeHttpClient(FakeResponse(text="<not really>xml<"))
        source = KrakenAnnouncementsSource(http_client=client)
        try:
            list(source.fetch_raw())
        except SourceFetchError:
            return
        raise AssertionError("expected SourceFetchError on malformed RSS")
