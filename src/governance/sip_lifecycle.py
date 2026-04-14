"""
SIP Lifecycle Manager.

Manages the state machine for System Improvement Proposals:

    DEBATE -> VOTING -> TALLIED -> GENESIS_REVIEW ->
    OWNER_REVIEW -> IMPLEMENTING -> IMPLEMENTED

The lifecycle manager runs as a periodic task (every Genesis cycle).
Each run, it checks for SIPs that need to advance based on time thresholds.
"""

__version__ = "0.1.0"

import logging
from datetime import datetime, timezone

from sqlalchemy import select, func

from src.common.models import (
    SystemImprovementProposal, SIPVote, SIPDebate,
    ParameterRegistryEntry, ParameterChangeLog,
)
from src.governance.maturity_tracker import ColonyMaturityTracker
from src.governance.parameter_registry import ParameterRegistry
from src.governance.vote_weights import get_vote_weight

logger = logging.getLogger(__name__)


class SIPLifecycleManager:
    """Advances SIPs through their governance lifecycle."""

    def __init__(self, maturity_tracker: ColonyMaturityTracker,
                 parameter_registry: ParameterRegistry,
                 agora_service=None):
        self.maturity = maturity_tracker
        self.registry = parameter_registry
        self.agora = agora_service

    async def advance_all_sips(self, db_session):
        """Main entry point. Called periodically (every Genesis cycle).

        Processes SIPs in lifecycle order to avoid double-advancing.
        """
        now = datetime.now(timezone.utc)

        # 1. DEBATE -> VOTING (debate period expired)
        debate_sips = db_session.execute(
            select(SystemImprovementProposal).where(
                SystemImprovementProposal.lifecycle_status == "debate",
                SystemImprovementProposal.debate_ends_at <= now,
            )
        ).scalars().all()
        for sip in debate_sips:
            await self._advance_to_voting(sip, db_session)

        # 2. VOTING -> TALLIED (voting period expired)
        voting_sips = db_session.execute(
            select(SystemImprovementProposal).where(
                SystemImprovementProposal.lifecycle_status == "voting",
                SystemImprovementProposal.voting_ends_at <= now,
            )
        ).scalars().all()
        for sip in voting_sips:
            await self._tally_votes(sip, db_session)

        # 3. TALLIED -> handled by Genesis review (Tier 3)

        # 4. OWNER_REVIEW with decision -> advance
        owner_decided = db_session.execute(
            select(SystemImprovementProposal).where(
                SystemImprovementProposal.lifecycle_status == "owner_review",
                SystemImprovementProposal.owner_decision.isnot(None),
                SystemImprovementProposal.owner_decision != "deferred",
            )
        ).scalars().all()
        for sip in owner_decided:
            if sip.owner_decision == "accepted":
                sip.lifecycle_status = "implementing"
                await self._post_agora(
                    f"[OWNER DECISION] SIP #{sip.id} '{sip.title}' approved by owner. "
                    f"Implementation pending."
                )
            elif sip.owner_decision == "rejected":
                sip.lifecycle_status = "rejected_by_owner"
                sip.resolved_at = now
                notes = f" Reason: {sip.owner_notes}" if sip.owner_notes else ""
                await self._post_agora(
                    f"[OWNER DECISION] SIP #{sip.id} '{sip.title}' rejected by owner.{notes}"
                )

        # 5. IMPLEMENTING -> IMPLEMENTED (execute parameter change)
        implementing_sips = db_session.execute(
            select(SystemImprovementProposal).where(
                SystemImprovementProposal.lifecycle_status == "implementing",
            )
        ).scalars().all()
        for sip in implementing_sips:
            await self._implement_sip(sip, db_session)

        db_session.flush()

    async def initiate_sip(self, sip_id: int, db_session):
        """Called after creating the SIP record to start the lifecycle.

        Returns (success, message) tuple.
        """
        now = datetime.now(timezone.utc)
        config = self.maturity.get_config(db_session)

        sip = db_session.execute(
            select(SystemImprovementProposal).where(
                SystemImprovementProposal.id == sip_id
            )
        ).scalar_one_or_none()

        if sip is None:
            return False, f"SIP #{sip_id} not found"

        # Validate target parameter if specified
        if sip.target_parameter_key:
            validation = await self.registry.validate_proposed_change(
                sip.target_parameter_key, sip.proposed_value or 0.0, db_session
            )
            if validation["tier"] == 3:
                sip.lifecycle_status = "rejected_by_vote"
                sip.resolved_at = now
                await self._post_agora(
                    f"[SIP #{sip.id} — AUTO-REJECTED] Targets a Forbidden parameter: "
                    f"{sip.target_parameter_key}"
                )
                db_session.flush()
                return False, "Targets a Tier 3 Forbidden parameter"

            if not validation["valid"]:
                sip.lifecycle_status = "rejected_by_vote"
                sip.resolved_at = now
                await self._post_agora(
                    f"[SIP #{sip.id} — AUTO-REJECTED] {validation['reason']}"
                )
                db_session.flush()
                return False, validation["reason"]

            sip.parameter_tier = validation["tier"]

        # Set lifecycle fields
        sip.lifecycle_status = "debate"
        sip.debate_ends_at = self.maturity.get_debate_end_time(db_session, now)
        sip.colony_maturity_at_proposal = config.stage.value

        hours = config.debate_period_hours
        await self._post_agora(
            f"[SIP #{sip.id} — DEBATE OPEN] '{sip.title}' by {sip.proposer_agent_name}. "
            f"Debate closes in {hours} hours. All agents: review and debate."
        )

        db_session.flush()
        return True, "SIP debate started"

    async def _advance_to_voting(self, sip, db_session):
        """Transition from DEBATE -> VOTING."""
        config = self.maturity.get_config(db_session)

        debate_count = db_session.execute(
            select(func.count()).select_from(SIPDebate).where(
                SIPDebate.sip_id == sip.id
            )
        ).scalar() or 0

        sip.lifecycle_status = "voting"
        sip.voting_ends_at = self.maturity.get_voting_end_time(
            db_session, datetime.now(timezone.utc)
        )

        hours = config.voting_period_hours
        await self._post_agora(
            f"[SIP #{sip.id} — VOTING OPEN] '{sip.title}'. "
            f"{debate_count} debate entries recorded. "
            f"Voting closes in {hours} hours. "
            f"Cast your vote: support, oppose, or abstain."
        )

    async def _tally_votes(self, sip, db_session):
        """Transition from VOTING -> TALLIED or REJECTED."""
        now = datetime.now(timezone.utc)
        config = self.maturity.get_config(db_session)

        votes = db_session.execute(
            select(SIPVote).where(SIPVote.sip_id == sip.id)
        ).scalars().all()

        weighted_support = sum(v.vote_weight for v in votes if v.vote == "support")
        weighted_oppose = sum(v.vote_weight for v in votes if v.vote == "oppose")
        weighted_total_cast = weighted_support + weighted_oppose

        sip.weighted_support = weighted_support
        sip.weighted_oppose = weighted_oppose
        sip.weighted_total_cast = weighted_total_cast
        sip.tallied_at = now

        # No votes cast -> expired
        if weighted_total_cast == 0:
            sip.lifecycle_status = "expired"
            sip.resolved_at = now
            await self._post_agora(
                f"[SIP #{sip.id} — EXPIRED] '{sip.title}' — no votes cast."
            )
            return

        vote_pct = weighted_support / weighted_total_cast
        sip.vote_pass_percentage = vote_pct

        # Determine threshold
        if sip.parameter_tier == 2:
            threshold = config.structural_threshold
        else:
            threshold = config.pass_threshold

        pct_display = f"{vote_pct * 100:.0f}%"

        if vote_pct < threshold:
            sip.lifecycle_status = "rejected_by_vote"
            sip.resolved_at = now
            await self._post_agora(
                f"[SIP #{sip.id} — REJECTED] '{sip.title}' failed to reach "
                f"{threshold * 100:.0f}% support. Result: {pct_display} support "
                f"({weighted_support:.1f} for, {weighted_oppose:.1f} against)."
            )
        else:
            sip.lifecycle_status = "tallied"
            await self._post_agora(
                f"[SIP #{sip.id} — VOTE PASSED] '{sip.title}' achieved "
                f"{pct_display} support. Awaiting Genesis ratification."
            )

    async def _implement_sip(self, sip, db_session):
        """Transition from IMPLEMENTING -> IMPLEMENTED."""
        now = datetime.now(timezone.utc)

        # General (non-parameter) SIPs
        if not sip.target_parameter_key:
            sip.lifecycle_status = "implemented"
            sip.implemented_at = now
            sip.resolved_at = now
            await self._post_agora(
                f"[SIP #{sip.id} — IMPLEMENTED] General proposal '{sip.title}' "
                f"approved. No system parameter changed."
            )
            return

        # Validate change is still valid
        validation = await self.registry.validate_proposed_change(
            sip.target_parameter_key, sip.proposed_value, db_session
        )
        if not validation["valid"]:
            sip.lifecycle_status = "expired"
            sip.resolved_at = now
            await self._post_agora(
                f"[SIP #{sip.id} — IMPLEMENTATION FAILED] {validation['reason']}"
            )
            return

        # Evaluation weight constraint check
        if sip.target_parameter_key.startswith("evaluation."):
            if not await self._validate_eval_weights(
                sip.target_parameter_key, sip.proposed_value, db_session
            ):
                sip.lifecycle_status = "expired"
                sip.resolved_at = now
                await self._post_agora(
                    f"[SIP #{sip.id} — IMPLEMENTATION FAILED] Changing "
                    f"{sip.target_parameter_key} to {sip.proposed_value} "
                    f"would make evaluation weights not sum to 1.0."
                )
                return

        # Apply the change
        try:
            change = await self.registry.apply_change(
                sip.target_parameter_key, sip.proposed_value, sip.id, db_session
            )
        except ValueError as e:
            sip.lifecycle_status = "expired"
            sip.resolved_at = now
            await self._post_agora(
                f"[SIP #{sip.id} — IMPLEMENTATION FAILED] {e}"
            )
            return

        sip.lifecycle_status = "implemented"
        sip.implemented_at = now
        sip.resolved_at = now

        pct = f"{sip.vote_pass_percentage * 100:.0f}%" if sip.vote_pass_percentage else "N/A"
        param_info = await self.registry.get_parameter(sip.target_parameter_key, db_session)
        await self._post_agora(
            f"[SYSTEM CHANGE] SIP #{sip.id} implemented: {param_info['display_name']} "
            f"changed from {change['old_value']} to {change['new_value']}. "
            f"Proposed by {sip.proposer_agent_name}. Vote: {pct} support.",
            channel="system-alerts",
        )

    async def _validate_eval_weights(
        self, changing_key: str, new_value: float, db_session
    ) -> bool:
        """Check that evaluation weights still sum to 1.0 after a change."""
        eval_params = db_session.execute(
            select(ParameterRegistryEntry).where(
                ParameterRegistryEntry.parameter_key.like("evaluation.%"),
                ParameterRegistryEntry.parameter_key.like("%_weight"),
            )
        ).scalars().all()

        total = 0.0
        for p in eval_params:
            if p.parameter_key == changing_key:
                total += new_value
            else:
                total += p.current_value

        return abs(total - 1.0) <= 0.01

    async def _post_agora(self, content: str, channel: str = "sip-proposals"):
        """Post to Agora if service available."""
        if self.agora:
            try:
                await self.agora.post_message(
                    channel=channel,
                    agent_id=0,
                    agent_name="GOVERNANCE",
                    content=content,
                    message_type="SYSTEM",
                )
            except Exception as e:
                logger.warning(f"Failed to post governance message: {e}")
