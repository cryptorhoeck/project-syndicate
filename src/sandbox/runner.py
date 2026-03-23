"""
Project Syndicate — Sandbox Runner

Executes agent-written Python scripts in a subprocess for true isolation.
Security layers: static analysis blocklist → subprocess boundary → restricted builtins.
"""

__version__ = "0.2.0"

import json
import logging
import subprocess
import sys
import time
from dataclasses import dataclass

from src.common.config import config
from src.sandbox.security import scan_script, try_compile, hash_script

logger = logging.getLogger(__name__)


@dataclass
class SandboxResult:
    """Result of a sandbox execution."""
    success: bool
    output: any = None
    error: str | None = None
    execution_time_ms: int = 0
    cost_usd: float = 0.0
    script_hash: str = ""


# The wrapper script that runs inside the subprocess.
# It sets up a restricted namespace, injects data API functions,
# executes the agent's script, and returns the result via stdout.
_SUBPROCESS_WRAPPER = r'''
import sys, json, math, statistics, collections, itertools, functools
import datetime, decimal, re, copy

# Block dangerous builtins
_blocked = {"open", "exec", "eval", "compile", "__import__", "breakpoint",
            "exit", "quit", "globals", "locals", "getattr", "setattr", "delattr",
            "vars", "dir", "super", "classmethod", "staticmethod",
            "property", "memoryview", "bytearray"}

if isinstance(__builtins__, dict):
    _safe = {k: v for k, v in __builtins__.items() if k not in _blocked}
else:
    _safe = {k: getattr(__builtins__, k) for k in dir(__builtins__) if k not in _blocked and not k.startswith("_")}

_safe["True"] = True
_safe["False"] = False
_safe["None"] = None

_allowed_mods = {"math","statistics","collections","itertools","functools",
                 "datetime","json","re","decimal","copy","numpy","pandas"}
def _restricted_import(name, *a, **k):
    if name not in _allowed_mods:
        raise ImportError(f"Import of '{name}' is blocked")
    return __import__(name, *a, **k)
_safe["__import__"] = _restricted_import

# Read data from stdin
_payload = json.loads(sys.stdin.read())
_pc = _payload.get("prices", {})
_tc = _payload.get("tickers", {})
_tr = _payload.get("trades", [])
_po = _payload.get("positions", [])
_ag = _payload.get("agora", {})
_rg = _payload.get("regime", {})
_out = [None]

def get_price_history(symbol="BTC/USDT", timeframe="1h", limit=100):
    return _pc.get(f"{symbol}:{timeframe}", [])[:min(limit, 500)]

def get_current_price(symbol="BTC/USDT"):
    return _tc.get(symbol, {})

def get_my_trades(limit=50):
    return _tr[:min(limit, 50)]

def get_my_positions():
    return _po

def get_agora_messages(channel="market-intel", limit=50):
    return _ag.get(channel, [])[:min(limit, 50)]

def get_market_regime():
    return _rg

def output(data):
    s = json.dumps(data)
    if len(s) > 10240:
        raise ValueError("Output too large")
    _out[0] = data

_ns = {
    "__builtins__": _safe,
    "get_price_history": get_price_history,
    "get_current_price": get_current_price,
    "get_my_trades": get_my_trades,
    "get_my_positions": get_my_positions,
    "get_agora_messages": get_agora_messages,
    "get_market_regime": get_market_regime,
    "output": output,
    "math": math, "statistics": statistics, "collections": collections,
    "json": json, "re": re, "datetime": datetime, "decimal": decimal,
}

try:
    import numpy as np
    for a in ("load","save","savez","savetxt","genfromtxt","fromfile","tofile","memmap"):
        if hasattr(np, a): delattr(np, a)
    if hasattr(np, "ctypes"): delattr(np, "ctypes")
    _ns["np"] = np
    _ns["numpy"] = np
except ImportError:
    pass

try:
    import pandas as pd
    for a in ("read_csv","read_excel","read_json","read_html","read_sql",
              "read_parquet","read_pickle","read_feather","read_fwf",
              "read_hdf","read_stata","read_sas","to_pickle","ExcelWriter"):
        if hasattr(pd, a): delattr(pd, a)
    _ns["pd"] = pd
    _ns["pandas"] = pd
except ImportError:
    pass

try:
    exec(compile(_payload["script"], "<agent_script>", "exec"), _ns)
    print(json.dumps({"ok": True, "out": _out[0]}))
except Exception as e:
    print(json.dumps({"ok": False, "err": f"{type(e).__name__}: {str(e)[:500]}"}))
'''


