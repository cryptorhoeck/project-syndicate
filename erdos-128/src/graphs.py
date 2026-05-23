"""
graphs.py — generators for named triangle-free graph families (Strategy B).

Strategy B is the "generate good candidates" half of the search. Rather than
search blindly, we build graphs from families known to be triangle-free, then
hand each to the verifier (verify.py) to see whether it might be a counterexample
to Erdos #128.

Which families are promising, and why
-------------------------------------
A counterexample needs EVERY half of the graph to stay dense (> n^2/50 edges).
That rules out graphs with a sparse "side":

  * Complete bipartite K_{a,b} is triangle-free and edge-dense overall, but one
    whole side is an independent set -> a half with ZERO edges. Hopeless, yet we
    include it precisely as a known-bad sanity reference.

  * Sparse regular graphs (Petersen, generalised Petersen, Mycielski) have far
    too few edges total (~n*const), so even their densest halves fall miles below
    n^2/50. Included mainly so the verifier has clear NON-counterexamples to
    confirm against.

The genuinely interesting (dense, no obvious sparse half) families:

  * Odd-cycle blow-ups (e.g. C5, C7): replace each cycle vertex with an
    independent set and fully join consecutive groups. Triangle-free (the base
    odd cycle is), and dense (~n^2 / cycle_len edges).

  * "Middle-third" Cayley graphs on Z_n: connect i ~ j when their circular
    distance lands in the middle third. The connection set is sum-free, making
    the graph triangle-free, with degree ~ n/3 (so ~ n^2/6 edges) and a lot of
    symmetry that resists a sparse half.

Every generator documents WHEN its output is triangle-free, but the source of
truth is always verify.is_triangle_free — generators never silently assume it.
"""

from __future__ import annotations

from itertools import combinations

import networkx as nx

__version__ = "0.1.0"


# ---------------------------------------------------------------------------
# Sparse / classical references (used as known NON-counterexamples in tests)
# ---------------------------------------------------------------------------
def petersen() -> nx.Graph:
    """The Petersen graph: 10 vertices, 3-regular, girth 5 (so triangle-free).

    It is isomorphic to the Kneser graph KG(5, 2) — kneser(5, 2) below produces
    the same graph up to relabelling.
    """
    return nx.petersen_graph()


def kneser(n: int, k: int) -> nx.Graph:
    """Kneser graph KG(n, k): vertices are the k-subsets of {0,..,n-1};
    two vertices are adjacent iff the subsets are DISJOINT.

    Triangle-free condition: a triangle needs three pairwise-disjoint k-subsets,
    which requires 3k <= n. So KG(n, k) is triangle-free iff n < 3k (e.g.
    KG(5, 2): 3k = 6 > 5, triangle-free — it is the Petersen graph).
    """
    verts = list(combinations(range(n), k))
    G = nx.Graph()
    G.add_nodes_from(verts)
    for a, b in combinations(verts, 2):
        if set(a).isdisjoint(b):
            G.add_edge(a, b)
    return G


def mycielskian(k: int) -> nx.Graph:
    """The k-th Mycielski graph M_k (triangle-free, chromatic number k).

    M_2 = K_2, M_3 = C_5, M_4 = Grotzsch graph (11 vertices). These are
    triangle-free by construction but quite sparse, so they are NON-counterexamples
    — useful as verifier sanity checks.
    """
    # networkx ships mycielski_graph; fall back to iterating mycielskian() if a
    # given version lacks it.
    try:
        return nx.mycielski_graph(k)
    except AttributeError:  # pragma: no cover - depends on networkx version
        G = nx.complete_graph(2)  # M_2
        for _ in range(k - 2):
            G = nx.mycielskian(G)
        return G


def generalized_petersen(n: int, k: int) -> nx.Graph:
    """Generalised Petersen graph GP(n, k): an outer n-cycle, an inner "star
    polygon" with step k, and spokes joining corresponding inner/outer vertices.

    Defined for n >= 3 and 1 <= k < n/2. GP(5, 2) is the Petersen graph. These
    are 3-regular (sparse), and triangle-free for the usual parameter ranges
    (the outer cycle alone is triangle-free once n >= 4) — verify to be sure.
    """
    if not (n >= 3 and 1 <= k < n / 2):
        raise ValueError("require n >= 3 and 1 <= k < n/2")
    G = nx.Graph()
    for j in range(n):
        G.add_edge(("o", j), ("o", (j + 1) % n))   # outer cycle
        G.add_edge(("o", j), ("i", j))             # spoke
        G.add_edge(("i", j), ("i", (j + k) % n))   # inner star polygon
    return G


