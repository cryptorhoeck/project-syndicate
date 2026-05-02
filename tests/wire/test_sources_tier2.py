"""Smoke tests for Tier 2 sources (Etherscan, funding, FRED, TradingEconomics, F&G)."""

from src.wire.constants import (
    SEVERITY_HIGH_IMPACT,
    SEVERITY_MATERIAL,
    SEVERITY_NOTABLE,
)
from src.wire.sources.base import SourceFetchError
from src.wire.sources.etherscan_transfers import EtherscanTransfersSource
from src.wire.sources.fear_greed import FearGreedSource
from src.wire.sources.fred import FredSource
from src.wire.sources.funding_rates import FundingRatesSource
from src.wire.sources.trading_economics import TradingEconomicsSource

from tests.wire.conftest import FakeHttpClient, FakeResponse


# -----------------------------------------------------------------------
# Etherscan
# -----------------------------------------------------------------------

class TestEtherscan:
    def test_missing_api_key_raises(self) -> None:
        source = EtherscanTransfersSource()  # no api_key
        try:
            list(source.fetch_raw())
        except SourceFetchError as exc:
            assert "ETHERSCAN_API_KEY" in str(exc)
            return
        raise AssertionError("expected SourceFetchError on missing key")

    def test_above_threshold_with_exchange_wallet_severity_4(self) -> None:
        # Use a known watched address as `to`.
        from src.wire.sources.etherscan_transfers import DEFAULT_EXCHANGE_WALLETS
        watched = next(iter(DEFAULT_EXCHANGE_WALLETS.keys()))
        fixture = {
            "result": [
                {
                    "hash": "0xabc",
                    "from": "0x1111111111111111111111111111111111111111",
                    "to": watched,
                    "value": str(int(2000 * 1e18)),  # 2000 ETH
                    "timeStamp": "1746086400",
                    "blockNumber": "12345",
                }
            ]
        }
        client = FakeHttpClient(FakeResponse(json_data=fixture))
        source = EtherscanTransfersSource(
            api_key="dummy",
            http_client=client,
            config={"exchange_wallets": {watched: "test_exchange"}, "min_value_eth": 1000},
        )
        items = list(source.fetch_raw())
        assert len(items) == 1
        assert items[0].deterministic_severity == SEVERITY_HIGH_IMPACT
        assert items[0].deterministic_event_type == "whale_transfer"
        assert items[0].deterministic_direction == "bearish"  # inflow to exchange

    def test_below_threshold_filtered_out(self) -> None:
        fixture = {
            "result": [
                {
                    "hash": "0xdef",
                    "from": "0xaaa",
                    "to": "0xbbb",
                    "value": str(int(50 * 1e18)),  # 50 ETH
                    "timeStamp": "1746086400",
                }
            ]
        }
        client = FakeHttpClient(FakeResponse(json_data=fixture))
        source = EtherscanTransfersSource(
            api_key="dummy",
            http_client=client,
            config={"exchange_wallets": {"0xaaa": "test"}, "min_value_eth": 1000},
        )
        items = list(source.fetch_raw())
        assert items == []


# -----------------------------------------------------------------------
# Funding rates
# -----------------------------------------------------------------------


class _FakeCcxt:
    def __init__(self, rates: dict) -> None:
        self._rates = rates

    def fetch_funding_rate(self, symbol: str) -> dict:
        return self._rates[symbol]


class TestFundingRates:
    def test_below_threshold_filtered(self) -> None:
        client = _FakeCcxt({
            "BTC/USD:USD": {"fundingRate": 0.0005, "interval": "8h"},
            "ETH/USD:USD": {"fundingRate": -0.0001, "interval": "8h"},
        })
        source = FundingRatesSource(
            http_client=client,
            config={"extreme_threshold": 0.001, "pairs": ["BTC/USD:USD", "ETH/USD:USD"]},
        )
        items = list(source.fetch_raw())
        assert items == []

    def test_above_threshold_emits_severity_2(self) -> None:
        client = _FakeCcxt({
            "BTC/USD:USD": {"fundingRate": 0.0015, "interval": "8h"},
        })
        source = FundingRatesSource(
            http_client=client,
            config={"extreme_threshold": 0.001, "pairs": ["BTC/USD:USD"]},
        )
        items = list(source.fetch_raw())
        assert len(items) == 1
        assert items[0].deterministic_severity == SEVERITY_NOTABLE
        assert items[0].deterministic_direction == "bearish"  # positive funding

    def test_extreme_funding_severity_3(self) -> None:
        client = _FakeCcxt({
            "BTC/USD:USD": {"fundingRate": -0.005, "interval": "8h"},
        })
        source = FundingRatesSource(
            http_client=client,
            config={"extreme_threshold": 0.001, "pairs": ["BTC/USD:USD"]},
        )
        items = list(source.fetch_raw())
        assert items[0].deterministic_severity == SEVERITY_MATERIAL
        assert items[0].deterministic_direction == "bullish"


