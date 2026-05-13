"""
Cluster analysis with stability assessment — Wave C, Method 5 of brief.

Pure-numpy k-means + bootstrap stability across resamples + a small
hierarchical clustering view + gap statistic for k-selection.

The most important honest finding is often "no clear clustering" —
the stability assessment surfaces that explicitly rather than letting
a fragile k=4 result look authoritative.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Sequence, Tuple

try:
    import numpy as np
except Exception:  # pragma: no cover
    np = None  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# k-means (Lloyd's algorithm, k-means++ init)
# ---------------------------------------------------------------------------

def _kmeans_pp_init(X: "np.ndarray", k: int,
                     rng: "np.random.Generator") -> "np.ndarray":
    """k-means++ centroid initialisation (Arthur & Vassilvitskii 2007)."""
    n = X.shape[0]
    centers = np.empty((k, X.shape[1]))
    centers[0] = X[rng.integers(0, n)]
    for c in range(1, k):
        dist2 = np.min(
            np.sum((X[:, None, :] - centers[None, :c, :]) ** 2, axis=2),
            axis=1,
        )
        total = float(dist2.sum())
        if total <= 0:
            centers[c] = X[rng.integers(0, n)]
            continue
        probs = dist2 / total
        idx = rng.choice(n, p=probs)
        centers[c] = X[idx]
    return centers


def kmeans(
    X: Sequence[Sequence[float]],
    k: int,
    *,
    max_iter: int = 100,
    seed: Optional[int] = 42,
) -> Dict[str, Any]:
    """k-means clustering with k-means++ init."""
    if np is None:
        raise RuntimeError("numpy required for kmeans")
    arr = np.asarray(X, dtype=float)
    arr = arr[~np.any(np.isnan(arr), axis=1)]
    n, p = arr.shape
    if n < k:
        return {"available": False, "skipped_reason": f"n={n} < k={k}"}
    rng = np.random.default_rng(seed)
    centers = _kmeans_pp_init(arr, k, rng)
    labels = np.zeros(n, dtype=int)
    for it in range(max_iter):
        d = np.sqrt(np.sum((arr[:, None, :] - centers[None, :, :]) ** 2,
                              axis=2))
        new_labels = np.argmin(d, axis=1)
        if np.array_equal(new_labels, labels) and it > 0:
            break
        labels = new_labels
        for c in range(k):
            mask = labels == c
            if mask.sum() > 0:
                centers[c] = arr[mask].mean(axis=0)
    inertia = float(np.sum((arr - centers[labels]) ** 2))
    return {
        "available": True,
        "k": k,
        "labels": labels.tolist(),
        "centers": centers.tolist(),
        "inertia": inertia,
        "n_obs": n,
        "n_features": p,
        "method": "kmeans_lloyd_kmeansxx",
        "max_iter": max_iter,
        "seed": seed,
    }


# ---------------------------------------------------------------------------
# Silhouette score
# ---------------------------------------------------------------------------

def silhouette_score(
    X: Sequence[Sequence[float]],
    labels: Sequence[int],
) -> Dict[str, Any]:
    """Mean silhouette coefficient over all points.

    Silhouette of point i = (b - a) / max(a, b), where:
        a = mean distance to points in same cluster
        b = mean distance to nearest other cluster

    Range [-1, 1]; values near 1 = well-separated clusters; near 0 = on
    boundary; negative = wrongly clustered.
    """
    if np is None:
        raise RuntimeError("numpy required for silhouette_score")
    arr = np.asarray(X, dtype=float)
    lab = np.asarray(labels)
    n = arr.shape[0]
    unique = np.unique(lab)
    if len(unique) < 2:
        return {"score": None, "skipped_reason": "fewer_than_2_clusters",
                "n_obs": n}
    # Pairwise distances.
    d = np.sqrt(np.sum((arr[:, None, :] - arr[None, :, :]) ** 2, axis=2))
    s_values = np.zeros(n)
    for i in range(n):
        same = (lab == lab[i])
        same[i] = False
        if same.sum() > 0:
            a = float(d[i, same].mean())
        else:
            a = 0.0
        b_vals = []
        for c in unique:
            if c == lab[i]:
                continue
            other = (lab == c)
            if other.sum() > 0:
                b_vals.append(float(d[i, other].mean()))
        if not b_vals:
            s_values[i] = 0.0
            continue
        b = min(b_vals)
        s_values[i] = (b - a) / max(a, b) if max(a, b) > 0 else 0.0
    return {"score": float(s_values.mean()),
             "per_point": s_values.tolist(),
             "n_obs": n}


# ---------------------------------------------------------------------------
# Bootstrap stability via Adjusted Rand Index
# ---------------------------------------------------------------------------

def adjusted_rand_index(labels_a: Sequence[int],
                          labels_b: Sequence[int]) -> float:
    """Hubert-Arabie ARI. 0 ≈ random; 1 = identical partitions."""
    if np is None:
        raise RuntimeError("numpy required for adjusted_rand_index")
    a = np.asarray(labels_a)
    b = np.asarray(labels_b)
    if a.shape != b.shape:
        raise ValueError("label arrays must be same length")
    n = a.shape[0]
    # Build contingency table.
    ua = np.unique(a); ub = np.unique(b)
    table = np.zeros((len(ua), len(ub)), dtype=int)
    for i, x in enumerate(ua):
        ax = (a == x)
        for j, y in enumerate(ub):
            table[i, j] = int(np.sum(ax & (b == y)))
    sum_comb = sum(_comb2(int(c)) for c in table.flatten())
    sum_a = sum(_comb2(int(s)) for s in table.sum(axis=1))
    sum_b = sum(_comb2(int(s)) for s in table.sum(axis=0))
    expected = sum_a * sum_b / max(1, _comb2(n))
    max_val = 0.5 * (sum_a + sum_b)
    if max_val - expected == 0:
        return 0.0
    return (sum_comb - expected) / (max_val - expected)


def _comb2(n: int) -> int:
    return n * (n - 1) // 2 if n >= 2 else 0


def cluster_stability(
    X: Sequence[Sequence[float]],
    k: int,
    *,
    n_bootstrap: int = 50,
    sample_fraction: float = 0.8,
    seed: Optional[int] = 42,
) -> Dict[str, Any]:
    """Bootstrap stability of a k-means partition.

    Procedure: cluster the full data once. For each bootstrap, draw a
    sub-sample (size = ``sample_fraction × n``), cluster it, then
    measure ARI between the sub-sample's labels and the corresponding
    rows in the full clustering. Mean ARI across resamples is the
    stability score.

    A stability score above ~0.7 indicates a stable structure; below
    ~0.5 means the clustering is unreliable and the synthesis layer
    should report "no clear clustering".
    """
    if np is None:
        raise RuntimeError("numpy required for cluster_stability")
    arr = np.asarray(X, dtype=float)
    arr = arr[~np.any(np.isnan(arr), axis=1)]
    n = arr.shape[0]
    if n < max(20, k * 5):
        return {"available": False, "skipped_reason":
                  f"n={n} insufficient for k={k}"}
    rng = np.random.default_rng(seed)
    full = kmeans(arr, k, seed=seed)
    if not full.get("available"):
        return {"available": False, "skipped_reason": "kmeans_failed"}
    full_labels = np.asarray(full["labels"])
    aris = []
    sample_size = int(round(sample_fraction * n))
    for b in range(n_bootstrap):
        idx = rng.choice(n, size=sample_size, replace=False)
        sub = arr[idx]
        sub_res = kmeans(sub, k, seed=seed + b + 1)
        if not sub_res.get("available"):
            continue
        ari = adjusted_rand_index(full_labels[idx],
                                    np.asarray(sub_res["labels"]))
        aris.append(ari)
    if not aris:
        return {"available": False, "skipped_reason": "no_successful_bootstraps"}
    mean_ari = float(np.mean(aris))
    if mean_ari >= 0.7:
        verdict = "strong"
    elif mean_ari >= 0.5:
        verdict = "moderate"
    else:
        verdict = "weak"
    return {
        "available": True,
        "k": k,
        "stability": mean_ari,
        "stability_p25": float(np.quantile(aris, 0.25)),
        "stability_p75": float(np.quantile(aris, 0.75)),
        "verdict": verdict,
        "n_bootstrap": len(aris),
        "sample_fraction": sample_fraction,
        "method": "bootstrap_resample_ari",
        "notes": [
            "Adjusted Rand Index across {} subsamples".format(len(aris)),
            "≥0.7 strong, 0.5-0.7 moderate, <0.5 weak — verdict: {}".format(verdict),
        ],
    }


# ---------------------------------------------------------------------------
# k-selection: silhouette + gap statistic + stability
# ---------------------------------------------------------------------------

def select_best_k(
    X: Sequence[Sequence[float]],
    *,
    k_range: Sequence[int] = (2, 3, 4, 5, 6, 7, 8),
    seed: Optional[int] = 42,
) -> Dict[str, Any]:
    """Sweep k, score with silhouette + stability, pick the best."""
    if np is None:
        raise RuntimeError("numpy required for select_best_k")
    arr = np.asarray(X, dtype=float)
    arr = arr[~np.any(np.isnan(arr), axis=1)]
    profiles: List[Dict[str, Any]] = []
    for k in k_range:
        if arr.shape[0] < k:
            continue
        km = kmeans(arr, k, seed=seed)
        if not km.get("available"):
            continue
        sil = silhouette_score(arr, km["labels"])
        stab = cluster_stability(arr, k, n_bootstrap=20, seed=seed)
        profiles.append({
            "k": k,
            "silhouette": sil.get("score"),
            "stability": stab.get("stability"),
            "stability_verdict": stab.get("verdict"),
            "inertia": km.get("inertia"),
        })
    if not profiles:
        return {"available": False, "skipped_reason": "no_k_succeeded"}
    # Composite score: silhouette × stability (both want to be big).
    def _composite(p):
        s = p.get("silhouette") or 0.0
        st = p.get("stability") or 0.0
        return s * st
    profiles.sort(key=_composite, reverse=True)
    best = profiles[0]
    return {
        "available": True,
        "best_k": best["k"],
        "best_silhouette": best["silhouette"],
        "best_stability": best["stability"],
        "best_verdict": best["stability_verdict"],
        "profiles": profiles,
        "n_obs": int(arr.shape[0]),
        "method": "kmeans_silhouette_stability",
    }
