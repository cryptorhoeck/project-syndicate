"""
Project Syndicate — Thinking Cycle Engine

The master orchestrator that ties everything together.
Runs the OODA loop: Budget → Observe → Orient+Decide → Validate → Act → Record

This is the single most important piece of code in the entire project.
"""

__version__ = "0.9.0"

import logging
import time
from dataclasses import dataclass

from sqlalchemy.orm import Session

from src.common.models import Agent
from src.agents.budget_gate import BudgetGate, BudgetStatus
from src.agents.claude_client import ClaudeClient, APIResponse
from src.agents.context_assembler import ContextAssembler, AssembledContext
from src.agents.output_validator import OutputValidator, ValidationResult
from src.agents.action_executor import ActionExecutor, ActionResult
from src.agents.cycle_recorder import CycleRecorder, CycleData
from src.agents.memory_manager import MemoryManager
from src.agents.roles import get_role

logger = logging.getLogger(__name__)


@dataclass
class CycleResult:
    """Result of a complete thinking cycle."""
    success: bool
    skipped: bool = False
    failed: bool = False
    action_type: str | None = None
    reason: str = ""
    api_cost: float = 0.0
    cycle_number: int = 0


class ThinkingCycle:
    """The OODA loop engine — runs a single thinking cycle for an agent.

    Pipeline:
        Phase 0: Budget Check  → Can I afford to think?
        Phase 1: Observe        → Assemble context window
        Phase 2: Orient+Decide  → Single Claude API call (or Reflect every 10th)
        Phase 3: Validate       → Is the output well-formed and legal?
        Phase 4: Act            → Execute the chosen action
        Phase 5: Record         → Write everything to the black box
    """

    def __init__(
        self,
        db_session: Session,
        claude_client: ClaudeClient,
        redis_client=None,
        agora_service=None,
        warden=None,
        config=None,
    ):
        self.db = db_session
        self.claude = claude_client
        self.redis = redis_client
        self.agora = agora_service

        # Initialize sub-components
        self.budget_gate = BudgetGate(db_session, agora_service)
        self.context_assembler = ContextAssembler(
            db_session,
            token_budget=getattr(config, "context_token_budget_normal", 3000),
        )
        self.output_validator = OutputValidator(warden=warden)
        self.action_executor = ActionExecutor(db_session, agora_service, warden)
        self.cycle_recorder = CycleRecorder(db_session, redis_client, agora_service)
        self.memory_manager = MemoryManager(db_session, redis_client)

        # Config
        self.reflection_interval = getattr(config, "reflection_every_n_cycles", 10)
        self.retry_tax_multiplier = getattr(config, "retry_tax_multiplier", 2.0)

    async def run(self, agent_id: int) -> CycleResult:
        """Run a complete thinking cycle for an agent.

        Args:
            agent_id: The agent to run.

        Returns:
            CycleResult with outcome details.
        """
        cycle_start = time.time()

        # Load agent
        agent = self.db.query(Agent).filter(Agent.id == agent_id).first()
        if not agent:
            return CycleResult(success=False, reason="agent_not_found")

        if agent.status not in ("active", "initializing"):
            return CycleResult(success=False, reason=f"agent_status_{agent.status}")

        cycle_number = agent.cycle_count

        # ── Phase 0: Budget Check ──
        budget_result = self.budget_gate.check(agent)
        if budget_result.status == BudgetStatus.SKIP_CYCLE:
            logger.info(f"Cycle skipped for {agent.name}: budget exhausted")
            return CycleResult(
                success=False,
                skipped=True,
                reason="budget_exhausted",
                cycle_number=cycle_number,
            )

        # Determine cycle type
        is_reflection = (
            cycle_number > 0
            and cycle_number % self.reflection_interval == 0
        )
        cycle_type = "reflection" if is_reflection else "normal"
        if budget_result.status == BudgetStatus.SURVIVAL_MODE:
            cycle_type = "survival" if not is_reflection else "reflection"

        # ── Phase 1: Observe (Context Assembly) ──
        context = self.context_assembler.assemble(
            agent,
            budget_status=budget_result.status,
            cycle_type=cycle_type,
        )

        # ── Phase 2: Orient + Decide (API Call) ──
        role_def = get_role(agent.type)
        temperature = agent.api_temperature or role_def.default_temperature

        api_start = time.time()
        try:
            api_response = await self.claude.call(
                system_prompt=context.system_prompt,
                user_prompt=context.user_prompt,
                temperature=temperature,
            )
        except Exception as e:
            logger.error(f"API call failed for {agent.name}: {e}")
            # Record failed cycle
            self._record_failed_cycle(
                agent, cycle_number, cycle_type, context, str(e), cycle_start
            )
            return CycleResult(
                success=False,
                failed=True,
                reason=f"api_error: {e}",
                cycle_number=cycle_number,
            )
        api_latency = int((time.time() - api_start) * 1000)

        total_cost = api_response.cost_usd

        # ── Phase 3: Validate ──
        validation = self.output_validator.validate(
            agent_type=agent.type,
            raw_output=api_response.content,
            cycle_type=cycle_type,
            agent_capital=agent.capital_current,
        )

        if not validation.passed and validation.retryable:
            # One retry with repair prompt — costs double thinking tax
            repair_prompt = self.output_validator.build_repair_prompt(
                api_response.content, validation.failure_detail
            )
            try:
                repair_response = await self.claude.call_repair(
                    system_prompt=context.system_prompt,
                    original_user_prompt=context.user_prompt,
                    repair_prompt=repair_prompt,
                    temperature=0.2,
                )
                total_cost += repair_response.cost_usd * self.retry_tax_multiplier

                validation = self.output_validator.validate(
                    agent_type=agent.type,
                    raw_output=repair_response.content,
                    cycle_type=cycle_type,
                    agent_capital=agent.capital_current,
                )

                # Update response to the repair response for recording
                api_response = APIResponse(
                    content=repair_response.content,
                    input_tokens=api_response.input_tokens + repair_response.input_tokens,
                    output_tokens=api_response.output_tokens + repair_response.output_tokens,
                    cost_usd=total_cost,
                    latency_ms=api_latency + repair_response.latency_ms,
                    model=api_response.model,
                    stop_reason=repair_response.stop_reason,
                )
            except Exception as e:
                logger.warning(f"Repair call failed for {agent.name}: {e}")

        if not validation.passed:
            # Record failed cycle
            cycle_duration = int((time.time() - cycle_start) * 1000)
            data = CycleData(
                agent_id=agent.id,
                agent_name=agent.name,
                generation=agent.generation,
                cycle_number=cycle_number,
                cycle_type=cycle_type,
                context_mode=context.mode.value,
                context_tokens=context.total_tokens,
                validation_passed=False,
                validation_retries=1 if validation.retryable else 0,
                input_tokens=api_response.input_tokens,
                output_tokens=api_response.output_tokens,
                api_cost_usd=total_cost,
                cycle_duration_ms=cycle_duration,
                api_latency_ms=api_latency,
            )
            self.cycle_recorder.record_failed(data)
            self.db.commit()

            return CycleResult(
                success=False,
                failed=True,
                reason=f"validation_failed: {validation.failure_type.value if validation.failure_type else 'unknown'}",
                api_cost=total_cost,
                cycle_number=cycle_number,
            )

        parsed = validation.parsed

        # ── Phase 4: Act ──
        if is_reflection:
            # Process reflection — update long-term memory
            self.memory_manager.process_reflection(
                agent_id=agent.id,
                cycle_number=cycle_number,
                reflection=parsed,
            )
            action_type = "reflection"
            action_result = ActionResult(
                success=True, action_type="reflection", details="Reflection processed"
            )
        else:
            action_result = await self.action_executor.execute(agent, parsed)
            action_type = parsed.get("action", {}).get("type", "unknown")

        # ── Phase 5: Record ──
        cycle_duration = int((time.time() - cycle_start) * 1000)

        if is_reflection:
            data = CycleData(
                agent_id=agent.id,
                agent_name=agent.name,
                generation=agent.generation,
                cycle_number=cycle_number,
                cycle_type="reflection",
                context_mode=context.mode.value,
                context_tokens=context.total_tokens,
                situation=parsed.get("lesson"),
                confidence_score=None,
                confidence_reason=parsed.get("confidence_reason"),
                recent_pattern=parsed.get("pattern_detected"),
                action_type="reflection",
                action_params=None,
                reasoning=parsed.get("what_worked", "") + " | " + parsed.get("what_failed", ""),
                self_note=parsed.get("strategy_note"),
                validation_passed=True,
                input_tokens=api_response.input_tokens,
                output_tokens=api_response.output_tokens,
                api_cost_usd=total_cost,
                cycle_duration_ms=cycle_duration,
                api_latency_ms=api_latency,
            )
        else:
            data = CycleData(
                agent_id=agent.id,
                agent_name=agent.name,
                generation=agent.generation,
                cycle_number=cycle_number,
                cycle_type=cycle_type,
                context_mode=context.mode.value,
                context_tokens=context.total_tokens,
                situation=parsed.get("situation"),
                confidence_score=parsed.get("confidence", {}).get("score"),
                confidence_reason=parsed.get("confidence", {}).get("reasoning"),
                recent_pattern=parsed.get("recent_pattern"),
                action_type=action_type,
                action_params=parsed.get("action", {}).get("params"),
                reasoning=parsed.get("reasoning"),
                self_note=parsed.get("self_note"),
                validation_passed=True,
                input_tokens=api_response.input_tokens,
                output_tokens=api_response.output_tokens,
                api_cost_usd=total_cost,
                cycle_duration_ms=cycle_duration,
                api_latency_ms=api_latency,
            )

        self.cycle_recorder.record(data)
        self.db.commit()

        logger.info(
            "cycle_complete",
            extra={
                "agent": agent.name,
                "cycle": cycle_number,
                "type": cycle_type,
                "action": action_type,
                "cost": total_cost,
                "duration_ms": cycle_duration,
            },
        )

        return CycleResult(
            success=True,
            action_type=action_type,
            api_cost=total_cost,
            cycle_number=cycle_number,
        )

    def _record_failed_cycle(
        self,
        agent: Agent,
        cycle_number: int,
        cycle_type: str,
        context: AssembledContext,
        error: str,
        cycle_start: float,
    ) -> None:
        """Record a cycle that failed at the API call stage."""
        cycle_duration = int((time.time() - cycle_start) * 1000)
        data = CycleData(
            agent_id=agent.id,
            agent_name=agent.name,
            generation=agent.generation,
            cycle_number=cycle_number,
            cycle_type=cycle_type,
            context_mode=context.mode.value,
            context_tokens=context.total_tokens,
            situation=f"API error: {error[:200]}",
            validation_passed=False,
            cycle_duration_ms=cycle_duration,
        )
        self.cycle_recorder.record_failed(data)
        try:
            self.db.commit()
        except Exception:
            self.db.rollback()
