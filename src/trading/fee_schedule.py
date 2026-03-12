"""
Project Syndicate — Fee Schedule

Exchange fee rates for paper trading simulation.
Kraken (primary): 0.16% maker / 0.26% taker
Binance (secondary): 0.10% maker / 0.10% taker
"""

__version__ = "0.9.0"


class FeeSchedule:
    """Calculates trading fees based on exchange and order type."""

    EXCHANGES: dict[str, dict[str, float]] = {
        "kraken": {"maker": 0.0016, "taker": 0.0026},
        "binance": {"maker": 0.0010, "taker": 0.0010},
    }

    @classmethod
    def calculate_fee(
        cls,
        order_size_usd: float,
        order_type: str,
        exchange: str = "kraken",
    ) -> tuple[float, float]:
        """Calculate trading fee for an order.

        Args:
            order_size_usd: The order value in USD.
            order_type: "market" (taker) or "limit" (maker).
            exchange: Exchange name ("kraken" or "binance").

        Returns:
            Tuple of (fee_usd, fee_rate).
        """
        rates = cls.EXCHANGES.get(exchange, cls.EXCHANGES["kraken"])
        rate = rates["taker"] if order_type == "market" else rates["maker"]
        fee = order_size_usd * rate
        return round(fee, 8), rate

    @classmethod
    def get_rate(cls, order_type: str, exchange: str = "kraken") -> float:
        """Get the fee rate for an order type and exchange.

        Args:
            order_type: "market" or "limit".
            exchange: Exchange name.

        Returns:
            Fee rate as a decimal.
        """
        rates = cls.EXCHANGES.get(exchange, cls.EXCHANGES["kraken"])
        return rates["taker"] if order_type == "market" else rates["maker"]
