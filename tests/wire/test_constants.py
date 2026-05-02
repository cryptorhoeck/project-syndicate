"""Sanity tests for wire constants — protect the contract."""

from src.wire import constants as C


def test_severity_band_ordering() -> None:
    assert (
        C.SEVERITY_TRIVIAL
        < C.SEVERITY_NOTABLE
        < C.SEVERITY_MATERIAL
        < C.SEVERITY_HIGH_IMPACT
        < C.SEVERITY_CRITICAL
    )


def test_haiku_cannot_assign_critical() -> None:
    assert C.HAIKU_MAX_SEVERITY == C.SEVERITY_HIGH_IMPACT
    assert C.HAIKU_MAX_SEVERITY < C.SEVERITY_CRITICAL


def test_ticker_publish_threshold_matches_kickoff() -> None:
    assert C.TICKER_PUBLISH_MIN_SEVERITY == C.SEVERITY_MATERIAL


def test_event_types_closed_set_includes_required() -> None:
    required = {
        "listing", "delisting", "hack", "exploit", "tvl_change",
        "funding_extreme", "whale_transfer", "exchange_outage",
        "withdrawal_halt", "chain_halt", "macro_calendar", "macro_data",
        "regulatory", "other",
    }
    assert required <= C.EVENT_TYPES_SET


def test_operator_halt_event_types_are_severity_5_eligible() -> None:
    assert C.OPERATOR_HALT_EVENT_TYPES == frozenset(
        {"exchange_outage", "withdrawal_halt", "chain_halt"}
    )


def test_health_states_complete() -> None:
    assert {"healthy", "degraded", "failing", "disabled", "unknown"} == set(C.HEALTH_STATES)


def test_tier1_source_names_are_in_registry_order() -> None:
    assert C.TIER1_SOURCE_NAMES == (
        "kraken_announcements",
        "cryptopanic",
        "defillama",
    )
