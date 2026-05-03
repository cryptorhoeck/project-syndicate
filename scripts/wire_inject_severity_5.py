"""
Inject a synthetic severity-5 wire_event for live validation (Step C).

Bypasses the Haiku digest path on purpose — we are validating the
post-digest hooks (operator_halt, genesis_regime_review, ticker publish)
fire correctly when a sev-5 event lands in the database.

Usage:
    python scripts/wire_inject_severity_5.py \
        --coin BTC --event-type exchange_outage --summary "synthetic outage test"

Defaults to BTC/exchange_outage if no flags supplied.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
from datetime import datetime, timezone

# Match existing script convention so `python scripts/wire_inject_severity_5.py`
# works without setting PYTHONPATH.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.common.config import config
from src.wire.constants import (
    AGORA_EVENT_TICKER,
    OPERATOR_HALT_EVENT_TYPES,
    SEVERITY_CRITICAL,
)
from src.wire.integration.genesis_regime import (
    maybe_dispatch as dispatch_severity_5,
    register_severity_5_review_hook,
)
from src.wire.integration.operator_halt import (
    list_active,
    publish_halt_for_event,
)
from src.wire.models import WireEvent
from src.wire.publishing.ticker import WireTicker


def _canonical_hash(coin: str, event_type: str, summary: str) -> str:
    payload = f"{(coin or '').upper()}|{event_type.lower()}|{summary.strip().lower()}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Inject a synthetic severity-5 wire_event for validation."
    )
    parser.add_argument("--coin", default="BTC")
    parser.add_argument(
        "--event-type",
        default="exchange_outage",
        choices=sorted(OPERATOR_HALT_EVENT_TYPES) + ["hack", "exploit", "chain_halt"],
    )
    parser.add_argument(
        "--summary",
        default="SYNTHETIC: Step C validation injection — exchange outage test",
    )
    parser.add_argument(
        "--source-url",
        default="https://internal.syndicate/test/severity-5-injection",
    )
    args = parser.parse_args()

    captured_ticker_events = []
    captured_genesis_payloads = []
    register_severity_5_review_hook(captured_genesis_payloads.append)

    engine = create_engine(config.database_url, pool_pre_ping=True)
    Session = sessionmaker(bind=engine)

    with Session() as session:
        canonical = _canonical_hash(args.coin, args.event_type, args.summary)
        now = datetime.now(timezone.utc)
        event = WireEvent(
            canonical_hash=canonical,
            coin=args.coin,
            event_type=args.event_type,
            severity=SEVERITY_CRITICAL,
            direction="bearish",
            summary=args.summary,
            source_url=args.source_url,
            occurred_at=now,
            haiku_cost_usd=0.0,
        )
        session.add(event)
        session.flush()

        ticker = WireTicker(
            publisher=lambda cls, payload: captured_ticker_events.append((cls, payload))
        )
        ticker.publish_event(session, event)

        dispatch_severity_5(
            event_id=event.id,
            severity=event.severity,
            coin=event.coin,
            event_type=event.event_type,
            summary=event.summary,
            occurred_at_iso=event.occurred_at.isoformat(),
        )
        publish_halt_for_event(
            event_id=event.id,
            coin=event.coin,
            event_type=event.event_type,
            severity=event.severity,
            summary=event.summary,
        )

        session.commit()
        injected_id = event.id

    print("=== INJECTION COMPLETE ===")
    print(f"event_id={injected_id}")
    print(f"coin={args.coin} event_type={args.event_type} severity={SEVERITY_CRITICAL}")
    print(f"timestamp={datetime.now(timezone.utc).isoformat(timespec='seconds')}")
    print()
    print("=== HOOKS FIRED ===")
    print(f"ticker.publish_event:     {len(captured_ticker_events)} event(s)")
    for cls, payload in captured_ticker_events:
        print(
            f"  {cls} severity={payload.get('severity')} coin={payload.get('coin')} "
            f"event_type={payload.get('event_type')}"
        )
    print(f"genesis.regime_review:    {len(captured_genesis_payloads)} hook(s) fired")
    for p in captured_genesis_payloads:
        print(
            f"  event_id={p.get('event_id')} severity={p.get('severity')} "
            f"coin={p.get('coin')} event_type={p.get('event_type')}"
        )

    print()
    print("=== OPERATOR HALT REGISTRY ===")
    actives = list_active()
    print(f"active_signals_total: {len(actives)}")
    for s in actives:
        print(
            f"  trigger_event_id={s.trigger_event_id} coin={s.coin} "
            f"event_type={s.event_type} severity={s.severity} "
            f"issued_at={s.issued_at.isoformat(timespec='seconds')} "
            f"expires_at={s.expires_at.isoformat(timespec='seconds')} "
            f"auto_resume_minutes={int((s.expires_at - s.issued_at).total_seconds() / 60)}"
        )

    # Per-coin scope check.
    btc_actives = list_active(coin="BTC")
    eth_actives = list_active(coin="ETH")
    print()
    print("=== HALT SCOPE PROOF (per-coin, NOT colony-wide) ===")
    print(f"halts_for_BTC: {len(btc_actives)}")
    print(f"halts_for_ETH: {len(eth_actives)}")
    print(f"AGORA_EVENT_TICKER class: {AGORA_EVENT_TICKER}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
