"""Tests for the first-party tool rail (src/signals/registry.py) + JJ's tool.

Includes the BOUNDARY-GUARD INVARIANTS: turning "a tool can never reach execution"
from a property that's merely true today into one that's enforced by tests. If a
future change wires a trading/exchange/Warden handle into the signal layer or hands
a tool anything but read-only data, these fail.
"""

import dataclasses
import json
import re
from pathlib import Path

import pytest

from src.signals.registry import (
    MarketView,
    available_tools,
    load_builtin_tools,
    register,
    run_first_party_tool,
)

SIGNALS_DIR = Path(__file__).resolve().parents[1] / "src" / "signals"

# Execution layers a signal module must never import.
EXECUTION_MODULES = ("src.trading", "src.common.exchange_service", "src.risk.warden")


def _execution_imports(text: str) -> list[str]:
    """Return execution modules that `text` imports via a real import statement.

    Matches `import X`, `import X.y`, `import X as z`, and `from X[.y] import ...`
    — but NOT prose mentions or string literals (so the guard can't false-positive
    on a docstring, and — the point — can actually FAIL when a real import exists).
    """
    hits = []
    for line in text.splitlines():
        stripped = line.strip()
        for mod in EXECUTION_MODULES:
            if re.match(rf"^(from|import)\s+{re.escape(mod)}(\.|\s|$)", stripped):
                hits.append(mod)
    return hits


@pytest.fixture(autouse=True)
def _tools_loaded():
    load_builtin_tools()


def _make_view(n: int = 40, symbol: str = "BTC/USDT", regime: str = "neutral") -> MarketView:
    candles = [
        {"high": 100.0 + i * 0.1 + 0.5, "low": 100.0 + i * 0.1 - 0.5,
         "close": 100.0 + i * 0.1, "volume": 1000.0 + i}
        for i in range(n)
    ]
    return MarketView.from_ohlcv(candles, symbol=symbol, regime=regime)


# ---------------------------------------------------------------- the rail itself

def test_jj_tool_is_registered():
    assert "jj_signals" in available_tools()


def test_run_returns_json_serializable_data():
    result = run_first_party_tool("jj_signals", _make_view())
    json.dumps(result)  # must not raise
    assert result["tool"] == "jj_signals"
    assert result["market"] == "BTC/USDT"
    assert {s["source"] for s in result["signals"]} == {
        "vwap_deviation", "rsi", "momentum", "volume_breakout"
    }


def test_unknown_tool_raises():
    with pytest.raises(KeyError):
        run_first_party_tool("does_not_exist", _make_view())


def test_duplicate_registration_raises():
    with pytest.raises(ValueError):
        @register("jj_signals")
        def _dupe(view):  # pragma: no cover
            return {}


# ---------------------------------------------------- BOUNDARY INVARIANT: read-only

def test_market_view_is_frozen_and_data_only():
    view = _make_view()
    # frozen: cannot be mutated
    with pytest.raises(dataclasses.FrozenInstanceError):
        view.symbol = "ETH/USDT"  # type: ignore[misc]
    # price series are immutable tuples, not mutable arrays
    for series in (view.high, view.low, view.close, view.volume):
        assert isinstance(series, tuple)
    # every field is plain data — nothing callable is reachable on the view
    for f in dataclasses.fields(view):
        assert not callable(getattr(view, f.name))


def test_tool_result_contains_no_callables():
    result = run_first_party_tool("jj_signals", _make_view())

    def _assert_inert(obj):
        assert not callable(obj)
        if isinstance(obj, dict):
            for v in obj.values():
                _assert_inert(v)
        elif isinstance(obj, (list, tuple)):
            for v in obj:
                _assert_inert(v)

    _assert_inert(result)


# ------------------------------------------ BOUNDARY INVARIANT: no execution route

def test_import_guard_actually_bites():
    """Positive control: prove the guard CAN fail (it isn't vacuous).

    A safety test that can't fail is worse than no test. This asserts the matcher
    flags real execution imports and ignores prose / string mentions.
    """
    # real imports in every form -> flagged
    assert _execution_imports("from src.trading import x") == ["src.trading"]
    assert _execution_imports("import src.risk.warden") == ["src.risk.warden"]
    assert _execution_imports("import src.common.exchange_service as e") == ["src.common.exchange_service"]
    assert _execution_imports("from src.trading.execution_service import S") == ["src.trading"]
    # prose, strings, and look-alikes -> NOT flagged
    assert _execution_imports("# talks to the trading service in prose") == []
    assert _execution_imports("x = 'src.trading'  # a string literal") == []
    assert _execution_imports("import src.trading_helpers") == []  # different module
    assert _execution_imports("import numpy as np") == []


def test_signals_package_does_not_import_execution_layers():
    """No module under src/signals/ may import the trading / exchange / Warden layers.

    The structural guarantee that a consulted tool cannot place a trade.
    """
    scanned = {p.name for p in SIGNALS_DIR.rglob("*.py")}
    # NON-VACUITY: prove we actually scanned the real signal modules (a wrong path
    # would otherwise pass this test against zero files).
    assert {"registry.py", "vwap.py", "indicators.py", "tool.py", "signal_types.py"} <= scanned, (
        f"guard scanned the wrong location; found only: {sorted(scanned)}"
    )
    offenders = []
    for path in SIGNALS_DIR.rglob("*.py"):
        for mod in _execution_imports(path.read_text(encoding="utf-8")):
            offenders.append(f"{path.name} -> {mod}")
    assert not offenders, f"signal layer must not reach execution: {offenders}"


def test_tool_runs_without_any_trading_or_warden_context():
    # The tool takes only a MarketView; there is no trading/warden parameter it
    # *could* be handed. Running it end-to-end touches nothing but data.
    result = run_first_party_tool("jj_signals", _make_view(n=60, regime="bull"))
    assert result["regime"] == "bull"
    assert all(s["direction"] in ("long", "short", "flat") for s in result["signals"])
