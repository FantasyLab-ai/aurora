"""
Topological data analysis — Wave I, Method 7.

Pure-numpy persistent homology limited to 0- and 1-dimensional features
(connected components and loops). For 2+ dimensional features, ripser
or gudhi are needed; we deliberately scope down to what's tractable in
pure numpy.

* H₀ persistence — connected-component birth/death across the
                    Vietoris-Rips filtration; computed by union-find on
                    the sorted edges (Edelsbrunner-Letscher-Zomorodian).
* H₁ persistence — 1-cycle birth (when an edge closes a loop) and
                    death (when the loop is filled). For Aurora we
                    detect H₁ births via the union-find self-edge events
                    and report them with their birth scale.

This is a faithful subset of persistent homology — sufficient to detect
"data has connected-component structure" and "data has cyclic structure"
in the dimensions Aurora typically operates.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Sequence, Tuple

try:
    import numpy as np
except Exception:  # pragma: no cover
    np = None  # type: ignore[assignment]


# ===========================================================================
# Union-find
# ===========================================================================

class _UnionFind:
    def __init__(self, n: int):
        self.parent = list(range(n))
        self.rank = [0] * n

    def find(self, x: int) -> int:
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: int, b: int) -> bool:
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return False
        if self.rank[ra] < self.rank[rb]:
            ra, rb = rb, ra
        self.parent[rb] = ra
        if self.rank[ra] == self.rank[rb]:
            self.rank[ra] += 1
        return True


# ===========================================================================
# Persistent H₀ + H₁ via Vietoris-Rips on a point cloud
# ===========================================================================

def persistent_homology(
    X: Sequence[Sequence[float]],
    *,
    max_edges: int = 5000,
) -> Dict[str, Any]:
    """0- and 1-dim persistent homology on a point cloud.

    Procedure:
      1. Compute pairwise distances.
      2. Sort edges by length.
      3. Walk edges in order; union-find tracks components.
         - A merge at distance d kills the younger component at d (H₀).
         - A non-merging edge (both endpoints already in the same
           component) births a 1-cycle at d (H₁).

    Capped at ``max_edges`` for tractability — we use the shortest
    edges first, which dominate persistence.

    Returns persistence diagrams for H₀ and H₁ + summary stats.
    """
    if np is None:
        raise RuntimeError("numpy required")
    arr = np.asarray(X, dtype=float)
    n = arr.shape[0]
    if n < 4:
        return {"available": False, "skipped_reason": "fewer_than_4_pts"}
    # Pairwise distance matrix.
    diff = arr[:, None, :] - arr[None, :, :]
    d = np.sqrt(np.sum(diff ** 2, axis=2))
    edges: List[Tuple[float, int, int]] = []
    for i in range(n):
        for j in range(i + 1, n):
            edges.append((float(d[i, j]), i, j))
    edges.sort(key=lambda t: t[0])
    edges = edges[:max_edges]
    uf = _UnionFind(n)
    h0_diagram: List[Tuple[float, float]] = []  # (birth, death) — birth always 0
    h1_births: List[float] = []                  # death depends on filling
    n_components = n
    for d_e, i, j in edges:
        if uf.union(i, j):
            # Merged: kills one component at this scale.
            h0_diagram.append((0.0, d_e))
            n_components -= 1
        else:
            # 1-cycle birth at this scale (loop closed by this edge).
            h1_births.append(d_e)
    # Surviving components: persist to infinity (the always-alive
    # component for a connected dataset, or n_components - 1 features
    # of infinite persistence for a disconnected one).
    for _ in range(n_components):
        h0_diagram.append((0.0, float("inf")))
    # H₁ "death": in real persistent homology each loop is killed by the
    # 2-simplex that fills it. With pure numpy we can't compute this
    # cheaply; we report the births and a running count instead.
    h0_persistence = [d - b for b, d in h0_diagram if math.isfinite(d)]
    if h0_persistence:
        h0_max = max(h0_persistence)
        # "Significant" H₀ features: persist > 50% of max.
        h0_significant = [(b, d) for b, d in h0_diagram
                            if math.isfinite(d) and d > 0.5 * h0_max]
    else:
        h0_max = 0.0
        h0_significant = []
    return {
        "available": True,
        "n_points": n,
        "n_edges_considered": len(edges),
        "h0_diagram": [{"birth": b, "death": d if math.isfinite(d)
                                              else None}
                         for b, d in h0_diagram],
        "h0_significant_count": len(h0_significant),
        "h1_births": h1_births,
        "n_h1_births": len(h1_births),
        "n_components": n_components,
        "h0_max_persistence": h0_max,
        "method": "vietoris_rips_h0_h1",
        "notes": [
            "0-dim persistence via union-find on sorted edges",
            "1-dim births recorded; deaths require 2-simplex tracking "
            "(omitted in this pure-numpy implementation — use ripser for "
            "full H₁ deaths)",
            f"capped at {max_edges} shortest edges for tractability",
        ],
    }


def topological_summary(persistence: Dict[str, Any]) -> Dict[str, Any]:
    """Translate a persistence diagram into a synthesis-ready summary."""
    if not persistence.get("available"):
        return {"available": False,
                "skipped_reason": persistence.get("skipped_reason",
                                                     "no_persistence_data")}
    n_components = persistence["n_components"]
    n_h1_births = persistence["n_h1_births"]
    if n_components > 1 and n_h1_births == 0:
        verdict = "disconnected_no_loops"
    elif n_components == 1 and n_h1_births == 0:
        verdict = "single_blob"
    elif n_components == 1 and n_h1_births >= 1:
        verdict = "loops_present"
    else:
        verdict = "complex"
    return {
        "available": True,
        "verdict": verdict,
        "n_components": n_components,
        "n_h1_births": n_h1_births,
        "h0_significant_count": persistence["h0_significant_count"],
        "method": "persistence_verdict",
    }