def complete_bipartite(a: int, b: int) -> nx.Graph:
    """K_{a,b}: triangle-free and edge-dense, but each side is independent.

    Included as the canonical KNOWN-BAD candidate: taking one whole side as the
    half gives zero edges, so it can never be a counterexample. Great for proving
    the verifier correctly rejects an "obvious" failure.
    """
    return nx.complete_bipartite_graph(a, b)


# ---------------------------------------------------------------------------
# Dense candidate families (the ones actually worth searching)
# ---------------------------------------------------------------------------
def blowup(base: nx.Graph, part_size: int) -> nx.Graph:
    """Balanced blow-up: replace every vertex of `base` with an independent set of
    `part_size` copies, and fully join two groups whenever their base vertices are
    adjacent.

    Key fact: a blow-up is triangle-free iff `base` is triangle-free. (Within a
    group there are no edges; across groups, a triangle in the blow-up projects to
    a triangle in the base.) So blowing up an odd cycle keeps it triangle-free.
    """
    G = nx.Graph()
    groups = {v: [(v, i) for i in range(part_size)] for v in base.nodes}
    for v in base.nodes:
        G.add_nodes_from(groups[v])
    for u, v in base.edges():
        for a in groups[u]:
            for b in groups[v]:
                G.add_edge(a, b)
    return G


def cycle_blowup(cycle_len: int, part_size: int) -> nx.Graph:
    """Balanced blow-up of the cycle C_{cycle_len}.

    Triangle-free iff cycle_len != 3 (C_3 is itself a triangle). For a DENSE,
    NON-bipartite candidate use an ODD length >= 5 (C_5, C_7, ...): even cycles
    are bipartite and inherit the "sparse side" weakness. A balanced C_k blow-up
    on n = k * part_size vertices has k * part_size^2 = n^2 / k edges.
    """
    return blowup(nx.cycle_graph(cycle_len), part_size)


def middle_third_connection_set(n: int) -> list[int]:
    """The symmetric, sum-free connection set used by middle_third_cayley.

    We connect circular positions whose gap s satisfies n/3 < s < 2n/3. This set
    is symmetric (if s qualifies, so does n - s) and sum-free modulo n (the sum of
    two middle-third gaps lands outside the middle third), which is exactly what
    forces the resulting Cayley graph to be triangle-free.
    """
    return [s for s in range(1, n) if n / 3 < s < 2 * n / 3]


def cayley_graph(n: int, connection_set) -> nx.Graph:
    """Cayley graph on the cyclic group Z_n with the given connection set S.

    Vertices are 0..n-1; we join x and x+s (mod n) for each s in S. For an
    undirected simple graph S should be symmetric (s in S <=> n-s in S) and must
    not contain 0 (which would be a self-loop). Triangle-free iff S is sum-free
    mod n, i.e. no a, b in S have (a + b) mod n in S (see is_sum_free_mod).
    """
    S = {s % n for s in connection_set if s % n != 0}
    G = nx.Graph()
    G.add_nodes_from(range(n))
    for x in range(n):
        for s in S:
            G.add_edge(x, (x + s) % n)
    return G


def middle_third_cayley(n: int) -> nx.Graph:
    """Convenience: the middle-third Cayley graph on Z_n (dense + triangle-free).

    Degree is ~ n/3, giving ~ n^2 / 6 edges — comfortably above the n^2/50 bar on
    average. Whether EVERY half stays above it is exactly what the verifier tests.
    """
    return cayley_graph(n, middle_third_connection_set(n))


def is_sum_free_mod(connection_set, n: int) -> bool:
    """True iff no two elements of S sum (mod n) to another element of S.

    A sum-free connection set yields a triangle-free Cayley graph, because a
    triangle x, x+a, x+a+b would need a, b and a+b all in S.
    """
    S = {s % n for s in connection_set if s % n != 0}
    return not any((a + b) % n in S for a in S for b in S)
