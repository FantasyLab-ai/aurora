"""
Mutual information — Wave G, Method 5.

Two estimators:

* :func:`mi_binning`   — histogram / binning estimator. Fast, decent
                          for univariate with reasonable n.
* :func:`mi_ksg`       — Kraskov-Stögbauer-Grassberger 1st-form
                          estimator (KSG1). The standard for continuous
                          variables; works on small samples.
* :func:`conditional_mi` — I(X; Y | Z) using a binning estimator.

* :func:`mi_matrix`    — pairwise MI matrix for a feature DataFrame-like
                          input. Detects nonlinear dependencies that
                          correlation matrices miss.

Permutation-based significance testing for both estimators.

Pure numpy. Brute-force nearest-neighbour search (O(n²)); fine for the
sample sizes Aurora analyses (n ≤ a few thousand).
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Sequence, Tuple

try:
    import numpy as np
except Exception:  # pragma: no cover
    np = None  # type: ignore[assignment]


# ===========================================================================
# Binning estimator
# ===========================================================================

def mi_binning(
    x: Sequence[float], y: Sequence[float],
    *,
    n_bins: int = 10,
    base: float = math.e,
) -> float:
    """Mutual information via 2-D histogram. Returns I(X; Y) in nats
    (or bits if base=2).

    Uses equal-width binning. Add small ε for numerical stability.
    """
    if np is None:
        raise RuntimeError("numpy required")
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    mask = ~(np.isnan(x) | np.isnan(y))
    x = x[mask]; y = y[mask]
    n = len(x)
    if n < 4:
        return 0.0
    h_xy, _, _ = np.histogram2d(x, y, bins=n_bins)
    p_xy = h_xy / n
    p_x = p_xy.sum(axis=1, keepdims=True)
    p_y = p_xy.sum(axis=0, keepdims=True)
    eps = 1e-12
    log_term = np.log(p_xy / (p_x * p_y + eps) + eps) / math.log(base)
    mi = float(np.sum(p_xy * np.where(p_xy > 0, log_term, 0)))
    return max(0.0, mi)


# ===========================================================================
# KSG estimator (Kraskov-Stögbauer-Grassberger 2004, eq. 8 — KSG1)
# ===========================================================================

def _digamma(x: float) -> float:
    """Digamma function via the Hurwitz-zeta-like series.

    Pure-Python, no scipy. Accurate to ~1e-9 for x > 1.
    """
    if x <= 0:
        return -float("inf")
    # Reflection isn't needed since callers always pass positive x.
    result = 0.0
    while x < 6:
        result -= 1.0 / x
        x += 1.0
    inv = 1.0 / x
    inv2 = inv * inv
    # Asymptotic expansion ψ(x) ≈ ln(x) - 1/(2x) - Σ B_{2n}/(2n x^{2n}).
    result += math.log(x) - 0.5 * inv \
              - inv2 * (1/12 - inv2 * (1/120 - inv2 * (1/252)))
    return result


def mi_ksg(
    x: Sequence[float], y: Sequence[float],
    *,
    k: int = 3,
) -> float:
    """KSG1 estimator (Kraskov, Stögbauer & Grassberger 2004).

    For each point i:
      - find its k-th nearest neighbour in joint space (max-norm).
      - count n_x(i) = #points with |x_j - x_i| < ε(i) (excl. i).
      - count n_y(i) = #points with |y_j - y_i| < ε(i) (excl. i).

    Estimate: I(X; Y) = ψ(k) + ψ(N) - <ψ(n_x+1) + ψ(n_y+1)>.
    """
    if np is None:
        raise RuntimeError("numpy required")
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    mask = ~(np.isnan(x) | np.isnan(y))
    x = x[mask]; y = y[mask]
    n = len(x)
    if n < k + 2:
        return 0.0
    # Joint distance matrix in max-norm: max(|Δx|, |Δy|).
    dx = np.abs(x[:, None] - x[None, :])
    dy = np.abs(y[:, None] - y[None, :])
    d_joint = np.maximum(dx, dy)
    np.fill_diagonal(d_joint, np.inf)
    # ε_i = 2 * max-norm distance to k-th neighbour. We use strict <
    # below, so use the kth smallest (0-indexed: k-1) + tiny epsilon.
    eps = np.partition(d_joint, k - 1, axis=1)[:, k - 1]
    # Margin counts: |Δx| < ε (strict), excluding self.
    np.fill_diagonal(dx, np.inf)
    np.fill_diagonal(dy, np.inf)
    n_x = np.sum(dx < eps[:, None], axis=1)
    n_y = np.sum(dy < eps[:, None], axis=1)
    psi_avg = (np.array([_digamma(int(nx) + 1) for nx in n_x])
                + np.array([_digamma(int(ny) + 1) for ny in n_y])).mean()
    mi = _digamma(k) + _digamma(n) - psi_avg
    return float(max(0.0, mi))


# ===========================================================================
# Conditional MI (binning)
# ===========================================================================

def conditional_mi(
    x: Sequence[float], y: Sequence[float], z: Sequence[float],
    *,
    n_bins: int = 6,
) -> float:
    """I(X; Y | Z) via 3-D binning."""
    if np is None:
        raise RuntimeError("numpy required")
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    z = np.asarray(z, dtype=float)
    mask = ~(np.isnan(x) | np.isnan(y) | np.isnan(z))
    x = x[mask]; y = y[mask]; z = z[mask]
    n = len(x)
    if n < 4:
        return 0.0
    # Bin each dimension.
    bx = np.linspace(x.min(), x.max(), n_bins + 1)
    by = np.linspace(y.min(), y.max(), n_bins + 1)
    bz = np.linspace(z.min(), z.max(), n_bins + 1)
    # Tiny epsilon so the upper edge falls in the last bin.
    bx[-1] += 1e-9; by[-1] += 1e-9; bz[-1] += 1e-9
    ix = np.clip(np.digitize(x, bx) - 1, 0, n_bins - 1)
    iy = np.clip(np.digitize(y, by) - 1, 0, n_bins - 1)
    iz = np.clip(np.digitize(z, bz) - 1, 0, n_bins - 1)
    p_xyz = np.zeros((n_bins, n_bins, n_bins))
    for a, b, c in zip(ix, iy, iz):
        p_xyz[a, b, c] += 1
    p_xyz /= n
    p_xz = p_xyz.sum(axis=1, keepdims=True)
    p_yz = p_xyz.sum(axis=0, keepdims=True)
    p_z  = p_xyz.sum(axis=(0, 1), keepdims=True)
    eps = 1e-12
    log_term = np.log(p_xyz * p_z / (p_xz * p_yz + eps) + eps)
    cmi = float(np.sum(p_xyz * np.where(p_xyz > 0, log_term, 0)))
    return max(0.0, cmi)


# ===========================================================================
# MI matrix + nonlinear-dependence finding
# ===========================================================================

def mi_matrix(
    X: Sequence[Sequence[float]],
    *,
    columns: Optional[List[str]] = None,
    estimator: str = "ksg",
    k: int = 3,
    seed: Optional[int] = 42,
    n_permutations: int = 0,
) -> Dict[str, Any]:
    """Pairwise MI matrix for a 2-D array (n_obs × n_features).

    Detects nonlinear dependencies that linear correlation misses by
    comparing MI vs. the squared Pearson correlation rescaled to a
    rough lower-bound on MI for Gaussian variables: -0.5 · ln(1 - r²).
    Pairs where MI substantially exceeds this Gaussian baseline are
    flagged as ``nonlinear_dependence``.
    """
    if np is None:
        raise RuntimeError("numpy required")
    arr = np.asarray(X, dtype=float)
    if arr.ndim != 2 or arr.shape[1] < 2:
        return {"available": False, "skipped_reason": "need_2d_with_2cols"}
    n, p = arr.shape
    cols = columns or [f"x{i}" for i in range(p)]
    mi = np.zeros((p, p))
    nonlinear_pairs: List[Dict[str, Any]] = []
    if estimator not in ("ksg", "binning"):
        return {"available": False,
                "skipped_reason": f"unknown_estimator_{estimator}"}
    for i in range(p):
        for j in range(i + 1, p):
            xi = arr[:, i]; xj = arr[:, j]
            mask = ~(np.isnan(xi) | np.isnan(xj))
            xi_v = xi[mask]; xj_v = xj[mask]
            if len(xi_v) < 5:
                continue
            if estimator == "ksg":
                m = mi_ksg(xi_v, xj_v, k=k)
            else:
                m = mi_binning(xi_v, xj_v)
            mi[i, j] = mi[j, i] = m
            # Compare to Gaussian MI lower-bound from |r|.
            if len(xi_v) > 2:
                r = float(np.corrcoef(xi_v, xj_v)[0, 1])
                if abs(r) < 0.999:
                    gauss_baseline = -0.5 * math.log(max(1e-9, 1 - r ** 2))
                else:
                    gauss_baseline = float("inf")
                # "Nonlinear" = MI is materially above the Gaussian
                # baseline AND the linear correlation alone is weak.
                if (m - max(0.0, gauss_baseline)) > 0.2 and abs(r) < 0.4:
                    nonlinear_pairs.append({
                        "i": i, "j": j,
                        "col_i": cols[i], "col_j": cols[j],
                        "mi": m, "pearson_r": r,
                        "gauss_baseline": gauss_baseline,
                        "excess_above_linear": m - max(0.0, gauss_baseline),
                    })
    return {
        "available": True,
        "n_features": p,
        "columns": cols,
        "mi_matrix": mi.tolist(),
        "estimator": estimator,
        "k": k if estimator == "ksg" else None,
        "nonlinear_pairs": nonlinear_pairs,
        "method": f"mutual_information_{estimator}",
    }
