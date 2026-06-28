"""Consumer-side tests for the consult_tool round-trip (Step 2b-2b).

The load-bearing risk here is the consumer: it must surface a queued result
exactly ONCE and never double-surface across a failure. These tests prove:
  - happy path: surfaced once, then consumed (not surfaced again);
  - NO DOUBLE-SURFACE across a crash between render and mark-delivered;
  - the poison-pill caps a stuck pending row to 'failed';
  - the maintenance prune deletes old terminal rows and expires stale-pending
    orphans (a terminated agent's request can't linger).
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from src.agents.context_assembler import ContextAssembler
from src.agents.maintenance import MaintenanceService
from src.common.models import Agent, ToolConsultResult


def _agent(session, name="A"):
    a = Agent(
        name=name, type="scout", status="active",
        capital_allocated=100.0, capital_current=100.0,
        thinking_budget_daily=0.5, thinking_budget_used_today=0.0,
        evaluation_count=1, profitable_evaluations=0,
    )
    session.add(a)
    session.commit()
    return a


def _payload():
    return {
        "tool": "jj_signals", "market": "BTC/USDT", "regime": "neutral",
        "signals": [{"source": "vwap_deviation", "direction": "long",
                     "confidence": 0.7, "reason": "below VWAP"}],
    }


def _assembler(session):
    asm = ContextAssembler.__new__(ContextAssembler)  # bypass full ctor
    asm.db = session
    return asm


def _pending(session, agent, **kw):
    row = ToolConsultResult(
        requesting_agent_id=agent.id, tool_name="jj_signals", market="BTC/USDT",
        result_payload=_payload(), status="pending", attempt_count=0,
    )
    for k, v in kw.items():
        setattr(row, k, v)
    session.add(row)
    session.commit()
    return row


def test_surfaced_once_then_consumed(db_session_factory):
    session = db_session_factory()
    agent = _agent(session)
    row = _pending(session, agent)
    asm = _assembler(session)

    text1 = asm._consume_pending_consult_results(agent)
    assert "TOOL RESULTS YOU REQUESTED" in text1
    assert "vwap_deviation" in text1
    session.refresh(row)
    assert row.status == "delivered"

    text2 = asm._consume_pending_consult_results(agent)
    assert text2 == ""  # consumed — never surfaced again


def test_no_double_surface_on_mark_delivered_failure(db_session_factory, monkeypatch):
    session = db_session_factory()
    agent = _agent(session)
    row = _pending(session, agent)
    row_id = row.id
    asm = _assembler(session)

    # Crash specifically at the mark-delivered commit (the 2nd commit; the 1st is
    # the consume-pass attempt_count commit). The block must NOT surface.
    real_commit = session.commit
    state = {"n": 0}

    def flaky_commit():
        state["n"] += 1
        if state["n"] == 2:
            raise RuntimeError("crash at mark-delivered commit")
        return real_commit()

    monkeypatch.setattr(session, "commit", flaky_commit)
    text = asm._consume_pending_consult_results(agent)
    assert text == ""  # delivery didn't commit -> nothing surfaced
    monkeypatch.undo()
    session.rollback()

    row = session.get(ToolConsultResult, row_id)
    assert row.status == "pending"  # still pending -> clean retry, no loss

    # Retry succeeds: surfaced exactly once, then consumed.
    text2 = asm._consume_pending_consult_results(agent)
    assert "TOOL RESULTS YOU REQUESTED" in text2
    assert session.get(ToolConsultResult, row_id).status == "delivered"
    assert asm._consume_pending_consult_results(agent) == ""  # and never again


def test_poison_pill_caps_stuck_pending(db_session_factory):
    session = db_session_factory()
    agent = _agent(session)
    row = _pending(session, agent, attempt_count=3)  # at MAX_ATTEMPTS
    asm = _assembler(session)

    text = asm._consume_pending_consult_results(agent)
    assert text == ""  # capped row is not surfaced
    session.refresh(row)
    assert row.status == "failed"
    assert row.last_error and "attempts" in row.last_error


def test_prune_deletes_old_terminal_and_expires_stale_pending(db_session_factory):
    now = datetime.now(timezone.utc)
    with db_session_factory() as s:
        agent = _agent(s)
        stale_id = _pending(s, agent, requested_at=now - timedelta(hours=12)).id
        fresh_id = _pending(s, agent, requested_at=now - timedelta(minutes=5)).id
        old_delivered_id = _pending(
            s, agent, status="delivered",
            requested_at=now - timedelta(hours=48),
            delivered_at=now - timedelta(hours=48),
        ).id

    pruned = MaintenanceService(db_session_factory).prune_tool_consult_results()
    assert pruned == 2  # 1 old delivered deleted + 1 stale pending expired

    with db_session_factory() as s2:
        assert s2.get(ToolConsultResult, old_delivered_id) is None       # deleted
        assert s2.get(ToolConsultResult, stale_id).status == "failed"    # expired
        assert s2.get(ToolConsultResult, fresh_id).status == "pending"   # untouched
