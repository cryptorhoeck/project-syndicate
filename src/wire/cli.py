"""
Wire admin CLI.

Usage:
  python -m src.wire.cli fetch <source-name>
  python -m src.wire.cli health [--verbose]
  python -m src.wire.cli digest-pending [--limit N]
  python -m src.wire.cli run-scheduler [--max-ticks N]
"""

from __future__ import annotations

import argparse
import logging
import sys
from typing import Optional
from typing import Optional

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from src.common.config import config
from src.wire.digest.haiku_digester import HaikuDigester, make_default_haiku_client
from src.wire.health.monitor import HealthMonitor
from src.wire.ingestors.runner import SourceRunner
from src.wire.ingestors.scheduler import IngestorScheduler
from src.wire.integration.halt_store import RedisHaltStore
from src.wire.integration.operator_halt import (
    get_halt_store as get_producer_halt_store,
    set_alert_publisher as set_producer_alert_publisher,
    set_halt_store as set_producer_halt_store,
)
from src.wire.models import WireSource


def _build_session_factory():
    engine = create_engine(config.database_url, pool_pre_ping=True)
    return sessionmaker(bind=engine)


def _initialize_producer_halt_store(
    *,
    redis_url: Optional[str] = None,
    key_prefix: Optional[str] = None,
) -> RedisHaltStore:
    """Construct + register the producer-side RedisHaltStore.

    Wire scheduler subprocess calls this at startup so subsequent
    `publish_halt_for_event` calls write through to the same Memurai
    keyspace the agents subprocess (consumer) reads from. Same
    fail-fast wiring contract as Warden / TradeExecutionService.

    Post-construction verification (Critic Finding 3, iteration 5):
    `redis_client.ping()` proves the Redis connection works, but it
    does NOT prove the module-level assignment landed. After
    `set_producer_halt_store(store)` we re-read it via
    `get_producer_halt_store()` and `sys.exit(2)` if it isn't the
    instance we just registered — covers any future bug where the
    setter no-ops or the import path skews between writer and reader.

    `redis_url` / `key_prefix` are optional overrides for tests
    (Critic Finding 2, iteration 5: the cross-process boundary test
    must exercise this same factory path, with isolated namespacing).
    Production callers pass nothing; the production code path is
    unchanged.
    """
    import redis as _redis_lib
    effective_url = redis_url if redis_url is not None else config.redis_url
    redis_client = _redis_lib.Redis.from_url(
        effective_url, decode_responses=True,
        socket_timeout=10, socket_connect_timeout=5, retry_on_timeout=True,
    )
    redis_client.ping()  # fail fast if Memurai is down
    if key_prefix is None:
        store = RedisHaltStore(redis_client=redis_client)
    else:
        store = RedisHaltStore(redis_client=redis_client, key_prefix=key_prefix)
    set_producer_halt_store(store)

    # Post-construction verification.
    registered = get_producer_halt_store()
    if registered is None:
        logging.getLogger(__name__).critical(
            "wire_scheduler_halt_store_assignment_lost",
            extra={"reason": "set_halt_store completed but get_halt_store returned None"},
        )
        sys.exit(2)
    if registered is not store:
        logging.getLogger(__name__).critical(
            "wire_scheduler_halt_store_assignment_mismatch",
            extra={
                "expected": id(store),
                "registered": id(registered),
                "reason": "module-level reference is not the instance we just registered",
            },
        )
        sys.exit(2)
    return store


# ---------------------------------------------------------------------------
# Subcommands
# ---------------------------------------------------------------------------


def cmd_fetch(args: argparse.Namespace) -> int:
    factory = _build_session_factory()
    with factory() as session:
        runner = SourceRunner(session=session)
        result = runner.run_source_by_name(args.source)
    print(
        f"source={result.source_name} success={result.success} "
        f"items_seen={result.items_seen} items_inserted={result.items_inserted} "
        f"error={result.error or ''}"
    )
    return 0 if result.success else 1


def cmd_health(args: argparse.Namespace) -> int:
    factory = _build_session_factory()
    with factory() as session:
        monitor = HealthMonitor(session)
        snapshots = monitor.snapshot_all()

    if not snapshots:
        print("(no sources configured)")
        return 0

    print(f"{'name':<28}{'status':<12}{'fails':<8}{'24h_items':<12}{'last_success':<22}error")
    print("-" * 100)
    for s in snapshots:
        last = s.last_fetch_success.isoformat(timespec="seconds") if s.last_fetch_success else "-"
        err = (s.last_fetch_error or "")[:40] if args.verbose else ""
        print(
            f"{s.source_name:<28}{s.status:<12}{s.consecutive_failures:<8}{s.items_last_24h:<12}{last:<22}{err}"
        )
    return 0


