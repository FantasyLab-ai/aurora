"""
Spatial autocorrelation — Wave E (part 1).

Two classical statistics:

* :func:`morans_i`  — Moran's I global spatial autocorrelation. Ranges
                        roughly [-1, 1]; positive = like values cluster,
                        negative = like values disperse.
* :func:`gearys_c`  — Geary's C; complementary local-scale measure.
                        ~1 = no autocorrelation; <1 = positive
                        autocorrelation; >1 = negative.

Significance is computed by random permutation (no Gaussian
assumptions). Glass-box: returns the permutation distribution for the
synthesis layer to render.

Pure numpy. No external dependencies.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Sequence

try:
    import numpy as np
except Exception:  # pragma: no cover
    np = None  # type: ignore[assignment]


def _build_inverse_distance_weights(
    coords: "np.ndarray",
    *,
    knn: Optional[int] = None,
) -> "np.ndarray":
    """Build a row-standardised inverse-distance weight matrix.

    With ``knn`` set, only the k nearest neighbours of each point
    contribute to its row; otherwise all other points do (with weight
    1/d). Diagonal is zero. Each row sums to 1.
    """
    n = coords.shape[0]
    d = np.sqrt(np.sum((coords[:, None, :] - coords[None, :, :]) ** 2,
                          axis=2))
    np.fill_diagonal(d, np.inf)
    if knn is not None:
        # Mask out non-knn entries.
        order = np.argsort(d, axis=1)
        keep = np.zeros_like(d, dtype=bool)
        for i in range(n):
            keep[i, order[i, :knn]] = True
        w = np.where(keep, 1.0 / d, 0.0)
    else:
        w = 1.0 / d
        w[~np.isfinite(w)] = 0.0
    # Row-standardise.
    row_sums = w.sum(axis=1, keepdims=True)
    row_sums = np.where(row_sums > 0, row_sums, 1.0)
    return w / row_sums


def morans_i(
    values: Sequence[float],
    coordinates: Sequence[Sequence[float]],
    *,
    knn: Optional[int] = 8,
    n_permutations: int = 999,
    seed: Optional[int] = 42,
) -> Dict[str, Any]:
    """Moran's I global spatial autocorrelation.

    Parameters
    ----------
    values
        Length-n vector of attribute values.
    coordinates
        n × 2 (or n × d) array of point locations.
    knn
        Number of nearest neighbours to use for the weight matrix.
        ``None`` → use all points (1/d weights).
    n_permutations
        Permutation iterations for the significance test.
    seed
        RNG seed.

    Returns
    -------
    dict::

        {
          "available":        True,
          "morans_i":         float,
          "expected_i":       -1/(n-1),
          "p_value":          float,            # one-sided
          "z_score":          float,
          "n":                int,
          "n_permutations":   int,
          "interpretation":   "clustered" | "dispersed" | "random",
          "method":           "morans_i_permutation",
        }
    """
    if np is None:
        raise RuntimeError("numpy required for morans_i")
    y = np.asarray(values, dtype=float)
    coords = np.asarray(coordinates, dtype=float)
    n = y.shape[0]
    if n < 5 or coords.shape[0] != n:
        return {"available": False,
                "skipped_reason": f"need_5_obs_with_coords, got {n}"}
    W = _build_inverse_distance_weights(coords, knn=knn)
    y_dev = y - y.mean()
    den = float(np.sum(y_dev ** 2))
    if den <= 0:
        return {"available": False, "skipped_reason": "zero_variance"}
    num = float(np.sum(W * np.outer(y_dev, y_dev)))
    s0 = float(W.sum())
    I = (n / s0) * (num / den) if s0 > 0 else 0.0
    expected = -1.0 / (n - 1)
    # Permutation test.
    rng = np.random.default_rng(seed)
    null = np.empty(n_permutations)
    for k in range(n_permutations):
        y_perm = rng.permutation(y)
        yd = y_perm - y_perm.mean()
        d_ = float(np.sum(yd ** 2))
        if d_ <= 0:
            null[k] = 0.0
            continue
        n_ = float(np.sum(W * np.outer(yd, yd)))
        null[k] = (n / s0) * (n_ / d_)
    # Two-sided p-value.
    p_value = float((np.sum(np.abs(null - expected) >=
                              abs(I - expected)) + 1) / (n_permutations + 1))
    sd_null = float(np.std(null, ddof=1))
    z = (I - expected) / sd_null if sd_null > 0 else 0.0
    if I > expected and p_value < 0.05:
        interpretation = "clustered"
    elif I < expected and p_value < 0.05:
        interpretation = "dispersed"
    else:
        interpretation = "random"
    return {
        "available": True,
        "morans_i": float(I),
        "expected_i": expected,
        "p_value": p_value,
        "z_score": float(z),
        "n": n,
        "n_permutations": n_permutations,
        "interpretation": interpretation,
        "method": "morans_i_permutation",
        "weight_method": "inverse_distance_knn" if knn else "inverse_distance_full",
        "knn": knn,
    }


def gearys_c(
    values: Sequence[float],
    coordinates: Sequence[Sequence[float]],
    *,
    knn: Optional[int] = 8,
    n_permutations: int = 999,
    seed: Optional[int] = 42,
) -> Dict[str, Any]:
    """Geary's C — complementary local-scale autocorrelation."""
    if np is None:
        raise RuntimeError("numpy required for gearys_c")
    y = np.asarray(values, dtype=float)
    coords = np.asarray(coordinates, dtype=float)
    n = y.shape[0]
    if n < 5 or coords.shape[0] != n:
        return {"available": False,
                "skipped_reason": f"need_5_obs_with_coords, got {n}"}
    W = _build_inverse_distance_weights(coords, knn=knn)
    y_dev = y - y.mean()
    den = float(np.sum(y_dev ** 2))
    if den <= 0:
        return {"available": False, "skipped_reason": "zero_variance"}
    s0 = float(W.sum())

    def _c(y_local: "np.ndarray") -> float:
        diff2 = (y_local[:, None] - y_local[None, :]) ** 2
        num = float(np.sum(W * diff2))
        denom = 2.0 * s0 * float(np.sum((y_local - y_local.mean()) ** 2))
        return ((n - 1) / denom) * num if denom > 0 else 1.0

    c_obs = _c(y)
    rng = np.random.default_rng(seed)
    null = np.empty(n_permutations)
    for k in range(n_permutations):
        null[k] = _c(rng.permutation(y))
    # Two-sided p-value (deviation from 1.0).
    p_value = float((np.sum(np.abs(null - 1.0) >=
                              abs(c_obs - 1.0)) + 1) / (n_permutations + 1))
    if c_obs < 1.0 and p_value < 0.05:
        interpretation = "clustered"   # positive autocorrelation
    elif c_obs > 1.0 and p_value < 0.05:
        interpretation = "dispersed"
    else:
        interpretation = "random"
    return {
        "available": True,
        "gearys_c": float(c_obs),
        "p_value": p_value,
        "n": n,
        "n_permutations": n_permutations,
        "interpretation": interpretation,
        "method": "gearys_c_permutation",
    }