async def execute_script(
    script: str,
    data_api=None,
    agent_id: int = 0,
    purpose: str = "",
) -> SandboxResult:
    """Execute a script in a subprocess sandbox.

    Security: static analysis blocklist → subprocess isolation → restricted builtins.
    The subprocess has no access to parent memory, DB connections, or file handles.
    Hard timeout via subprocess.run() — kills the process on expiry.
    """
    script_h = hash_script(script)

    # 1. Static analysis (fast rejection of obviously bad scripts)
    safe, error = scan_script(script)
    if not safe:
        return SandboxResult(success=False, error=error, script_hash=script_h)

    # 2. Compile check
    compiles, error = try_compile(script)
    if not compiles:
        return SandboxResult(success=False, error=error, script_hash=script_h)

    # 3. Build data payload for subprocess
    data_dict = {}
    if data_api:
        data_dict = data_api.to_serializable()

    payload = json.dumps({
        "script": script,
        **data_dict,
    })

    # 4. Execute in subprocess with hard timeout
    timeout = config.sandbox_timeout_seconds
    start = time.perf_counter()

    try:
        creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        result = subprocess.run(
            [sys.executable, "-c", _SUBPROCESS_WRAPPER],
            input=payload,
            capture_output=True,
            text=True,
            timeout=timeout,
            creationflags=creationflags,
        )

        execution_ms = int((time.perf_counter() - start) * 1000)
        cost = _calculate_cost(execution_ms)

        if result.returncode == 0 and result.stdout.strip():
            # Parse the last line of stdout (the JSON result)
            lines = result.stdout.strip().split("\n")
            output_data = json.loads(lines[-1])

            if output_data.get("ok"):
                return SandboxResult(
                    success=True,
                    output=output_data.get("out"),
                    execution_time_ms=execution_ms,
                    cost_usd=cost,
                    script_hash=script_h,
                )
            else:
                return SandboxResult(
                    success=False,
                    error=output_data.get("err", "Script error"),
                    execution_time_ms=execution_ms,
                    cost_usd=cost,
                    script_hash=script_h,
                )
        else:
            stderr = result.stderr[:1000] if result.stderr else "No output"
            return SandboxResult(
                success=False,
                error=stderr,
                execution_time_ms=execution_ms,
                cost_usd=cost,
                script_hash=script_h,
            )

    except subprocess.TimeoutExpired:
        execution_ms = int((time.perf_counter() - start) * 1000)
        return SandboxResult(
            success=False,
            error=f"Execution timed out after {timeout}s",
            execution_time_ms=execution_ms,
            cost_usd=_calculate_cost(execution_ms),
            script_hash=script_h,
        )
    except Exception as e:
        execution_ms = int((time.perf_counter() - start) * 1000)
        return SandboxResult(
            success=False,
            error=f"Sandbox error: {str(e)[:500]}",
            execution_time_ms=execution_ms,
            cost_usd=_calculate_cost(execution_ms),
            script_hash=script_h,
        )


def _calculate_cost(execution_time_ms: int) -> float:
    """Calculate sandbox execution cost."""
    return config.sandbox_base_cost_usd + (execution_time_ms * config.sandbox_time_rate_usd_per_ms)
