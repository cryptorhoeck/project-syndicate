"""
Project Syndicate — Gen 1 Health Check

Day-10 evaluation of Gen 1 agents to catch issues early.
Checks cycle count, idle rate, validation failures, and API efficiency.
Can extend/shorten survival clock, reallocate capital, or flag for termination.
"""

__version__ = "0.8.0"

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from src.common.config import config
from src.common.models import Agent, BootSequenceLog

logger = logging.getLogger(__name__)

# Health check thresholds
MIN_CYCLES_BY_DAY_10 = 20       # Should have run at least 20 cycles in 10 days
MAX_IDLE_RATE = 0.90             # More than 90% idle = not doing anything useful
MAX_VALIDATION_FAIL_RATE = 0.30  # More than 30% validation failures = bad outputs
MAX_AVG_CYCLE_COST = 0.02       # More than $0.02 per cycle on average = too expensive
HEALTH_CHECK_DAY = 10            # Run at day 10 of survival clock


@dataclass
class HealthCheckResult:
    """Result of a health check for a single agent."""
    agent_id: int
    agent_name: str
    passed: bool
    cycle_count: int
    idle_rate: float
    validation_fail_rate: float
    avg_cycle_cost: float
    issues: list[str]
    actions_taken: list[str]


class HealthCheckService:
    """Runs day-10 health checks for Gen 1 agents."""

    def __init__(self, db_session_factory: sessionmaker):
        self.db_factory = db_session_factory

    async def run_health_checks(self) -> list[HealthCheckResult]:
        """Check all Gen 1 agents that are due for health check.

        Returns:
            List of HealthCheckResult for each checked agent.
        """
        results = []

        with self.db_factory() as session:
            # Find Gen 1 agents due for health check
            now = datetime.now(timezone.utc)
            agents = session.execute(
                select(Agent).where(
                    Agent.generation == 1,
                    Agent.status == "active",
                    Agent.health_check_passed == None,
                )
            ).scalars().all()

            for agent in agents:
                if not self._is_due_for_check(agent, now):
                    continue

                result = self._check_agent(session, agent)
                results.append(result)

                # Log the health check
                log = BootSequenceLog(
                    wave_number=agent.spawn_wave or 0,
                    event_type="health_check",
                    agent_id=agent.id,
                    agent_name=agent.name,
                    details=(
                        f"passed={result.passed}, issues={result.issues}, "
                        f"actions={result.actions_taken}"
                    ),
                )
                session.add(log)

            session.commit()

        return results

    def _is_due_for_check(self, agent: Agent, now: datetime) -> bool:
        """Check if an agent is due for the day-10 health check.

        Args:
            agent: The agent.
            now: Current time.

        Returns:
            True if the agent should be checked.
        """
        if not agent.survival_clock_start:
            return False

        start = agent.survival_clock_start.replace(tzinfo=timezone.utc)
        days_elapsed = (now - start).total_seconds() / 86400

        return days_elapsed >= HEALTH_CHECK_DAY

    def _check_agent(self, session, agent: Agent) -> HealthCheckResult:
        """Run health check on a single agent.

        Args:
            session: DB session.
            agent: The agent to check.

        Returns:
            HealthCheckResult with findings and actions.
        """
        issues = []
        actions = []

        # Check cycle count
        if agent.cycle_count < MIN_CYCLES_BY_DAY_10:
            issues.append(
                f"Low cycle count: {agent.cycle_count} < {MIN_CYCLES_BY_DAY_10}"
            )

        # Check idle rate
        if agent.idle_rate > MAX_IDLE_RATE:
            issues.append(
                f"High idle rate: {agent.idle_rate:.1%} > {MAX_IDLE_RATE:.0%}"
            )

        # Check validation failure rate
        if agent.validation_fail_rate > MAX_VALIDATION_FAIL_RATE:
            issues.append(
                f"High validation fail rate: {agent.validation_fail_rate:.1%} > "
                f"{MAX_VALIDATION_FAIL_RATE:.0%}"
            )

        # Check API cost efficiency
        if agent.avg_cycle_cost > MAX_AVG_CYCLE_COST:
            issues.append(
                f"High avg cycle cost: ${agent.avg_cycle_cost:.4f} > "
                f"${MAX_AVG_CYCLE_COST:.4f}"
            )

        # Determine pass/fail and take actions
        passed = len(issues) <= 1  # Allow 1 minor issue

        if not passed and len(issues) >= 3:
            # Serious problems — shorten survival clock by 3 days
            if agent.survival_clock_end:
                new_end = agent.survival_clock_end - timedelta(days=3)
                agent.survival_clock_end = new_end
                actions.append("survival_clock_shortened_3d")

            # Reduce daily thinking budget
            agent.thinking_budget_daily = max(
                0.10, agent.thinking_budget_daily * 0.75
            )
            actions.append("budget_reduced_25pct")

        elif not passed:
            # Minor problems — extend survival clock to allow more learning
            if agent.survival_clock_end:
                new_end = agent.survival_clock_end + timedelta(days=3)
                agent.survival_clock_end = new_end
                actions.append("survival_clock_extended_3d")

        # Mark health check as done
        agent.health_check_passed = passed
        agent.health_check_at = datetime.now(timezone.utc)
        session.add(agent)
        session.flush()

        logger.info(
            f"Health check for {agent.name}: passed={passed}, "
            f"issues={len(issues)}, actions={actions}"
        )

        return HealthCheckResult(
            agent_id=agent.id,
            agent_name=agent.name,
            passed=passed,
            cycle_count=agent.cycle_count,
            idle_rate=agent.idle_rate,
            validation_fail_rate=agent.validation_fail_rate,
            avg_cycle_cost=agent.avg_cycle_cost,
            issues=issues,
            actions_taken=actions,
        )

    async def check_single_agent(self, agent_id: int) -> HealthCheckResult | None:
        """Run health check on a specific agent (manual trigger).

        Args:
            agent_id: The agent to check.

        Returns:
            HealthCheckResult, or None if agent not found.
        """
        with self.db_factory() as session:
            agent = session.get(Agent, agent_id)
            if not agent:
                return None

            result = self._check_agent(session, agent)
            session.commit()
            return result
