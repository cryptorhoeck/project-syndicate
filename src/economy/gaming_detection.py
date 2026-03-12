"""
Project Syndicate — Gaming Detection

Detects gaming behavior: wash trading, rubber-stamp critics,
intel spam. Runs daily during the Genesis cycle.
"""

__version__ = "0.5.0"

from datetime import datetime, timedelta, timezone
from typing import Optional, TYPE_CHECKING

import structlog
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from src.common.models import (
    Agent,
    CriticAccuracy,
    GamingFlag,
    IntelEndorsement,
    IntelSignal,
)

if TYPE_CHECKING:
    from src.agora.agora_service import AgoraService

logger = structlog.get_logger()


def _utcnow_naive() -> datetime:
    """UTC now without timezone info (for DB compatibility with SQLite)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class GamingDetector:
    """Detects gaming behavior in the economy."""

    WASH_TRADING_THRESHOLD_PCT = 50  # Flag if >50% of endorsements between same pair
    RUBBER_STAMP_THRESHOLD_PCT = 90  # Flag if Critic approves >90% over 10+ reviews
    RUBBER_STAMP_MIN_REVIEWS = 10
    INTEL_SPAM_ENDORSEMENT_RATE_PCT = 10  # Flag if <10% endorsement rate over 20+ signals
    INTEL_SPAM_MIN_SIGNALS = 20

    def __init__(
        self,
        db_session_factory: sessionmaker,
        agora_service: Optional["AgoraService"] = None,
    ) -> None:
        self.db = db_session_factory
        self.agora = agora_service
        self.log = logger.bind(component="gaming_detection")

    # ──────────────────────────────────────────────
    # FULL DETECTION CYCLE
    # ──────────────────────────────────────────────

    async def run_full_detection(self, lookback_days: int = 7) -> list[GamingFlag]:
        """Run all gaming detection checks. Called daily by Genesis."""
        flags: list[GamingFlag] = []
        flags.extend(await self.check_wash_trading(lookback_days))
        flags.extend(await self.check_rubber_stamp_critics())
        flags.extend(await self.check_intel_spam())

        if flags:
            await self._post_gaming_summary(flags)

        return flags

    # ──────────────────────────────────────────────
    # WASH TRADING
    # ──────────────────────────────────────────────

    async def check_wash_trading(self, lookback_days: int = 7) -> list[GamingFlag]:
        """Detect agents repeatedly endorsing each other's intel."""
        cutoff = _utcnow_naive() - timedelta(days=lookback_days)
        flag_ids: list[int] = []

        with self.db() as session:
            rows = session.execute(
                select(
                    IntelSignal.scout_agent_id,
                    IntelEndorsement.endorser_agent_id,
                    func.count().label("cnt"),
                )
                .join(IntelSignal, IntelEndorsement.signal_id == IntelSignal.id)
                .where(IntelEndorsement.created_at >= cutoff)
                .group_by(IntelSignal.scout_agent_id, IntelEndorsement.endorser_agent_id)
            ).all()

            endorser_totals: dict[int, int] = {}
            scout_totals: dict[int, int] = {}
            pair_counts: list[tuple[int, int, int]] = []

            for scout_id, endorser_id, cnt in rows:
                pair_counts.append((scout_id, endorser_id, cnt))
                endorser_totals[endorser_id] = endorser_totals.get(endorser_id, 0) + cnt
                scout_totals[scout_id] = scout_totals.get(scout_id, 0) + cnt

            for scout_id, endorser_id, cnt in pair_counts:
                if cnt <= 2:
                    continue

                endorser_pct = (cnt / endorser_totals.get(endorser_id, 1)) * 100
                scout_pct = (cnt / scout_totals.get(scout_id, 1)) * 100

                if endorser_pct > self.WASH_TRADING_THRESHOLD_PCT or scout_pct > self.WASH_TRADING_THRESHOLD_PCT:
                    existing = session.execute(
                        select(GamingFlag).where(
                            GamingFlag.flag_type == "wash_trading",
                            GamingFlag.resolved == False,
                        )
                    ).scalars().all()

                    pair_set = {scout_id, endorser_id}
                    is_repeat = any(set(f.agent_ids) == pair_set for f in existing)

                    severity = "penalty" if is_repeat else "warning"
                    evidence = (
                        f"Agent {endorser_id} endorsed Agent {scout_id}'s signals "
                        f"{cnt} times ({endorser_pct:.0f}% of their endorsements)"
                    )

                    flag = GamingFlag(
                        flag_type="wash_trading",
                        agent_ids=[scout_id, endorser_id],
                        evidence=evidence,
                        severity=severity,
                    )
                    session.add(flag)
                    session.flush()
                    flag_ids.append(flag.id)

            if flag_ids:
                session.commit()

        # Re-fetch flags outside session so they're usable
        flags = self._refetch_flags(flag_ids)
        if flags:
            self.log.warning("wash_trading_detected", count=len(flags))
        return flags

    # ──────────────────────────────────────────────
    # RUBBER STAMP CRITICS
    # ──────────────────────────────────────────────

    async def check_rubber_stamp_critics(self) -> list[GamingFlag]:
        """Detect Critics that approve everything."""
        flag_ids: list[int] = []

        with self.db() as session:
            critics = list(
                session.execute(
                    select(CriticAccuracy)
                    .where(CriticAccuracy.total_reviews >= self.RUBBER_STAMP_MIN_REVIEWS)
                ).scalars().all()
            )

            for critic in critics:
                total = critic.total_reviews or 0
                approves = critic.approve_count or 0
                if total == 0:
                    continue

                approval_rate = (approves / total) * 100
                if approval_rate > self.RUBBER_STAMP_THRESHOLD_PCT:
                    agent = session.get(Agent, critic.critic_agent_id)
                    name = agent.name if agent else f"Agent-{critic.critic_agent_id}"

                    evidence = (
                        f"{name} approved {approves}/{total} reviews ({approval_rate:.0f}%)"
                    )
                    flag = GamingFlag(
                        flag_type="rubber_stamp",
                        agent_ids=[critic.critic_agent_id],
                        evidence=evidence,
                        severity="warning",
                    )
                    session.add(flag)
                    session.flush()
                    flag_ids.append(flag.id)

            if flag_ids:
                session.commit()

        flags = self._refetch_flags(flag_ids)
        if flags:
            self.log.warning("rubber_stamp_detected", count=len(flags))
        return flags

    # ──────────────────────────────────────────────
    # INTEL SPAM
    # ──────────────────────────────────────────────

    async def check_intel_spam(self) -> list[GamingFlag]:
        """Detect Scouts posting many low-quality signals."""
        cutoff = _utcnow_naive() - timedelta(days=30)
        flag_ids: list[int] = []

        with self.db() as session:
            scouts = session.execute(
                select(
                    IntelSignal.scout_agent_id,
                    IntelSignal.scout_agent_name,
                    func.count().label("total"),
                )
                .where(IntelSignal.created_at >= cutoff)
                .group_by(IntelSignal.scout_agent_id, IntelSignal.scout_agent_name)
                .having(func.count() >= self.INTEL_SPAM_MIN_SIGNALS)
            ).all()

            for scout_id, scout_name, total in scouts:
                endorsed = session.execute(
                    select(func.count()).select_from(IntelSignal)
                    .where(
                        IntelSignal.scout_agent_id == scout_id,
                        IntelSignal.created_at >= cutoff,
                        IntelSignal.endorsement_count > 0,
                    )
                ).scalar() or 0

                rate = (endorsed / total) * 100 if total > 0 else 0
                if rate < self.INTEL_SPAM_ENDORSEMENT_RATE_PCT:
                    evidence = (
                        f"{scout_name} posted {total} signals, only {endorsed} received "
                        f"endorsements ({rate:.0f}%)"
                    )
                    flag = GamingFlag(
                        flag_type="intel_spam",
                        agent_ids=[scout_id],
                        evidence=evidence,
                        severity="warning",
                    )
                    session.add(flag)
                    session.flush()
                    flag_ids.append(flag.id)

            if flag_ids:
                session.commit()

        flags = self._refetch_flags(flag_ids)
        if flags:
            self.log.warning("intel_spam_detected", count=len(flags))
        return flags

    def _refetch_flags(self, flag_ids: list[int]) -> list[GamingFlag]:
        """Re-fetch GamingFlag objects, eagerly loading all attributes."""
        if not flag_ids:
            return []
        with self.db() as session:
            flags = list(
                session.execute(
                    select(GamingFlag).where(GamingFlag.id.in_(flag_ids))
                ).scalars().all()
            )
            # Eagerly access all attributes before session closes
            for f in flags:
                _ = f.id, f.flag_type, f.agent_ids, f.evidence, f.severity
                _ = f.penalty_applied, f.detected_at, f.reviewed_by, f.resolved
            # Expunge from session so they don't try to lazy-load
            for f in flags:
                session.expunge(f)
            return flags

    # ──────────────────────────────────────────────
    # FLAG MANAGEMENT
    # ──────────────────────────────────────────────

    async def get_unresolved_flags(self) -> list[GamingFlag]:
        """Get all unresolved gaming flags for Genesis/owner review."""
        with self.db() as session:
            return list(
                session.execute(
                    select(GamingFlag)
                    .where(GamingFlag.resolved == False)
                    .order_by(GamingFlag.detected_at.desc())
                ).scalars().all()
            )

    async def resolve_flag(
        self,
        flag_id: int,
        reviewed_by: str,
        penalty: float | None = None,
    ) -> bool:
        """Resolve a gaming flag, optionally applying a penalty."""
        with self.db() as session:
            flag = session.get(GamingFlag, flag_id)
            if flag is None:
                return False

            flag.resolved = True
            flag.resolved_at = datetime.now(timezone.utc)
            flag.reviewed_by = reviewed_by
            flag.penalty_applied = penalty
            agent_ids = list(flag.agent_ids) if flag.agent_ids else []
            flag_type = flag.flag_type
            session.commit()

        if penalty and penalty > 0:
            # Import here to avoid circular dependency
            from src.economy.economy_service import EconomyService
            # Note: penalty application requires an EconomyService reference.
            # In practice, this is called through EconomyService.gaming_detector.resolve_flag()
            # and Genesis handles the penalty application separately.
            self.log.info(
                "flag_resolved_with_penalty",
                flag_id=flag_id,
                penalty=penalty,
                agents=agent_ids,
            )
        else:
            self.log.info("flag_resolved", flag_id=flag_id, reviewed_by=reviewed_by)

        return True

    # ──────────────────────────────────────────────
    # Helpers
    # ──────────────────────────────────────────────

    async def _post_gaming_summary(self, flags: list[GamingFlag]) -> None:
        """Post a summary of detected gaming to system-alerts."""
        if self.agora is None:
            return
        from src.agora.schemas import AgoraMessage, MessageType

        types = set()
        for f in flags:
            types.add(f.flag_type)

        msg = AgoraMessage(
            agent_id=0,
            agent_name="GamingDetector",
            channel="system-alerts",
            content=f"Gaming detection: {len(flags)} flag(s) raised. Types: {', '.join(types)}. See gaming_flags table.",
            message_type=MessageType.ALERT,
            importance=2,
        )
        try:
            await self.agora.post_message(msg)
        except Exception as exc:
            self.log.warning("agora_post_failed", error=str(exc))
