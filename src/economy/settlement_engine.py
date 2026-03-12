"""
Project Syndicate — Settlement Engine

Settles expired intel signals by comparing predicted direction against
actual price movement. Supports hybrid settlement: trade-linked (full
multipliers) and time-based fallback (half multipliers).
"""

__version__ = "0.5.0"

from datetime import datetime, timedelta, timezone
from typing import Optional, TYPE_CHECKING

import structlog
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from src.common.models import IntelSignal, IntelEndorsement, Transaction

if TYPE_CHECKING:
    from src.agora.agora_service import AgoraService
    from src.economy.economy_service import EconomyService

logger = structlog.get_logger()


def _utcnow_naive() -> datetime:
    """UTC now without timezone info (for DB compatibility with SQLite)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class SettlementEngine:
    """Settles intel signals after expiry using live price data."""

    # Direction must move at least this % to count as directional
    DIRECTION_THRESHOLD_PCT = 0.5

    # Trade-linked multipliers (full confidence — endorser actually traded)
    TRADE_LINKED_SCOUT_WIN_MULTIPLIER = 1.0
    TRADE_LINKED_SCOUT_LOSS_MULTIPLIER = 1.0
    TRADE_LINKED_ENDORSER_WIN_BONUS = 2.0

    # Time-based multipliers (less certain — no trade was made)
    TIME_BASED_SCOUT_WIN_MULTIPLIER = 0.5
    TIME_BASED_SCOUT_LOSS_MULTIPLIER = 0.5
    TIME_BASED_ENDORSER_REFUND = True  # Always refund endorser in time-based

    def __init__(
        self,
        db_session_factory: sessionmaker,
        economy_service: "EconomyService",
        exchange_service=None,
        agora_service: Optional["AgoraService"] = None,
    ) -> None:
        self.db = db_session_factory
        self.economy = economy_service
        self.exchange = exchange_service
        self.agora = agora_service
        self.log = logger.bind(component="settlement_engine")

    # ──────────────────────────────────────────────
    # MAIN CYCLE
    # ──────────────────────────────────────────────

    async def run_settlement_cycle(self) -> dict:
        """Process all signals ready for settlement.

        Returns summary: {settled, profitable, unprofitable, expired, errors}
        """
        summary = {"settled": 0, "profitable": 0, "unprofitable": 0, "expired": 0, "errors": 0}

        now = _utcnow_naive()
        with self.db() as session:
            ready = list(
                session.execute(
                    select(IntelSignal)
                    .where(IntelSignal.status == "active", IntelSignal.expires_at <= now)
                ).scalars().all()
            )
            # Capture IDs + data before session closes
            signal_data = [
                {
                    "id": s.id,
                    "endorsement_count": s.endorsement_count or 0,
                    "asset": s.asset,
                    "scout_agent_name": s.scout_agent_name,
                }
                for s in ready
            ]

        for sdata in signal_data:
            try:
                if sdata["endorsement_count"] == 0:
                    # No endorsements — just expire
                    with self.db() as session:
                        sig = session.get(IntelSignal, sdata["id"])
                        if sig:
                            sig.status = "expired_no_endorsements"
                            sig.settled_at = datetime.now(timezone.utc)
                            session.commit()
                    summary["expired"] += 1
                    self.log.info("signal_expired_no_endorsements", signal_id=sdata["id"])
                else:
                    result = await self._settle_signal(sdata["id"])
                    summary["settled"] += 1
                    if result == "profitable":
                        summary["profitable"] += 1
                    elif result == "unprofitable":
                        summary["unprofitable"] += 1
            except Exception as exc:
                summary["errors"] += 1
                self.log.error("settlement_error", signal_id=sdata["id"], error=str(exc))

        if summary["settled"] > 0 or summary["expired"] > 0:
            self.log.info("settlement_cycle_complete", **summary)

        return summary

    # ──────────────────────────────────────────────
    # SINGLE SIGNAL SETTLEMENT
    # ──────────────────────────────────────────────

    async def _settle_signal(self, signal_id: int) -> str:
        """Settle a single signal and all its endorsements. Returns 'profitable' or 'unprofitable'."""

        # Load signal data
        with self.db() as session:
            signal = session.get(IntelSignal, signal_id)
            if signal is None:
                return "error"
            asset = signal.asset
            direction = signal.direction
            price_at_creation = signal.price_at_creation
            scout_agent_id = signal.scout_agent_id
            scout_agent_name = signal.scout_agent_name
            endorsement_count = signal.endorsement_count or 0

        # 1. FETCH CURRENT PRICE
        settlement_price = await self._get_settlement_price(asset)
        if settlement_price is None:
            # Extend expiry by 1 hour — retry next cycle
            with self.db() as session:
                sig = session.get(IntelSignal, signal_id)
                if sig:
                    sig.expires_at = sig.expires_at + timedelta(hours=1)
                    session.commit()
            self.log.warning("settlement_price_unavailable", signal_id=signal_id, asset=asset)
            return "deferred"

        # 2. CALCULATE PRICE CHANGE
        price_change_pct = ((settlement_price - price_at_creation) / price_at_creation) * 100

        # 3. DETERMINE IF SIGNAL WAS CORRECT
        if direction == "bullish":
            signal_was_correct = price_change_pct > self.DIRECTION_THRESHOLD_PCT
        elif direction == "bearish":
            signal_was_correct = price_change_pct < -self.DIRECTION_THRESHOLD_PCT
        else:  # neutral
            signal_was_correct = abs(price_change_pct) < self.DIRECTION_THRESHOLD_PCT

        # 4. UPDATE SIGNAL
        status = "settled_profitable" if signal_was_correct else "settled_unprofitable"
        with self.db() as session:
            sig = session.get(IntelSignal, signal_id)
            if sig:
                sig.settlement_price = settlement_price
                sig.settlement_price_change_pct = price_change_pct
                sig.status = status
                sig.settled_at = datetime.now(timezone.utc)
                session.commit()

        # 5. SETTLE EACH ENDORSEMENT
        with self.db() as session:
            endorsements = list(
                session.execute(
                    select(IntelEndorsement).where(IntelEndorsement.signal_id == signal_id)
                ).scalars().all()
            )
            endorsement_data = [
                {
                    "id": e.id,
                    "endorser_agent_id": e.endorser_agent_id,
                    "stake_amount": e.stake_amount,
                    "linked_trade_id": e.linked_trade_id,
                }
                for e in endorsements
            ]

        for edata in endorsement_data:
            await self._settle_endorsement(
                endorsement_id=edata["id"],
                endorser_agent_id=edata["endorser_agent_id"],
                stake_amount=edata["stake_amount"],
                linked_trade_id=edata["linked_trade_id"],
                scout_agent_id=scout_agent_id,
                signal_was_correct=signal_was_correct,
            )

        # 6. POST SETTLEMENT SUMMARY
        correct_str = "CORRECT" if signal_was_correct else "INCORRECT"
        await self._post_to_agora(
            "trade-results",
            f"{asset} {direction} signal by {scout_agent_name}: {correct_str}. "
            f"Price moved {price_change_pct:+.2f}%. {endorsement_count} endorsement(s) settled.",
            importance=1,
        )

        self.log.info(
            "signal_settled",
            signal_id=signal_id,
            asset=asset,
            direction=direction,
            correct=signal_was_correct,
            price_change_pct=round(price_change_pct, 4),
            endorsement_count=endorsement_count,
        )
        return "profitable" if signal_was_correct else "unprofitable"

    # ──────────────────────────────────────────────
    # ENDORSEMENT SETTLEMENT
    # ──────────────────────────────────────────────

    async def _settle_endorsement(
        self,
        endorsement_id: int,
        endorser_agent_id: int,
        stake_amount: float,
        linked_trade_id: int | None,
        scout_agent_id: int,
        signal_was_correct: bool,
    ) -> None:
        """Settle a single endorsement — trade-linked or time-based."""
        scout_rep_change = 0.0
        endorser_rep_change = 0.0
        settlement_pnl = None
        settlement_status = "settled_win" if signal_was_correct else "settled_loss"

        if linked_trade_id is not None:
            # ── TRADE-LINKED SETTLEMENT ──
            trade_pnl = await self._get_trade_pnl(linked_trade_id)
            settlement_pnl = trade_pnl

            if trade_pnl is not None and trade_pnl > 0:
                # Scout rewarded, endorser wins
                scout_reward = stake_amount * self.TRADE_LINKED_SCOUT_WIN_MULTIPLIER
                await self.economy.apply_reward(scout_agent_id, scout_reward, "intel_signal_win")
                await self.economy.release_escrow(endorser_agent_id, stake_amount, "endorsement_win")
                await self.economy.apply_reward(
                    endorser_agent_id, self.TRADE_LINKED_ENDORSER_WIN_BONUS, "endorsement_judgment_bonus"
                )
                scout_rep_change = scout_reward
                endorser_rep_change = stake_amount + self.TRADE_LINKED_ENDORSER_WIN_BONUS
                settlement_status = "settled_win"
            else:
                # Scout penalized, endorser loses stake
                scout_penalty = stake_amount * self.TRADE_LINKED_SCOUT_LOSS_MULTIPLIER
                await self.economy.apply_penalty(scout_agent_id, scout_penalty, "intel_signal_loss")
                # Don't release escrow — endorser loses stake
                scout_rep_change = -scout_penalty
                endorser_rep_change = -stake_amount
                settlement_status = "settled_loss"
        else:
            # ── TIME-BASED FALLBACK SETTLEMENT ──
            if signal_was_correct:
                scout_reward = stake_amount * self.TIME_BASED_SCOUT_WIN_MULTIPLIER
                await self.economy.apply_reward(scout_agent_id, scout_reward, "intel_signal_time_win")
                await self.economy.release_escrow(endorser_agent_id, stake_amount, "endorsement_time_refund")
                scout_rep_change = scout_reward
                endorser_rep_change = stake_amount  # Got their stake back
                settlement_status = "settled_win"
            else:
                scout_penalty = stake_amount * self.TIME_BASED_SCOUT_LOSS_MULTIPLIER
                await self.economy.apply_penalty(scout_agent_id, scout_penalty, "intel_signal_time_loss")
                # Endorser gets refund (didn't trade on it)
                await self.economy.release_escrow(endorser_agent_id, stake_amount, "endorsement_time_refund")
                scout_rep_change = -scout_penalty
                endorser_rep_change = stake_amount  # Refunded
                settlement_status = "settled_loss"

        # Update endorsement record
        with self.db() as session:
            end = session.get(IntelEndorsement, endorsement_id)
            if end:
                end.settlement_status = settlement_status
                end.settlement_pnl = settlement_pnl
                end.scout_reputation_change = scout_rep_change
                end.endorser_reputation_change = endorser_rep_change
                end.settled_at = datetime.now(timezone.utc)
                session.commit()

    # ──────────────────────────────────────────────
    # PRICE HELPERS
    # ──────────────────────────────────────────────

    async def _get_settlement_price(self, asset: str) -> float | None:
        """Get current price for settlement. Returns None if unavailable."""
        if self.exchange is None:
            self.log.warning("no_exchange_service")
            return None
        try:
            ticker = await self.exchange.get_ticker(asset)
            if ticker and "last" in ticker:
                return ticker["last"]
            return None
        except Exception as exc:
            self.log.error("price_fetch_failed", asset=asset, error=str(exc))
            return None

    async def _get_trade_pnl(self, trade_id: int) -> float | None:
        """Get PnL for a linked trade."""
        with self.db() as session:
            trade = session.get(Transaction, trade_id)
            if trade is None:
                return None
            return trade.pnl or 0.0

    # ──────────────────────────────────────────────
    # Agora helper
    # ──────────────────────────────────────────────

    async def _post_to_agora(
        self, channel: str, content: str, importance: int = 0,
    ) -> None:
        if self.agora is None:
            return
        from src.agora.schemas import AgoraMessage, MessageType
        msg = AgoraMessage(
            agent_id=0, agent_name="SettlementEngine", channel=channel,
            content=content, message_type=MessageType.ECONOMY, importance=importance,
        )
        try:
            await self.agora.post_message(msg)
        except Exception as exc:
            self.log.warning("agora_post_failed", channel=channel, error=str(exc))
