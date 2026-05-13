"""
Multivariate outlier detection — Wave B.

Three classical, deterministic methods ship side-by-side:

* :func:`mahalanobis_outliers` — Mahalanobis distance with iterative
                                   MCD-style robust covariance estimation.
                                   Captures multivariate ellipsoidal
                                   outliers; robust to leverage points.
* :func:`isolation_forest_outliers` — pure-numpy isolation forest.
                                       Tree-based, non-parametric, scales
                                       to high-d data.
* :func:`lof_outliers`        — Local Outlier Factor (Breunig 2000).
                                   Density-based; catches local outliers
                                   in heterogeneous data.
* :func:`detect_multivariate_outliers` — runs all three, joins the
                                            per-point results, and reports
                                            consensus (points flagged by
                                            multiple methods).

Glass-box: every detector returns its raw scores per point alongside
the boolean flag, so reviewers can audit without re-running. The
orchestrator records the parameters used (chi-square cutoff, tree
depth, neighbour count, etc.) in the result dict.

No external statistical libraries. Everything is pure numpy.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Sequence, Tuple

try:
    import numpy as np
except Exception:  # pragma: no cover
    np = None  # type: ignore[assignment]


# ===========================================================================
# Mahalanobis — robust covariance + chi-square cutoff
# ===========================================================================

# Chi-square inverse CDF table at α=0.025 (one-sided upper) and α=0.975
# (one-sided lower). Used as the standard cutoff for robust Mahalanobis.
# Computed once with scipy and frozen here so we don't carry a scipy dep.
_CHI2_CRIT_975 = {
    1: 5.0239, 2: 7.3778, 3: 9.3484, 4: 11.1433, 5: 12.8325,
    6: 14.4494, 7: 16.0128, 8: 17.5345, 9: 19.0228, 10: 20.4832,
    12: 23.3367, 15: 27.4884, 20: 34.1696, 30: 46.9792, 50: 71.4202,
}


def _chi2_critical_value(df: int, level: float = 0.975) -> float:
    """Critical value of the chi-square distribution at the requested
    one-sided level. Falls back to a numerical inverse for df not in
    the table — but the table covers the dimensions we'd ever pass."""
    if level != 0.975:
        # We only ship the 0.975 table; for other levels, log a fallback
        # using the Wilson-Hilferty approximation.
        return df * (1 - 2 / (9 * df) + math.sqrt(2 / (9 * df))
                       * _norm_ppf(level)) ** 3
    if df in _CHI2_CRIT_975:
        return _CHI2_CRIT_975[df]
    # Wilson-Hilferty for unsupported dimensions.
    return df * (1 - 2 / (9 * df) + math.sqrt(2 / (9 * df))
                   * 1.959964) ** 3


def _norm_ppf(p: float) -> float:
    """Tiny inline inverse-normal — same approximation we ship in
    bootstrap.py. Used only for chi-square fallback."""
    p = max(min(p, 1.0 - 1e-12), 1e-12)
    a = [-3.969683028665376e+01,  2.209460984245205e+02,
         -2.759285104469687e+02,  1.383577518672690e+02,
         -3.066479806614716e+01,  2.506628277459239e+00]
    b = [-5.447609879822406e+01,  1.615858368580409e+02,
         -1.556989798598866e+02,  6.680131188771972e+01,
         -1.328068155288572e+01]
    q = p - 0.5
    r = q * q
    return (((((a[0]*r + a[1])*r + a[2])*r + a[3])*r + a[4])*r + a[5])*q / \
           (((((b[0]*r + b[1])*r + b[2])*r + b[3])*r + b[4])*r + 1)


def _c_step(X: "np.ndarray", idx: "np.ndarray",
             keep: int, max_iter: int = 10) -> Tuple["np.ndarray", "np.ndarray", "np.ndarray", float]:
    """One concentration-step (C-step) iteration to convergence.

    Given a starting subset, repeatedly: fit (μ, Σ) on the subset,
    compute Mahalanobis distance for ALL points, take the ``keep``
    smallest. Return (mu, cov, idx, det_cov) at convergence.
    """
    p = X.shape[1]
    cur_idx = idx.copy()
    last_det = np.inf
    for _ in range(max_iter):
        sub = X[cur_idx]
        mu = sub.mean(axis=0)
        cov = np.cov(sub, rowvar=False, ddof=1) + 1e-8 * np.eye(p)
        try:
            inv = np.linalg.inv(cov)
            sign, logdet = np.linalg.slogdet(cov)
            det = float(sign) * float(np.exp(logdet))
        except np.linalg.LinAlgError:
            return mu, cov, cur_idx, np.inf
        diff = X - mu
        d2 = np.sum(diff @ inv * diff, axis=1)
        new_idx = np.argsort(d2)[:keep]
        if np.array_equal(np.sort(new_idx), np.sort(cur_idx)):
            return mu, cov, new_idx, det
        cur_idx = new_idx
        last_det = det
    sub = X[cur_idx]
    mu = sub.mean(axis=0)
    cov = np.cov(sub, rowvar=False, ddof=1) + 1e-8 * np.eye(p)
    return mu, cov, cur_idx, last_det