def cmd_digest_pending(args: argparse.Namespace) -> int:
    factory = _build_session_factory()
    haiku_client = make_default_haiku_client()
    with factory() as session:
        digester = HaikuDigester(haiku_client=haiku_client, session=session)
        results = digester.digest_pending(limit=args.limit)
    digested = sum(1 for r in results if r.status == "digested")
    dead = sum(1 for r in results if r.status == "dead_letter")
    total_cost = sum(r.cost_usd for r in results)
    print(f"digested={digested} dead_letter={dead} total_cost_usd={total_cost:.6f}")
    return 0


def _build_producer_alert_publisher():
    """Sync alert publisher used by `publish_halt_for_event` when a
    Redis-backed halt write fails. Posts the failure to the same Redis
    channel Agora uses for `system-alerts` (`agora:system-alerts`) so
    any subscriber in any subprocess can observe the producer-side
    failure. Mirrors the Critic-mandated "loudness" requirement (Finding
    1, iteration 5).

    Wire scheduler is sync; Agora's normal publish path is async. Going
    direct to Redis PUBLISH bypasses the async-boundary problem and
    reaches the exact channel any Agora consumer is already subscribed
    to. The CRITICAL log + raised exception remain the load-bearing
    loud signal — this is the cross-process mirror.
    """
    import json as _json
    import redis as _redis_lib
    redis_client = _redis_lib.Redis.from_url(
        config.redis_url, decode_responses=True,
        socket_timeout=5, socket_connect_timeout=5,
    )

    def _publish(event_class: str, payload: dict) -> None:
        message = {
            "channel": "system-alerts",
            "content": payload.get("summary", f"[{event_class}]"),
            "message_type": "alert",
            "importance": 2,  # critical
            "metadata": {"event_class": event_class, **payload},
        }
        redis_client.publish("agora:system-alerts", _json.dumps(message, default=str))

    return _publish


def cmd_run_scheduler(args: argparse.Namespace) -> int:
    factory = _build_session_factory()
    haiku_client = make_default_haiku_client() if args.with_digest else None
    # Initialize producer-side halt store so severity-5 events from this
    # subprocess are visible cross-process (to PaperTradingService running
    # in the agents subprocess). Fail-fast on Redis unavailability —
    # without this, severity-5 halts would be invisible to consumers and
    # the trading layer would silently keep trading affected coins.
    try:
        _initialize_producer_halt_store()
    except SystemExit:
        raise
    except Exception as exc:
        logging.getLogger(__name__).critical(
            "wire_scheduler_halt_store_init_failed", extra={"error": str(exc)},
        )
        sys.exit(2)

    # Wire alert publisher so producer-side Redis-write failures mirror
    # to system-alerts cross-process. Best-effort: if the publisher
    # itself raises, the CRITICAL log + raised OperatorHaltPublishError
    # remain the load-bearing signal.
    try:
        set_producer_alert_publisher(_build_producer_alert_publisher())
    except Exception as exc:
        logging.getLogger(__name__).warning(
            "wire_scheduler_alert_publisher_init_failed",
            extra={"error": str(exc)},
        )

    scheduler = IngestorScheduler(session_factory=factory, haiku_client=haiku_client)
    scheduler.run_forever(max_ticks=args.max_ticks)
    return 0


def cmd_list_sources(args: argparse.Namespace) -> int:
    factory = _build_session_factory()
    with factory() as session:
        rows = session.execute(select(WireSource).order_by(WireSource.name)).scalars().all()
    print(f"{'name':<28}{'tier':<6}{'enabled':<10}{'interval':<10}requires_key")
    for r in rows:
        print(
            f"{r.name:<28}{r.tier:<6}{str(r.enabled):<10}{r.fetch_interval_seconds:<10}"
            f"{r.requires_api_key}"
        )
    return 0


# ---------------------------------------------------------------------------
# Argparse wiring
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="syndicate.wire", description="The Wire admin CLI")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_fetch = sub.add_parser("fetch", help="Run one source once")
    p_fetch.add_argument("source", help="Source name (e.g. kraken_announcements)")
    p_fetch.set_defaults(func=cmd_fetch)

    p_health = sub.add_parser("health", help="Show per-source health")
    p_health.add_argument("--verbose", action="store_true")
    p_health.set_defaults(func=cmd_health)

    p_dp = sub.add_parser("digest-pending", help="Digest pending raw items via Haiku")
    p_dp.add_argument("--limit", type=int, default=50)
    p_dp.set_defaults(func=cmd_digest_pending)

    p_sched = sub.add_parser("run-scheduler", help="Run scheduler loop")
    p_sched.add_argument("--max-ticks", type=int, default=None)
    p_sched.add_argument(
        "--with-digest",
        action="store_true",
        help="Also run the Haiku digester each tick (requires ANTHROPIC_API_KEY)",
    )
    p_sched.set_defaults(func=cmd_run_scheduler)

    p_list = sub.add_parser("list-sources", help="List configured sources")
    p_list.set_defaults(func=cmd_list_sources)

    return parser


def main(argv: Optional[list[str]] = None) -> int:
    from src.wire.logging_config import configure_wire_logging
    configure_wire_logging()
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