# -----------------------------------------------------------------------
# FRED
# -----------------------------------------------------------------------


class TestFred:
    def test_missing_api_key_raises(self) -> None:
        try:
            list(FredSource().fetch_raw())
        except SourceFetchError:
            return
        raise AssertionError("expected SourceFetchError")

    def test_parses_observation(self) -> None:
        fixture = {
            "observations": [
                {"date": "2026-04-15", "value": "4.32"}
            ]
        }
        client = FakeHttpClient(FakeResponse(json_data=fixture))
        source = FredSource(
            api_key="dummy",
            http_client=client,
            config={"series": ["DGS10"]},
        )
        items = list(source.fetch_raw())
        assert len(items) == 1
        assert items[0].deterministic_severity == SEVERITY_NOTABLE
        assert items[0].deterministic_event_type == "macro_data"
        assert items[0].deterministic_is_macro is True
        assert items[0].external_id == "DGS10::2026-04-15"

    def test_dot_value_skipped(self) -> None:
        fixture = {"observations": [{"date": "2026-04-15", "value": "."}]}
        client = FakeHttpClient(FakeResponse(json_data=fixture))
        source = FredSource(
            api_key="dummy",
            http_client=client,
            config={"series": ["DGS10"]},
        )
        items = list(source.fetch_raw())
        assert items == []


# -----------------------------------------------------------------------
# TradingEconomics
# -----------------------------------------------------------------------


class TestTradingEconomics:
    def test_skips_low_importance(self) -> None:
        fixture = [
            {"CalendarId": "1", "Event": "Minor data", "Country": "US",
             "Importance": 1, "Date": "2026-04-15T12:00:00"},
        ]
        client = FakeHttpClient(FakeResponse(json_data=fixture))
        source = TradingEconomicsSource(http_client=client)
        items = list(source.fetch_raw())
        assert items == []

    def test_high_importance_event_severity_2_default(self) -> None:
        # Far-future event -> severity 2.
        fixture = [
            {"CalendarId": "abc", "Event": "FOMC", "Country": "US",
             "Importance": 3, "Date": "2099-01-01T19:00:00"},
        ]
        client = FakeHttpClient(FakeResponse(json_data=fixture))
        source = TradingEconomicsSource(http_client=client)
        items = list(source.fetch_raw())
        assert len(items) == 1
        assert items[0].deterministic_severity == SEVERITY_NOTABLE
        assert items[0].deterministic_event_type == "macro_calendar"


# -----------------------------------------------------------------------
# Fear & Greed
# -----------------------------------------------------------------------


class TestFearGreed:
    def test_extreme_fear_emits_bullish(self) -> None:
        fixture = {
            "data": [
                {"value": "10", "value_classification": "Extreme Fear",
                 "timestamp": "1746086400"}
            ]
        }
        client = FakeHttpClient(FakeResponse(json_data=fixture))
        source = FearGreedSource(http_client=client)
        items = list(source.fetch_raw())
        assert len(items) == 1
        assert items[0].deterministic_direction == "bullish"
        assert items[0].deterministic_is_macro is True

    def test_extreme_greed_emits_bearish(self) -> None:
        fixture = {
            "data": [
                {"value": "85", "value_classification": "Extreme Greed",
                 "timestamp": "1746086400"}
            ]
        }
        client = FakeHttpClient(FakeResponse(json_data=fixture))
        source = FearGreedSource(http_client=client)
        items = list(source.fetch_raw())
        assert items[0].deterministic_direction == "bearish"

    def test_neutral_emits_neutral(self) -> None:
        fixture = {
            "data": [
                {"value": "50", "value_classification": "Neutral",
                 "timestamp": "1746086400"}
            ]
        }
        client = FakeHttpClient(FakeResponse(json_data=fixture))
        source = FearGreedSource(http_client=client)
        items = list(source.fetch_raw())
        assert items[0].deterministic_direction == "neutral"

    def test_empty_data_returns_empty(self) -> None:
        client = FakeHttpClient(FakeResponse(json_data={"data": []}))
        items = list(FearGreedSource(http_client=client).fetch_raw())
        assert items == []