def _robust_covariance(
    X: "np.ndarray",
    *,
    keep_fraction: float = 0.75,
    max_iter: int = 10,
    n_random_starts: int = 25,
    seed: Optional[int] = 42,
) -> Tuple["np.ndarray", "np.ndarray", List[int]]:
    """FAST-MCD-style robust covariance with multiple random starts.

    Closer to Rousseeuw & Van Driessen 1999:
      1. Draw ``n_random_starts`` initial subsets of size p+1 at random.
      2. Run a few C-step iterations on each.
      3. Keep the ``best_n`` (default 5) starts with smallest covariance
         determinant.
      4. Run C-steps to convergence on each survivor.
      5. Return the (mu, cov, idx) with the smallest |Σ| — the MCD
         estimator's defining objective.

    This isn't the literal FAST-MCD code (no nested partitioning for
    very large n), but it carries the algorithm's three core ideas:
    multiple random starts, the concentration-step iteration, and
    determinant-minimisation as the selection criterion.
    """
    n, p = X.shape
    keep = max(p + 1, int(round(keep_fraction * n)))
    rng = np.random.default_rng(seed)
    candidates: List[Tuple["np.ndarray", "np.ndarray", "np.ndarray", float]] = []
    # Always include the all-points start as a baseline.
    candidates.append(_c_step(X, np.arange(n), keep, max_iter=3))
    # Random elemental starts: pick p+1 points, fit a cov, run 3 C-steps.
    init_size = p + 1
    if n >= init_size + 1:
        for _ in range(n_random_starts):
            try:
                init_idx = rng.choice(n, size=init_size, replace=False)
                # Use the starting subset directly — _c_step refines it.
                candidates.append(_c_step(X, init_idx, keep, max_iter=3))
            except Exception:
                continue
    # Pick top 5 by determinant, run to convergence.
    candidates.sort(key=lambda t: t[3])
    best_n = min(5, len(candidates))
    refined = []
    for mu0, cov0, idx0, _det0 in candidates[:best_n]:
        refined.append(_c_step(X, idx0, keep, max_iter=max_iter))
    refined.sort(key=lambda t: t[3])
    mu, cov, kept_idx, _det = refined[0]
    return mu, cov, list(kept_idx)


