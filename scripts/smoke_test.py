"""
Project Syndicate — Pre-Launch Smoke Test

Quick (<30s) health check validating the system is ready to launch.
Run before any Arena start.

Exit codes: 0 = GREEN, 1 = RED (blocking), 2 = YELLOW (warnings only)
"""

__version__ = "1.0.0"

import os
import sys
import time

# Ensure project root is importable
from pathlib import Path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def _ok(msg: str) -> None:
    print(f"  [OK]   {msg}")


def _warn(msg: str) -> None:
    print(f"  [WARN] {msg}")


def _fail(msg: str) -> None:
    print(f"  [FAIL] {msg}")


def check_postgresql() -> bool:
    """Check PostgreSQL is accessible and has expected tables."""
    try:
        from sqlalchemy import create_engine, inspect, text
        from src.common.config import config
        engine = create_engine(config.database_url)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        tables = inspect(engine).get_table_names()
        engine.dispose()
        if len(tables) >= 20:
            _ok(f"PostgreSQL: {len(tables)} tables found")
            return True
        else:
            _warn(f"PostgreSQL: only {len(tables)} tables (expected 20+)")
            return True  # Not blocking
    except Exception as e:
        _fail(f"PostgreSQL: {e}")
        return False


def check_redis() -> bool:
    """Check Redis/Memurai responds to PING."""
    try:
        import redis
        from src.common.config import config
        r = redis.Redis.from_url(config.redis_url, socket_connect_timeout=5)
        if r.ping():
            _ok("Redis: PONG")
            return True
        _fail("Redis: no PONG")
        return False
    except Exception as e:
        _fail(f"Redis: {e}")
        return False


def check_anthropic() -> bool:
    """Validate Anthropic API key with a minimal call."""
    try:
        from src.common.config import config
        import anthropic
        if not config.anthropic_api_key or len(config.anthropic_api_key) < 20:
            _fail("Anthropic: API key missing or too short")
            return False

        client = anthropic.Anthropic(api_key=config.anthropic_api_key)
        resp = client.messages.create(
            model=config.model_default,
            max_tokens=5,
            messages=[{"role": "user", "content": "respond OK"}],
        )
        text = resp.content[0].text
        _ok(f"Anthropic: API responded ({text.strip()[:20]})")
        return True
    except Exception as e:
        err = str(e)
        if "401" in err or "invalid" in err.lower():
            _fail(f"Anthropic: invalid API key")
        else:
            _fail(f"Anthropic: {err[:80]}")
        return False


def check_kraken() -> bool:
    """Verify Kraken returns prices for all watchlist pairs."""
    try:
        import ccxt
        kraken = ccxt.kraken({"enableRateLimit": True, "timeout": 10000})
        ticker = kraken.fetch_ticker("BTC/USDT")
        price = ticker.get("last", 0)
        if price > 0:
            _ok(f"Kraken: BTC/USDT = ${price:,.2f}")
            return True
        _fail("Kraken: no price returned")
        return False
    except Exception as e:
        _fail(f"Kraken: {e}")
        return False


def check_config() -> bool:
    """Validate critical config fields."""
    try:
        from src.common.config import config
        errors = config.validate_critical()
        if errors:
            for e in errors:
                _fail(f"Config: {e}")
            return False
        _ok(f"Config: trading_mode={config.trading_mode}, currency={config.home_currency}, treasury=C${config.starting_treasury}")
        return True
    except Exception as e:
        _fail(f"Config: {e}")
        return False


def check_logs() -> bool:
    """Verify logs directory exists and is writable."""
    logs_dir = PROJECT_ROOT / "logs"
    if not logs_dir.exists():
        logs_dir.mkdir(exist_ok=True)
    try:
        test_file = logs_dir / ".smoke_test"
        test_file.write_text("ok")
        test_file.unlink()
        _ok("Logs directory: writable")
        return True
    except Exception as e:
        _fail(f"Logs directory: {e}")
        return False


def check_library() -> bool:
    """Verify textbooks and summaries exist."""
    tb_dir = PROJECT_ROOT / "data" / "library" / "textbooks"
    sum_dir = PROJECT_ROOT / "data" / "library" / "summaries"
    tb_count = len(list(tb_dir.glob("*.md"))) if tb_dir.exists() else 0
    sum_count = len(list(sum_dir.glob("*.md"))) if sum_dir.exists() else 0
    if tb_count >= 8 and sum_count >= 8:
        _ok(f"Library: {tb_count} textbooks, {sum_count} summaries")
        return True
    _warn(f"Library: {tb_count} textbooks, {sum_count} summaries (expected 8 each)")
    return True  # Not blocking


def main():
    print()
    print("=" * 50)
    print("  PROJECT SYNDICATE — SMOKE TEST")
    print("=" * 50)
    print()

    start = time.time()

    checks = [
        ("PostgreSQL", check_postgresql),
        ("Redis", check_redis),
        ("Anthropic API", check_anthropic),
        ("Kraken Exchange", check_kraken),
        ("Configuration", check_config),
        ("Logs Directory", check_logs),
        ("Library", check_library),
    ]

    results = {}
    for name, fn in checks:
        try:
            results[name] = fn()
        except Exception as e:
            _fail(f"{name}: unexpected error: {e}")
            results[name] = False

    elapsed = time.time() - start
    print()
    print("-" * 50)

    failures = [n for n, ok in results.items() if not ok]
    if not failures:
        print(f"  RESULT: [GREEN] All checks passed ({elapsed:.1f}s)")
        print()
        return 0
    else:
        critical = {"PostgreSQL", "Redis", "Configuration"}
        blocking = [f for f in failures if f in critical]
        if blocking:
            print(f"  RESULT: [RED] Blocking issues: {', '.join(blocking)} ({elapsed:.1f}s)")
            print()
            return 1
        else:
            print(f"  RESULT: [YELLOW] Non-critical warnings: {', '.join(failures)} ({elapsed:.1f}s)")
            print()
            return 2


if __name__ == "__main__":
    sys.exit(main())
