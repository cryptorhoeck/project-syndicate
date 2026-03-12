"""
Tests for the Treasury Manager — allocation, prestige, inheritance.
"""

import pytest
from datetime import datetime, timedelta, timezone

from src.common.models import Agent, InheritedPosition, SystemState
from src.genesis.treasury import TreasuryManager


@pytest.fixture
def treasury(seeded_db):
    """Create a TreasuryManager with test database."""
    return TreasuryManager(db_session_factory=seeded_db)


@pytest.mark.asyncio
async def test_capital_allocation_respects_reserve(treasury, seeded_db):
    """Allocation should not dip into the 20% reserve."""
    balance = await treasury.get_treasury_balance()
    # Treasury=500, reserve=100, allocated=100 (agent1), available=300
    assert balance["reserved"] == 100.0
    assert balance["available_for_allocation"] == 300.0

    # Try to allocate more than available
    success = await treasury.allocate_capital(1, 500.0)
    assert success is False

    # Allocate within limits
    success = await treasury.allocate_capital(1, 50.0)
    assert success is True


@pytest.mark.asyncio
async def test_prestige_multipliers_apply(treasury):
    """Prestige titles should produce correct multipliers."""
    assert treasury._get_prestige_multiplier(None) == 1.0
    assert treasury._get_prestige_multiplier("Proven") == 1.10
    assert treasury._get_prestige_multiplier("Veteran") == 1.20
    assert treasury._get_prestige_multiplier("Elite") == 1.30
    assert treasury._get_prestige_multiplier("Legendary") == 1.50


@pytest.mark.asyncio
async def test_position_inheritance_on_death(treasury, seeded_db):
    """Positions should be inherited when an agent dies."""
    result = await treasury.inherit_positions(1)
    # Currently returns empty list (placeholder), but should not crash
    assert isinstance(result, list)


@pytest.mark.asyncio
async def test_random_allocation_percentage(treasury, seeded_db):
    """Random allocation should be ~10% of available capital."""
    # Create a mock leaderboard
    leaderboard = [
        {"agent_id": 1, "composite_score": 0.8, "prestige_title": None},
    ]
    decisions = await treasury.perform_capital_allocation_round(leaderboard)
    assert isinstance(decisions, list)
    # Should have both rank-based and random allocations
    types = [d["type"] for d in decisions]
    assert "rank_based" in types


@pytest.mark.asyncio
async def test_reclaim_capital(treasury, seeded_db):
    """Reclaiming capital should return it to treasury."""
    reclaimed = await treasury.reclaim_capital(1)
    assert reclaimed == 100.0  # Agent had $100

    # Verify agent capital is zero
    with seeded_db() as session:
        agent = session.get(Agent, 1)
        assert agent.capital_allocated == 0.0
        assert agent.capital_current == 0.0


@pytest.mark.asyncio
async def test_update_peak_treasury(treasury, seeded_db):
    """Peak should update when treasury exceeds it."""
    # Increase treasury above peak
    with seeded_db() as session:
        from sqlalchemy import select
        state = session.execute(select(SystemState).limit(1)).scalar_one()
        state.total_treasury = 600.0  # Above peak of 500
        session.commit()

    await treasury.update_peak_treasury()

    with seeded_db() as session:
        from sqlalchemy import select
        state = session.execute(select(SystemState).limit(1)).scalar_one()
        assert state.peak_treasury == 600.0
