"""Wire digestion: raw_item -> wire_event via Haiku and deterministic rules."""

from src.wire.digest.deduper import canonical_hash, find_duplicate
from src.wire.digest.haiku_digester import (
    DigestionResult,
    HaikuDigester,
    HaikuOutputError,
)
from src.wire.digest.severity import (
    SeverityViolation,
    apply_severity_rules,
    bound_haiku_severity,
)

__all__ = [
    "DigestionResult",
    "HaikuDigester",
    "HaikuOutputError",
    "SeverityViolation",
    "apply_severity_rules",
    "bound_haiku_severity",
    "canonical_hash",
    "find_duplicate",
]
