"""
Etherscan large-transfer source.

Watches a configured set of exchange wallet addresses for inbound/outbound
transfers above a min-value threshold. Severity is deterministic:

  - whale -> non-exchange OR non-exchange -> whale: severity 2
  - exchange-wallet either side: severity 4 (potential market-impact flow)

Requires ETHERSCAN_API_KEY. Without a key, fetch_raw raises SourceFetchError
immediately so the runner records the source as degraded/failing rather than
silently emitting nothing.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Iterable

import httpx

from src.wire.constants import (
    SEVERITY_HIGH_IMPACT,
    SEVERITY_NOTABLE,
    SOURCE_ETHERSCAN_TRANSFERS,
)
from src.wire.sources.base import FetchedItem, SourceFetchError, WireSourceBase

logger = logging.getLogger(__name__)


# Public exchange wallets (well-known; source: Etherscan tags). Lowercase only.
DEFAULT_EXCHANGE_WALLETS: dict[str, str] = {
    "0x28c6c06298d514db089934071355e5743bf21d60": "binance_14",
    "0x21a31ee1afc51d94c2efccaa2092ad1028285549": "binance_15",
    "0x71660c4005ba85c37ccec55d0c4493e66fe775d3": "coinbase_1",
    "0xa090e606e30bd747d4e6245a1517ebe430f0057e": "coinbase_2",
    "0x267be1c1d684f78cb4f6a176c4911b741e4ffdc0": "kraken_4",
}


class EtherscanTransfersSource(WireSourceBase):
    name = SOURCE_ETHERSCAN_TRANSFERS
    display_name = "Etherscan Large Transfers"
    default_interval_seconds = 900
    requires_api_key = True
    api_key_env_var = "ETHERSCAN_API_KEY"

    DEFAULT_BASE_URL = "https://api.etherscan.io/api"

    def fetch_raw(self) -> Iterable[FetchedItem]:
        if not self.api_key:
            raise SourceFetchError(
                "etherscan_transfers requires ETHERSCAN_API_KEY"
            )
        base_url = self.config.get("base_url") or self.DEFAULT_BASE_URL
        min_value_eth = float(self.config.get("min_value_eth", 1000))
        wallets: dict[str, str] = self.config.get(
            "exchange_wallets"
        ) or dict(DEFAULT_EXCHANGE_WALLETS)

        client = self.http_client or httpx
        items: list[FetchedItem] = []
        for address, wallet_label in wallets.items():
            params = {
                "module": "account",
                "action": "txlist",
                "address": address,
                "startblock": 0,
                "endblock": 99999999,
                "page": 1,
                "offset": 25,
                "sort": "desc",
                "apikey": self.api_key,
            }
            try:
                response = client.get(
                    base_url,
                    params=params,
                    timeout=15.0,
                    follow_redirects=True,
                    headers={"User-Agent": "syndicate-wire/1.0"},
                )
                response.raise_for_status()
                payload = response.json()
            except Exception as exc:
                raise SourceFetchError(
                    f"etherscan_transfers fetch failed for {address}: {exc}"
                ) from exc

            if not isinstance(payload, dict):
                raise SourceFetchError("etherscan returned non-object body")
            txs = payload.get("result")
            if not isinstance(txs, list):
                # Etherscan returns "Max rate limit reached" or similar as string in result
                continue

            for tx in txs:
                if not isinstance(tx, dict):
                    continue
                ext_id = tx.get("hash")
                if not ext_id:
                    continue
                try:
                    value_wei = int(tx.get("value", "0"))
                except (TypeError, ValueError):
                    continue
                value_eth = value_wei / 1e18
                if value_eth < min_value_eth:
                    continue

                from_addr = (tx.get("from") or "").lower()
                to_addr = (tx.get("to") or "").lower()
                exchange_side = (
                    wallets.get(from_addr) or wallets.get(to_addr)
                )
                severity = SEVERITY_HIGH_IMPACT if exchange_side else SEVERITY_NOTABLE
                direction = "neutral"
                # Outflow from exchange tends to be bullish (custody pulled).
                # Inflow to exchange tends to be bearish (potential sell).
                if from_addr in wallets:
                    direction = "bullish"
                elif to_addr in wallets:
                    direction = "bearish"

                ts = tx.get("timeStamp")
                occurred_at = None
                if ts:
                    try:
                        occurred_at = datetime.fromtimestamp(int(ts), tz=timezone.utc)
                    except (TypeError, ValueError):
                        occurred_at = None

                payload_dict: dict[str, Any] = {
                    "hash": ext_id,
                    "from": from_addr,
                    "to": to_addr,
                    "value_eth": value_eth,
                    "block_number": tx.get("blockNumber"),
                    "watched_wallet": wallet_label,
                }
                haiku_brief = (
                    f"Etherscan whale transfer: {value_eth:,.0f} ETH from {from_addr[:10]}.. to "
                    f"{to_addr[:10]}.. (watched={wallet_label})"
                )

                items.append(
                    FetchedItem(
                        external_id=ext_id,
                        raw_payload=payload_dict,
                        occurred_at=occurred_at,
                        source_url=f"https://etherscan.io/tx/{ext_id}",
                        deterministic_severity=severity,
                        deterministic_event_type="whale_transfer",
                        deterministic_coin="ETH",
                        deterministic_direction=direction,
                        haiku_brief=haiku_brief,
                    )
                )

        return items
