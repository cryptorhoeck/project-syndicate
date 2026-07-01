"""Colony roster in survival context — closes the 'no-operator' self-awareness gap.

Before this section, an agent's context showed only its SAME-role peers plus a bare
head count, so scouts/strategists/critics could not tell whether an operator existed
and inferred 'no operator / execution unmanned' from Agora silence. These tests lock
in that every agent is now told the real roster by role + name (esp. the operator),
that Genesis (the overseer, id=0) is excluded, that recency renders, and that a
scout's fully assembled context actually names the operator.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.agents.survival_context import SurvivalContextAssembler
from src.common.models import Agent, Base, SystemState


@pytest.fixture
def session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _add(session, agent_id, name, type_, last_cycle_at=None, status="active"):
    session.add(Agent(
        id=agent_id, name=name, type=type_, status=status, generation=1,
        capital_allocated=100.0, capital_current=100.0,
        thinking_budget_daily=0.5, thinking_budget_used_today=0.0,
        last_cycle_at=last_cycle_at,
    ))
    session.commit()


def _colony(session):
    now = datetime.now(timezone.utc)
    _add(session, 0, "Genesis", "genesis", now)                              # overseer — EXCLUDED
    _add(session, 1, "Scout-Alpha", "scout", now - timedelta(seconds=30))
    _add(session, 2, "Scout-Beta", "scout", now - timedelta(minutes=5))
    _add(session, 3, "Strategist-Prime", "strategist", now - timedelta(minutes=2))
    _add(session, 4, "Arbiter", "critic", now - timedelta(minutes=1))
    _add(session, 5, "Operator-Genesis", "operator", now - timedelta(seconds=45))


def test_roster_names_every_role_including_the_operator(session):
    _colony(session)
    roster = SurvivalContextAssembler()._build_colony_roster(session)

    # The phantom-killer: the operator is named, so no agent can believe it's absent.
    assert "Operator-Genesis" in roster
    assert "Operators (1)" in roster
    # Every operational role listed by name.
    for name in ["Scout-Alpha", "Scout-Beta", "Strategist-Prime", "Arbiter", "Operator-Genesis"]:
        assert name in roster
    assert "Scouts (2)" in roster


def test_roster_excludes_genesis_overseer(session):
    _colony(session)
    roster = SurvivalContextAssembler()._build_colony_roster(session)
    # No "Genesiss (N)" role header — the id=0 overseer is not a routable teammate.
    assert "Genesiss" not in roster
    # And it isn't listed as a member line (guard against "Operator-Genesis" false match).
    assert "- Genesis —" not in roster


def test_roster_renders_recency(session):
    _colony(session)
    roster = SurvivalContextAssembler()._build_colony_roster(session)
    assert "ago" in roster  # last-active recency is shown


def test_roster_handles_never_active_agent(session):
    _add(session, 5, "Operator-Genesis", "operator", last_cycle_at=None)
    roster = SurvivalContextAssembler()._build_colony_roster(session)
    assert "Operator-Genesis" in roster
    assert "not yet" in roster


def test_roster_empty_when_only_genesis(session):
    _add(session, 0, "Genesis", "genesis")
    roster = SurvivalContextAssembler()._build_colony_roster(session)
    assert roster == ""  # no routable teammates -> section suppressed


def test_scout_full_context_now_names_the_operator(session):
    """End-to-end: the exact divergence, closed. A scout's assembled survival
    context contains the operator by name — which it never did before."""
    session.add(SystemState(
        id=1, total_treasury=500.0, peak_treasury=500.0,
        current_regime="volatile", active_agent_count=5, alert_status="green",
    ))
    session.commit()
    _colony(session)

    scout = session.get(Agent, 1)
    ctx = SurvivalContextAssembler().assemble(scout, session)

    assert "COLONY ROSTER" in ctx
    assert "Operator-Genesis" in ctx
