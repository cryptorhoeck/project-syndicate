"""Boot-sequence genome creation - persistence + deadlock regression.

The maiden full-stack launch froze on a genome-INSERT deadlock. Root cause:
`_spawn_agent` created each Gen-1 genome via ``asyncio.ensure_future(create_genome(
..., db_session=session))`` inside Genesis's *running* event loop - a fire-and-forget
coroutine that shared the spawning session, flushed an INSERT, but was never awaited
or committed on that path. The genome INSERTs sat 'idle in transaction' holding locks
and deadlocked concurrent writers (and emitted the "coroutine was never awaited"
RuntimeWarnings we'd been seeing).

This test reproduces the failure at its root: under a running event loop (which is
exactly Genesis's context, and what pytest-asyncio gives us), a spawned agent's genome
must be COMMITTED and readable from a FRESH session. It FAILS against the fire-and-forget
code (the genome is never committed) and PASSES once creation is synchronous and in the
spawning session.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.common.models import AgentGenome, Base, SystemState
from src.genesis.boot_sequence import BootSequenceOrchestrator, SPAWN_WAVES


def _factory():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as s:
        s.add(SystemState(
            id=1, total_treasury=500.0, peak_treasury=500.0,
            current_regime="volatile", active_agent_count=0, alert_status="green",
        ))
        s.commit()
    return factory


@pytest.mark.asyncio
async def test_spawned_agent_has_committed_genome():
    """A spawned Gen-1 agent's genome must persist (fresh-session reload), under a
    running event loop. Fire-and-forget creation fails this; synchronous passes."""
    factory = _factory()
    orch = BootSequenceOrchestrator(db_session_factory=factory)
    spec = SPAWN_WAVES[1][0]  # Scout-Alpha

    with factory() as s:
        agent = orch._spawn_agent(s, spec, wave_num=1)
        s.commit()
        agent_id = agent.id

    # Fresh session: is the genome ACTUALLY committed, not just scheduled?
    with factory() as s2:
        g = s2.query(AgentGenome).filter_by(agent_id=agent_id).one_or_none()
        assert g is not None, (
            "spawned agent has NO committed genome - fire-and-forget "
            "(asyncio.ensure_future) creation never committed it."
        )
        assert isinstance(g.genome_data, dict)
        assert "signal_generation" in g.genome_data  # a real random genome


@pytest.mark.asyncio
async def test_every_wave_agent_gets_exactly_one_committed_genome():
    """All Gen-1 agents across every wave end up with exactly one committed genome -
    no orphaned transactions, no duplicates, no missing rows."""
    factory = _factory()
    orch = BootSequenceOrchestrator(db_session_factory=factory)

    ids = []
    with factory() as s:
        for wave, specs in SPAWN_WAVES.items():
            for spec in specs:
                ids.append(orch._spawn_agent(s, spec, wave_num=wave).id)
        s.commit()

    assert len(ids) == 5  # 2 scouts + strategist + critic + operator
    with factory() as s2:
        for aid in ids:
            rows = s2.query(AgentGenome).filter_by(agent_id=aid).all()
            assert len(rows) == 1, f"agent {aid} has {len(rows)} genomes (want exactly 1)"
