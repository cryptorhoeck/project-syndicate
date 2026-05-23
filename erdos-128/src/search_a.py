"""
search_a.py — Strategy A: find the sparsest induced subgraph on a fixed number
of vertices.

WHY this is the heart of verification
-------------------------------------
Erdos #128 asks (for a counterexample) that EVERY induced subgraph on at least
m = floor(n/2) vertices has more than n^2/50 edges. There are astronomically many
such subgraphs, so we cannot check them all directly.

The saving grace is a monotonicity lemma (proved in verify.py): the *minimum*
edge count over m-vertex subsets is non-decreasing in m. So the single hardest
case is the smallest allowed size, m = floor(n/2). If we can find the
fewest-edge m-subset and it still beats the threshold, every larger subgraph
beats it too.

Finding that fewest-edge m-subset is NP-hard in general, so we offer three tools:
  * bruteforce  - exact, but only for tiny graphs (checks every subset).
  * ilp         - exact via integer programming (PuLP/CBC). Our rigorous workhorse.
  * local       - fast heuristic. Returns an UPPER bound on the true minimum,
                  i.e. "there exists a subset this sparse". Great for quickly
                  DISQUALIFYING a candidate, never for confirming one.

The asymmetry matters:
  * To PROVE a counterexample we need the EXACT minimum (bruteforce/ilp).
  * To DISQUALIFY a candidate, any single sparse subset suffices, so the cheap
    heuristic's upper bound is enough.
"""

from __future__ import annotations

import random
from itertools import combinations

import networkx as nx
import pulp

__version__ = "0.1.0"


def count_internal_edges(G: nx.Graph, subset) -> int:
    """Count edges of G with BOTH endpoints inside `subset`.

    WHY its own function: every method below needs to score a subset, and tests
    use it as ground truth. Centralising it avoids three slightly-different
    copies that could disagree.
    """
    s = set(subset)
    total = 0
    for v in s:
        # Count neighbours of v that are also selected. Each internal edge is
        # seen twice (once from each endpoint), so we halve at the end.
        total += sum(1 for w in G.neighbors(v) if w in s)
    return total // 2


def min_edge_subset_bruteforce(G: nx.Graph, m: int) -> tuple[int, tuple, bool]:
    """Exact minimum by checking every m-subset. Tiny graphs only.

    Returns (min_edge_count, the_subset, is_exact=True). The boolean is always
    True here; it exists so all three finders share one return shape, letting the
    caller treat them interchangeably.
    """
    nodes = list(G.nodes)
    if not 0 <= m <= len(nodes):
        raise ValueError(f"m={m} out of range for {len(nodes)} vertices")

    best_count: int | None = None
    best_subset: tuple = ()
    for combo in combinations(nodes, m):
        c = count_internal_edges(G, combo)
        if best_count is None or c < best_count:
            best_count, best_subset = c, combo
            if best_count == 0:
                break  # can't do better than zero edges; stop early
    return best_count or 0, best_subset, True


def min_edge_subset_ilp(
    G: nx.Graph, m: int, time_limit: float | None = None
) -> tuple[int, tuple, bool]:
    """Exact minimum via integer programming (PuLP + CBC).

    Model:
      * binary x_v = 1 iff vertex v is selected; we require sum(x_v) == m.
      * binary y_e = 1 iff edge e is "internal" (both endpoints selected).
        The single constraint  y_e >= x_u + x_v - 1  forces y_e to 1 when both
        endpoints are chosen. We don't need an upper bound on y_e: because we are
        MINIMISING sum(y_e), the solver pushes each y_e down to exactly
        max(0, x_u + x_v - 1), which is precisely the logical AND for binaries.
      * objective: minimise sum(y_e) = number of internal edges.

    Returns (min_count, subset, is_exact). is_exact is True only when CBC proves
    optimality; a time-limited run that stops early returns its best bound with
    is_exact=False, so callers never mistake a guess for a proof.
    """
    nodes = list(G.nodes)
    if not 0 <= m <= len(nodes):
        raise ValueError(f"m={m} out of range for {len(nodes)} vertices")

    prob = pulp.LpProblem("min_edge_subset", pulp.LpMinimize)
    x = {v: pulp.LpVariable(f"x_{i}", cat="Binary") for i, v in enumerate(nodes)}
    edges = list(G.edges())
    y = {e: pulp.LpVariable(f"y_{i}", cat="Binary") for i, e in enumerate(edges)}

    prob += pulp.lpSum(y.values())              # objective: internal edge count
    prob += pulp.lpSum(x.values()) == m         # pick exactly m vertices
    for (u, v) in edges:
        prob += y[(u, v)] >= x[u] + x[v] - 1    # both selected => edge counted

    prob.solve(pulp.PULP_CBC_CMD(msg=0, timeLimit=time_limit))

    subset = tuple(v for v in nodes if x[v].value() is not None and x[v].value() > 0.5)
    # Recompute the count from the actual subset rather than trusting the LP
    # objective value — robust against any solver rounding quirks.
    count = count_internal_edges(G, subset)
    is_exact = pulp.LpStatus[prob.status] == "Optimal"
    return count, subset, is_exact


