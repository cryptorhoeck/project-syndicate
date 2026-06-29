"""Shared output type for the JJ signal pack.

A `TechnicalSignal` is a purely descriptive observation — what an analysis saw and
how strongly. It carries no execution authority; an agent's reasoning (and then the
Warden) decides what, if anything, to do with it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict


class Direction(str, Enum):
    """Direction an analysis leans — advisory, not an order."""

    LONG = "long"
    SHORT = "short"
    FLAT = "flat"


@dataclass(frozen=True)
class TechnicalSignal:
    """One technical-analysis observation.

    Parameters
    ----------
    source : str
        Which analysis produced this (e.g. "vwap_deviation", "rsi").
    direction : Direction
        Advisory lean: LONG / SHORT / FLAT.
    confidence : float
        Strength in [0.0, 1.0]. 0.0 means "no view".
    reason : str
        Short human-readable rationale (good for prompt injection / logs).
    details : dict
        Supporting numbers (e.g. the RSI value, VWAP, deviation %).
    """

    source: str
    direction: Direction
    confidence: float
    reason: str
    details: Dict[str, Any] = field(default_factory=dict)
