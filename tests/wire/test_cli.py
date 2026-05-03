"""CLI argparse smoke tests."""

import pytest

from src.wire.cli import build_parser


def test_parser_fetch() -> None:
    parser = build_parser()
    args = parser.parse_args(["fetch", "kraken_announcements"])
    assert args.cmd == "fetch"
    assert args.source == "kraken_announcements"


def test_parser_health_default() -> None:
    parser = build_parser()
    args = parser.parse_args(["health"])
    assert args.cmd == "health"
    assert args.verbose is False


def test_parser_health_verbose() -> None:
    parser = build_parser()
    args = parser.parse_args(["health", "--verbose"])
    assert args.verbose is True


def test_parser_digest_pending_limit() -> None:
    parser = build_parser()
    args = parser.parse_args(["digest-pending", "--limit", "10"])
    assert args.cmd == "digest-pending"
    assert args.limit == 10


def test_parser_run_scheduler_max_ticks() -> None:
    parser = build_parser()
    args = parser.parse_args(["run-scheduler", "--max-ticks", "1"])
    assert args.cmd == "run-scheduler"
    assert args.max_ticks == 1
    assert args.with_digest is False


def test_parser_list_sources() -> None:
    parser = build_parser()
    args = parser.parse_args(["list-sources"])
    assert args.cmd == "list-sources"


def test_parser_requires_subcommand() -> None:
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args([])
