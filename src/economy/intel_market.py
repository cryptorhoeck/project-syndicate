"""
Project Syndicate — Intel Market

Scouts post intel signals; other agents endorse them by staking reputation.
Settlement is handled by the SettlementEngine after signal expiry.
"""

__version__ = "0.5.0"

from datetime import datetime, timedelta, timezone
from typing import Optional, TYPE_CHECKING

import structlog
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from src.common.models import Agent, IntelSignal, IntelEndorsement

if TYPE_CHECKING:
    from src.agora.agora_service import AgoraService
    from src.economy.economy_service import EconomyService

logger = structlog.get_logger()


def _utcnow_naive() -> datetime:
    """UTC now without timezone info (for DB compatibility with SQLite)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class IntelMarket:
    """Intel signal marketplace — agents share and endorse trading signals."""

    def __init__(
        self,
        db_session_factory: sessionmaker,
        economy_service: "EconomyService",
        agora_service: Optional["AgoraService"] = None,
    ) -> None:
        self.db = db_session_factory
        self.economy = economy_service
        self.agora = agora_service
        self.log = logger.bind(component="intel_market")

    # ──────────────────────────────────────────────
    # SIGNAL CREATION
    # ──────────────────────────────────────────────

    async def create_signal(
        self,
        scout_agent_id: int,
        scout_agent_name: str,
        message_id: int,
        asset: str,
        direction: str,
        confidence_level: int,
        price_at_creation: float,
        expires_at: datetime,
    ) -> Optional[IntelSignal]:
        """Create a new intel signal linked to an Agora message."""
        # Validate reputation
        balance = await self.economy.get_balance(scout_agent_id)
        if balance < self.economy.MIN_REPUTATION_FOR_INTEL:
            self.log.warning(
                "signal_low_reputation",
                agent_id=scout_agent_id,
                balance=balance,
                required=self.economy.MIN_REPUTATION_FOR_INTEL,
            )
            return None

        # Validate asset format
        if "/" not in asset:
            self.log.warning("signal_invalid_asset", asset=asset)
            return None

        # Validate expiry (strip tzinfo for DB compatibility)
        now = _utcnow_naive()
        expires_naive = expires_at.replace(tzinfo=None) if expires_at.tzinfo else expires_at
        if expires_naive <= now:
            self.log.warning("signal_past_expiry", expires_at=expires_at)
            return None
        if expires_naive > now + timedelta(days=7):
            self.log.warning("signal_expiry_too_far", expires_at=expires_at)
            return None

        # Clamp confidence
        confidence_level = max(1, min(5, confidence_level))

        with self.db() as session:
            signal = IntelSignal(
                message_id=message_id,
                scout_agent_id=scout_agent_id,
                scout_agent_name=scout_agent_name,
                asset=asset,
                direction=direction,
                confidence_level=confidence_level,
                price_at_creation=price_at_creation,
                expires_at=expires_at,
                status="active",
            )
            session.add(signal)
            session.commit()
            signal_id = signal.id

        await self._post_to_agora(
            "trade-signals",
            f"Intel Signal from {scout_agent_name}: {asset} {direction} "
            f"(confidence: {confidence_level}/5). Expires {expires_at.isoformat()}.",
            message_type="signal",
            metadata={"signal_id": signal_id, "asset": asset, "direction": direction},
        )
        self.log.info(
            "signal_created",
            signal_id=signal_id,
            scout_id=scout_agent_id,
            asset=asset,
            direction=direction,
        )

        # Re-load detached to return
        with self.db() as session:
            return session.get(IntelSignal, signal_id)

    # ──────────────────────────────────────────────
    # ENDORSEMENT
    # ──────────────────────────────────────────────

    async def endorse_signal(
        self,
        signal_id: int,
        endorser_agent_id: int,
        endorser_agent_name: str,
        stake_amount: float,
    ) -> Optional[IntelEndorsement]:
        """Endorse an intel signal by staking reputation."""
        with self.db() as session:
            signal = session.get(IntelSignal, signal_id)
            if signal is None or signal.status != "active":
                self.log.warning("endorse_invalid_signal", signal_id=signal_id)
                return None

            # Check expiry (DB may return naive datetime)
            expires = signal.expires_at.replace(tzinfo=None) if signal.expires_at.tzinfo else signal.expires_at
            if expires <= _utcnow_naive():
                self.log.warning("endorse_expired_signal", signal_id=signal_id)
                return None

            # Can't endorse own signal
            if signal.scout_agent_id == endorser_agent_id:
                self.log.warning("endorse_own_signal", signal_id=signal_id, agent_id=endorser_agent_id)
                return None

            scout_name = signal.scout_agent_name
            asset = signal.asset

        # Check for duplicate endorsement
        with self.db() as session:
            existing = session.execute(
                select(IntelEndorsement).where(
                    IntelEndorsement.signal_id == signal_id,
                    IntelEndorsement.endorser_agent_id == endorser_agent_id,
                )
            ).scalar_one_or_none()
            if existing is not None:
                self.log.warning("endorse_duplicate", signal_id=signal_id, agent_id=endorser_agent_id)
                return None

        # Validate stake range
        if stake_amount < self.economy.MIN_ENDORSEMENT_STAKE:
            self.log.warning("endorse_below_min_stake", stake=stake_amount)
            return None
        if stake_amount > self.economy.MAX_ENDORSEMENT_STAKE:
            self.log.warning("endorse_above_max_stake", stake=stake_amount)
            return None

        # Check reputation
        balance = await self.economy.get_balance(endorser_agent_id)
        if balance < self.economy.MIN_REPUTATION_FOR_ENDORSEMENT:
            self.log.warning("endorse_low_reputation", agent_id=endorser_agent_id, balance=balance)
            return None
        if balance < stake_amount:
            self.log.warning("endorse_insufficient_balance", agent_id=endorser_agent_id, balance=balance)
            return None

        # Escrow the stake
        escrowed = await self.economy.escrow_reputation(
            endorser_agent_id, stake_amount, f"intel_endorsement:{signal_id}"
        )
        if not escrowed:
            return None

        with self.db() as session:
            endorsement = IntelEndorsement(
                signal_id=signal_id,
                endorser_agent_id=endorser_agent_id,
                endorser_agent_name=endorser_agent_name,
                stake_amount=stake_amount,
                settlement_status="pending",
            )
            session.add(endorsement)

            # Update signal counters
            sig = session.get(IntelSignal, signal_id)
            if sig:
                sig.endorsement_count = (sig.endorsement_count or 0) + 1
                sig.total_endorsement_stake = (sig.total_endorsement_stake or 0) + stake_amount

            session.commit()
            endorsement_id = endorsement.id

        await self._post_to_agora(
            "trade-signals",
            f"{endorser_agent_name} endorsed {scout_name}'s {asset} signal (staked {stake_amount:.1f} rep)",
            message_type="economy",
        )
        self.log.info(
            "signal_endorsed",
            signal_id=signal_id,
            endorser_id=endorser_agent_id,
            stake=stake_amount,
        )

        with self.db() as session:
            return session.get(IntelEndorsement, endorsement_id)

    # ──────────────────────────────────────────────
    # TRADE LINKING
    # ──────────────────────────────────────────────

    async def link_trade_to_endorsement(
        self,
        endorser_agent_id: int,
        signal_id: int,
        trade_id: int,
    ) -> bool:
        """Link a completed trade to an endorsement for trade-based settlement."""
        with self.db() as session:
            endorsement = session.execute(
                select(IntelEndorsement).where(
                    IntelEndorsement.signal_id == signal_id,
                    IntelEndorsement.endorser_agent_id == endorser_agent_id,
                )
            ).scalar_one_or_none()

            if endorsement is None or endorsement.settlement_status != "pending":
                return False

            endorsement.linked_trade_id = trade_id
            session.commit()

        self.log.info(
            "trade_linked",
            signal_id=signal_id,
            endorser_id=endorser_agent_id,
            trade_id=trade_id,
        )
        return True

    # ──────────────────────────────────────────────
    # QUERIES
    # ──────────────────────────────────────────────

    async def get_active_signals(
        self,
        asset: Optional[str] = None,
        scout_id: Optional[int] = None,
    ) -> list[IntelSignal]:
        """Get all active (unsettled, unexpired) signals."""
        now = _utcnow_naive()
        with self.db() as session:
            stmt = (
                select(IntelSignal)
                .where(IntelSignal.status == "active", IntelSignal.expires_at > now)
            )
            if asset is not None:
                stmt = stmt.where(IntelSignal.asset == asset)
            if scout_id is not None:
                stmt = stmt.where(IntelSignal.scout_agent_id == scout_id)
            stmt = stmt.order_by(IntelSignal.created_at.desc())
            return list(session.execute(stmt).scalars().all())

    async def get_signals_ready_for_settlement(self) -> list[IntelSignal]:
        """Get signals that have expired and need settlement processing."""
        now = _utcnow_naive()
        with self.db() as session:
            return list(
                session.execute(
                    select(IntelSignal)
                    .where(IntelSignal.status == "active", IntelSignal.expires_at <= now)
                ).scalars().all()
            )

    async def get_endorsements_for_signal(self, signal_id: int) -> list[IntelEndorsement]:
        """Get all endorsements for a specific signal."""
        with self.db() as session:
            return list(
                session.execute(
                    select(IntelEndorsement)
                    .where(IntelEndorsement.signal_id == signal_id)
                ).scalars().all()
            )

    async def get_agent_signal_stats(self, agent_id: int) -> dict:
        """Get intel signal statistics for an agent (as scout)."""
        with self.db() as session:
            total_signals = session.execute(
                select(func.count()).select_from(IntelSignal)
                .where(IntelSignal.scout_agent_id == agent_id)
            ).scalar() or 0

            total_endorsements = session.execute(
                select(func.coalesce(func.sum(IntelSignal.endorsement_count), 0))
                .where(IntelSignal.scout_agent_id == agent_id)
            ).scalar() or 0

            avg_stake = session.execute(
                select(func.coalesce(func.avg(IntelEndorsement.stake_amount), 0))
                .join(IntelSignal, IntelEndorsement.signal_id == IntelSignal.id)
                .where(IntelSignal.scout_agent_id == agent_id)
            ).scalar() or 0

            profitable = session.execute(
                select(func.count()).select_from(IntelSignal)
                .where(
                    IntelSignal.scout_agent_id == agent_id,
                    IntelSignal.status == "settled_profitable",
                )
            ).scalar() or 0

            unprofitable = session.execute(
                select(func.count()).select_from(IntelSignal)
                .where(
                    IntelSignal.scout_agent_id == agent_id,
                    IntelSignal.status == "settled_unprofitable",
                )
            ).scalar() or 0

        return {
            "total_signals": total_signals,
            "total_endorsements": total_endorsements,
            "avg_endorsement_stake": float(avg_stake),
            "profitable_signals": profitable,
            "unprofitable_signals": unprofitable,
        }

    # ──────────────────────────────────────────────
    # Helpers
    # ──────────────────────────────────────────────

    async def _post_to_agora(
        self,
        channel: str,
        content: str,
        message_type: str = "economy",
        metadata: dict | None = None,
    ) -> None:
        if self.agora is None:
            return
        from src.agora.schemas import AgoraMessage, MessageType
        type_map = {
            "economy": MessageType.ECONOMY,
            "signal": MessageType.SIGNAL,
            "system": MessageType.SYSTEM,
        }
        mt = type_map.get(message_type, MessageType.ECONOMY)
        msg = AgoraMessage(
            agent_id=0, agent_name="IntelMarket", channel=channel,
            content=content, message_type=mt, metadata=metadata or {},
        )
        try:
            await self.agora.post_message(msg)
        except Exception as exc:
            self.log.warning("agora_post_failed", channel=channel, error=str(exc))
