"""
Network analysis — Wave E (part 2).

Pure-numpy graph utilities sufficient for Aurora's analytical needs:

* :func:`degree_centrality`     — fraction of nodes a vertex is
                                    connected to.
* :func:`betweenness_centrality` — Brandes algorithm O(V*E).
* :func:`eigenvector_centrality` — power iteration on the adjacency.
* :func:`louvain_communities`   — greedy modularity-gain merging.
* :func:`triangle_count`        — closed-triangle counts per node.

Input format is consistent: an undirected graph is represented as a
list of ``(u, v)`` edges over node ids 0..n-1. We build adjacency
matrices on the fly. For sparse, large graphs an adjacency-list
implementation would be more efficient — Aurora's typical use cases
fit comfortably in memory.
"""
from __future__ import annotations

import math
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

try:
    import numpy as np
except Exception:  # pragma: no cover
    np = None  # type: ignore[assignment]


def _adjacency(edges: Sequence[Tuple[int, int]],
                n_nodes: int) -> "np.ndarray":
    A = np.zeros((n_nodes, n_nodes), dtype=float)
    for u, v in edges:
        if u == v:
            continue
        A[u, v] = 1.0
        A[v, u] = 1.0
    return A


def degree_centrality(
    edges: Sequence[Tuple[int, int]],
    n_nodes: int,
) -> Dict[str, Any]:
    """Degree centrality — degree(v) / (n-1)."""
    if np is None:
        raise RuntimeError("numpy required for degree_centrality")
    A = _adjacency(edges, n_nodes)
    deg = A.sum(axis=1)
    centrality = deg / max(1, n_nodes - 1)
    return {
        "centrality": centrality.tolist(),
        "max_node": int(np.argmax(centrality)),
        "max_value": float(centrality.max()),
        "mean_degree": float(deg.mean()),
        "method": "degree_centrality",
    }


def betweenness_centrality(
    edges: Sequence[Tuple[int, int]],
    n_nodes: int,
) -> Dict[str, Any]:
    """Brandes' algorithm for betweenness centrality on unweighted graph."""
    if np is None:
        raise RuntimeError("numpy required for betweenness_centrality")
    # Adjacency lists.
    adj: List[List[int]] = [[] for _ in range(n_nodes)]
    for u, v in edges:
        if u == v:
            continue
        adj[u].append(v)
        adj[v].append(u)
    bc = np.zeros(n_nodes)
    for s in range(n_nodes):
        # BFS for shortest paths.
        sigma = np.zeros(n_nodes); sigma[s] = 1
        d = -np.ones(n_nodes, dtype=int); d[s] = 0
        stack: List[int] = []
        queue = [s]
        preds: List[List[int]] = [[] for _ in range(n_nodes)]
        head = 0
        while head < len(queue):
            v = queue[head]; head += 1
            stack.append(v)
            for w in adj[v]:
                if d[w] < 0:
                    d[w] = d[v] + 1
                    queue.append(w)
                if d[w] == d[v] + 1:
                    sigma[w] += sigma[v]
                    preds[w].append(v)
        delta = np.zeros(n_nodes)
        while stack:
            w = stack.pop()
            for v in preds[w]:
                if sigma[w] > 0:
                    delta[v] += (sigma[v] / sigma[w]) * (1 + delta[w])
            if w != s:
                bc[w] += delta[w]
    if n_nodes > 2:
        bc = bc / ((n_nodes - 1) * (n_nodes - 2))  # normalise undirected
    return {
        "centrality": bc.tolist(),
        "max_node": int(np.argmax(bc)),
        "max_value": float(bc.max()),
        "method": "brandes_betweenness",
    }


def eigenvector_centrality(
    edges: Sequence[Tuple[int, int]],
    n_nodes: int,
    *,
    max_iter: int = 100,
    tol: float = 1e-6,
) -> Dict[str, Any]:
    """Eigenvector centrality via power iteration on the adjacency."""
    if np is None:
        raise RuntimeError("numpy required for eigenvector_centrality")
    A = _adjacency(edges, n_nodes)
    x = np.ones(n_nodes) / math.sqrt(n_nodes)
    for _ in range(max_iter):
        y = A @ x
        norm = float(np.linalg.norm(y))
        if norm <= 0:
            break
        y = y / norm
        if float(np.linalg.norm(y - x)) < tol:
            x = y
            break
        x = y
    return {
        "centrality": x.tolist(),
        "max_node": int(np.argmax(x)),
        "max_value": float(x.max()),
        "method": "power_iteration",
    }


def triangle_count(
    edges: Sequence[Tuple[int, int]],
    n_nodes: int,
) -> Dict[str, Any]:
    """Per-node triangle (closed-triplet) count.

    For an undirected graph A, diag(A^3) / 2 gives 2× the number of
    triangles each node participates in; we report it without the /2
    for direct interpretation as "triangles touching this node × 2"
    (the conventional definition).
    """
    if np is None:
        raise RuntimeError("numpy required for triangle_count")
    A = _adjacency(edges, n_nodes)
    A3 = A @ A @ A
    per_node = np.diag(A3) / 2.0
    total = float(np.sum(np.diag(A3)) / 6.0)
    return {
        "per_node_triangles": per_node.astype(int).tolist(),
        "total_triangles": int(round(total)),
        "method": "matrix_power",
    }


# ---------------------------------------------------------------------------
# Greedy modularity community detection (a simplified Louvain)
# ---------------------------------------------------------------------------

