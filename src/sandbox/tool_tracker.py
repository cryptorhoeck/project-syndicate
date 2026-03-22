"""
Project Syndicate — Tool-Outcome Correlation

Tracks which tools were used before profitable/unprofitable actions.
"""

__version__ = "0.1.0"

import json
import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


class ToolOutcomeTracker:
    """Correlates tool usage with trade outcomes."""

    async def record_tool_usage(
        self, agent_id: int, tool_name: str, cycle_number: int, redis_client=None
    ) -> None:
        """Record that a tool was used at this cycle."""
        if not redis_client:
            return
        try:
            key = f"agent:{agent_id}:tool_usage:{cycle_number}"
            existing = redis_client.get(key)
            tools = json.loads(existing) if existing else []
            if tool_name not in tools:
                tools.append(tool_name)
            redis_client.setex(key, 604800, json.dumps(tools))  # 7 day TTL
        except Exception as e:
            logger.debug(f"Tool usage recording failed: {e}")

    async def correlate_outcome(
        self, agent_id: int, cycle_number: int, outcome_pnl: float,
        db_session: Session, redis_client=None,
    ) -> None:
        """Correlate recent tool usage with outcome."""
        if not redis_client:
            return

        from src.common.config import config
        from src.common.models import AgentTool

        lookback = getattr(config, "tool_outcome_correlation_lookback_cycles", 3)

        tools_used = set()
        for offset in range(lookback):
            key = f"agent:{agent_id}:tool_usage:{cycle_number - offset}"
            try:
                data = redis_client.get(key)
                if data:
                    tools_used.update(json.loads(data))
            except Exception:
                pass

        if not tools_used:
            return

        for tool_name in tools_used:
            try:
                tool = db_session.execute(
                    select(AgentTool).where(
                        AgentTool.agent_id == agent_id,
                        AgentTool.tool_name == tool_name,
                        AgentTool.is_active == True,
                    )
                ).scalar_one_or_none()

                if not tool:
                    continue

                if outcome_pnl > 0:
                    tool.times_before_profitable = (tool.times_before_profitable or 0) + 1
                else:
                    tool.times_before_unprofitable = (tool.times_before_unprofitable or 0) + 1

                total = (tool.times_before_profitable or 0) + (tool.times_before_unprofitable or 0)
                if total > 0:
                    tool.estimated_win_rate = (tool.times_before_profitable or 0) / total

                db_session.flush()
            except Exception as e:
                logger.debug(f"Tool outcome correlation failed for {tool_name}: {e}")
