"""
Project Syndicate — Sandbox Security

Static analysis and compilation for agent-written Python scripts.
Two layers: blocklist scanning + RestrictedPython compilation.
"""

__version__ = "0.1.0"

import hashlib
import re

# Patterns rejected before compilation
BLOCKED_PATTERNS = [
    (r'\bimport\s+os\b', "os module blocked"),
    (r'\bimport\s+sys\b', "sys module blocked"),
    (r'\bimport\s+subprocess\b', "subprocess blocked"),
    (r'\bimport\s+socket\b', "network access blocked"),
    (r'\bimport\s+requests\b', "network access blocked"),
    (r'\bimport\s+urllib\b', "network access blocked"),
    (r'\bimport\s+http\b', "network access blocked"),
    (r'\bimport\s+ctypes\b', "ctypes blocked"),
    (r'\bimport\s+importlib\b', "importlib blocked"),
    (r'\bimport\s+shutil\b', "filesystem blocked"),
    (r'\bimport\s+pathlib\b', "filesystem blocked"),
    (r'\bimport\s+pickle\b', "pickle blocked"),
    (r'\bimport\s+shelve\b', "shelve blocked"),
    (r'\bimport\s+signal\b', "signal blocked"),
    (r'\bimport\s+threading\b', "threading blocked"),
    (r'\bimport\s+multiprocessing\b', "multiprocessing blocked"),
    (r'\bimport\s+asyncio\b', "asyncio blocked"),
    (r'\b__import__\b', "__import__ blocked"),
    (r'\beval\s*\(', "eval() blocked"),
    (r'\bexec\s*\(', "exec() blocked"),
    (r'\bcompile\s*\(', "compile() blocked"),
    (r'\bopen\s*\(', "open() blocked — use data API functions"),
    (r'\bglobals\s*\(', "globals() blocked"),
    (r'\blocals\s*\(', "locals() blocked"),
    (r'\bgetattr\s*\(', "getattr() blocked"),
    (r'\bsetattr\s*\(', "setattr() blocked"),
    (r'\bdelattr\s*\(', "delattr() blocked"),
    (r'\bbreakpoint\s*\(', "breakpoint() blocked"),
    (r'\bexit\s*\(', "exit() blocked"),
    (r'\bquit\s*\(', "quit() blocked"),
    (r'__\w+__', "dunder attribute access blocked"),
]

ALLOWED_IMPORTS = [
    "math", "statistics", "collections", "itertools", "functools",
    "datetime", "json", "re", "decimal", "copy",
    "numpy", "pandas",
]

MAX_SCRIPT_LENGTH = 5000


def scan_script(script: str) -> tuple[bool, str | None]:
    """Static analysis scan. Returns (is_safe, error_message)."""
    if len(script) > MAX_SCRIPT_LENGTH:
        return False, f"Script too long: {len(script)} chars (max {MAX_SCRIPT_LENGTH})"

    for pattern, message in BLOCKED_PATTERNS:
        if re.search(pattern, script):
            return False, f"Blocked: {message}"

    # Check imports are on whitelist
    import_matches = re.findall(r'import\s+(\w+)', script)
    from_matches = re.findall(r'from\s+(\w+)', script)
    all_imports = set(import_matches + from_matches)

    for mod in all_imports:
        if mod not in ALLOWED_IMPORTS:
            return False, f"Blocked: import {mod} not in allowed list"

    return True, None


def hash_script(script: str) -> str:
    """SHA-256 hash of script for dedup and integrity."""
    return hashlib.sha256(script.encode()).hexdigest()


def try_compile(script: str) -> tuple[bool, str | None]:
    """Attempt to compile the script. Catches syntax errors early."""
    try:
        from RestrictedPython import compile_restricted
        try:
            compile_restricted(script, '<agent_script>', 'exec')
            return True, None
        except SyntaxError as e:
            return False, f"Syntax error: {e}"
    except ImportError:
        # RestrictedPython not available, fall back to standard compile
        try:
            compile(script, '<agent_script>', 'exec')
            return True, None
        except SyntaxError as e:
            return False, f"Syntax error: {e}"
