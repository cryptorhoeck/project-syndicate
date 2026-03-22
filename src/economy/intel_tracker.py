"""
Project Syndicate — Intel Accuracy Tracker

Correlates intel posts with subsequent market outcomes to determine
if the intel was useful. Runs periodically as a maintenance task.
"""

__version__ = "0.1.0"

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.common.config import config
from src.common.models import Agent, IntelAccuracyTracking, IntelChallenge

logger = logging.getLogger(__name__)


class IntelAccuracyTracker:
    """Settles intel accuracy over time and adjusts reputation."""

    async def settle_pending_intel(self, db_session: Session) -> int:
        """Settle intel older than the settlement window.

        Returns number of records settled.
        """
        cutoff = datetime.now(timezone.utc) - timedelta(
            hours=config.intel_settlement_window_hours
        )

        pending = list(
            db_session.execute(
                select(IntelAccuracyTracking)
                .where(
                    IntelAccuracyTracking.outcome == "pending",
                    IntelAccuracyTracking.posted_at < cutoff,
                )
            ).scalars().all()
        )

        settled = 0
        for record in pending:
            # Simplified settlement: without real price data, expire as inconclusive
            # When the Arena is live with price data, this will check actual market moves
            record.outcome = "expired"
            record.outcome_determined_at = datetime.now(timezone.utc)
            record.reputation_change = 0.0
            settled += 1

        if settled:
            db_session.flush()
            logger.info(f"Settled {settled} pending intel records")

        return settled

    async def settle_challenges(self, db_session: Session) -> int:
        """Settle intel challenges based on original intel outcome.

        Returns number of challenges settled.
        """
        cutoff = datetime.now(timezone.utc) - timedelta(
            hours=config.intel_settlement_window_hours
        )

        pending = list(
            db_session.execute(
                select(IntelChallenge)
                .where(
                    IntelChallenge.outcome == "pending",
                    IntelChallenge.created_at < cutoff,
                )
            ).scalars().all()
        )

        settled = 0
        for challenge in pending:
            # Look up the original intel's outcome
            original = db_session.execute(
                select(IntelAccuracyTracking)
                .where(IntelAccuracyTracking.message_id == challenge.target_message_id)
            ).scalar_one_or_none()

            if not original or original.outcome == "pending":
                continue

            if original.outcome == "confirmed_useless":
                # Challenger was right
                challenge.outcome = "challenger_right"
                challenge.challenger_reputation_change = original.confidence_stated / 10.0
                challenge.target_reputation_change = -0.05

                # Apply reputation changes
                challenger = db_session.get(Agent, challenge.challenger_agent_id)
                if challenger:
                    challenger.reputation_score = (challenger.reputation_score or 0) + challenge.challenger_reputation_change

                target = db_session.get(Agent, challenge.target_agent_id)
                if target:
                    target.reputation_score = (target.reputation_score or 0) + challenge.target_reputation_change

            elif original.outcome == "confirmed_useful":
                # Challenger was wrong
                challenge.outcome = "challenger_wrong"
                challenge.challenger_reputation_change = -(original.confidence_stated / 10.0)

                challenger = db_session.get(Agent, challenge.challenger_agent_id)
                if challenger:
                    challenger.reputation_score = (challenger.reputation_score or 0) + challenge.challenger_reputation_change
            else:
                challenge.outcome = "inconclusive"

            challenge.outcome_determined_at = datetime.now(timezone.utc)
            settled += 1

        if settled:
            db_session.flush()
            logger.info(f"Settled {settled} intel challenges")

        return settled
