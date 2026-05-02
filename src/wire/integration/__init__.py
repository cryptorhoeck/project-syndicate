"""Wire integration shims for agent OODA + Genesis + Operator hooks."""

from src.wire.integration.agent_context import (
    build_recent_signals_block,
    build_strategist_archive_helper,
)
from src.wire.integration.genesis_regime import (
    GenesisRegimeReviewHook,
    register_severity_5_review_hook,
)
from src.wire.integration.operator_halt import (
    OperatorHaltSignal,
    publish_halt_for_event,
)

__all__ = [
    "GenesisRegimeReviewHook",
    "OperatorHaltSignal",
    "build_recent_signals_block",
    "build_strategist_archive_helper",
    "publish_halt_for_event",
    "register_severity_5_review_hook",
]
