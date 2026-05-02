"""DefiLlama source tests."""

from src.wire.constants import SEVERITY_MATERIAL, SEVERITY_NOTABLE
from src.wire.sources.defillama import DefiLlamaSource

from tests.wire.conftest import FakeHttpClient, FakeResponse


def _fixture(*protos):
    return list(protos)


def _proto(**kwargs):
    base = {
        "slug": "uniswap",
        "name": "Uniswap",
        "symbol": "UNI",
        "chain": "Ethereum",
        "tvl": 1_000_000_000,
        "change_1d": 0.0,
    }
    base.update(kwargs)
    return base


class TestDefiLlamaThreshold:
    def test_below_threshold_filtered_out(self) -> None:
        # 4% change with default 5% threshold -> filtered
        proto = _proto(change_1d=4.0)
        client = FakeHttpClient(FakeResponse(json_data=_fixture(proto)))
        source = DefiLlamaSource(http_client=client)
        items = list(source.fetch_raw())
        assert items == []

    def test_at_5pct_emits_severity_notable(self) -> None:
        proto = _proto(change_1d=5.5)
        client = FakeHttpClient(FakeResponse(json_data=_fixture(proto)))
        source = DefiLlamaSource(http_client=client)
        items = list(source.fetch_raw())
        assert len(items) == 1
        assert items[0].deterministic_severity == SEVERITY_NOTABLE
        assert items[0].deterministic_direction == "bullish"

    def test_above_10pct_emits_severity_material(self) -> None:
        proto = _proto(change_1d=-15.0)
        client = FakeHttpClient(FakeResponse(json_data=_fixture(proto)))
        source = DefiLlamaSource(http_client=client)
        items = list(source.fetch_raw())
        assert len(items) == 1
        assert items[0].deterministic_severity == SEVERITY_MATERIAL
        assert items[0].deterministic_direction == "bearish"

    def test_event_type_is_tvl_change(self) -> None:
        proto = _proto(change_1d=12.0)
        client = FakeHttpClient(FakeResponse(json_data=_fixture(proto)))
        source = DefiLlamaSource(http_client=client)
        items = list(source.fetch_raw())
        assert items[0].deterministic_event_type == "tvl_change"

    def test_external_id_includes_day_bucket(self) -> None:
        proto = _proto(change_1d=15.0)
        client = FakeHttpClient(FakeResponse(json_data=_fixture(proto)))
        source = DefiLlamaSource(http_client=client)
        items = list(source.fetch_raw())
        assert items[0].external_id.startswith("uniswap::")

    def test_threshold_configurable(self) -> None:
        proto = _proto(change_1d=7.0)
        client = FakeHttpClient(FakeResponse(json_data=_fixture(proto)))
        source = DefiLlamaSource(
            http_client=client,
            config={"tvl_delta_threshold": 0.10},  # 10% threshold
        )
        items = list(source.fetch_raw())
        assert items == []  # 7% < 10%
