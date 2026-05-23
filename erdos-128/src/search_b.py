"""
search_b.py — Strategy B driver: walk named triangle-free families and test each
candidate for being a counterexample to Erdos #128.

How it fits together
--------------------
  graphs.py   builds candidate graphs from named families.
  verify.py   decides whether one graph is a counterexample (screen_candidate
              does the fast-disqualify-then-exact-confirm dance).
  search_b.py (this file) chooses WHICH candidates to try, runs them, logs every
              result to results/run_log.jsonl, and shouts if one survives.

This is the "long-running script", so on direct execution it runs the project
boilerplate first (env check -> version note -> backup -> process lock).
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import networkx as nx

# Make the project root importable so `from src import ...` works whether this is
# run as a module (`python -m src.search_b`) or as a plain script
# (`python src/search_b.py`). When run as a script, Python puts src/ on the path
# instead of the project root, which would otherwise break the package imports.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from src import graphs
from src.boilerplate import RESULTS_DIR, log_run, release_lock, run_boilerplate
from src.verify import STATUS_COUNTEREXAMPLE, screen_candidate

__version__ = "0.1.0"


@dataclass
class Candidate:
    """One graph to test, with a human label for logs."""

    family: str
    label: str
    builder: Callable[[], nx.Graph]


def default_suite() -> list[Candidate]:
    """A starter sweep across the families in graphs.py.

    Mix of:
      * dense candidates worth real hope (odd-cycle blow-ups, middle-third Cayley),
      * known-bad / sparse references (complete bipartite, Petersen, Mycielski)
        so a run always exercises the verifier on clear NON-counterexamples too.

    Sizes are kept modest so the exact ILP confirm stays fast; bump them once the
    pipeline is trusted.
    """
    suite: list[Candidate] = []

    # Dense, genuinely promising: blow-ups of odd cycles (n = cycle_len * t).
    for t in (2, 3, 4, 5, 6):
        suite.append(Candidate("C5_blowup", f"C5_blowup(t={t}) n={5*t}",
                                lambda t=t: graphs.cycle_blowup(5, t)))
    for t in (2, 3, 4):
        suite.append(Candidate("C7_blowup", f"C7_blowup(t={t}) n={7*t}",
                                lambda t=t: graphs.cycle_blowup(7, t)))

    # Dense: middle-third Cayley graphs on Z_n.
    for n in (12, 15, 18, 21, 24, 30):
        suite.append(Candidate("middle_third_cayley", f"middle_third_cayley(n={n})",
                                lambda n=n: graphs.middle_third_cayley(n)))

    # Known-bad / sparse references (expected NON-counterexamples).
    suite.append(Candidate("complete_bipartite", "K_{10,10}",
                            lambda: graphs.complete_bipartite(10, 10)))
    suite.append(Candidate("petersen", "Petersen", graphs.petersen))
    suite.append(Candidate("mycielski", "Mycielski M_4 (Grotzsch)",
                            lambda: graphs.mycielskian(4)))

    return suite


def run_search(
    candidates: list[Candidate] | None = None,
    local_restarts: int = 30,
    ilp_time_limit: float | None = 60.0,
    verbose: bool = True,
) -> list[dict]:
    """Test every candidate, log each outcome, and collect summaries.

    Returns a list of plain dicts (one per candidate) so callers/tests can inspect
    results without depending on the dataclass. Any proven counterexample is also
    written to results/ as its own record and flagged loudly in the return value.
    """
    if candidates is None:
        candidates = default_suite()

    summaries: list[dict] = []
    found: list[dict] = []

    for cand in candidates:
        G = cand.builder()
        result = screen_candidate(
            G, local_restarts=local_restarts, ilp_time_limit=ilp_time_limit
        )
        summary = {
            "family": cand.family,
            "label": cand.label,
            "n": result.n,
            "m": result.m,
            "threshold": round(result.threshold, 3),
            "edges_total": G.number_of_edges(),
            "min_edges": result.min_edges,
            "min_edges_is_exact": result.min_edges_is_exact,
            "status": result.status,
            "notes": result.notes,
        }
        summaries.append(summary)
        log_run("search_b_candidate", summary)

        if verbose:
            print(f"[{result.status:>17}] {cand.label}: "
                  f"min_edges={result.min_edges} vs threshold={result.threshold:.2f}")

        if result.status == STATUS_COUNTEREXAMPLE:
            found.append(summary)

    if found:
        # A survivor is the whole point — record it prominently and announce it.
        log_run("search_b_COUNTEREXAMPLE_FOUND", {"count": len(found), "found": found})
        if verbose:
            print("\n*** COUNTEREXAMPLE(S) FOUND ***")
            for f in found:
                print(f"  {f['label']}: min_edges={f['min_edges']} > {f['threshold']}")
    elif verbose:
        print(f"\nNo counterexample among {len(candidates)} candidate(s).")

    return summaries


def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Strategy B search over triangle-free families.")
    p.add_argument("--restarts", type=int, default=30,
                   help="local-search restarts used in the fast disqualify phase")
    p.add_argument("--ilp-time-limit", type=float, default=60.0,
                   help="seconds before the exact ILP confirm gives up (inconclusive)")
    return p


if __name__ == "__main__":
    args = _build_arg_parser().parse_args()

    # Boilerplate prelude. We don't pass backup_paths because this run only
    # APPENDS to the run log (never overwrites), so there is nothing to snapshot.
    ctx = run_boilerplate(
        script_name="search_b",
        script_version=__version__,
        required_packages=["networkx", "numpy", "igraph", "pulp"],
    )
    try:
        run_search(local_restarts=args.restarts, ilp_time_limit=args.ilp_time_limit)
    finally:
        release_lock(ctx["lockfile"])
