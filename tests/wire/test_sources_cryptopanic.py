"""CryptoPanic source tests."""

from src.wire.sources.base import SourceFetchError
from src.wire.sources.cryptopanic import CryptoPanicSource

from tests.wire.conftest import FakeHttpClient, FakeResponse


_FIXTURE = {
    "results": [
        {
            "id": 1234567,
            "title": "Bitcoin ETF approval rumor circulates",
            "url": "https://example.com/btc-etf",
            "published_at": "2026-04-15T10:00:00Z",
            "currencies": [{"code": "BTC", "title": "Bitcoin"}],
            "domain": "example.com",
            "kind": "news",
        },
        {
            "id": 1234568,
            "title": "ETH gas spike during airdrop",
            "url": "https://example.com/eth-gas",
            "published_at": "2026-04-15T10:30:00Z",
            "currencies": [{"code": "ETH", "title": "Ethereum"}],
            "domain": "example.com",
            "kind": "news",
        },
    ]
}


class TestCryptoPanicParse:
    def test_returns_two_items(self) -> None:
        client = FakeHttpClient(FakeResponse(json_data=_FIXTURE))
        source = CryptoPanicSource(http_client=client)
        items = list(source.fetch_raw())
        assert len(items) == 2

    def test_external_id_is_string_id(self) -> None:
        client = FakeHttpClient(FakeResponse(json_data=_FIXTURE))
        source = CryptoPanicSource(http_client=client)
        items = list(source.fetch_raw())
        assert items[0].external_id == "1234567"

    def test_first_currency_extracted_as_coin(self) -> None:
        client = FakeHttpClient(FakeResponse(json_data=_FIXTURE))
        source = CryptoPanicSource(http_client=client)
        items = list(source.fetch_raw())
        assert items[0].deterministic_coin == "BTC"
        assert items[1].deterministic_coin == "ETH"

    def test_no_severity_set_haiku_decides(self) -> None:
        client = FakeHttpClient(FakeResponse(json_data=_FIXTURE))
        source = CryptoPanicSource(http_client=client)
        items = list(source.fetch_raw())
        assert items[0].deterministic_severity is None
        assert items[0].deterministic_event_type is None

    def test_empty_results_returns_empty_list(self) -> None:
        client = FakeHttpClient(FakeResponse(json_data={"results": []}))
        source = CryptoPanicSource(http_client=client)
        items = list(source.fetch_raw())
        assert items == []

    def test_missing_results_key_returns_empty(self) -> None:
        client = FakeHttpClient(FakeResponse(json_data={"count": 0}))
        source = CryptoPanicSource(http_client=client)
        items = list(source.fetch_raw())
        assert items == []

    def test_non_dict_body_raises(self) -> None:
        client = FakeHttpClient(FakeResponse(json_data=["a", "b"]))
        source = CryptoPanicSource(http_client=client)
        try:
            list(source.fetch_raw())
        except SourceFetchError:
            return
        raise AssertionError("expected SourceFetchError")

    def test_results_not_list_raises(self) -> None:
        client = FakeHttpClient(FakeResponse(json_data={"results": "oops"}))
        source = CryptoPanicSource(http_client=client)
        try:
            list(source.fetch_raw())
        except SourceFetchError:
            return
        raise AssertionError("expected SourceFetchError")

    def test_post_without_id_skipped(self) -> None:
        bad_fixture = {"results": [{"title": "no id here"}]}
        client = FakeHttpClient(FakeResponse(json_data=bad_fixture))
        source = CryptoPanicSource(http_client=client)
        items = list(source.fetch_raw())
        assert items == []
