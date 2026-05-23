"""
verify.py — decide whether a graph G is a counterexample to Erdos Problem #128.

The target (restated)
---------------------
A counterexample is a TRIANGLE-FREE graph G on n vertices such that every induced
subgraph on at least m = floor(n/2) vertices has STRICTLY MORE than n^2/50 edges.

The monotonicity lemma (why we only check size m = floor(n/2))
--------------------------------------------------------------
Let f(k) = the minimum number of edges over all k-vertex induced subgraphs of G.

    Claim: f is non-decreasing, i.e. f(k) <= f(k+1) for every k.

    Proof: Take any (k+1)-subset T achieving f(k+1). Let v be a vertex of T with
    the fewest neighbours *inside* T. Dropping v gives a k-subset T' with
        e(T') = e(T) - deg_T(v) <= e(T) = f(k+1).
    By definition f(k) <= e(T'), so f(k) <= f(k+1).  QED.

Consequence: if f(m) > n^2/50 for m = floor(n/2), then f(k) > n^2/50 for every
k >= m as well — so EVERY induced subgraph on >= m vertices clears the bar.
Conversely, if f(m) <= n^2/50 there is an m-subset that fails, so G is not a
counterexample. Therefore:

    G satisfies the density condition  <==>  f(floor(n/2)) > n^2/50.

That single equivalence is what makes the search tractable: instead of checking
all subgraphs of all sizes, we compute one number, f(floor(n/2)), via search_a.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import networkx as nx

from src.search_a import count_internal_edges, min_edge_subset

__version__ = "0.1.0"

# Human-readable outcomes. Using named strings (not bare booleans) keeps the
# "we couldn't tell" case explicit, which matters because a heuristic-only run
# can be genuinely inconclusive.
STATUS_COUNTEREXAMPLE = "counterexample"
STATUS_NOT = "not_counterexample"
STATUS_INCONCLUSIVE = "inconclusive"
STATUS_HAS_TRIANGLE = "has_triangle"


@dataclass
class VerificationResult:
    """Everything we learned about one candidate graph."""

    n: int
    m: int                      # floor(n/2): the only subset size we must check
    threshold: float            # n^2 / 50
    is_triangle_free: bool
    status: str                 # one of the STATUS_* constants
    min_edges: int | None = None        # f(m) if computed (exact or upper bound)
    min_edges_is_exact: bool = False    # True only if proven (ilp/bruteforce)
    min_subset: tuple = ()              # the witnessing sparse subset, if any
    method: str = ""
    notes: str = ""

    @property
    def is_counterexample(self) -> bool:
        """Only a PROVEN counterexample counts. Inconclusive is never True."""
        return self.status == STATUS_COUNTEREXAMPLE


def edge_threshold(n: int) -> float:
    """The bar a half-graph must clear: n^2 / 50 (kept as a float on purpose).

    The condition is 'strictly more than n^2/50'. Since edge counts are integers,
    a subset passes iff its edge count > n^2/50 (no rounding of the threshold).
    """
    return (n * n) / 50.0


def is_triangle_free(G: nx.Graph) -> bool:
    """Return True iff G contains no triangle.

    Method: a triangle through edge (u, v) exists iff u and v share a common
    neighbour. So we check, for every edge, whether the neighbour sets intersect.
    This is simple to reason about and fast enough for our graph sizes.
    """
    adj = {v: set(G.neighbors(v)) for v in G}
    for (u, v) in G.edges():
        # Any common neighbour w gives the triangle u-v-w.
        if adj[u] & adj[v]:
            return False
    return True


def verify_counterexample(G: nx.Graph, method: str = "bruteforce") -> VerificationResult:
    """Single-method verification, used mainly by tests for an exact answer.

    With method="bruteforce" or "ilp" the min-edge computation is exact, so the
    result is a definitive counterexample / not-counterexample. With
    method="local" the minimum is only an upper bound, so a "passing" looking
    number is reported as INCONCLUSIVE rather than a counterexample.
    """
    n = G.number_of_nodes()
    m = n // 2
    thr = edge_threshold(n)

    if not is_triangle_free(G):
        return VerificationResult(
            n=n, m=m, threshold=thr, is_triangle_free=False,
            status=STATUS_HAS_TRIANGLE, method=method,
            notes="G contains a triangle, so it cannot be a counterexample.",
        )

    min_edges, subset, is_exact = min_edge_subset(G, m, method=method)
    passes = min_edges > thr

    if not passes:
        # We found an m-subset at or below the threshold. Its mere existence is a
        # proof that the density condition fails, regardless of method.
        status = STATUS_NOT
        notes = f"Found a {m}-subset with {min_edges} edges (<= threshold {thr:.2f})."
    elif is_exact:
        status = STATUS_COUNTEREXAMPLE
        notes = f"Sparsest {m}-subset has {min_edges} edges > threshold {thr:.2f}."
    else:
        # Heuristic couldn't find a sparse-enough subset, but it doesn't prove
        # none exists. Need an exact method to confirm.
        status = STATUS_INCONCLUSIVE
        notes = (
            f"Heuristic minimum {min_edges} > threshold {thr:.2f}, but unproven. "
            "Re-run with method='ilp' to confirm."
        )

    return VerificationResult(
        n=n, m=m, threshold=thr, is_triangle_free=True, status=status,
        min_edges=min_edges, min_edges_is_exact=is_exact, min_subset=subset,
        method=method, notes=notes,
    )


def screen_candidate(
    G: nx.Graph,
    local_restarts: int = 30,
    local_seed: int | None = 0,
    ilp_time_limit: float | None = 60.0,
) -> VerificationResult:
    """Two-phase screen, the efficient path used by the Strategy B search loop.

    Phase 1 (cheap disqualify): run local search. If it finds an m-subset with
        <= threshold edges, a sparse half provably exists -> NOT a counterexample.
        We stop here, having spent almost no time.
    Phase 2 (rigorous confirm): only if phase 1 fails to disqualify do we pay for
        an exact ILP solve. If the exact minimum still beats the threshold ->
        proven counterexample. If ILP times out without proving optimality ->
        inconclusive.

    This ordering matters because the overwhelming majority of candidates DO have
    a sparse half (e.g. complete bipartite graphs have a zero-edge half), so the
    fast phase-1 disqualifier saves the expensive solver for the rare survivors.
    """
    n = G.number_of_nodes()
    m = n // 2
    thr = edge_threshold(n)

    if not is_triangle_free(G):
        return VerificationResult(
            n=n, m=m, threshold=thr, is_triangle_free=False,
            status=STATUS_HAS_TRIANGLE, method="screen",
            notes="G contains a triangle.",
        )

    # Phase 1 — heuristic disqualify.
    local_min, local_sub, _ = min_edge_subset(
        G, m, method="local", restarts=local_restarts, seed=local_seed
    )
    if local_min <= thr:
        return VerificationResult(
            n=n, m=m, threshold=thr, is_triangle_free=True, status=STATUS_NOT,
            min_edges=local_min, min_edges_is_exact=False, min_subset=local_sub,
            method="screen:local",
            notes=f"Local search found a {m}-subset with {local_min} edges "
                  f"(<= threshold {thr:.2f}); disqualified.",
        )

    # Phase 2 — exact confirm.
    ilp_min, ilp_sub, is_exact = min_edge_subset(
        G, m, method="ilp", time_limit=ilp_time_limit
    )
    if ilp_min <= thr:
        status, notes = STATUS_NOT, (
            f"ILP sparsest {m}-subset has {ilp_min} edges (<= threshold {thr:.2f})."
        )
    elif is_exact:
        status, notes = STATUS_COUNTEREXAMPLE, (
            f"PROVEN: sparsest {m}-subset has {ilp_min} edges > threshold {thr:.2f}."
        )
    else:
        status, notes = STATUS_INCONCLUSIVE, (
            f"ILP hit time limit with best {ilp_min} > threshold {thr:.2f}; unproven."
        )

    return VerificationResult(
        n=n, m=m, threshold=thr, is_triangle_free=True, status=status,
        min_edges=ilp_min, min_edges_is_exact=is_exact, min_subset=ilp_sub,
        method="screen:ilp", notes=notes,
    )
