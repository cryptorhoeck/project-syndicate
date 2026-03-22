"""
Project Syndicate — Sandbox Runner

Executes agent-written Python scripts in a restricted environment.
Uses in-process execution with restricted globals and threading timeout.
"""

__version__ = "0.1.0"

import json
import logging
import threading
import time
import traceback
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


# Safe builtins for the sandbox
SAFE_BUILTINS = {
    "abs": abs, "all": all, "any": any, "bool": bool,
    "dict": dict, "divmod": divmod, "enumerate": enumerate,
    "filter": filter, "float": float, "format": format,
    "frozenset": frozenset, "int": int, "isinstance": isinstance,
    "len": len, "list": list, "map": map, "max": max, "min": min,
    "pow": pow, "print": lambda *a, **k: None,  # print is a no-op
    "range": range, "reversed": reversed, "round": round,
    "set": set, "slice": slice, "sorted": sorted, "str": str,
    "sum": sum, "tuple": tuple, "type": type, "zip": zip,
    "True": True, "False": False, "None": None,
}

# Allowed modules that can be imported
ALLOWED_MODULES = {
    "math", "statistics", "collections", "itertools", "functools",
    "datetime", "json", "re", "decimal", "copy",
    "numpy", "pandas",
}


def _safe_import(name, *args, **kwargs):
    """Restricted import that only allows whitelisted modules."""
    if name not in ALLOWED_MODULES:
        raise ImportError(f"Import of '{name}' is not allowed in the sandbox")
    return __builtins__.__import__(name, *args, **kwargs) if hasattr(__builtins__, '__import__') else __import__(name, *args, **kwargs)


async def execute_script(
    script: str,
    data_api=None,
    agent_id: int = 0,
    purpose: str = "",
) -> SandboxResult:
    """Execute a script in the sandbox with restricted globals.

    Uses in-process execution with threading timeout.
    Static analysis blocklist is the primary security layer.
    """
    script_h = hash_script(script)

    # 1. Static analysis
    safe, error = scan_script(script)
    if not safe:
        return SandboxResult(success=False, error=error, script_hash=script_h)

    # 2. Compile check
    compiles, error = try_compile(script)
    if not compiles:
        return SandboxResult(success=False, error=error, script_hash=script_h)

    # 3. Build restricted globals
    sandbox_globals = {"__builtins__": {**SAFE_BUILTINS, "__import__": _safe_import}}

    # Inject data API functions
    if data_api:
        sandbox_globals.update(data_api.get_injected_functions())

    # 4. Execute with timeout
    timeout = config.sandbox_timeout_seconds
    result_holder = {"output": None, "error": None, "success": False}

    def _run():
        try:
            exec(compile(script, '<agent_script>', 'exec'), sandbox_globals)
            result_holder["success"] = True
            if data_api:
                result_holder["output"] = data_api.get_captured_output()
        except Exception as e:
            result_holder["error"] = f"{type(e).__name__}: {str(e)[:500]}"

    start = time.time()
    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    thread.join(timeout=timeout)

    execution_ms = int((time.time() - start) * 1000)

    if thread.is_alive():
        # Timed out — can't kill a thread in Python, but it's daemon so it dies with process
        return SandboxResult(
            success=False,
            error=f"Execution timed out after {timeout}s",
            execution_time_ms=execution_ms,
            cost_usd=_calculate_cost(execution_ms),
            script_hash=script_h,
        )

    return SandboxResult(
        success=result_holder["success"],
        output=result_holder["output"],
        error=result_holder["error"],
        execution_time_ms=execution_ms,
        cost_usd=_calculate_cost(execution_ms),
        script_hash=script_h,
    )


def _calculate_cost(execution_time_ms: int) -> float:
    """Calculate sandbox execution cost."""
    return config.sandbox_base_cost_usd + (execution_time_ms * config.sandbox_time_rate_usd_per_ms)
