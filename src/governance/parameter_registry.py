"""
Parameter Registry Service.

The central source of truth for all SIP-modifiable system parameters.
System components read parameter values from here at runtime.

Key principle: agents turn knobs within predefined safe ranges.
They don't rewire the machine.
"""

__version__ = "0.1.0"

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, func

from src.common.models import ParameterRegistryEntry, ParameterChangeLog

logger = logging.getLogger(__name__)


class ParameterRegistry:
    """Reads and modifies system parameters stored in the database."""

    async def get_value(self, parameter_key: str, db_session) -> float:
        """Get the current value of a parameter.

        Raises KeyError if parameter_key doesn't exist.
        """
        row = db_session.execute(
            select(ParameterRegistryEntry).where(
                ParameterRegistryEntry.parameter_key == parameter_key
            )
        ).scalar_one_or_none()

        if row is None:
            raise KeyError(f"Unknown parameter: {parameter_key}")
        return row.current_value

    async def get_parameter(self, parameter_key: str, db_session) -> dict:
        """Get full parameter info including min/max/tier/description."""
        row = db_session.execute(
            select(ParameterRegistryEntry).where(
                ParameterRegistryEntry.parameter_key == parameter_key
            )
        ).scalar_one_or_none()

        if row is None:
            raise KeyError(f"Unknown parameter: {parameter_key}")

        return {
            "parameter_key": row.parameter_key,
            "display_name": row.display_name,
            "description": row.description,
            "category": row.category,
            "current_value": row.current_value,
            "default_value": row.default_value,
            "min_value": row.min_value,
            "max_value": row.max_value,
            "tier": row.tier,
            "unit": row.unit,
            "last_modified_by_sip_id": row.last_modified_by_sip_id,
            "last_modified_at": row.last_modified_at,
        }

    async def get_all_parameters(
        self, db_session, category: str = None, tier: int = None
    ) -> list[dict]:
        """List all parameters, optionally filtered by category or tier."""
        query = select(ParameterRegistryEntry)
        if category:
            query = query.where(ParameterRegistryEntry.category == category)
        if tier is not None:
            query = query.where(ParameterRegistryEntry.tier == tier)
        query = query.order_by(ParameterRegistryEntry.category, ParameterRegistryEntry.parameter_key)

        rows = db_session.execute(query).scalars().all()
        return [
            {
                "parameter_key": r.parameter_key,
                "display_name": r.display_name,
                "description": r.description,
                "category": r.category,
                "current_value": r.current_value,
                "default_value": r.default_value,
                "min_value": r.min_value,
                "max_value": r.max_value,
                "tier": r.tier,
                "unit": r.unit,
            }
            for r in rows
        ]

    async def validate_proposed_change(
        self, parameter_key: str, proposed_value: float, db_session
    ) -> dict:
        """Check if a proposed value is valid.

        Returns dict with 'valid' bool, 'reason' if invalid, and parameter info.
        """
        try:
            param = await self.get_parameter(parameter_key, db_session)
        except KeyError:
            return {
                "valid": False,
                "reason": f"Parameter '{parameter_key}' does not exist",
                "parameter": None,
                "current_value": None,
                "proposed_value": proposed_value,
                "tier": None,
            }

        if param["tier"] == 3:
            return {
                "valid": False,
                "reason": f"Parameter '{param['display_name']}' is Tier 3 (Forbidden) and cannot be modified by SIP",
                "parameter": param,
                "current_value": param["current_value"],
                "proposed_value": proposed_value,
                "tier": 3,
            }

        if proposed_value < param["min_value"]:
            return {
                "valid": False,
                "reason": f"Proposed value {proposed_value} is below minimum {param['min_value']}",
                "parameter": param,
                "current_value": param["current_value"],
                "proposed_value": proposed_value,
                "tier": param["tier"],
            }

        if proposed_value > param["max_value"]:
            return {
                "valid": False,
                "reason": f"Proposed value {proposed_value} is above maximum {param['max_value']}",
                "parameter": param,
                "current_value": param["current_value"],
                "proposed_value": proposed_value,
                "tier": param["tier"],
            }

        if proposed_value == param["current_value"]:
            return {
                "valid": False,
                "reason": f"Proposed value {proposed_value} is the same as current value",
                "parameter": param,
                "current_value": param["current_value"],
                "proposed_value": proposed_value,
                "tier": param["tier"],
            }

        return {
            "valid": True,
            "reason": None,
            "parameter": param,
            "current_value": param["current_value"],
            "proposed_value": proposed_value,
            "tier": param["tier"],
        }

    async def apply_change(
        self, parameter_key: str, new_value: float, sip_id: int, db_session
    ) -> dict:
        """Apply a SIP-approved parameter change.

        Returns the change record dict.
        """
        validation = await self.validate_proposed_change(parameter_key, new_value, db_session)
        if not validation["valid"]:
            raise ValueError(validation["reason"])

        now = datetime.now(timezone.utc)
        old_value = validation["current_value"]
        default_value = validation["parameter"]["default_value"]

        # Determine drift direction relative to default
        # Moving away from default in a "permissive" direction = softer
        # Moving toward default or in a "stricter" direction = harder
        distance_old = abs(old_value - default_value)
        distance_new = abs(new_value - default_value)
        drift_direction = "softer" if distance_new > distance_old else "harder"

        # Update registry
        row = db_session.execute(
            select(ParameterRegistryEntry).where(
                ParameterRegistryEntry.parameter_key == parameter_key
            )
        ).scalar_one()

        row.current_value = new_value
        row.last_modified_by_sip_id = sip_id
        row.last_modified_at = now

        # Create change log
        log_entry = ParameterChangeLog(
            parameter_key=parameter_key,
            old_value=old_value,
            new_value=new_value,
            changed_by_sip_id=sip_id,
            changed_at=now,
            drift_direction=drift_direction,
        )
        db_session.add(log_entry)
        db_session.flush()

        logger.info(
            "parameter_changed",
            extra={
                "key": parameter_key,
                "old": old_value,
                "new": new_value,
                "sip_id": sip_id,
                "drift": drift_direction,
            },
        )

        return {
            "parameter_key": parameter_key,
            "old_value": old_value,
            "new_value": new_value,
            "drift_direction": drift_direction,
            "sip_id": sip_id,
            "changed_at": now.isoformat(),
        }

    async def get_drift_summary(self, db_session) -> dict:
        """Analyze cumulative parameter drift for Genesis daily report."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=30)

        changes = db_session.execute(
            select(ParameterChangeLog).where(
                ParameterChangeLog.changed_at >= cutoff
            ).order_by(ParameterChangeLog.changed_at.desc())
        ).scalars().all()

        softer = sum(1 for c in changes if c.drift_direction == "softer")
        harder = sum(1 for c in changes if c.drift_direction == "harder")

        recent = [
            {
                "parameter": c.parameter_key,
                "old": c.old_value,
                "new": c.new_value,
                "direction": c.drift_direction,
                "sip_id": c.changed_by_sip_id,
                "changed_at": c.changed_at.isoformat() if c.changed_at else None,
            }
            for c in changes[:10]
        ]

        return {
            "total_changes": len(changes),
            "softer_changes": softer,
            "harder_changes": harder,
            "drift_alert": softer > harder + 2,
            "recent_changes": recent,
        }
