"""
Compositional data analysis — Wave I, Method 10.

Aitchison's log-ratio approach for proper handling of proportional /
share data:

* :func:`is_compositional`     — detect when rows sum to a constant
                                   (microbiome, market shares, etc.).
* :func:`clr_transform`        — centred log-ratio: log(x_i / g(x))
                                   where g is the geometric mean.
* :func:`ilr_transform`        — isometric log-ratio (orthonormal basis
                                   in simplex space).
* :func:`compositional_correlation` — Pearson correlation in CLR space;
                                        avoids the spurious correlations
                                        the closure constraint produces
                                        in raw proportions.

Pure numpy. No external dependencies.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Sequence, Tuple

try:
    import numpy as np
except Exception:  # pragma: no cover
    np = None  # type: ignore[assignment]


# ===========================================================================
# Detection
# ===========================================================================

def is_compositional(
    X: Sequence[Sequence[float]],
    *,
    tol: float = 0.05,
    canonical_sum_tolerance: float = 0.10,
) -> Dict[str, Any]:
    """Decide if a matrix is compositional (rows sum to a constant).

    Three checks must all pass:
      1. All values non-negative.
      2. Row-sum CV below ``tol`` (rows sum to "the same" constant).
      3. Mean row sum near a canonical simplex constant: 1.0
         (proportions) or 100.0 (percentages). Within
         ``canonical_sum_tolerance`` of one of these two.

    The canonical-sum check guards against false positives from
    datasets whose columns happen to vary little relative to a
    dominant column (e.g., NOAA buoy data where pressure ≈ 1013 hPa
    dominates the row sum, giving a low CV that has nothing to do
    with simplex structure).
    """
    if np is None:
        raise RuntimeError("numpy required")
    arr = np.asarray(X, dtype=float)
    if arr.ndim != 2 or arr.shape[1] < 2:
        return {"compositional": False,
                "reason": "need_2d_with_2cols"}
    if np.any(arr < 0):
        return {"compositional": False,
                "reason": "negative_values_present"}
    row_sums = arr.sum(axis=1)
    mean_sum = float(row_sums.mean())
    if mean_sum <= 0:
        return {"compositional": False,
                "reason": "zero_or_negative_row_sums"}
    cv = float(row_sums.std() / mean_sum)
    if cv >= tol:
        return {
            "compositional": False,
            "row_sum_cv": cv,
            "mean_row_sum": mean_sum,
            "tolerance": tol,
            "reason": "row_sums_vary_too_much",
        }
    # Canonical-sum check: the rows must sum to ≈1 or ≈100, the two
    # conventional simplex constants. Otherwise rows being "constant"
    # just means a single dominant column.
    near_one = abs(mean_sum - 1.0) / max(1e-9, abs(mean_sum)) < canonical_sum_tolerance
    near_hundred = abs(mean_sum - 100.0) / max(1e-9, abs(mean_sum)) < canonical_sum_tolerance
    if not (near_one or near_hundred):
        return {
            "compositional": False,
            "row_sum_cv": cv,
            "mean_row_sum": mean_sum,
            "tolerance": tol,
            "reason": (f"row_sum_constant_but_not_simplex "
                        f"(mean_sum={mean_sum:.3g}; expected ≈1 or ≈100)"),
        }
    return {
        "compositional": True,
        "row_sum_cv": cv,
        "mean_row_sum": mean_sum,
        "tolerance": tol,
        "canonical_target": (1.0 if near_one else 100.0),
        "reason": "rows_sum_to_simplex_constant",
    }


# ===========================================================================
# Transforms
# ===========================================================================

def clr_transform(
    X: Sequence[Sequence[float]],
    *,
    pseudocount: float = 0.5,
) -> Dict[str, Any]:
    """Centred log-ratio transform.

    For each row x = (x_1, ..., x_p):
      clr(x) = (log(x_1 / g(x)), ..., log(x_p / g(x)))
    where g(x) = geometric mean.

    Zero values are handled by adding ``pseudocount`` (Aitchison's
    convention is 0.5 for count data; 0 disables the adjustment).
    """
    if np is None:
        raise RuntimeError("numpy required")
    arr = np.asarray(X, dtype=float)
    if pseudocount > 0 and np.any(arr == 0):
        arr = arr + pseudocount
        notes = [f"pseudocount={pseudocount} added to zero entries"]
    else:
        notes = []
    if np.any(arr <= 0):
        return {"available": False,
                "skipped_reason": "non_positive_values",
                "notes": notes}
    log_x = np.log(arr)
    g = log_x.mean(axis=1, keepdims=True)
    clr = log_x - g
    return {
        "available": True,
        "transformed": clr.tolist(),
        "n_rows": arr.shape[0],
        "n_cols": arr.shape[1],
        "method": "centred_log_ratio",
        "notes": notes + [
            "CLR mean is zero by construction (rank deficient by 1)"
        ],
    }


def ilr_transform(
    X: Sequence[Sequence[float]],
    *,
    pseudocount: float = 0.5,
) -> Dict[str, Any]:
    """Isometric log-ratio transform via the canonical Helmert basis.

    Maps a p-part composition to (p-1) coordinates in unconstrained
    Euclidean space. Standard methods (PCA, regression, distance
    metrics) work correctly on ILR coordinates.

    Helmert basis V is (p-1) × p with V V^T = I. The k-th basis vector
    contrasts component k+1 with the geometric mean of components 1..k.
    """
    if np is None:
        raise RuntimeError("numpy required")
    arr = np.asarray(X, dtype=float)
    if pseudocount > 0 and np.any(arr == 0):
        arr = arr + pseudocount
    if np.any(arr <= 0):
        return {"available": False,
                "skipped_reason": "non_positive_values"}
    log_x = np.log(arr)
    p = arr.shape[1]
    # Helmert basis (orthonormal in p-simplex tangent space).
    V = np.zeros((p - 1, p))
    for k in range(p - 1):
        V[k, :k + 1] = 1.0 / math.sqrt((k + 1) * (k + 2))
        V[k, k + 1] = -math.sqrt((k + 1) / (k + 2))
    ilr = log_x @ V.T
    return {
        "available": True,
        "transformed": ilr.tolist(),
        "n_rows": arr.shape[0],
        "n_input_cols": p,
        "n_output_cols": p - 1,
        "method": "isometric_log_ratio_helmert",
        "notes": [
            "ILR coordinates are unconstrained — apply standard "
            "Euclidean methods directly without the closure penalty",
        ],
    }


# ===========================================================================
# Compositional correlation
# ===========================================================================

def compositional_correlation(
    X: Sequence[Sequence[float]],
    *,
    pseudocount: float = 0.5,
) -> Dict[str, Any]:
    """Pearson correlation in CLR space.

    The naive Pearson correlation of raw proportions is biased toward
    -1/(p-1) by the closure constraint. CLR-transforming first removes
    that bias.

    Returns both raw and CLR correlations so the synthesis layer can
    surface "correlation X disappears once we account for the closure
    constraint."
    """
    if np is None:
        raise RuntimeError("numpy required")
    arr = np.asarray(X, dtype=float)
    raw_corr = np.corrcoef(arr, rowvar=False)
    clr_res = clr_transform(arr, pseudocount=pseudocount)
    if not clr_res.get("available"):
        return {"available": False,
                "skipped_reason": clr_res.get("skipped_reason")}
    clr_arr = np.asarray(clr_res["transformed"])
    clr_corr = np.corrcoef(clr_arr, rowvar=False)
    # Find pairs whose correlation flips sign or magnitude substantially.
    differences = []
    p = arr.shape[1]
    for i in range(p):
        for j in range(i + 1, p):
            r_raw = float(raw_corr[i, j])
            r_clr = float(clr_corr[i, j])
            if abs(r_raw - r_clr) > 0.2:
                differences.append({
                    "i": i, "j": j,
                    "raw_correlation": r_raw,
                    "clr_correlation": r_clr,
                    "difference": r_clr - r_raw,
                })
    return {
        "available": True,
        "raw_correlation_matrix": raw_corr.tolist(),
        "clr_correlation_matrix": clr_corr.tolist(),
        "substantial_differences": differences,
        "n_substantial": len(differences),
        "method": "pearson_in_clr_space",
        "notes": [
            "raw Pearson on proportions is biased by closure constraint",
            "CLR removes the closure bias by mapping to (p-1)-d "
            "Euclidean space",
        ],
    }
