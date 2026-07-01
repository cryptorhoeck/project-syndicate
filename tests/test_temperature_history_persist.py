"""#6 — temperature_history must accumulate across evolutions (plain-JSON reassign bug).

temperature_history is a plain JSON column (no MutableDict). TemperatureEvolution.evolve
read it into a local, appended, and reassigned the SAME object — a no-op for SQLAlchemy
change tracking once the list was non-empty — so every evolution after the first was
silently lost, and an agent kept only its FIRST history entry. Second instance of the
modify_genome class, found by the #6 JSON-persistence audit. Fixed with flag_modified.

Proven across SEPARATE sessions, matching production (one evolution per evaluation cycle,
the agent reloaded between): three real evolve() calls must leave three history entries.
Fail-before this fix, the count stalls at 1.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.common.models import Agent
from src.personality.temperature_evolution import TemperatureEvolution


@pytest.mark.asyncio
async def test_temperature_history_accumulates_across_evolutions(db_session_factory):
    with db_session_factory() as s:
        a = Agent(
            name="Temp-6", type="scout", status="active", generation=1,
            capital_allocated=100.0, capital_current=100.0,
        )
        s.add(a)
        s.commit()
        aid = a.id

    evolver = TemperatureEvolution()
    p0 = datetime(2026, 1, 1, tzinfo=timezone.utc)

    # Three evolutions, each in its OWN session with a reload + commit — the real cadence
    # (one per evaluation cycle). Reloading between commits is what surfaces the
    # reassign-same-object bug: each session re-reads the last-persisted value.
    for i in range(3):
        with db_session_factory() as s:
            agent = s.get(Agent, aid)
            await evolver.evolve(s, agent, p0 + timedelta(days=i), p0 + timedelta(days=i + 1))
            s.commit()

    with db_session_factory() as s:
        agent = s.get(Agent, aid)
        assert len(agent.temperature_history) == 3, (
            f"temperature_history kept {len(agent.temperature_history)} of 3 entries — "
            "reassign-same-object silently dropped the later appends"
        )