def mahalanobis_outliers(
    X: Sequence[Sequence[float]],
    *,
    robust: bool = True,
    cutoff_level: float = 0.975,
) -> Dict[str, Any]:
    """Mahalanobis distance with optional robust covariance.

    Parameters
    ----------
    X
        2-D array (n_obs × n_features). Rows with NaNs are dropped
        before fitting, then their Mahalanobis distance is reported as
        NaN in the score array.
    robust
        When True (default), uses iterative trimmed covariance (a
        simplified MCD). Robust to leverage points.
    cutoff_level
        One-sided level for the chi-square cutoff. 0.975 ≈ standard
        2.5% tail, the convention.

    Returns
    -------
    dict with:
        scores         — per-point Mahalanobis distance (NaN where input
                          had NaNs).
        is_outlier     — boolean mask, scores > cutoff.
        cutoff         — the chi-square critical value used.
        n_outliers     — int.
        n_observations — int (rows used in the fit).
        n_features     — int.
        method         — "mahalanobis" | "mahalanobis_robust".
        cov_method     — "sample" | "robust_trimmed".
        kept_indices   — when robust, the row indices used for fitting.
        notes          — list of human-readable caveats.
    """
    if np is None:
        raise RuntimeError("numpy required for mahalanobis_outliers")
    arr = np.asarray(X, dtype=float)
    if arr.ndim != 2:
        raise ValueError(f"X must be 2-D; got shape {arr.shape}")
    n_orig, p = arr.shape
    notes: List[str] = []
    # Filter NaN rows for fitting; we still emit one score per original row.
    nan_mask = np.any(np.isnan(arr), axis=1)
    valid_idx = np.where(~nan_mask)[0]
    if len(valid_idx) < p + 1:
        return {
            "scores": [float("nan")] * n_orig,
            "is_outlier": [False] * n_orig,
            "cutoff": None,
            "n_outliers": 0,
            "n_observations": int(len(valid_idx)),
            "n_features": p,
            "method": "mahalanobis_robust" if robust else "mahalanobis",
            "cov_method": None,
            "kept_indices": [],
            "notes": notes + [
                f"insufficient_obs: need ≥ {p + 1}, got {len(valid_idx)}"
            ],
            "skipped_reason": "insufficient_observations",
        }
    valid = arr[valid_idx]
    if robust:
        mu, cov, kept = _robust_covariance(valid)
        cov_method = "robust_mcd"
        notes.append(
            "robust covariance estimated by FAST-MCD-style algorithm "
            "(Rousseeuw & Van Driessen 1999): multiple random elemental "
            "starts, C-step concentration iteration, determinant "
            "minimisation."
        )
    else:
        mu = valid.mean(axis=0)
        cov = np.cov(valid, rowvar=False, ddof=1) + 1e-8 * np.eye(p)
        kept = list(range(len(valid)))
        cov_method = "sample"
    try:
        inv = np.linalg.inv(cov)
    except np.linalg.LinAlgError:
        return {
            "scores": [float("nan")] * n_orig,
            "is_outlier": [False] * n_orig,
            "cutoff": None,
            "n_outliers": 0,
            "n_observations": int(len(valid_idx)),
            "n_features": p, "method": "mahalanobis",
            "cov_method": cov_method, "kept_indices": [],
            "notes": notes + ["singular covariance — could not invert"],
            "skipped_reason": "singular_covariance",
        }
    cutoff = _chi2_critical_value(p, cutoff_level)
    # Compute D² for all rows (NaN rows pass through as NaN naturally).
    diff = arr - mu
    d2_all = np.full(n_orig, float("nan"))
    valid_diff = diff[valid_idx]
    d2_all[valid_idx] = np.sum(valid_diff @ inv * valid_diff, axis=1)
    is_out = np.zeros(n_orig, dtype=bool)
    is_out[valid_idx] = d2_all[valid_idx] > cutoff
    return {
        "scores": d2_all.tolist(),
        "is_outlier": is_out.tolist(),
        "cutoff": float(cutoff),
        "n_outliers": int(is_out.sum()),
        "n_observations": int(len(valid_idx)),
        "n_features": p,
        "method": "mahalanobis_robust" if robust else "mahalanobis",
        "cov_method": cov_method,
        "kept_indices": [int(valid_idx[k]) for k in kept],
        "cutoff_level": cutoff_level,
        "notes": notes,
    }


# ===========================================================================
# Isolation Forest — pure numpy
# ===========================================================================

class _ITree:
    """Single isolation tree node — recursive structure."""
    __slots__ = ("feature", "threshold", "left", "right", "size")

    def __init__(self, feature=None, threshold=None,
                  left=None, right=None, size=1):
        self.feature = feature
        self.threshold = threshold
        self.left = left
        self.right = right
        self.size = size


def _build_itree(X: "np.ndarray", depth: int, max_depth: int,
                  rng: "np.random.Generator") -> _ITree:
    """Grow one isolation tree by random feature + random split."""
    n, p = X.shape
    if depth >= max_depth or n <= 1:
        return _ITree(size=n)
    f = int(rng.integers(0, p))
    col = X[:, f]
    lo, hi = float(col.min()), float(col.max())
    if lo == hi:
        return _ITree(size=n)
    thr = float(rng.uniform(lo, hi))
    mask = col < thr
    if mask.sum() == 0 or mask.sum() == n:
        return _ITree(size=n)
    left = _build_itree(X[mask], depth + 1, max_depth, rng)
    right = _build_itree(X[~mask], depth + 1, max_depth, rng)
    return _ITree(feature=f, threshold=thr,
                   left=left, right=right, size=n)


def _path_length(tree: _ITree, x: "np.ndarray", depth: int = 0) -> float:
    """Path length to isolation; expected-extra-depth correction at leaves
    follows Liu et al. 2008 (c(size) below)."""
    if tree.feature is None:
        return depth + _avg_path_length_unsuccessful(tree.size)
    if x[tree.feature] < tree.threshold:
        return _path_length(tree.left, x, depth + 1)
    return _path_length(tree.right, x, depth + 1)


def _avg_path_length_unsuccessful(n: int) -> float:
    """c(n) — average path length of an unsuccessful BST search."""
    if n <= 1:
        return 0.0
    # Harmonic number H(n-1) ≈ ln(n-1) + 0.5772.
    h = math.log(n - 1) + 0.5772156649
    return 2.0 * h - 2.0 * (n - 1) / n


