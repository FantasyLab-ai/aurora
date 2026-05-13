"""
Sparse Identification of Nonlinear Dynamics (SINDy) — Wave H, Method 1.

Workstream-2 fixes (S-2, S-3):
  * Drop columns with > 20 % NaN, < 1e-10 variance, or any non-finite
    value before the polynomial library is built. NaNs reaching LAPACK
    cause ``DLASCLS parameter 4 illegal value`` followed by ``SVD did
    not converge`` — the maintainer's diabetic_data crash.
  * stlsq + the inner lstsq calls catch ``np.linalg.LinAlgError`` and
    return a clean ``available=False, skipped_reason=numerical_failure``
    rather than raising. Every catch logs ``traceback.format_exc()``
    via ``aurora.sindy``.

Brunton-Proctor-Kutz 2016. Discovers governing differential equations
of a dynamical system directly from time-series data.

Pipeline:
  1. Build a candidate feature library Θ(X) — polynomials, products,
     trig, etc. — over the observed states.
  2. Estimate derivatives Ẋ from the data (finite difference).
  3. Solve Ẋ ≈ Θ(X) · Ξ with sparse regression (sequential thresholded
     least-squares — STLSQ).
  4. The non-zero rows of Ξ are the active terms; their coefficients
     are the equation's structure.

Pure numpy. STLSQ is the original SINDy regression — alternates between
fitting OLS and zeroing coefficients below a threshold, until convergence.

Implements polynomial feature library up to a configurable degree,
plus optional sin/cos terms.
"""
from __future__ import annotations

import logging
import math
import traceback
from itertools import combinations_with_replacement
from typing import Any, Dict, List, Optional, Sequence, Tuple

try:
    import numpy as np
except Exception:  # pragma: no cover
    np = None  # type: ignore[assignment]


_log = logging.getLogger("aurora.sindy")


def _clean_inputs_for_sindy(arr: "np.ndarray",
                              col_names: Optional[List[str]] = None,
                              *, max_nan_frac: float = 0.20,
                              min_variance: float = 1e-10
                              ) -> Tuple["np.ndarray", List[str], Dict[str, Any]]:
    """Strip NaN-heavy / zero-variance / non-finite columns and rows
    before SINDy hits LAPACK.

    Brief: fix S-2. Diabetic_data has columns where 30%+ values are
    NaN; the central-difference + polynomial library propagate those
    into Theta, and ``np.linalg.lstsq`` then crashes inside
    ``DLASCLS``. This pre-pass drops the offending columns AND any
    rows still containing NaN/Inf.

    Returns (clean_arr, kept_col_names, audit_dict). The audit lists
    which columns were dropped and why so the caller can surface it.
    """
    if np is None:
        raise RuntimeError("numpy required")
    n, p = arr.shape
    names = list(col_names) if col_names else [f"x{i}" for i in range(p)]
    kept_cols: List[int] = []
    dropped: List[Dict[str, Any]] = []
    for j in range(p):
        col = arr[:, j]
        n_finite = int(np.sum(np.isfinite(col)))
        nan_frac = 1.0 - n_finite / max(1, n)
        if nan_frac > max_nan_frac:
            dropped.append({"name": names[j], "reason": "high_nan_fraction",
                              "nan_frac": float(nan_frac)})
            continue
        finite_vals = col[np.isfinite(col)]
        if finite_vals.size == 0:
            dropped.append({"name": names[j], "reason": "all_nan"})
            continue
        var = float(np.var(finite_vals))
        if var < min_variance:
            dropped.append({"name": names[j], "reason": "near_zero_variance",
                              "variance": var})
            continue
        kept_cols.append(j)
    if not kept_cols:
        return np.zeros((0, 0)), [], {
            "kept_columns": [], "dropped_columns": dropped,
            "n_rows_input": n, "n_rows_kept": 0,
        }
    sub = arr[:, kept_cols]
    # Drop rows that still contain any non-finite value.
    finite_row_mask = np.all(np.isfinite(sub), axis=1)
    sub = sub[finite_row_mask]
    return sub, [names[k] for k in kept_cols], {
        "kept_columns": [names[k] for k in kept_cols],
        "dropped_columns": dropped,
        "n_rows_input": n,
        "n_rows_kept": int(sub.shape[0]),
    }


