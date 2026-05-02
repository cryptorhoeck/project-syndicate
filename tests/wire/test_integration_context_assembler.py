"""End-to-end: ContextAssembler builds Scout context with Wire signals injected."""

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from src.common.models import Agent
from src.wire.models import WireEvent, WireSource


def _seed_published_event(session, *, severity: int = 3, summary: str = "BTC listed") -> None:
    evt = WireEvent(
        canonical_hash=f"hash-{severity}-{summary}",
        coin="BTC",
        event_type="listing",
        severity=severity,
        summary=summary,
        occurred_at=datetime.now(timezone.utc) - timedelta(minutes=5),
        digested_at=datetime.now(timezone.utc) - timedelta(minutes=5),
        published_to_ticker=True,
    )
    session.add(evt)
    session.commit()


def _make_scout(session) -> Agent:
    agent = Agent(
        name="Wire-Scout-1",
        type="scout",
        status="active",
        capital_allocated=100.0,
        capital_current=100.0,
        thinking_budget_daily=0.50,
        thinking_budget_used_today=0.0,
        evaluation_count=5,
        profitable_evaluations=3,
    )
    session.add(agent)
    session.commit()
    return agent


def test_scout_priority_context_includes_wire_signals(wire_seeded_session) -> None:
    from src.agents.context_assembler import ContextAssembler

    _seed_published_event(
        wire_seeded_session, severity=4, summary="Major listing event"
    )
    scout = _make_scout(wire_seeded_session)

    assembler = ContextAssembler(db_session=wire_seeded_session)
    text = assembler._build_priority_context(scout, token_budget=4000)  # type: ignore[arg-type]
    assert "THE WIRE — RECENT SIGNALS" in text
    assert "Major listing event" in text
    assert "S4" in text


def test_non_scout_does_not_get_wire_block(wire_seeded_session) -> None:
    from src.agents.context_assembler import ContextAssembler

    _seed_published_event(wire_seeded_session, severity=3, summary="something")
    strategist = Agent(
        name="StratX",
        type="strategist",
        status="active",
        capital_allocated=100.0,
        capital_current=100.0,
        thinking_budget_daily=0.50,
        thinking_budget_used_today=0.0,
        evaluation_count=5,
        profitable_evaluations=3,
    )
    wire_seeded_session.add(strategist)
    wire_seeded_session.commit()

    assembler = ContextAssembler(db_session=wire_seeded_session)
    text = assembler._build_priority_context(strategist, token_budget=4000)
    assert "THE WIRE — RECENT SIGNALS" not in text


def test_scout_with_no_signals_shows_explicit_empty_marker(wire_seeded_session) -> None:
    from src.agents.context_assembler import ContextAssembler

    scout = _make_scout(wire_seeded_session)
    assembler = ContextAssembler(db_session=wire_seeded_session)
    text = assembler._build_priority_context(scout, token_budget=4000)  # type: ignore[arg-type]
    assert "THE WIRE — RECENT SIGNALS" in text
    assert "no severity-3+ events on the wire" in text
