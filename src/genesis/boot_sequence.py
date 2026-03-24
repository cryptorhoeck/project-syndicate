"""
Project Syndicate — Boot Sequence Orchestrator

Manages the cold start boot sequence for Gen 1 agents:
  - Wave 1: 2 Scouts (the eyes)
  - Wave 2: 1 Strategist (the brain) — after Scouts are oriented
  - Wave 3: 1 Critic + 1 Operator (the gatekeepers) — after Strategist is oriented

Each agent goes through orientation before the next wave can spawn.
21-day survival clock for Gen 1.
"""

__version__ = "1.2.0"

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from src.agora.schemas import AgoraMessage
from src.common.config import config
from src.common.models import Agent, BootSequenceLog, Dynasty, Lineage

logger = logging.getLogger(__name__)

# Gen 1 survival clock (longer than default to allow learning)
GEN1_SURVIVAL_DAYS = 21


# Wave definitions: wave_number → list of agent specs
SPAWN_WAVES: dict[int, list[dict[str, str]]] = {
    1: [
        {"name": "Scout-Alpha", "type": "scout", "mandate": "Crypto market scanner — find opportunities in top-10 markets"},
        {"name": "Scout-Beta", "type": "scout", "mandate": "Broader opportunity scanner — watch mid-cap and trending pairs"},
    ],
    2: [
        {"name": "Strategist-Prime", "type": "strategist", "mandate": "Strategy builder — turn Scout intel into actionable trading plans"},
    ],
    3: [
        {"name": "Critic-One", "type": "critic", "mandate": "Plan stress-tester — approve only sound plans, catch fatal flaws"},
        {"name": "Operator-Genesis", "type": "operator", "mandate": "First execution agent — disciplined trade execution on approved plans"},
    ],
}