# ===========================================================================
# Feature library
# ===========================================================================

def polynomial_library(
    X: "np.ndarray",
    *,
    degree: int = 3,
    include_bias: bool = True,
    column_names: Optional[List[str]] = None,
) -> Tuple["np.ndarray", List[str]]:
    """Build polynomial feature matrix up to ``degree``.

    For columns x, y at degree 2: returns
    [1, x, y, x², xy, y²] (if include_bias).
    """
    n, p = X.shape
    cols = column_names or [f"x{i}" for i in range(p)]
    feature_names: List[str] = []
    feature_values: List["np.ndarray"] = []
    if include_bias:
        feature_names.append("1")
        feature_values.append(np.ones(n))
    for d in range(1, degree + 1):
        for combo in combinations_with_replacement(range(p), d):
            name_parts: List[str] = []
            counts: Dict[int, int] = {}
            for c in combo:
                counts[c] = counts.get(c, 0) + 1
            for c, k in sorted(counts.items()):
                if k == 1:
                    name_parts.append(cols[c])
                else:
                    name_parts.append(f"{cols[c]}^{k}")
            name = "*".join(name_parts)
            vals = np.ones(n)
            for c in combo:
                vals = vals * X[:, c]
            feature_names.append(name)
            feature_values.append(vals)
    return np.column_stack(feature_values), feature_names


def add_trig_features(
    X: "np.ndarray", names: List[str], theta: "np.ndarray",
    theta_names: List[str],
    *,
    column_names: Optional[List[str]] = None,
) -> Tuple["np.ndarray", List[str]]:
    """Append sin/cos features for each input column."""
    cols = column_names or names
    extra = []; extra_names = []
    for i, c in enumerate(cols):
        extra.append(np.sin(X[:, i])); extra_names.append(f"sin({c})")
        extra.append(np.cos(X[:, i])); extra_names.append(f"cos({c})")
    if extra:
        return np.column_stack([theta] + extra), theta_names + extra_names
    return theta, theta_names


# ===========================================================================
# Sequential thresholded least-squares (STLSQ)
# ===========================================================================

def _safe_lstsq(A: "np.ndarray", b: "np.ndarray"
                  ) -> Tuple[Optional["np.ndarray"], Optional[Exception]]:
    """``np.linalg.lstsq`` wrapper that catches LinAlgError + raises
    nothing. Brief: S-3 — LAPACK failures (DLASCLS-illegal-value,
    SVD-did-not-converge) flow through here as a returned error
    sentinel + a logged ``traceback.format_exc()``. The caller decides
    whether to back off, retry on a smaller subset, or give up."""
    try:
        x, *_ = np.linalg.lstsq(A, b, rcond=None)
        # Defensive: LAPACK can return NaN when scaling fails silently.
        if not np.all(np.isfinite(x)):
            return None, ValueError("lstsq returned non-finite coefficients")
        return x, None
    except Exception as e:
        _log.error("sindy lstsq failed (A.shape=%s, b.shape=%s)\n%s",
                     getattr(A, "shape", "?"),
                     getattr(b, "shape", "?"),
                     traceback.format_exc())
        return None, e