def _local_move_phase(A: "np.ndarray", deg: "np.ndarray",
                       m: float, comm: "np.ndarray",
                       max_passes: int = 20) -> "np.ndarray":
    """Local-move modularity optimisation (Louvain phase 1).

    Each node greedily moves to the neighbour community whose merge
    yields the largest modularity gain. Repeats until no node moves.
    """
    n = comm.shape[0]
    moved = True
    passes = 0
    while moved and passes < max_passes:
        moved = False; passes += 1
        for i in range(n):
            current_c = comm[i]
            best_delta = 0.0
            best_c = current_c
            # Consider every distinct neighbour community.
            seen = set([int(current_c)])
            for j in range(n):
                if i == j or A[i, j] == 0:
                    continue
                tc = int(comm[j])
                if tc in seen:
                    continue
                seen.add(tc)
                k_in = float(A[i, comm == tc].sum())
                k_total = float(deg[comm == tc].sum())
                delta_q = k_in / m - deg[i] * k_total / (2 * m * m)
                if delta_q > best_delta:
                    best_delta = delta_q; best_c = tc
            if best_c != current_c:
                comm[i] = best_c; moved = True
    return comm


def _aggregate_graph(A: "np.ndarray", comm: "np.ndarray"
                       ) -> Tuple["np.ndarray", "np.ndarray", "np.ndarray"]:
    """Build the community-aggregated graph.

    Each community becomes a single node in the new graph; edges are
    weighted by total edge weight between communities (with self-loops
    for intra-community edges). Returns (A_new, mapping, deg_new) where
    mapping[old_node_idx] = new_node_idx.
    """
    unique = sorted(set(int(c) for c in comm))
    relabel = {c: i for i, c in enumerate(unique)}
    new_comm = np.array([relabel[int(c)] for c in comm])
    n_new = len(unique)
    A_new = np.zeros((n_new, n_new))
    n = A.shape[0]
    for i in range(n):
        ci = new_comm[i]
        for j in range(i, n):
            if A[i, j] == 0:
                continue
            cj = new_comm[j]
            w = A[i, j]
            if i == j:
                A_new[ci, ci] += w
            elif ci == cj:
                A_new[ci, ci] += 2 * w  # both directions
            else:
                A_new[ci, cj] += w
                A_new[cj, ci] += w
    deg_new = A_new.sum(axis=1)
    return A_new, new_comm, deg_new


def _modularity(A: "np.ndarray", comm: "np.ndarray", m: float) -> float:
    """Modularity Q of a partition on a given graph."""
    if m <= 0:
        return 0.0
    deg = A.sum(axis=1)
    Q = 0.0
    for c in sorted(set(int(x) for x in comm)):
        in_c = (comm == c)
        e_in = float(A[np.ix_(in_c, in_c)].sum() / 2.0)
        d_c = float(deg[in_c].sum())
        Q += (e_in / m) - (d_c / (2 * m)) ** 2
    return float(Q)


def louvain_communities(
    edges: Sequence[Tuple[int, int]],
    n_nodes: int,
    *,
    max_passes: int = 20,
    max_levels: int = 10,
) -> Dict[str, Any]:
    """Full multi-level Louvain (Blondel et al. 2008).

    Iterates between two phases:
      Phase 1: each node greedily moves to the neighbour community
               that gives the largest modularity gain.
      Phase 2: aggregate communities into super-nodes (weighted).
    Repeat until modularity stops improving.

    The community-aggregation step (vs. the simplified single-phase
    version) materially improves modularity on graphs with hierarchical
    structure (cliques connected by bridges, etc.).
    """
    if np is None:
        raise RuntimeError("numpy required for louvain_communities")
    A = _adjacency(edges, n_nodes)
    m = float(A.sum() / 2.0)
    if m <= 0:
        return {"available": False, "skipped_reason": "no_edges"}

    # Track the membership at the original-graph level across levels.
    original_membership = np.arange(n_nodes)
    cur_A = A.copy()
    cur_m = m
    Q_prev = -np.inf
    levels_run = 0

    for level in range(max_levels):
        levels_run = level + 1
        cur_deg = cur_A.sum(axis=1)
        cur_comm = np.arange(cur_A.shape[0])
        cur_comm = _local_move_phase(cur_A, cur_deg, cur_m, cur_comm,
                                       max_passes=max_passes)
        Q_cur = _modularity(cur_A, cur_comm, cur_m)
        if Q_cur <= Q_prev + 1e-9:
            break
        # Project membership back to the original graph.
        new_orig = np.empty_like(original_membership)
        # original_membership currently maps original node → super-node
        # in cur_A's indexing. Now cur_comm tells us the new community
        # of each super-node. So original → cur_comm[super_node].
        unique = sorted(set(int(c) for c in cur_comm))
        relabel = {c: i for i, c in enumerate(unique)}
        for orig_i, super_i in enumerate(original_membership):
            new_orig[orig_i] = relabel[int(cur_comm[int(super_i)])]
        original_membership = new_orig
        Q_prev = Q_cur
        # Aggregate for next level. If there's only one community left,
        # we're done.
        if len(unique) <= 1:
            break
        cur_A, _, _ = _aggregate_graph(cur_A, cur_comm)
        cur_m = float(cur_A.sum() / 2.0)
        if cur_m <= 0:
            break

    # Compute final modularity on the original graph.
    Q_final = _modularity(A, original_membership, m)
    n_comm = len(set(int(c) for c in original_membership))
    return {
        "available": True,
        "communities": original_membership.tolist(),
        "n_communities": n_comm,
        "modularity": float(Q_final),
        "verdict": "well_separated" if Q_final > 0.4 else
                    ("moderate" if Q_final > 0.2 else "weak"),
        "method": "louvain_multi_level",
        "n_levels": levels_run,
        "notes": [
            "full Louvain (Blondel et al. 2008) — multi-level with "
            "community-aggregation phase",
            "modularity > 0.4 indicates clear community structure",
        ],
    }