class BootSequenceOrchestrator:
    """Orchestrates the Gen 1 cold start boot sequence.

    Manages condition-based spawn waves where each wave's agents
    must complete orientation before the next wave can spawn.
    """

    def __init__(
        self,
        db_session_factory: sessionmaker,
        orientation_protocol=None,
        agora_service=None,
        economy_service=None,
    ):
        self.db_factory = db_session_factory
        self.orientation = orientation_protocol
        self.agora = agora_service
        self.economy = economy_service

    async def run_boot_sequence(self) -> dict[str, Any]:
        """Run the full boot sequence.

        Spawns agents in waves, orienting each before proceeding.
        Can be called multiple times — it's idempotent and resumes
        from wherever it left off.

        Returns:
            Dict with boot sequence results.
        """
        result: dict[str, Any] = {
            "waves_completed": [],
            "agents_spawned": [],
            "agents_oriented": [],
            "orientation_failures": [],
            "status": "in_progress",
        }

        # Check if boot sequence is needed
        with self.db_factory() as session:
            active_count = session.execute(
                select(func.count()).where(Agent.status.in_(["active", "initializing"]))
            ).scalar() or 0

            # If we already have all 5 Gen 1 agents active, we're done
            gen1_active = session.execute(
                select(func.count()).where(
                    Agent.generation == 1,
                    Agent.status == "active",
                    Agent.orientation_completed == True,
                )
            ).scalar() or 0

        if gen1_active >= 5:
            result["status"] = "already_complete"
            return result

        # Process each wave
        for wave_num in sorted(SPAWN_WAVES.keys()):
            wave_result = await self._process_wave(wave_num)
            result["waves_completed"].append(wave_num)
            result["agents_spawned"].extend(wave_result.get("spawned", []))
            result["agents_oriented"].extend(wave_result.get("oriented", []))
            result["orientation_failures"].extend(wave_result.get("failures", []))

            # If any orientation failed in this wave, stop
            if wave_result.get("failures"):
                result["status"] = "orientation_failure"
                logger.warning(
                    f"Boot sequence paused at wave {wave_num}: orientation failures"
                )
                return result

        result["status"] = "complete"

        # Post Genesis Record Zero to Agora
        await self._post_genesis_record(result)

        logger.info(f"Boot sequence complete: {len(result['agents_spawned'])} agents spawned")
        return result

    async def _process_wave(self, wave_num: int) -> dict[str, Any]:
        """Process a single spawn wave.

        Args:
            wave_num: The wave number to process.

        Returns:
            Dict with wave results.
        """
        wave_result: dict[str, Any] = {
            "wave": wave_num,
            "spawned": [],
            "oriented": [],
            "failures": [],
        }

        specs = SPAWN_WAVES.get(wave_num, [])
        if not specs:
            return wave_result

        # Check wave preconditions
        if not self._check_wave_preconditions(wave_num):
            logger.info(f"Wave {wave_num} preconditions not met, skipping")
            return wave_result

        with self.db_factory() as session:
            for spec in specs:
                # Check if agent already exists
                existing = session.execute(
                    select(Agent).where(
                        Agent.name == spec["name"],
                        Agent.generation == 1,
                    )
                ).scalar_one_or_none()

                if existing and existing.orientation_completed:
                    wave_result["oriented"].append({
                        "agent_id": existing.id,
                        "name": existing.name,
                        "status": "already_oriented",
                    })
                    continue

                if existing and not existing.orientation_completed:
                    agent = existing
                else:
                    # Spawn the agent
                    agent = self._spawn_agent(session, spec, wave_num)
                    wave_result["spawned"].append({
                        "agent_id": agent.id,
                        "name": agent.name,
                        "type": agent.type,
                        "capital": round(agent.capital_allocated, 2),
                    })
                    self._log_event(session, wave_num, "spawn", agent)

                session.commit()

            # Orient each agent in this wave
            for spec in specs:
                agent = session.execute(
                    select(Agent).where(
                        Agent.name == spec["name"],
                        Agent.generation == 1,
                    )
                ).scalar_one_or_none()

                if not agent or agent.orientation_completed:
                    continue

                orient_result = await self._orient_agent(session, agent, wave_num)
                if orient_result["success"]:
                    wave_result["oriented"].append(orient_result)
                else:
                    wave_result["failures"].append(orient_result)

                session.commit()

        return wave_result

    def _check_wave_preconditions(self, wave_num: int) -> bool:
        """Check if a wave's preconditions are met.

        Args:
            wave_num: The wave number.

        Returns:
            True if the wave can proceed.
        """
        if wave_num == 1:
            # Wave 1: just need sufficient capital
            return True

        with self.db_factory() as session:
            if wave_num == 2:
                # Wave 2: need Wave 1 scouts oriented
                oriented_scouts = session.execute(
                    select(func.count()).where(
                        Agent.type == "scout",
                        Agent.generation == 1,
                        Agent.orientation_completed == True,
                    )
                ).scalar() or 0
                return oriented_scouts >= 2

            if wave_num == 3:
                # Wave 3: need Wave 2 strategist oriented
                oriented_strategists = session.execute(
                    select(func.count()).where(
                        Agent.type == "strategist",
                        Agent.generation == 1,
                        Agent.orientation_completed == True,
                    )
                ).scalar() or 0
                return oriented_strategists >= 1

        return False

    def _spawn_agent(
        self, session: Session, spec: dict, wave_num: int
    ) -> Agent:
        """Spawn a single Gen 1 agent.

        Args:
            session: DB session.
            spec: Agent specification dict.
            wave_num: Which wave this agent belongs to.

        Returns:
            The created Agent.
        """
        now = datetime.now(timezone.utc)

        # Calculate capital per agent
        # Treasury is in CAD → convert to USDT for agent capital
        from src.common.models import SystemState
        from src.common.currency_service import CurrencyService
        state = session.execute(select(SystemState).limit(1)).scalar_one_or_none()
        total_treasury_cad = state.total_treasury if state else config.starting_treasury

        # Reserve ratio, then split among 5 agents, then convert to USDT
        available_cad = total_treasury_cad * (1 - config.treasury_reserve_ratio)
        per_agent_cad = available_cad / 5
        cs = CurrencyService()
        per_agent = cs.cad_to_usdt(per_agent_cad)  # Agent capital in USDT

        agent = Agent(
            name=spec["name"],
            type=spec["type"],
            status="initializing",
            generation=1,
            capital_allocated=per_agent,
            capital_current=per_agent,
            cash_balance=per_agent,  # Available trading capital (USDT)
            thinking_budget_daily=config.new_agent_daily_thinking_budget,
            strategy_summary=spec["mandate"],
            survival_clock_start=now,
            survival_clock_end=now + timedelta(days=GEN1_SURVIVAL_DAYS),
            spawn_wave=wave_num,
        )
        session.add(agent)
        session.flush()

        # Create dynasty for Gen 1 agent (each founder starts their own dynasty)
        dynasty = Dynasty(
            founder_id=agent.id,
            founder_name=agent.name,
            founder_role=agent.type,
            dynasty_name=f"Dynasty {agent.name}",
            status="active",
            total_generations=1,
            total_members=1,
            living_members=1,
            peak_members=1,
        )
        session.add(dynasty)
        session.flush()

        agent.dynasty_id = dynasty.id
        session.add(agent)

        # Create lineage record
        lineage = Lineage(
            agent_id=agent.id,
            agent_name=agent.name,
            parent_id=None,
            generation=1,
            lineage_path=str(agent.id),
            dynasty_id=dynasty.id,
        )
        session.add(lineage)
        session.flush()

        # Phase 8C: Create initial genome for Gen 1 agent
        try:
            from src.genome.genome_manager import GenomeManager
            import asyncio
            genome_mgr = GenomeManager()
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    # Schedule as task — will run in the current event loop
                    asyncio.ensure_future(genome_mgr.create_genome(
                        agent_id=agent.id, role=agent.type, db_session=session,
                    ))
                else:
                    loop.run_until_complete(genome_mgr.create_genome(
                        agent_id=agent.id, role=agent.type, db_session=session,
                    ))
            except RuntimeError:
                # No event loop — create genome synchronously
                from src.genome.genome_schema import create_random_genome
                from src.common.models import AgentGenome
                genome_data = create_random_genome(agent.type)
                session.add(AgentGenome(
                    agent_id=agent.id, genome_version=1, genome_data=genome_data,
                ))
                session.flush()
        except Exception as e:
            logger.warning(f"Genome creation failed for {agent.name}: {e}")

        logger.info(
            f"Spawned {spec['name']} (wave {wave_num}): "
            f"C${per_agent_cad:.2f} ({per_agent:.2f} USDT), {GEN1_SURVIVAL_DAYS}-day clock"
        )

        return agent

    async def _orient_agent(
        self, session: Session, agent: Agent, wave_num: int
    ) -> dict:
        """Run orientation for an agent.

        Args:
            session: DB session.
            agent: The agent to orient.
            wave_num: Current wave number.

        Returns:
            Dict with orientation result.
        """
        self._log_event(session, wave_num, "orientation_start", agent)
        session.flush()

        if not self.orientation:
            # No orientation protocol — mark as complete with defaults
            agent.orientation_completed = True
            agent.status = "active"
            agent.initial_watchlist = []
            session.add(agent)
            session.flush()
            self._log_event(session, wave_num, "orientation_complete", agent,
                            "No orientation protocol — auto-passed")
            return {
                "agent_id": agent.id,
                "name": agent.name,
                "success": True,
                "status": "auto_passed",
            }

        result = await self.orientation.orient_agent(agent)

        if result.success:
            # Ensure orientation flags are set (in case orientation protocol
            # ran in a different session or is mocked)
            agent.orientation_completed = True
            agent.orientation_failed = False
            agent.status = "active"
            if hasattr(result, 'initial_watchlist') and result.initial_watchlist:
                agent.initial_watchlist = result.initial_watchlist
                agent.watched_markets = result.initial_watchlist
            session.add(agent)
            session.flush()

            self._log_event(session, wave_num, "orientation_complete", agent,
                            f"watchlist={getattr(result, 'initial_watchlist', [])}")

            # Initialize reputation if economy service available
            if self.economy:
                try:
                    await self.economy.initialize_agent_reputation(agent.id)
                except Exception as e:
                    logger.warning(f"Rep init failed for {agent.name}: {e}")

            return {
                "agent_id": agent.id,
                "name": agent.name,
                "success": True,
                "watchlist": result.initial_watchlist,
                "api_cost": result.api_cost,
            }
        else:
            self._log_event(session, wave_num, "orientation_failed", agent,
                            result.failure_reason)
            return {
                "agent_id": agent.id,
                "name": agent.name,
                "success": False,
                "failure_reason": result.failure_reason,
            }

    def _log_event(
        self,
        session: Session,
        wave_num: int,
        event_type: str,
        agent: Agent | None = None,
        details: str | None = None,
    ) -> None:
        """Log a boot sequence event.

        Args:
            session: DB session.
            wave_num: Wave number.
            event_type: Type of event.
            agent: Optional agent involved.
            details: Optional details text.
        """
        log = BootSequenceLog(
            wave_number=wave_num,
            event_type=event_type,
            agent_id=agent.id if agent else None,
            agent_name=agent.name if agent else None,
            details=details,
        )
        session.add(log)

    async def _post_genesis_record(self, result: dict) -> None:
        """Post Genesis Record Zero to Agora."""
        if not self.agora:
            return

        spawned_names = [a["name"] for a in result.get("agents_spawned", [])]
        try:
            await self.agora.post_message(AgoraMessage(
                agent_id=0,
                agent_name="Genesis",
                channel="genesis-log",
                content=(
                    f"GENESIS RECORD ZERO: Boot sequence complete. "
                    f"{len(spawned_names)} Gen 1 agents spawned: {', '.join(spawned_names)}. "
                    f"Waves completed: {result.get('waves_completed', [])}"
                ),
                message_type="system",
                importance=2,
                metadata={"boot_result": result},
            ))
        except Exception as e:
            logger.warning(f"Failed to post Genesis Record Zero: {e}")

    def get_boot_status(self) -> dict[str, Any]:
        """Get the current boot sequence status.

        Returns:
            Dict with wave status, agent states, etc.
        """
        with self.db_factory() as session:
            gen1_agents = session.execute(
                select(Agent).where(Agent.generation == 1)
            ).scalars().all()

            waves = {}
            for wave_num, specs in SPAWN_WAVES.items():
                wave_agents = [a for a in gen1_agents if a.spawn_wave == wave_num]
                waves[wave_num] = {
                    "expected": len(specs),
                    "spawned": len(wave_agents),
                    "oriented": sum(1 for a in wave_agents if a.orientation_completed),
                    "failed": sum(1 for a in wave_agents if a.orientation_failed),
                    "agents": [
                        {
                            "name": a.name,
                            "type": a.type,
                            "status": a.status,
                            "oriented": a.orientation_completed,
                        }
                        for a in wave_agents
                    ],
                }

            logs = session.execute(
                select(BootSequenceLog)
                .order_by(BootSequenceLog.timestamp.desc())
                .limit(20)
            ).scalars().all()

        return {
            "waves": waves,
            "total_spawned": sum(w["spawned"] for w in waves.values()),
            "total_oriented": sum(w["oriented"] for w in waves.values()),
            "recent_events": [
                {
                    "wave": log.wave_number,
                    "event": log.event_type,
                    "agent": log.agent_name,
                    "details": log.details,
                    "timestamp": log.timestamp.isoformat() if log.timestamp else None,
                }
                for log in logs
            ],
        }
