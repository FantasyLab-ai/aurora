"""
Gaussian process regression — Wave I, Method 6.

Pure-numpy 1-D GP with three kernels:
* RBF / squared-exponential
* Matérn-3/2 (rougher than RBF)
* Periodic (sin² distance)

Hyperparameters (length scale, variance, noise) optimised by gradient-
free grid search on log-marginal likelihood. Posterior mean + 95%
credible band returned.

For Aurora's typical 1-D forecast / smoothing use cases this is
fast and adequate; full-featured GPyTorch isn't needed.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Sequence, Tuple

try:
    import numpy as np
except Exception:  # pragma: no cover
    np = None  # type: ignore[assignment]


# ===========================================================================
# Kernels
# ===========================================================================

def _rbf_kernel(X1: "np.ndarray", X2: "np.ndarray",
                 length_scale: float, variance: float) -> "np.ndarray":
    d2 = (X1[:, None] - X2[None, :]) ** 2
    return variance * np.exp(-0.5 * d2 / length_scale ** 2)


def _matern32_kernel(X1: "np.ndarray", X2: "np.ndarray",
                      length_scale: float, variance: float) -> "np.ndarray":
    d = np.abs(X1[:, None] - X2[None, :])
    arg = math.sqrt(3.0) * d / length_scale
    return variance * (1.0 + arg) * np.exp(-arg)


def _periodic_kernel(X1: "np.ndarray", X2: "np.ndarray",
                       length_scale: float, variance: float,
                       period: float) -> "np.ndarray":
    d = X1[:, None] - X2[None, :]
    sin2 = np.sin(math.pi * d / period) ** 2
    return variance * np.exp(-2.0 * sin2 / length_scale ** 2)


_KERNEL_MAP = {
    "rbf": _rbf_kernel,
    "matern32": _matern32_kernel,
    "periodic": _periodic_kernel,
}


# ===========================================================================
# GP fit + predict
# ===========================================================================

def _log_marginal_likelihood(
    K: "np.ndarray", y: "np.ndarray", noise: float,
) -> float:
    n = K.shape[0]
    K_noise = K + noise * np.eye(n)
    try:
        L = np.linalg.cholesky(K_noise)
    except np.linalg.LinAlgError:
        return -np.inf
    alpha = np.linalg.solve(L.T, np.linalg.solve(L, y))
    log_det = 2.0 * np.sum(np.log(np.diag(L)))
    return float(-0.5 * y @ alpha - 0.5 * log_det
                  - 0.5 * n * math.log(2 * math.pi))


def fit_gp(
    X: Sequence[float],
    y: Sequence[float],
    *,
    kernel: str = "rbf",
    length_scale_grid: Optional[Sequence[float]] = None,
    variance_grid: Optional[Sequence[float]] = None,
    noise_grid: Optional[Sequence[float]] = None,
    period: Optional[float] = None,
) -> Dict[str, Any]:
    """Fit a 1-D GP by grid search over hyperparameters.

    Returns the best hyperparameters + LML at optimum.
    """
    if np is None:
        raise RuntimeError("numpy required")
    if kernel not in _KERNEL_MAP:
        return {"available": False,
                "skipped_reason": f"unknown_kernel_{kernel}"}
    Xa = np.asarray(X, dtype=float)
    ya = np.asarray(y, dtype=float)
    mask = ~(np.isnan(Xa) | np.isnan(ya))
    Xa = Xa[mask]; ya = ya[mask]
    n = len(Xa)
    if n < 5:
        return {"available": False, "skipped_reason": "fewer_than_5_obs"}
    y_mean = float(ya.mean())
    y_centred = ya - y_mean
    span = float(Xa.max() - Xa.min())
    if length_scale_grid is None:
        length_scale_grid = np.geomspace(span / 50, span, 8)
    if variance_grid is None:
        variance_grid = np.geomspace(0.1 * float(np.var(ya)) + 1e-3,
                                        10 * float(np.var(ya)) + 1e-3, 4)
    if noise_grid is None:
        noise_grid = np.geomspace(1e-4, float(np.var(ya)), 4)
    best_lml = -np.inf
    best_params = None
    for ls in length_scale_grid:
        for var in variance_grid:
            for ns in noise_grid:
                if kernel == "periodic":
                    if period is None:
                        per = span / 5.0
                    else:
                        per = period
                    K = _periodic_kernel(Xa, Xa, ls, var, per)
                else:
                    K = _KERNEL_MAP[kernel](Xa, Xa, ls, var)
                lml = _log_marginal_likelihood(K, y_centred, ns)
                if lml > best_lml:
                    best_lml = lml
                    best_params = {"length_scale": float(ls),
                                    "variance": float(var),
                                    "noise": float(ns),
                                    "period": float(period) if period
                                                else (span / 5.0
                                                       if kernel == "periodic"
                                                       else None)}
    if best_params is None:
        return {"available": False, "skipped_reason": "lml_search_failed"}
    return {
        "available": True,
        "kernel": kernel,
        "hyperparameters": best_params,
        "log_marginal_likelihood": float(best_lml),
        "n_obs": n,
        "y_mean": y_mean,
        "X_train": Xa.tolist(),
        "y_train": ya.tolist(),
        "method": f"gp_{kernel}_grid_lml",
    }


def predict_gp(
    fit: Dict[str, Any],
    X_new: Sequence[float],
    *,
    ci_level: float = 0.95,
) -> Dict[str, Any]:
    """Posterior mean + credible interval at new inputs."""
    if np is None:
        raise RuntimeError("numpy required")
    if not fit.get("available"):
        return {"available": False,
                "skipped_reason": "fit_unavailable"}
    Xa = np.asarray(fit["X_train"], dtype=float)
    ya = np.asarray(fit["y_train"], dtype=float)
    Xn = np.asarray(X_new, dtype=float)
    y_mean = float(fit["y_mean"])
    h = fit["hyperparameters"]
    kernel = fit["kernel"]
    ls = h["length_scale"]; var = h["variance"]; noise = h["noise"]
    if kernel == "periodic":
        per = h.get("period")
        K = _periodic_kernel(Xa, Xa, ls, var, per)
        K_s = _periodic_kernel(Xa, Xn, ls, var, per)
        K_ss = _periodic_kernel(Xn, Xn, ls, var, per)
    else:
        K = _KERNEL_MAP[kernel](Xa, Xa, ls, var)
        K_s = _KERNEL_MAP[kernel](Xa, Xn, ls, var)
        K_ss = _KERNEL_MAP[kernel](Xn, Xn, ls, var)
    K_y = K + noise * np.eye(K.shape[0])
    try:
        L = np.linalg.cholesky(K_y)
        alpha = np.linalg.solve(L.T, np.linalg.solve(L, ya - y_mean))
    except np.linalg.LinAlgError:
        return {"available": False,
                "skipped_reason": "cholesky_failed"}
    mu = (K_s.T @ alpha) + y_mean
    v = np.linalg.solve(L, K_s)
    cov = K_ss - v.T @ v
    sigma = np.sqrt(np.maximum(np.diag(cov), 0))
    z = 1.959964 if ci_level == 0.95 else 2.575829
    ci_low = mu - z * sigma
    ci_high = mu + z * sigma
    return {
        "available": True,
        "X_new": Xn.tolist(),
        "mean": mu.tolist(),
        "sigma": sigma.tolist(),
        "ci_low": ci_low.tolist(),
        "ci_high": ci_high.tolist(),
        "ci_level": ci_level,
        "kernel": kernel,
        "method": f"gp_predict_{kernel}",
    }
