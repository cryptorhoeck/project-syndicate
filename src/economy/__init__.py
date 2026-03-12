"""
Project Syndicate — Economy Package

Internal Economy: reputation-based marketplace for intel, reviews, and services.
"""

__version__ = "0.5.0"

from src.economy.economy_service import EconomyService
from src.economy.intel_market import IntelMarket
from src.economy.review_market import ReviewMarket
from src.economy.service_market import ServiceMarket
from src.economy.settlement_engine import SettlementEngine
from src.economy.gaming_detection import GamingDetector
from src.economy.schemas import (
    SignalDirection,
    SignalStatus,
    EndorsementStatus,
    ReviewRequestStatus,
    ReviewVerdict,
    GamingFlagType,
    GamingFlagSeverity,
    IntelSignalResponse,
    IntelEndorsementResponse,
    ReviewRequestResponse,
    ReviewAssignmentResponse,
    CriticAccuracyResponse,
    ServiceListingResponse,
    GamingFlagResponse,
    EconomyStats,
)

__all__ = [
    "EconomyService",
    "IntelMarket",
    "ReviewMarket",
    "ServiceMarket",
    "SettlementEngine",
    "GamingDetector",
    "SignalDirection",
    "SignalStatus",
    "EndorsementStatus",
    "ReviewRequestStatus",
    "ReviewVerdict",
    "GamingFlagType",
    "GamingFlagSeverity",
    "IntelSignalResponse",
    "IntelEndorsementResponse",
    "ReviewRequestResponse",
    "ReviewAssignmentResponse",
    "CriticAccuracyResponse",
    "ServiceListingResponse",
    "GamingFlagResponse",
    "EconomyStats",
]
