"""Handler boundary test for `consult_tool` — the load-bearing wall.

`_handle_consult_tool` lives in action_executor.py, the SAME module as
`_handle_execute_trade` (which holds self.trading / self.warden). So the static
import-guard on src/signals/ does NOT cover it. This is the BEHAVIOURAL guard:
mock trading + warden and assert the handler never calls them, runs with both
None, only reads/writes data, and charges the uniform consult cost.
"""

import pytest
from unittest.mock import MagicMock

from src.agents.action_executor import ActionExecutor
from src.agents.roles import get_action_names
from src.common.config import config as cfg
from src.common.models import Agent, ToolConsultResult
from src.sandbox.data_api import SandboxDataAPI


def _candles(n: int = 60):
    return [
        {"timestamp": i, "open": 100 + i * 0.1, "high": 100 + i * 0.1 + 0.5,
         "low": 100 + i * 0.1 - 0.5, "close": 100 + i * 0.1, "volume": 1000.0 + i}
        for i in range(n)
    ]


@pytest.fixture
def patched_data_api(monkeypatch):
    """Feed the handler synthetic OHLCV without Redis (read-only surface stand-in)."""
    async def _noop_prefetch(self, *a, **k):
        return None
    monkeypatch.setattr(SandboxDataAPI, "prefetch_all", _noop_prefetch)
    monkeypatch.setattr(SandboxDataAPI, "_get_price_history", lambda self, *a, **k: _candles())
    monkeypatch.setattr(SandboxDataAPI, "_get_market_regime", lambda self: {"regime": "neutral"})


def _make_agent(session, name="A", role="scout"):
    agent = Agent(
        name=name, type=role, status="active",
        capital_allocated=100.0, capital_current=100.0,
        thinking_budget_daily=0.5, thinking_budget_used_today=0.0,
        evaluation_count=5, profitable_evaluations=3,
    )
    session.add(agent)
    session.commit()
    return agent


def test_consult_tool_is_wired_and_in_every_role_action_space():
    ex = ActionExecutor(db_session=MagicMock())
    assert ex._get_handler("consult_tool") == ex._handle_consult_tool
    for role in ("scout", "strategist", "critic", "operator"):
        assert "consult_tool" in get_action_names(role)


@pytest.mark.asyncio
async def test_handler_never_touches_trading_or_warden(db_session_factory, patched_data_api):
    session = db_session_factory()
    agent = _make_agent(session)
    trading, warden = MagicMock(), MagicMock()
    ex = ActionExecutor(session, agora_service=None, warden=warden, trading_service=trading)

    res = await ex._handle_consult_tool(
        agent, "consult_tool", {"tool_name": "jj_signals", "market": "BTC/USDT"}
    )

    assert res.success is True
    # THE WALL: nothing on trading or warden was ever called.
    assert trading.mock_calls == [], f"trading was touched: {trading.mock_calls}"
    assert warden.mock_calls == [], f"warden was touched: {warden.mock_calls}"

    # A single pending row, scoped to this agent, holding the tool's data.
    rows = (
        session.query(ToolConsultResult)
        .filter_by(requesting_agent_id=agent.id, status="pending")
        .all()
    )
    assert len(rows) == 1
    assert rows[0].result_payload["tool"] == "jj_signals"
    assert len(rows[0].result_payload["signals"]) == 4


@pytest.mark.asyncio
async def test_handler_runs_with_no_trading_or_warden(db_session_factory, patched_data_api):
    session = db_session_factory()
    agent = _make_agent(session)
    ex = ActionExecutor(session, agora_service=None, warden=None, trading_service=None)
    res = await ex._handle_consult_tool(
        agent, "consult_tool", {"tool_name": "jj_signals", "market": "BTC/USDT"}
    )
    assert res.success is True  # no AttributeError when trading/warden are absent


@pytest.mark.asyncio
async def test_handler_charges_uniform_consult_cost(db_session_factory, patched_data_api):
    session = db_session_factory()
    agent = _make_agent(session)
    ex = ActionExecutor(session, warden=None, trading_service=None)
    res = await ex._handle_consult_tool(
        agent, "consult_tool", {"tool_name": "jj_signals", "market": "BTC/USDT"}
    )
    assert res.cost == cfg.consult_tool_cost_usd
    session.refresh(agent)
    assert agent.thinking_budget_used_today == pytest.approx(cfg.consult_tool_cost_usd)


@pytest.mark.asyncio
async def test_unknown_tool_fails_and_writes_no_row(db_session_factory, patched_data_api):
    session = db_session_factory()
    agent = _make_agent(session)
    ex = ActionExecutor(session, warden=None, trading_service=None)
    res = await ex._handle_consult_tool(
        agent, "consult_tool", {"tool_name": "does_not_exist", "market": "BTC/USDT"}
    )
    assert res.success is False
    assert session.query(ToolConsultResult).count() == 0


@pytest.mark.asyncio
async def test_no_price_history_fails_gracefully(db_session_factory, monkeypatch):
    async def _noop(self, *a, **k):
        return None
    monkeypatch.setattr(SandboxDataAPI, "prefetch_all", _noop)
    monkeypatch.setattr(SandboxDataAPI, "_get_price_history", lambda self, *a, **k: [])
    session = db_session_factory()
    agent = _make_agent(session)
    ex = ActionExecutor(session, warden=None, trading_service=None)
    res = await ex._handle_consult_tool(
        agent, "consult_tool", {"tool_name": "jj_signals", "market": "BTC/USDT"}
    )
    assert res.success is False
    assert session.query(ToolConsultResult).count() == 0
