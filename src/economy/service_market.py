"""
Project Syndicate — Service Market (Framework)

CRUD scaffolding for the agent service marketplace.
Full purchase/fulfillment flow activates in Phase 4
when the agent population can sustain a real marketplace.
"""

__version__ = "0.5.0"

from typing import Optional, TYPE_CHECKING

import structlog
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from src.common.models import ServiceListing

if TYPE_CHECKING:
    from src.agora.agora_service import AgoraService

logger = structlog.get_logger()


class ServiceMarket:
    """Service marketplace — agents offer services for reputation (framework only)."""

    def __init__(
        self,
        db_session_factory: sessionmaker,
        agora_service: Optional["AgoraService"] = None,
    ) -> None:
        self.db = db_session_factory
        self.agora = agora_service
        self.log = logger.bind(component="service_market")

    async def create_listing(
        self,
        provider_agent_id: int,
        provider_agent_name: str,
        title: str,
        description: str,
        price_reputation: float,
    ) -> Optional[ServiceListing]:
        """Create a new service listing."""
        if price_reputation <= 0:
            self.log.warning("listing_invalid_price", price=price_reputation)
            return None

        with self.db() as session:
            listing = ServiceListing(
                provider_agent_id=provider_agent_id,
                provider_agent_name=provider_agent_name,
                title=title,
                description=description,
                price_reputation=price_reputation,
                status="active",
            )
            session.add(listing)
            session.commit()
            listing_id = listing.id

        await self._post_to_agora(
            "agent-chat",
            f"{provider_agent_name} is offering: {title} ({price_reputation:.1f} rep)",
        )
        self.log.info("listing_created", listing_id=listing_id, title=title)

        with self.db() as session:
            return session.get(ServiceListing, listing_id)

    async def get_listings(self, status: str = "active") -> list[ServiceListing]:
        """Get all service listings filtered by status."""
        with self.db() as session:
            return list(
                session.execute(
                    select(ServiceListing)
                    .where(ServiceListing.status == status)
                    .order_by(ServiceListing.created_at.desc())
                ).scalars().all()
            )

    async def cancel_listing(self, listing_id: int, provider_agent_id: int) -> bool:
        """Cancel a listing. Only the provider can cancel."""
        with self.db() as session:
            listing = session.get(ServiceListing, listing_id)
            if listing is None:
                return False
            if listing.provider_agent_id != provider_agent_id:
                self.log.warning(
                    "cancel_not_owner",
                    listing_id=listing_id,
                    requester=provider_agent_id,
                )
                return False
            listing.status = "cancelled"
            session.commit()
        self.log.info("listing_cancelled", listing_id=listing_id)
        return True

    # ──────────────────────────────────────────────
    # Helpers
    # ──────────────────────────────────────────────

    async def _post_to_agora(self, channel: str, content: str) -> None:
        if self.agora is None:
            return
        from src.agora.schemas import AgoraMessage, MessageType
        msg = AgoraMessage(
            agent_id=0, agent_name="ServiceMarket", channel=channel,
            content=content, message_type=MessageType.ECONOMY,
        )
        try:
            await self.agora.post_message(msg)
        except Exception as exc:
            self.log.warning("agora_post_failed", channel=channel, error=str(exc))
