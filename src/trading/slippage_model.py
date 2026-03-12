"""
Project Syndicate — Slippage Model

Order-book-based slippage calculation with noise for realistic simulation.
Walks the order book to compute VWAP, adds ±20% noise, enforces minimum floor.
"""

__version__ = "0.9.0"

import logging
import random

from src.common.config import config

logger = logging.getLogger(__name__)


class SlippageModel:
    """Calculates realistic slippage from order book depth."""

    MIN_SLIPPAGE: float = config.min_slippage_pct
    NOISE_RANGE: float = config.slippage_noise_range
    DEPTH_PENALTY: float = config.book_depth_penalty_pct

    async def calculate_slippage(
        self,
        order_size_usd: float,
        symbol: str,
        side: str,
        price_cache=None,
    ) -> float:
        """Calculate slippage percentage for an order.

        Args:
            order_size_usd: Order value in USD.
            symbol: Trading pair.
            side: "buy" or "sell".
            price_cache: Optional PriceCache instance for order book data.

        Returns:
            Slippage as a decimal (e.g., 0.001 = 0.1%).
        """
        order_book = None
        if price_cache:
            book_data, _ = await price_cache.get_order_book(symbol)
            if book_data:
                order_book = book_data

        if order_book:
            slippage = self._walk_book(order_size_usd, side, order_book)
        else:
            # Fallback: estimate based on order size
            slippage = self._estimate_slippage(order_size_usd)

        # Add noise: ±NOISE_RANGE
        noise_factor = random.uniform(1.0 - self.NOISE_RANGE, 1.0 + self.NOISE_RANGE)
        slippage *= noise_factor

        # Floor: minimum slippage
        slippage = max(slippage, self.MIN_SLIPPAGE)

        return slippage

    def _walk_book(self, order_size_usd: float, side: str, order_book: dict) -> float:
        """Walk the order book to calculate VWAP-based slippage.

        Args:
            order_size_usd: Size in USD.
            side: "buy" walks asks, "sell" walks bids.
            order_book: Dict with 'asks' and 'bids' lists of [price, quantity].

        Returns:
            Slippage as a decimal.
        """
        levels = order_book.get("asks", []) if side == "buy" else order_book.get("bids", [])

        if not levels:
            return self._estimate_slippage(order_size_usd)

        best_price = levels[0][0] if levels else 0
        if best_price <= 0:
            return self._estimate_slippage(order_size_usd)

        filled_usd = 0.0
        total_qty = 0.0

        for level in levels:
            price = level[0]
            qty = level[1]
            level_value = price * qty
            remaining = order_size_usd - filled_usd

            if remaining <= 0:
                break

            fill_value = min(level_value, remaining)
            fill_qty = fill_value / price
            total_qty += fill_qty
            filled_usd += fill_value

        if filled_usd <= 0 or total_qty <= 0:
            return self._estimate_slippage(order_size_usd)

        # VWAP = total USD filled / total quantity filled
        vwap = filled_usd / total_qty

        slippage_pct = abs(vwap - best_price) / best_price if best_price > 0 else 0

        # Depth penalty if order exceeds visible book
        if filled_usd < order_size_usd:
            slippage_pct += self.DEPTH_PENALTY

        return slippage_pct

    def _estimate_slippage(self, order_size_usd: float) -> float:
        """Fallback slippage estimate when no order book is available.

        Args:
            order_size_usd: Order value in USD.

        Returns:
            Estimated slippage as a decimal.
        """
        # Rough tiered model
        if order_size_usd < 100:
            return 0.0005  # 0.05%
        elif order_size_usd < 1000:
            return 0.001  # 0.1%
        elif order_size_usd < 10000:
            return 0.002  # 0.2%
        else:
            return 0.005  # 0.5%
