"""#2 — opportunities must be born with an expiry, or the strategist never sees them.

The strategist's "AVAILABLE OPPORTUNITIES" query filters on ``expires_at > now``
(context_assembler), so an opportunity created with a NULL expiry — how they were born
before this fix — is silently dropped and never reaches a strategist. That starved the
whole structured pipeline.

These tests prove it end-to-end through the REAL code paths: an opportunity created via
``ActionExecutor._handle_broadcast_opportunity`` now survives ``ContextAssembler.assemble``
for a strategist, and a NULL-expiry opportunity still (correctly) does not — so the fix is
at the creation site, not the filter (which is right to drop the genuinely expired).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

from src.agents.action_executor import ActionExecutor
from src.agents.context_assembler import ContextAssembler
from src.common.config import config
from src.common.models import Agent, Opportunity


def _agent(name: str, atype: str) -> Agent:
    return Agent(
        name=name, type=atype, status="active", generation=1,
        capital_allocated=100.0, capital_current=100.0,
    )


@pytest.mark.asyncio
async def test_new_opportunity_gets_ttl_and_survives_strategist_filter(db_session_factory):
    with db_session_factory() as db_session:
        scout = _agent("Scout-Expiry", "scout")
        strat = _agent("Strat-Expiry", "strategist")
        db_session.add_all([scout, strat])
        db_session.commit()
        db_session.refresh(scout)
        db_session.refresh(strat)

        # Create an opportunity through the REAL handler (only the Agora broadcast is stubbed).
        ex = ActionExecutor(db_session, agora_service=None)
        ex._post_to_agora = AsyncMock()
        await ex._handle_broadcast_opportunity(
            scout,
            "broadcast_opportunity",
            {"market": "BTC/USDT", "signal": "volume_breakout", "urgency": "high",
             "details": "volume spike", "confidence": 7},
        )

        # 1. Creation-site fix: expiry set, from the CONFIG ttl (not hardcoded 6), in the future.
        opp = db_session.query(Opportunity).filter(Opportunity.market == "BTC/USDT").one()
        assert opp.expires_at is not None, "expires_at not set at creation — the bug"
        got = opp.expires_at if opp.expires_at.tzinfo else opp.expires_at.replace(tzinfo=timezone.utc)
        expected = datetime.now(timezone.utc) + timedelta(hours=config.opportunity_ttl_hours)
        assert abs((got - expected).total_seconds()) < 60, "expiry is not now + configured TTL"

        # 2. END-TO-END (the real scoreboard): strategist's assembled context now includes it.
        ctx = ContextAssembler(db_session).assemble(strat)
        full = ctx.system_prompt + "\n" + ctx.user_prompt
        assert "BTC/USDT" in full, "fresh opportunity did not survive the strategist filter"


@pytest.mark.asyncio
async def test_null_expiry_opportunity_is_dropped_by_the_filter(db_session_factory):
    """Fail-before condition, locked in: an opportunity born with NULL expiry (the old bug)
    is correctly excluded by the strategist filter — proving the filter is right and the
    creation site was the defect."""
    with db_session_factory() as db_session:
        scout = _agent("Scout-Null", "scout")
        strat = _agent("Strat-Null", "strategist")
        db_session.add_all([scout, strat])
        db_session.commit()
        db_session.refresh(scout)

        db_session.add(Opportunity(
            scout_agent_id=scout.id, scout_agent_name=scout.name, market="ETH/USDT",
            signal_type="volume_breakout", details="d", urgency="high", confidence=5,
            status="new", expires_at=None,
        ))
        db_session.commit()

        ctx = ContextAssembler(db_session).assemble(strat)
        full = ctx.system_prompt + "\n" + ctx.user_prompt
        assert "ETH/USDT" not in full, "NULL-expiry opportunity leaked into the strategist view"
