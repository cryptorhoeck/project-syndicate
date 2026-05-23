"""
test_known_graphs.py — Phase 1 sanity checks for the generators + verifier.

The point of these tests is NOT to find a counterexample (none is expected from
small known graphs). It is to prove the machinery is TRUSTWORTHY:

  * the triangle-free detector correctly accepts/rejects,
  * the exact min-edge finders (bruteforce and ILP) agree with each other,
  * the heuristic local search never reports a value below the true minimum,
  * well-known triangle-free graphs (Petersen, Kneser(5,2), Grotzsch) are
    correctly classified as NON-counterexamples,
  * the verifier's threshold and monotonic-reduction logic behave as intended.

If a future bug makes the verifier wrongly call something a counterexample, these
tests are the trip-wire.
"""

from __future__ import annotations

import sys
from pathlib import Path

import networkx as nx
import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src import graphs  # noqa: E402
from src.search_a import (  # noqa: E402
    count_internal_edges,
    min_edge_subset_bruteforce,
    min_edge_subset_ilp,
    min_edge_subset_local_search,
)
from src.verify import (  # noqa: E402
    STATUS_HAS_TRIANGLE,
    STATUS_NOT,
    edge_threshold,
    is_triangle_free,
    screen_candidate,
    verify_counterexample,
)


# ---------------------------------------------------------------------------
# Triangle-free detection
# ---------------------------------------------------------------------------
def test_triangle_free_on_known_triangle_free_graphs():
    assert is_triangle_free(graphs.petersen())
    assert is_triangle_free(graphs.kneser(5, 2))          # = Petersen
    assert is_triangle_free(graphs.mycielskian(4))        # Grotzsch graph
    assert is_triangle_free(graphs.cycle_blowup(5, 3))    # odd-cycle blow-up
    assert is_triangle_free(graphs.middle_third_cayley(15))
    assert is_triangle_free(graphs.complete_bipartite(6, 6))


def test_triangle_free_detects_triangles():
    assert not is_triangle_free(nx.complete_graph(3))     # K3 is a triangle
    # A C3 blow-up is full of triangles (the base C3 is a triangle).
    assert not is_triangle_free(graphs.cycle_blowup(3, 2))
    assert not is_triangle_free(nx.complete_graph(5))


def test_kneser_52_is_petersen():
    # KG(5,2) is isomorphic to the Petersen graph.
    assert nx.is_isomorphic(graphs.kneser(5, 2), graphs.petersen())


def test_sum_free_connection_set_gives_triangle_free_cayley():
    for n in (12, 13, 15, 18, 21):
        S = graphs.middle_third_connection_set(n)
        assert graphs.is_sum_free_mod(S, n)
        assert is_triangle_free(graphs.cayley_graph(n, S))


# ---------------------------------------------------------------------------
# Edge counting + exact finders agree
# ---------------------------------------------------------------------------
def test_count_internal_edges_matches_subgraph():
    G = graphs.petersen()
    nodes = list(G.nodes)[:5]
    assert count_internal_edges(G, nodes) == G.subgraph(nodes).number_of_edges()


@pytest.mark.parametrize("builder", [
    lambda: graphs.petersen(),
    lambda: graphs.mycielskian(4),
    lambda: graphs.cycle_blowup(5, 2),
    lambda: graphs.middle_third_cayley(12),
    lambda: graphs.complete_bipartite(5, 5),
])
def test_ilp_matches_bruteforce(builder):
    """The two EXACT methods must return the same minimum edge count."""
    G = builder()
    m = G.number_of_nodes() // 2
    bf_count, _, bf_exact = min_edge_subset_bruteforce(G, m)
    ilp_count, _, ilp_exact = min_edge_subset_ilp(G, m)
    assert bf_exact and ilp_exact
    assert ilp_count == bf_count


@pytest.mark.parametrize("builder", [
    lambda: graphs.petersen(),
    lambda: graphs.cycle_blowup(5, 2),
    lambda: graphs.middle_third_cayley(12),
])
def test_local_search_is_a_valid_upper_bound(builder):
    """Heuristic must never claim fewer edges than the true minimum, and must
    return a subset of the right size whose recomputed edge count matches."""
    G = builder()
    m = G.number_of_nodes() // 2
    true_min, _, _ = min_edge_subset_bruteforce(G, m)
    heur_count, heur_subset, exact = min_edge_subset_local_search(G, m, seed=0)
    assert exact is False
    assert len(set(heur_subset)) == m
    assert count_internal_edges(G, heur_subset) == heur_count
    assert heur_count >= true_min            # never below the truth
    # On these small symmetric graphs the heuristic should actually reach it.
    assert heur_count == true_min


# ---------------------------------------------------------------------------
# Known graphs are NOT counterexamples (the core sanity guarantee)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("builder,name", [
    (lambda: graphs.petersen(), "Petersen"),
    (lambda: graphs.kneser(5, 2), "Kneser(5,2)"),
    (lambda: graphs.mycielskian(4), "Mycielski_4/Grotzsch"),
])
def test_known_triangle_free_graphs_are_not_counterexamples(builder, name):
    result = verify_counterexample(builder(), method="bruteforce")
    assert result.is_triangle_free, f"{name} should be triangle-free"
    assert not result.is_counterexample, f"{name} must NOT be a counterexample"
    assert result.status == STATUS_NOT


def test_complete_bipartite_has_zero_edge_half():
    """K_{10,10}: picking one whole side (10 = floor(20/2)) gives zero edges, the
    clearest possible disqualification."""
    G = graphs.complete_bipartite(10, 10)
    result = verify_counterexample(G, method="bruteforce")
    assert result.status == STATUS_NOT
    assert result.min_edges == 0


def test_graph_with_triangle_is_reported_as_such():
    G = graphs.cycle_blowup(3, 3)  # blow-up of a triangle => lots of triangles
    result = verify_counterexample(G, method="bruteforce")
    assert result.status == STATUS_HAS_TRIANGLE
    assert not result.is_counterexample


# ---------------------------------------------------------------------------
# Threshold + screening pipeline
# ---------------------------------------------------------------------------
def test_edge_threshold_value():
    assert edge_threshold(10) == 2.0          # 100 / 50
    assert edge_threshold(20) == 8.0          # 400 / 50
    assert edge_threshold(50) == 50.0         # 2500 / 50


def test_screen_candidate_agrees_with_exact_on_known_graphs():
    """The fast two-phase screen must reach the same verdict as exact verification
    on graphs small enough to also check exactly."""
    for builder in (graphs.petersen, lambda: graphs.cycle_blowup(5, 3),
                    lambda: graphs.middle_third_cayley(18)):
        G = builder()
        screened = screen_candidate(G, local_restarts=40)
        exact = verify_counterexample(G, method="bruteforce")
        # The fast screen must reach the same verdict as exact verification...
        assert screened.status == exact.status
        # ...and on these small known graphs that verdict is NOT a counterexample.
        assert exact.status == STATUS_NOT
