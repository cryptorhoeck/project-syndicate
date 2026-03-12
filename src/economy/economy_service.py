"""
Project Syndicate — Economy Service

Central orchestrator for the Internal Economy. Handles reputation management
and delegates to market-specific modules.
"""

__version__ = "0.5.0"

from datetime import datetime, timedelta, timezone
from typing import Optional, TYPE_CHECKING

import structlog
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from src.common.models import Agent, ReputationTransaction
from src.economy.schemas import EconomyStats

if TYPE_CHECKING:
    from src.agora.agora_service import AgoraService
    from src.economy.intel_market import IntelMarket
    from src.economy.review_market import ReviewMarket
    from src.economy.service_market import ServiceMarket
    from src.economy.settlement_engine import SettlementEngine
    from src.economy.gaming_detection import GamingDetector

logger = structlog.get_logger()


class EconomyService:
    """Core service for all Economy operations."""

    STARTING_REPUTATION = 100.0
    NEGATIVE_REPUTATION_THRESHOLD = -50.0
    MIN_REPUTATION_FOR_INTEL = 50.0
    MIN_REPUTATION_FOR_ENDORSEMENT = 25.0
    MIN_ENDORSEMENT_STAKE = 5.0
    MAX_ENDORSEMENT_STAKE = 25.0
    MIN_REVIEW_BUDGET = 10.0
    MAX_REVIEW_BUDGET = 25.0

    def __init__(
        self,
        db_session_factory: sessionmaker,
        agora_service: Optional["AgoraService"] = None,
        exchange_service=None,
    ) -> None:
        self.db = db_session_factory
        self.agora = agora_service
        self.exchange = exchange_service
        self.log = logger.bind(component="economy")

        # Lazy imports to avoid circular deps
        from src.economy.intel_market import IntelMarket
        from src.economy.review_market import ReviewMarket
        from src.economy.service_market import ServiceMarket
        from src.economy.settlement_engine import SettlementEngine
        from src.economy.gaming_detection import GamingDetector

        self.intel_market = IntelMarket(db_session_factory, self, agora_service)
        self.review_market = ReviewMarket(db_session_factory, self, agora_service)
        self.service_market = ServiceMarket(db_session_factory, agora_service)
        self.settlement_engine = SettlementEngine(db_session_factory, self, exchange_service, agora_service)
        self.gaming_detector = GamingDetector(db_session_factory, agora_service)

    # ──────────────────────────────────────────────
    # REPUTATION MANAGEMENT
    # ──────────────────────────────────────────────

    async def initialize_agent_reputation(self, agent_id: int) -> None:
        """Called when a new agent is spawned. Sets starting reputation to 100."""
        with self.db() as session:
            agent = session.get(Agent, agent_id)
            if agent is None:
                return
            agent.reputation_score = self.STARTING_REPUTATION
            session.add(ReputationTransaction(
                from_agent_id=None,
                to_agent_id=agent_id,
                amount=self.STARTING_REPUTATION,
                reason="initial_balance",
            ))
            session.commit()
        self.log.info("reputation_initialized", agent_id=agent_id, amount=self.STARTING_REPUTATION)

    async def get_balance(self, agent_id: int) -> float:
        """Get an agent's current reputation balance."""
        with self.db() as session:
            agent = session.get(Agent, agent_id)
            if agent is None:
                return 0.0
            return agent.reputation_score or 0.0

    async def transfer_reputation(
        self,
        from_agent_id: int,
        to_agent_id: int,
        amount: float,
        reason: str,
        related_trade_id: int | None = None,
    ) -> bool:
        """Transfer reputation between agents. Returns True if successful."""
        with self.db() as session:
            sender = session.get(Agent, from_agent_id)
            receiver = session.get(Agent, to_agent_id)
            if sender is None or receiver is None:
                return False

            if (sender.reputation_score or 0) < amount:
                self.log.warning("transfer_insufficient", from_id=from_agent_id, amount=amount)
                return False

            sender.reputation_score = (sender.reputation_score or 0) - amount
            receiver.reputation_score = (receiver.reputation_score or 0) + amount

            sender_name = sender.name
            receiver_name = receiver.name
            sender_rep = sender.reputation_score

            session.add(ReputationTransaction(
                from_agent_id=from_agent_id,
                to_agent_id=to_agent_id,
                amount=amount,
                reason=reason,
                related_trade_id=related_trade_id,
            ))
            session.commit()

        await self._post_to_agora(
            "agent-chat",
            f"{sender_name} -> {receiver_name}: {amount:.1f} rep ({reason})",
            message_type="economy",
        )

        if sender_rep < self.NEGATIVE_REPUTATION_THRESHOLD:
            await self._post_to_agora(
                "genesis-log",
                f"ALERT: {sender_name} reputation dropped to {sender_rep:.1f} (below {self.NEGATIVE_REPUTATION_THRESHOLD})",
                message_type="alert",
                importance=2,
            )

        self.log.info("reputation_transferred", from_id=from_agent_id, to_id=to_agent_id, amount=amount, reason=reason)
        return True

    async def apply_reward(self, agent_id: int, amount: float, reason: str) -> None:
        """Give reputation to an agent (from the system)."""
        with self.db() as session:
            agent = session.get(Agent, agent_id)
            if agent is None:
                return
            agent.reputation_score = (agent.reputation_score or 0) + amount
            session.add(ReputationTransaction(
                from_agent_id=0,
                to_agent_id=agent_id,
                amount=amount,
                reason=reason,
            ))
            session.commit()
        self.log.info("reputation_reward", agent_id=agent_id, amount=amount, reason=reason)

    async def apply_penalty(self, agent_id: int, amount: float, reason: str) -> None:
        """Deduct reputation from an agent (system penalty)."""
        with self.db() as session:
            agent = session.get(Agent, agent_id)
            if agent is None:
                return
            agent.reputation_score = (agent.reputation_score or 0) - amount
            new_rep = agent.reputation_score
            session.add(ReputationTransaction(
                from_agent_id=agent_id,
                to_agent_id=0,
                amount=amount,
                reason=reason,
            ))
            session.commit()

        if new_rep < self.NEGATIVE_REPUTATION_THRESHOLD:
            await self._post_to_agora(
                "genesis-log",
                f"ALERT: Agent {agent_id} reputation at {new_rep:.1f} after penalty ({reason})",
                message_type="alert",
                importance=2,
            )
        self.log.info("reputation_penalty", agent_id=agent_id, amount=amount, reason=reason)

    async def escrow_reputation(self, agent_id: int, amount: float, reason: str) -> bool:
        """Hold reputation in escrow (deduct from balance)."""
        with self.db() as session:
            agent = session.get(Agent, agent_id)
            if agent is None:
                return False
            if (agent.reputation_score or 0) < amount:
                self.log.warning("escrow_insufficient", agent_id=agent_id, amount=amount)
                return False
            agent.reputation_score = (agent.reputation_score or 0) - amount
            session.add(ReputationTransaction(
                from_agent_id=agent_id,
                to_agent_id=0,
                amount=amount,
                reason=f"escrow:{reason}",
            ))
            session.commit()
        self.log.info("reputation_escrowed", agent_id=agent_id, amount=amount, reason=reason)
        return True

    async def release_escrow(self, agent_id: int, amount: float, reason: str) -> None:
        """Refund escrowed reputation back to the agent."""
        with self.db() as session:
            agent = session.get(Agent, agent_id)
            if agent is None:
                return
            agent.reputation_score = (agent.reputation_score or 0) + amount
            session.add(ReputationTransaction(
                from_agent_id=0,
                to_agent_id=agent_id,
                amount=amount,
                reason=f"escrow_release:{reason}",
            ))
            session.commit()
        self.log.info("escrow_released", agent_id=agent_id, amount=amount, reason=reason)

    async def get_transaction_history(self, agent_id: int, limit: int = 50) -> list[dict]:
        """Get reputation transaction history for an agent."""
        with self.db() as session:
            txns = session.execute(
                select(ReputationTransaction)
                .where(
                    (ReputationTransaction.from_agent_id == agent_id)
                    | (ReputationTransaction.to_agent_id == agent_id)
                )
                .order_by(ReputationTransaction.timestamp.desc())
                .limit(limit)
            ).scalars().all()
            return [
                {
                    "id": t.id,
                    "from_agent_id": t.from_agent_id,
                    "to_agent_id": t.to_agent_id,
                    "amount": t.amount,
                    "reason": t.reason,
                    "timestamp": t.timestamp,
                }
                for t in txns
            ]

    async def check_negative_reputation_agents(self) -> list[int]:
        """Get all agents below the negative reputation threshold."""
        with self.db() as session:
            agents = session.execute(
                select(Agent.id).where(
                    Agent.reputation_score < self.NEGATIVE_REPUTATION_THRESHOLD,
                    Agent.status == "active",
                )
            ).scalars().all()
            return list(agents)

    # ──────────────────────────────────────────────
    # DELEGATED MARKET OPERATIONS
    # ──────────────────────────────────────────────

    async def create_intel_signal(self, **kwargs):
        return await self.intel_market.create_signal(**kwargs)

    async def endorse_intel(self, **kwargs):
        return await self.intel_market.endorse_signal(**kwargs)

    async def link_trade_to_endorsement(self, **kwargs):
        return await self.intel_market.link_trade_to_endorsement(**kwargs)

    async def get_active_signals(self, **kwargs):
        return await self.intel_market.get_active_signals(**kwargs)

    async def request_review(self, **kwargs):
        return await self.review_market.request_review(**kwargs)

    async def accept_review(self, **kwargs):
        return await self.review_market.accept_review(**kwargs)

    async def submit_review(self, **kwargs):
        return await self.review_market.submit_review(**kwargs)

    async def get_open_review_requests(self, **kwargs):
        return await self.review_market.get_open_requests(**kwargs)

    async def create_service_listing(self, **kwargs):
        return await self.service_market.create_listing(**kwargs)

    async def get_service_listings(self, **kwargs):
        return await self.service_market.get_listings(**kwargs)

    async def cancel_service_listing(self, **kwargs):
        return await self.service_market.cancel_listing(**kwargs)

    async def run_settlement_cycle(self):
        return await self.settlement_engine.run_settlement_cycle()

    async def run_gaming_detection(self, **kwargs):
        return await self.gaming_detector.run_full_detection(**kwargs)

    async def get_unresolved_flags(self):
        return await self.gaming_detector.get_unresolved_flags()

    # ──────────────────────────────────────────────
    # STATS
    # ──────────────────────────────────────────────

    async def get_economy_stats(self) -> EconomyStats:
        """Aggregate economy statistics for the daily report."""
        now = datetime.now(timezone.utc)
        day_ago = now - timedelta(hours=24)

        from src.common.models import (
            IntelSignal as ISModel,
            IntelEndorsement as IEModel,
            ReviewRequest as RRModel,
            ReviewAssignment as RAModel,
            GamingFlag as GFModel,
        )

        with self.db() as session:
            # Total reputation in circulation
            total_rep = session.execute(
                select(func.coalesce(func.sum(Agent.reputation_score), 0))
                .where(Agent.status.in_(["active", "hibernating"]))
            ).scalar() or 0

            # Escrow estimation (sum of pending endorsement stakes)
            escrow = session.execute(
                select(func.coalesce(func.sum(IEModel.stake_amount), 0))
                .where(IEModel.settlement_status == "pending")
            ).scalar() or 0

            # Active signals
            active_signals = session.execute(
                select(func.count()).select_from(ISModel)
                .where(ISModel.status == "active")
            ).scalar() or 0

            # 24h endorsements
            endorsements_24h = session.execute(
                select(func.count()).select_from(IEModel)
                .where(IEModel.created_at >= day_ago)
            ).scalar() or 0

            endorsement_stake_24h = session.execute(
                select(func.coalesce(func.sum(IEModel.stake_amount), 0))
                .where(IEModel.created_at >= day_ago)
            ).scalar() or 0

            # Settlements 24h
            settled_24h = session.execute(
                select(func.count()).select_from(ISModel)
                .where(ISModel.settled_at >= day_ago)
            ).scalar() or 0

            profitable_24h = session.execute(
                select(func.count()).select_from(ISModel)
                .where(ISModel.settled_at >= day_ago, ISModel.status == "settled_profitable")
            ).scalar() or 0

            unprofitable_24h = session.execute(
                select(func.count()).select_from(ISModel)
                .where(ISModel.settled_at >= day_ago, ISModel.status == "settled_unprofitable")
            ).scalar() or 0

            # Open reviews
            open_reviews = session.execute(
                select(func.count()).select_from(RRModel)
                .where(RRModel.status == "open")
            ).scalar() or 0

            reviews_completed_24h = session.execute(
                select(func.count()).select_from(RAModel)
                .where(RAModel.completed_at >= day_ago)
            ).scalar() or 0

            # Gaming flags
            flags_unresolved = session.execute(
                select(func.count()).select_from(GFModel)
                .where(GFModel.resolved == False)
            ).scalar() or 0

            # Top agents by reputation
            top_agents = session.execute(
                select(Agent.id, Agent.name, Agent.reputation_score)
                .where(Agent.status == "active", Agent.id != 0)
                .order_by(Agent.reputation_score.desc())
                .limit(5)
            ).all()

        return EconomyStats(
            total_reputation_in_circulation=total_rep,
            total_reputation_in_escrow=escrow,
            active_intel_signals=active_signals,
            total_endorsements_24h=endorsements_24h,
            total_endorsement_stake_24h=endorsement_stake_24h,
            signals_settled_24h=settled_24h,
            profitable_signals_24h=profitable_24h,
            unprofitable_signals_24h=unprofitable_24h,
            open_review_requests=open_reviews,
            reviews_completed_24h=reviews_completed_24h,
            gaming_flags_unresolved=flags_unresolved,
            top_reputation_agents=[
                {"agent_id": a[0], "agent_name": a[1], "reputation_score": a[2]}
                for a in top_agents
            ],
        )

    # ──────────────────────────────────────────────
    # Helpers
    # ──────────────────────────────────────────────

    async def _post_to_agora(
        self, channel: str, content: str, message_type: str = "economy", importance: int = 0,
    ) -> None:
        if self.agora is None:
            return
        from src.agora.schemas import AgoraMessage, MessageType
        type_map = {
            "economy": MessageType.ECONOMY,
            "system": MessageType.SYSTEM,
            "alert": MessageType.ALERT,
            "signal": MessageType.SIGNAL,
        }
        mt = type_map.get(message_type, MessageType.ECONOMY)
        msg = AgoraMessage(
            agent_id=0, agent_name="EconomyService", channel=channel,
            content=content, message_type=mt, importance=importance,
        )
        try:
            await self.agora.post_message(msg)
        except Exception as exc:
            self.log.warning("agora_post_failed", channel=channel, error=str(exc))
