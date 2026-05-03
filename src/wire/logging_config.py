"""
Wire logging configuration.

Defensive defaults that must be applied at every Wire entry point (CLI,
scheduler, tests that exercise live sources). Two goals:

1. Prevent secrets-in-URLs leaks. The httpx INFO log emits the full request
   URL including query strings. Several free APIs (FRED, Etherscan, CryptoPanic,
   TradingEconomics paid) authenticate via `?api_key=...`. Downgrading httpx
   to WARNING removes the leak surface.

2. Reduce noise. urllib3 INFO and asyncio DEBUG aren't useful at runtime.

Production note: this is a tactical fix. The structural fix tracked in
DEFERRED_ITEMS_TRACKER.md ("Wire URL/secret leak via httpx INFO logging")
is to scrub query strings at the source layer and prefer header auth where
the API supports it.
"""

from __future__ import annotations

import logging

NOISY_LOGGERS_DOWNGRADE = (
    "httpx",
    "httpcore",
    "urllib3",
    "asyncio",
    "anthropic._base_client",
)


def configure_wire_logging(level: int = logging.INFO) -> None:
    """Apply Wire defaults. Idempotent — safe to call multiple times."""
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    for name in NOISY_LOGGERS_DOWNGRADE:
        logging.getLogger(name).setLevel(logging.WARNING)
