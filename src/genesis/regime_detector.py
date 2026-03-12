"""
Project Syndicate — Market Regime Detector

Rules-based regime classification using BTC price data:
  - BULL:     Golden cross + expanding market cap + low volatility
  - BEAR:     Death cross + contracting market cap
  - CRAB:     No clear cross + low volatility + flat market cap
  - VOLATILE: Any regime + volatility > 80th percentile
"""

__version__ = "0.2.0"

import math
from datetime import datetime, timezone

import numpy as np
import structlog
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from src.common.config import config
from src.common.models import MarketRegime

logger = structlog.get_logger()


class RegimeDetector:
    """Rules-based market regime detector using exchange data."""

    def __init__(
        self,
        exchange_service,
        db_session_factory: sessionmaker | None = None,
    ) -> None:
        self.log = logger.bind(component="regime_detector")
        self.exchange = exchange_service

        if db_session_factory:
            self.db_session_factory = db_session_factory
        else:
            engine = create_engine(config.database_url)
            self.db_session_factory = sessionmaker(bind=engine)

    async def detect_regime(self) -> dict:
        """Detect current market regime and compare with last recorded."""
        if self.exchange is None:
            self.log.warning("no_exchange_service", reason="Cannot detect regime without exchange data")
            return {
                "regime": "unknown",
                "changed": False,
                "indicators": {},
                "reason": "No exchange service configured",
            }

        # 1. Fetch market data
        data = await self.exchange.get_market_data_for_regime()
        ohlcv = data.get("ohlcv", [])

        if len(ohlcv) < 50:
            self.log.warning("insufficient_ohlcv_data", candles=len(ohlcv))
            return {
                "regime": "unknown",
                "changed": False,
                "indicators": {},
                "reason": "Insufficient OHLCV data",
            }

        # 2. Calculate indicators
        closes = np.array([candle[4] for candle in ohlcv], dtype=np.float64)

        # Moving averages
        ma_20 = float(np.mean(closes[-20:]))
        ma_50 = float(np.mean(closes[-50:]))
        golden_cross = ma_20 > ma_50
        btc_price = float(closes[-1])

        # 30-day volatility (annualized)
        if len(closes) >= 31:
            daily_returns = np.diff(closes[-31:]) / closes[-31:-1]
            volatility_30d = float(np.std(daily_returns, ddof=1) * math.sqrt(365))
        else:
            volatility_30d = 0.0

        # Historical volatility for percentile calculation (use all available data)
        if len(closes) >= 31:
            all_daily_returns = np.diff(closes) / closes[:-1]
            # Rolling 30-day volatility
            rolling_vols = []
            for i in range(30, len(all_daily_returns)):
                window = all_daily_returns[i - 30:i]
                vol = float(np.std(window, ddof=1) * math.sqrt(365))
                rolling_vols.append(vol)
            if rolling_vols:
                vol_80_pct = float(np.percentile(rolling_vols, 80))
            else:
                vol_80_pct = volatility_30d * 1.5
        else:
            vol_80_pct = volatility_30d * 1.5

        # Market cap trend (use 20-day MA of closes as proxy)
        market_cap_expanding = ma_20 > float(np.mean(closes[-30:-10])) if len(closes) >= 30 else True

        btc_dominance = data.get("btc_dominance", 50.0)
        total_market_cap = data.get("total_market_cap", 0.0)

        indicators = {
            "btc_price": round(btc_price, 2),
            "ma_20": round(ma_20, 2),
            "ma_50": round(ma_50, 2),
            "golden_cross": golden_cross,
            "volatility_30d": round(volatility_30d, 4),
            "vol_80_percentile": round(vol_80_pct, 4),
            "market_cap_expanding": market_cap_expanding,
            "btc_dominance": round(btc_dominance, 2),
        }

        # 3. Classify regime
        high_vol = volatility_30d > vol_80_pct

        if high_vol:
            regime = "volatile"
        elif golden_cross and market_cap_expanding:
            regime = "bull"
        elif not golden_cross and not market_cap_expanding:
            regime = "bear"
        else:
            regime = "crab"

        # 4. Compare with last recorded regime
        previous_regime = self._get_last_regime()
        changed = previous_regime is None or previous_regime != regime

        # 5. If changed, record it
        if changed:
            self._record_regime(
                regime=regime,
                btc_price=btc_price,
                ma_20=ma_20,
                ma_50=ma_50,
                volatility_30d=volatility_30d,
                btc_dominance=btc_dominance,
                total_market_cap=total_market_cap,
            )
            self.log.info(
                "regime_changed",
                regime=regime,
                previous=previous_regime,
                btc_price=btc_price,
            )

        return {
            "regime": regime,
            "changed": changed,
            "previous_regime": previous_regime,
            "indicators": indicators,
        }

    def _get_last_regime(self) -> str | None:
        """Get the most recently recorded regime."""
        with self.db_session_factory() as session:
            result = session.execute(
                select(MarketRegime)
                .order_by(MarketRegime.detected_at.desc())
                .limit(1)
            ).scalar_one_or_none()
            return result.regime if result else None

    def _record_regime(
        self,
        regime: str,
        btc_price: float,
        ma_20: float,
        ma_50: float,
        volatility_30d: float,
        btc_dominance: float,
        total_market_cap: float,
    ) -> None:
        """Insert a new regime record into the database."""
        with self.db_session_factory() as session:
            record = MarketRegime(
                regime=regime,
                btc_price=btc_price,
                btc_ma_20=ma_20,
                btc_ma_50=ma_50,
                btc_volatility_30d=volatility_30d,
                btc_dominance=btc_dominance,
                total_market_cap=total_market_cap,
            )
            session.add(record)
            session.commit()