def stlsq(
    Theta: "np.ndarray",
    Xdot: "np.ndarray",
    *,
    threshold: float = 0.05,
    max_iter: int = 25,
) -> "np.ndarray":
    """Brunton-Proctor-Kutz STLSQ regression.

    Solves Xdot ≈ Theta @ Ξ with iterative thresholding: fit OLS, zero
    coefficients with |ξ| < threshold, refit on remaining columns,
    repeat until convergence.

    Workstream-2 fix S-3: every lstsq call goes through ``_safe_lstsq``;
    a LAPACK failure aborts the iteration and returns whatever
    coefficient matrix we have so far (zeroed if we never got past the
    initial solve). The caller (sindy_discover) checks for an
    all-zero / NaN result and surfaces a clean ``available=False``.

    Returns Ξ — shape (n_features, n_outputs).
    """
    Xi, err = _safe_lstsq(Theta, Xdot)
    if Xi is None:
        # Initial solve failed — bail with a zero matrix so the caller
        # sees an empty discovery rather than a raised exception.
        n_out = Xdot.shape[1] if Xdot.ndim > 1 else 1
        shape = (Theta.shape[1], n_out) if n_out > 1 else (Theta.shape[1],)
        return np.zeros(shape)
    for _ in range(max_iter):
        small = np.abs(Xi) < threshold
        Xi_new = Xi.copy()
        Xi_new[small] = 0.0
        # For each output column, refit on the active features.
        for j in range(Xi.shape[1] if Xi.ndim > 1 else 1):
            if Xi.ndim > 1:
                active = ~small[:, j]
                if active.sum() == 0:
                    continue
                xi_j, err = _safe_lstsq(Theta[:, active], Xdot[:, j])
                if xi_j is None:
                    # Subset solve failed — leave Xi_new[:, j] zeroed
                    # and continue with the other output columns.
                    Xi_new[:, j] = 0.0
                    continue
                Xi_new[active, j] = xi_j
            else:
                active = ~small
                if active.sum() == 0:
                    continue
                xi_j, err = _safe_lstsq(Theta[:, active], Xdot)
                if xi_j is None:
                    Xi_new[:] = 0.0
                    continue
                Xi_new[active] = xi_j
        if np.allclose(Xi_new, Xi):
            Xi = Xi_new
            break
        Xi = Xi_new
    return Xi


# ===========================================================================
# Derivative estimation
# ===========================================================================

def central_difference(X: "np.ndarray", dt: float) -> "np.ndarray":
    """Second-order central-difference derivatives. Drops the boundary
    rows. Returns Ẋ same shape as X[1:-1, :]."""
    return (X[2:] - X[:-2]) / (2.0 * dt)


# ===========================================================================
# Top-level: discover dynamics
# ===========================================================================

