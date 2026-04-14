"""
Parameter Reader — helper for system components.

Reads values from the parameter registry with fallback to config defaults.
Use this instead of direct config access for any SIP-modifiable parameter.
"""

__version__ = "0.1.0"

import logging

from src.governance.parameter_registry import ParameterRegistry

logger = logging.getLogger(__name__)

_registry = ParameterRegistry()


async def get_param(key: str, db_session, fallback=None) -> float:
    """Read a parameter value from the registry with fallback.

    Use this in system components instead of direct config access
    for any parameter that's in the registry.
    """
    try:
        return await _registry.get_value(key, db_session)
    except (KeyError, Exception):
        if fallback is not None:
            return fallback
        raise
