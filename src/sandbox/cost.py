"""
Project Syndicate — Sandbox Cost Accounting

Tracks execution costs and adds them to agent thinking tax.
"""

__version__ = "0.1.0"

import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from src.common.config import config

logger = logging.getLogger(__name__)


def calculate_execution_cost(execution_time_ms: int) -> float:
    """Cost of a single sandbox execution."""
    return config.sandbox_base_cost_usd + (execution_time_ms * config.sandbox_time_rate_usd_per_ms)


async def record_sandbox_execution(
    agent_id: int,
    cycle_number: int,
    tool_name: str | None,
    script_hash: str,
    script_length: int,
    success: bool,
    output: str | None,
    error: str | None,
    execution_time_ms: int,
    cost_usd: float,
    purpose: str | None,
    was_pre_compute: bool,
    db_session: Session,
) -> None:
    """Record sandbox execution in database and charge agent."""
    from src.common.models import Agent, SandboxExecution

    try:
        record = SandboxExecution(
            agent_id=agent_id,
            cycle_number=cycle_number,
            tool_name=tool_name,
            script_hash=script_hash,
            script_length=script_length,
            success=success,
            output=(output[:5000] if output else None),
            error=(error[:2000] if error else None),
            execution_time_ms=execution_time_ms,
            execution_cost_usd=cost_usd,
            purpose=purpose,
            was_pre_compute=was_pre_compute,
        )
        db_session.add(record)

        # Charge agent
        agent = db_session.get(Agent, agent_id)
        if agent:
            agent.thinking_budget_used_today = (agent.thinking_budget_used_today or 0.0) + cost_usd
            agent.total_api_cost = (agent.total_api_cost or 0.0) + cost_usd

        db_session.flush()
    except Exception as e:
        logger.warning(f"Failed to record sandbox execution: {e}")