def isolation_forest_outliers(
    X: Sequence[Sequence[float]],
    *,
    n_trees: int = 100,
    sample_size: int = 256,
    threshold: float = 0.6,
    seed: Optional[int] = 42,
) -> Dict[str, Any]:
    """Pure-numpy Isolation Forest (Liu, Ting & Zhou 2008).

    Score is in [0, 1]; values approaching 1 are anomalies. The
    conventional cut-off is 0.5 (above is anomalous), but we expose
    ``threshold`` for tuning.

    Parameters
    ----------
    X
        2-D array (n × p). NaN rows treated like the ``mahalanobis``
        function.
    n_trees
        Forest size. 100 is the original-paper default.
    sample_size
        Each tree is built from a random subsample of this size; bigger
        samples give finer scores but cost more.
    threshold
        Cut-off on the [0, 1] score above which a point is flagged.

    Returns
    -------
    dict — same shape as :func:`mahalanobis_outliers`, with extra
    ``n_trees`` / ``sample_size`` for audit.
    """
    if np is None:
        raise RuntimeError("numpy required for isolation_forest_outliers")
    arr = np.asarray(X, dtype=float)
    n_orig, p = arr.shape
    nan_mask = np.any(np.isnan(arr), axis=1)
    valid_idx = np.where(~nan_mask)[0]
    if len(valid_idx) < 2:
        return {
            "scores": [float("nan")] * n_orig,
            "is_outlier": [False] * n_orig,
            "cutoff": threshold, "n_outliers": 0,
            "n_observations": int(len(valid_idx)), "n_features": p,
            "method": "isolation_forest", "n_trees": n_trees,
            "sample_size": sample_size,
            "skipped_reason": "fewer_than_2_observations",
        }
    valid = arr[valid_idx]
    n_valid = valid.shape[0]
    sample_size = min(sample_size, n_valid)
    max_depth = max(1, int(math.ceil(math.log2(max(2, sample_size)))))
    rng = np.random.default_rng(seed)
    trees = []
    for _ in range(n_trees):
        idx = rng.choice(n_valid, size=sample_size, replace=False)
        trees.append(_build_itree(valid[idx], 0, max_depth, rng))
    c_n = _avg_path_length_unsuccessful(sample_size)
    scores_valid = np.empty(n_valid, dtype=float)
    for i, x in enumerate(valid):
        avg_path = float(np.mean([_path_length(t, x) for t in trees]))
        scores_valid[i] = 2.0 ** (-avg_path / c_n) if c_n > 0 else 0.5
    scores_all = np.full(n_orig, float("nan"))
    scores_all[valid_idx] = scores_valid
    is_out = np.zeros(n_orig, dtype=bool)
    is_out[valid_idx] = scores_valid > threshold
    return {
        "scores": scores_all.tolist(),
        "is_outlier": is_out.tolist(),
        "cutoff": threshold,
        "n_outliers": int(is_out.sum()),
        "n_observations": int(n_valid),
        "n_features": p,
        "method": "isolation_forest",
        "n_trees": n_trees,
        "sample_size": sample_size,
        "max_depth": max_depth,
        "seed": seed,
        "notes": [
            "isolation forest implemented per Liu/Ting/Zhou 2008",
            "score >0.5 is the canonical anomaly threshold; 0.6 is a "
            "conservative default",
        ],
    }


# ===========================================================================
# Local Outlier Factor — pure numpy
# ===========================================================================