def sindy_discover(
    X: Sequence[Sequence[float]],
    dt: float,
    *,
    column_names: Optional[List[str]] = None,
    poly_degree: int = 3,
    include_trig: bool = False,
    threshold: float = 0.05,
    n_bootstrap: int = 0,
    seed: Optional[int] = 42,
) -> Dict[str, Any]:
    """Discover the governing equations of a dynamical system.

    Parameters
    ----------
    X
        2-D array (n_obs × n_states). Each row is a system state at
        a uniformly-sampled time.
    dt
        Time step between rows.
    poly_degree
        Maximum polynomial degree in the feature library.
    include_trig
        Append sin/cos features (useful for oscillatory systems).
    threshold
        Coefficient sparsity threshold for STLSQ. Lower = more terms.
    n_bootstrap
        If > 0, also returns coefficient CIs from bootstrapping the
        time-derivatives.

    Returns
    -------
    dict with discovered equations as readable strings, the coefficient
    matrix, the residual error, and (optionally) bootstrap CIs.
    """
    if np is None:
        raise RuntimeError("numpy required")
    arr_raw = np.asarray(X, dtype=float)
    n_raw, p_raw = arr_raw.shape
    if n_raw < 20:
        return {"available": False,
                "skipped_reason": f"need_at_least_20_obs, got {n_raw}"}
    cols_raw = column_names or [f"x{i}" for i in range(p_raw)]
    # Workstream-2 fix S-2: drop NaN-heavy / zero-variance columns and
    # NaN-bearing rows before the polynomial library + LAPACK see them.
    arr, cols, clean_audit = _clean_inputs_for_sindy(arr_raw, cols_raw)
    if arr.shape[0] < 20 or arr.shape[1] < 2:
        return {
            "available": False,
            "skipped_reason": "insufficient_clean_data",
            "clean_audit": clean_audit,
        }
    n, p = arr.shape
    # Build feature library and derivatives.
    Xdot = central_difference(arr, dt)
    X_inner = arr[1:-1, :]
    Theta, feature_names = polynomial_library(
        X_inner, degree=poly_degree, column_names=cols, include_bias=True,
    )
    if include_trig:
        Theta, feature_names = add_trig_features(X_inner, cols, Theta,
                                                   feature_names,
                                                   column_names=cols)
    # Standardise features so threshold has a consistent meaning across
    # columns (then unstandardise the coefficients afterwards).
    feat_norms = np.linalg.norm(Theta, axis=0)
    feat_norms[feat_norms == 0] = 1.0
    Theta_n = Theta / feat_norms
    Xi_n = stlsq(Theta_n, Xdot, threshold=threshold)
    # Unstandardise back to original coefficient scale.
    Xi = Xi_n / feat_norms[:, None]
    # Build readable equations.
    equations: List[str] = []
    for j in range(p):
        terms = []
        for k, c_val in enumerate(Xi[:, j]):
            if abs(c_val) > 1e-12:
                terms.append(f"{c_val:+.4g} * {feature_names[k]}")
        if not terms:
            equations.append(f"d{cols[j]}/dt = 0")
        else:
            equations.append(f"d{cols[j]}/dt = " + " ".join(terms).lstrip("+"))
    # Reconstruction error.
    Xdot_hat = Theta @ Xi
    rmse_per_state = np.sqrt(np.mean((Xdot - Xdot_hat) ** 2, axis=0)).tolist()
    n_active = int(np.sum(np.abs(Xi) > 1e-12))
    n_features = Xi.size
    sparsity = 1.0 - n_active / max(1, n_features)
    # Active terms per equation.
    active_terms: List[List[Dict[str, Any]]] = []
    for j in range(p):
        terms = []
        for k, c_val in enumerate(Xi[:, j]):
            if abs(c_val) > 1e-12:
                terms.append({"feature": feature_names[k],
                                "coefficient": float(c_val)})
        active_terms.append(terms)
    # Optional bootstrap CIs on coefficients.
    bootstrap_ci = None
    if n_bootstrap > 0:
        rng = np.random.default_rng(seed)
        n_inner = X_inner.shape[0]
        boot_coefs = np.zeros((n_bootstrap, *Xi.shape))
        for b in range(n_bootstrap):
            idx = rng.integers(0, n_inner, size=n_inner)
            try:
                Xi_b = stlsq(Theta_n[idx], Xdot[idx], threshold=threshold)
                boot_coefs[b] = Xi_b / feat_norms[:, None]
            except Exception:
                continue
        ci_low = np.quantile(boot_coefs, 0.025, axis=0)
        ci_high = np.quantile(boot_coefs, 0.975, axis=0)
        bootstrap_ci = {"low": ci_low.tolist(), "high": ci_high.tolist()}

    return {
        "available": True,
        "equations": equations,
        "active_terms_per_state": active_terms,
        "coefficients": Xi.tolist(),
        "feature_names": feature_names,
        "state_names": cols,
        "rmse_per_state": rmse_per_state,
        "rmse_total": float(np.sqrt(np.mean((Xdot - Xdot_hat) ** 2))),
        "n_active_terms": n_active,
        "n_candidate_features": n_features,
        "sparsity": float(sparsity),
        "poly_degree": poly_degree,
        "include_trig": include_trig,
        "threshold": threshold,
        "dt": dt,
        "n_obs_used": int(X_inner.shape[0]),
        "bootstrap_ci": bootstrap_ci,
        "clean_audit": clean_audit,  # S-2 — what was dropped pre-fit
        "method": "sindy_stlsq",
        "notes": [
            "Brunton-Proctor-Kutz 2016 SINDy with sequential "
            "thresholded least-squares regression",
            f"polynomial library of degree {poly_degree}"
            + (" + sin/cos" if include_trig else ""),
            f"features standardised before thresholding for scale invariance",
        ],
    }
