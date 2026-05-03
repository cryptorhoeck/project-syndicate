"""
critic.py — Automated adversarial review for Project Syndicate hotfixes.

Usage:
    python critic.py --branch hotfix/warden-trade-gate-wiring
    python critic.py --branch hotfix/warden-trade-gate-wiring --report path/to/CC_REPORT.md
    python critic.py --branch hotfix/warden-trade-gate-wiring --max-diff-lines 800

What it does:
    1. Reads CRITIC_PROMPT.md from the project root
    2. Gathers context: branch diff vs main, latest commit message,
       and an optional CC report file
    3. Calls the Anthropic API with prompt + context using a fresh conversation
    4. Prints the review to stdout
    5. Saves the review to reviews/CRITIC_<branch>_<timestamp>.md

Design notes:
    - Critic gets NO project memory. Fresh context every time.
    - Critic uses the same ANTHROPIC_API_KEY from .env
    - Failure modes are loud, not silent (no swallowed exceptions)
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

try:
    from anthropic import Anthropic
except ImportError:
    print("ERROR: anthropic SDK not installed. Run: pip install anthropic", file=sys.stderr)
    sys.exit(2)

try:
    from dotenv import load_dotenv
except ImportError:
    print("ERROR: python-dotenv not installed. Run: pip install python-dotenv", file=sys.stderr)
    sys.exit(2)


# ---- config ----------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent
PROMPT_FILE = PROJECT_ROOT / "CRITIC_PROMPT.md"
REVIEWS_DIR = PROJECT_ROOT / "reviews"
DEFAULT_MAX_DIFF_LINES = 1500
CRITIC_MODEL = "claude-opus-4-5"  # use the strongest model for adversarial review
MAX_TOKENS = 2048


# ---- helpers ---------------------------------------------------------------

def run_git(args: list[str]) -> str:
    """Run a git command and return stdout. Raise on failure."""
    result = subprocess.run(
        ["git"] + args,
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"git {' '.join(args)} failed (exit {result.returncode}):\n{result.stderr}"
        )
    return result.stdout


def get_branch_diff(branch: str, max_lines: int) -> tuple[str, bool]:
    """Return (diff_text, was_truncated)."""
    diff = run_git(["diff", f"main...{branch}"])
    lines = diff.splitlines()
    if len(lines) > max_lines:
        truncated = "\n".join(lines[:max_lines])
        truncated += f"\n\n[... diff truncated at {max_lines} lines, total was {len(lines)} ...]"
        return truncated, True
    return diff, False


def get_commit_messages(branch: str) -> str:
    """Get all commit messages on the branch since main."""
    return run_git(["log", f"main..{branch}", "--pretty=format:%h %s%n%n%b%n---"])


def get_files_changed(branch: str) -> str:
    """Summary of files changed."""
    return run_git(["diff", "--stat", f"main...{branch}"])


def load_prompt() -> str:
    if not PROMPT_FILE.exists():
        print(
            f"ERROR: {PROMPT_FILE} not found.\n"
            f"Save the Critic prompt to {PROMPT_FILE} before running.",
            file=sys.stderr,
        )
        sys.exit(2)
    return PROMPT_FILE.read_text(encoding="utf-8")


def load_report(report_path: Optional[Path]) -> Optional[str]:
    if report_path is None:
        return None
    if not report_path.exists():
        print(f"ERROR: report file not found: {report_path}", file=sys.stderr)
        sys.exit(2)
    return report_path.read_text(encoding="utf-8")


def assemble_context(
    branch: str,
    report_text: Optional[str],
    max_diff_lines: int,
) -> str:
    """Build the user-message body for Critic."""
    sections = []

    sections.append(f"# Branch under review\n\n`{branch}`\n")

    sections.append("# Files changed\n\n```\n" + get_files_changed(branch) + "\n```\n")

    sections.append("# Commit messages on this branch\n\n```\n" + get_commit_messages(branch) + "\n```\n")

    if report_text:
        sections.append("# CC report (provided)\n\n" + report_text + "\n")
    else:
        sections.append("# CC report\n\n_(no separate report file provided; review the diff and commits)_\n")

    diff_text, truncated = get_branch_diff(branch, max_diff_lines)
    if truncated:
        sections.append(
            "# Diff (TRUNCATED — review may be incomplete)\n\n```diff\n"
            + diff_text
            + "\n```\n"
        )
    else:
        sections.append("# Diff\n\n```diff\n" + diff_text + "\n```\n")

    return "\n".join(sections)


def call_critic(api_key: str, system_prompt: str, user_content: str) -> str:
    client = Anthropic(api_key=api_key)
    response = client.messages.create(
        model=CRITIC_MODEL,
        max_tokens=MAX_TOKENS,
        system=system_prompt,
        messages=[{"role": "user", "content": user_content}],
    )
    # response.content is a list of blocks; concatenate text blocks
    parts = []
    for block in response.content:
        if hasattr(block, "text"):
            parts.append(block.text)
    return "\n".join(parts).strip()


def save_review(branch: str, review_text: str) -> Path:
    REVIEWS_DIR.mkdir(parents=True, exist_ok=True)
    safe_branch = branch.replace("/", "_").replace("\\", "_")
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = REVIEWS_DIR / f"CRITIC_{safe_branch}_{timestamp}.md"
    out_path.write_text(review_text, encoding="utf-8")
    return out_path


# ---- main ------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run automated Critic review on a Project Syndicate branch."
    )
    parser.add_argument(
        "--branch",
        required=True,
        help="Branch to review (e.g. hotfix/warden-trade-gate-wiring)",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=None,
        help="Optional path to a CC report file to include verbatim in the review context",
    )
    parser.add_argument(
        "--max-diff-lines",
        type=int,
        default=DEFAULT_MAX_DIFF_LINES,
        help=f"Truncate diff above this many lines (default {DEFAULT_MAX_DIFF_LINES})",
    )
    args = parser.parse_args()

    # Load API key from .env
    load_dotenv(PROJECT_ROOT / ".env")
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key or api_key.strip() in ("", "sk-ant-"):
        print(
            "ERROR: ANTHROPIC_API_KEY missing or incomplete in .env",
            file=sys.stderr,
        )
        return 2

    # Load Critic prompt (the one you saved to CRITIC_PROMPT.md)
    system_prompt = load_prompt()

    # Verify branch exists
    try:
        run_git(["rev-parse", "--verify", args.branch])
    except RuntimeError as e:
        print(f"ERROR: branch '{args.branch}' not found in this repo.\n{e}", file=sys.stderr)
        return 2

    # Verify main exists (we diff against it)
    try:
        run_git(["rev-parse", "--verify", "main"])
    except RuntimeError as e:
        print(f"ERROR: 'main' branch not found.\n{e}", file=sys.stderr)
        return 2

    # Optional CC report
    report_text = load_report(args.report)

    # Assemble context
    print(f"Gathering context for {args.branch}...", file=sys.stderr)
    user_content = assemble_context(args.branch, report_text, args.max_diff_lines)

    # Approximate token budget warning (rough: 4 chars/token)
    approx_tokens = len(user_content) // 4
    if approx_tokens > 100_000:
        print(
            f"WARNING: context is ~{approx_tokens:,} tokens. "
            "Consider reducing --max-diff-lines.",
            file=sys.stderr,
        )

    # Call Critic
    print("Calling Critic...", file=sys.stderr)
    try:
        review = call_critic(api_key, system_prompt, user_content)
    except Exception as e:
        # Loud failure. No silent swallow.
        print(f"ERROR: Critic API call failed: {e}", file=sys.stderr)
        return 3

    if not review:
        print("ERROR: Critic returned empty response. Treating as failure.", file=sys.stderr)
        return 3

    # Save and print
    out_path = save_review(args.branch, review)
    print("\n" + "=" * 72)
    print(f"CRITIC REVIEW — {args.branch}")
    print("=" * 72 + "\n")
    print(review)
    print("\n" + "=" * 72)
    print(f"Saved to: {out_path}")
    print("=" * 72)

    # Exit non-zero if review contains FLAG so this can gate scripts/CI later
    if "FLAG" in review and "GREEN-LIGHT" not in review:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())