def lof_outliers(
    X: Sequence[Sequence[float]],
    *,
    k: int = 20,
    threshold: float = 1.5,
) -> Dict[str, Any]:
    """Local Outlier Factor (Breunig, Kriegel, Ng & Sander 2000).

    For each point, the LOF score is the ratio of the local
    reachability density of its k nearest neighbours to its own. Points
    with LOF substantially > 1 are denser-around-them than they
    themselves are — i.e., outliers in their local neighbourhood.

    Default ``threshold = 1.5`` is a standard practical cutoff; vary
    by application via the sensitivity layer.

    Brute-force O(n²) distance matrix; fine for n ≤ a few thousand.
    """
    if np is None:
        raise RuntimeError("numpy required for lof_outliers")
    arr = np.asarray(X, dtype=float)
    n_orig, p = arr.shape
    nan_mask = np.any(np.isnan(arr), axis=1)
    valid_idx = np.where(~nan_mask)[0]
    n_valid = len(valid_idx)
    if n_valid < k + 1:
        return {
            "scores": [float("nan")] * n_orig,
            "is_outlier": [False] * n_orig,
            "cutoff": threshold, "n_outliers": 0,
            "n_observations": n_valid, "n_features": p,
            "method": "lof", "k": k,
            "skipped_reason": f"need_at_least_{k + 1}_obs",
        }
    valid = arr[valid_idx]
    # Pairwise Euclidean distances.
    d = np.sqrt(np.sum((valid[:, None, :] - valid[None, :, :]) ** 2, axis=2))
    np.fill_diagonal(d, np.inf)  # exclude self
    # k-distance and neighbour indices.
    k_neighbours = np.argpartition(d, k - 1, axis=1)[:, :k]
    k_dists = np.take_along_axis(d, k_neighbours, axis=1).max(axis=1)
    # Reachability distance: max(k-distance(o), d(p, o)).
    reach_dist = np.maximum(d, k_dists[None, :])
    np.fill_diagonal(reach_dist, np.inf)
    # Local reachability density: 1 / mean reach_dist over k neighbours.
    neighbour_reach = np.take_along_axis(reach_dist, k_neighbours, axis=1)
    mean_reach = neighbour_reach.mean(axis=1)
    lrd = 1.0 / np.where(mean_reach > 0, mean_reach, np.inf)
    # LOF.
    neighbour_lrd = lrd[k_neighbours]
    lof = neighbour_lrd.mean(axis=1) / np.where(lrd > 0, lrd, np.inf)
    scores_all = np.full(n_orig, float("nan"))
    scores_all[valid_idx] = lof
    is_out = np.zeros(n_orig, dtype=bool)
    is_out[valid_idx] = lof > threshold
    return {
        "scores": scores_all.tolist(),
        "is_outlier": is_out.tolist(),
        "cutoff": threshold,
        "n_outliers": int(is_out.sum()),
        "n_observations": n_valid,
        "n_features": p,
        "method": "lof",
        "k": k,
        "notes": [
            "LOF per Breunig 2000; LOF >> 1 indicates a local outlier",
            "threshold=1.5 is conventional but application-dependent",
        ],
    }


# ===========================================================================
# Combined orchestrator
# ===========================================================================

def detect_multivariate_outliers(
    X: Sequence[Sequence[float]],
    *,
    columns: Optional[List[str]] = None,
    methods: Sequence[str] = ("mahalanobis_robust", "isolation_forest", "lof"),
    seed: Optional[int] = 42,
) -> Dict[str, Any]:
    """Run the requested detectors and join their results.

    Each row in the input gets:
      * per-method scores
      * per-method outlier flag
      * a ``consensus`` count (number of methods that flagged it)
      * a ``confidence`` in [0, 1] (consensus / len(methods))

    Returns a dict with::

        {
          "n_observations": int,
          "n_features":     int,
          "columns":        list[str] | None,
          "methods_run":    list[str],
          "per_method":     {method_id: {raw result dict}},
          "points":         [{row_idx, mahalanobis, isolation_forest,
                                lof, consensus, confidence,
                                flagged_by: [method_ids]}],
          "n_consensus_2of3": int,
          "n_any_method":    int,
        }
    """
    if np is None:
        raise RuntimeError("numpy required for detect_multivariate_outliers")
    arr = np.asarray(X, dtype=float)
    n, p = arr.shape

    runs: Dict[str, Dict[str, Any]] = {}
    for m in methods:
        if m == "mahalanobis_robust":
            runs[m] = mahalanobis_outliers(arr, robust=True)
        elif m == "mahalanobis":
            runs[m] = mahalanobis_outliers(arr, robust=False)
        elif m == "isolation_forest":
            runs[m] = isolation_forest_outliers(arr, seed=seed)
        elif m == "lof":
            runs[m] = lof_outliers(arr)

    points: List[Dict[str, Any]] = []
    for i in range(n):
        flags = {m: bool(runs[m].get("is_outlier", [False] * n)[i])
                  for m in runs}
        scores = {m: runs[m].get("scores", [None] * n)[i] for m in runs}
        flagged_by = [m for m, fl in flags.items() if fl]
        consensus = len(flagged_by)
        points.append({
            "row_idx": i,
            "scores": scores,
            "flags": flags,
            "consensus": consensus,
            "confidence": consensus / max(1, len(runs)),
            "flagged_by": flagged_by,
        })
    return {
        "n_observations": n,
        "n_features": p,
        "columns": columns,
        "methods_run": list(runs.keys()),
        "per_method": runs,
        "points": points,
        "n_consensus_2of3": sum(1 for pt in points if pt["consensus"] >= 2),
        "n_any_method": sum(1 for pt in points if pt["consensus"] >= 1),
    }
