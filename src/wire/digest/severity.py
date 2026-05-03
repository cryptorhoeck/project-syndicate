"""
Severity assignment.

Two paths produce a final severity for a wire_event:

1. Deterministic source rules (FetchedItem.deterministic_severity): if a source
   classifies an item with certainty (e.g. Kraken withdrawal halt -> 5), that
   value wins outright. This is the only path that can yield severity 5.

2. Haiku judgment (1-4): for items without a deterministic severity, Haiku
   assigns 1-4. Haiku attempting 5 is treated as a violation and capped to 4
   with a logged Agora event.

`apply_severity_rules` is the single funnel through which all severities pass.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from src.wire.constants import (
    HAIKU_MAX_SEVERITY,
    SEVERITY_CRITICAL,
    SEVERITY_HIGH_IMPACT,
    SEVERITY_TRIVIAL,
)


@dataclass(slots=True, frozen=True)
class SeverityResult:
    """Outcome of severity adjudication for a single item."""

    severity: int
    capped: bool
    reason: str  # 'deterministic', 'haiku', 'haiku_capped', 'fallback'


class SeverityViolation(Exception):
    """Raised when severity assignment hits an unrecoverable state."""


def bound_haiku_severity(value: int) -> tuple[int, bool]:
    """Clamp a Haiku-proposed severity into [1, HAIKU_MAX_SEVERITY].

    Returns (final_value, was_capped). Out-of-range high values are capped to
    HAIKU_MAX_SEVERITY (4) — never to 5. Out-of-range low or non-int values
    fall back to SEVERITY_TRIVIAL.
    """
    try:
        as_int = int(value)
    except (TypeError, ValueError):
        return SEVERITY_TRIVIAL, False

    if as_int < SEVERITY_TRIVIAL:
        return SEVERITY_TRIVIAL, False
    if as_int > HAIKU_MAX_SEVERITY:
        return HAIKU_MAX_SEVERITY, True
    return as_int, False


def apply_severity_rules(
    deterministic_severity: Optional[int],
    haiku_severity: Optional[int],
) -> SeverityResult:
    """Adjudicate severity for one item.

    Precedence:
      1. deterministic_severity (any value 1-5) — wins, never modified
      2. haiku_severity (1-4) — clamped via bound_haiku_severity
      3. fallback to SEVERITY_TRIVIAL
    """
    if deterministic_severity is not None:
        if not (SEVERITY_TRIVIAL <= int(deterministic_severity) <= SEVERITY_CRITICAL):
            raise SeverityViolation(
                f"deterministic severity out of range: {deterministic_severity}"
            )
        return SeverityResult(
            severity=int(deterministic_severity),
            capped=False,
            reason="deterministic",
        )

    if haiku_severity is None:
        return SeverityResult(severity=SEVERITY_TRIVIAL, capped=False, reason="fallback")

    bounded, capped = bound_haiku_severity(haiku_severity)
    return SeverityResult(
        severity=bounded,
        capped=capped,
        reason="haiku_capped" if capped else "haiku",
    )


__all__ = [
    "SEVERITY_HIGH_IMPACT",
    "SeverityResult",
    "SeverityViolation",
    "apply_severity_rules",
    "bound_haiku_severity",
]