def min_edge_subset_local_search(
    G: nx.Graph,
    m: int,
    restarts: int = 20,
    max_no_improve: int = 200,
    seed: int | None = None,
) -> tuple[int, tuple, bool]:
    """Heuristic minimum via random-restart swap local search.

    Idea: start from a random m-subset, then repeatedly swap one selected vertex
    out and one unselected vertex in, preferring swaps that reduce the number of
    internal edges. When stuck, try a random non-worsening swap to escape a local
    minimum. Keep the best subset seen across several restarts.

    Returns (count, subset, is_exact=False). It is NEVER exact: it can only show
    "a subset at least this sparse exists" (an upper bound on the true minimum).
    The reported count is recomputed exactly with count_internal_edges, so the
    upper bound itself is trustworthy even though the search is heuristic.
    """
    nodes = list(G.nodes)
    n = len(nodes)
    if not 0 <= m <= n:
        raise ValueError(f"m={m} out of range for {n} vertices")
    if m == 0 or m == n:
        sub = tuple(nodes[:m])
        return count_internal_edges(G, sub), sub, False

    rng = random.Random(seed)
    adj = {v: set(G.neighbors(v)) for v in nodes}

    best_count: int | None = None
    best_subset: tuple = ()

    for _ in range(restarts):
        sel = set(rng.sample(nodes, m))
        # indeg[v] = number of v's neighbours currently selected (for any v).
        indeg = {v: len(adj[v] & sel) for v in nodes}
        no_improve = 0

        while no_improve < max_no_improve:
            unsel = [v for v in nodes if v not in sel]
            # Greedy candidates: drop the selected vertex with the most internal
            # edges, add the unselected vertex with the fewest connections inward.
            out_v = max(sel, key=lambda v: indeg[v])
            in_v = min(unsel, key=lambda v: indeg[v])

            def swap_delta(o, i):
                # Change in internal edges if we remove o and add i. Removing o
                # deletes indeg[o] edges; adding i creates indeg[i] edges, minus
                # one if i-o was itself an edge (o is leaving, so that edge can't
                # count toward the new subset).
                add = indeg[i] - (1 if i in adj[o] else 0)
                return add - indeg[o]

            delta = swap_delta(out_v, in_v)
            if delta >= 0:
                # Greedy move doesn't help — try a random move to escape, but only
                # accept it if it doesn't make things worse.
                out_v = rng.choice(list(sel))
                in_v = rng.choice(unsel)
                delta = swap_delta(out_v, in_v)
                if delta > 0:
                    no_improve += 1
                    continue

            # Apply the swap and keep indeg in sync.
            sel.remove(out_v)
            for w in adj[out_v]:
                indeg[w] -= 1
            sel.add(in_v)
            for w in adj[in_v]:
                indeg[w] += 1

            no_improve = 0 if delta < 0 else no_improve + 1

        cur = count_internal_edges(G, sel)  # exact score of this restart's result
        if best_count is None or cur < best_count:
            best_count, best_subset = cur, tuple(sel)

    return best_count or 0, best_subset, False


def min_edge_subset(
    G: nx.Graph, m: int, method: str = "ilp", **kwargs
) -> tuple[int, tuple, bool]:
    """Dispatch to one of the three finders by name.

    method: "ilp" (exact, default), "bruteforce" (exact, tiny graphs),
            "local" (heuristic upper bound). Extra kwargs pass through, e.g.
            min_edge_subset(G, m, method="local", restarts=50, seed=1).
    """
    if method == "ilp":
        return min_edge_subset_ilp(G, m, **kwargs)
    if method == "bruteforce":
        return min_edge_subset_bruteforce(G, m, **kwargs)
    if method == "local":
        return min_edge_subset_local_search(G, m, **kwargs)
    raise ValueError(f"unknown method {method!r}; use 'ilp', 'bruteforce', or 'local'")
