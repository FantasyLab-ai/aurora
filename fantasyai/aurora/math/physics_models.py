from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Any, Callable, Tuple, List
import numpy as np


def _rmse(y: np.ndarray, yhat: np.ndarray) -> float:
    y = np.asarray(y, dtype=float)
    yhat = np.asarray(yhat, dtype=float)
    m = np.isfinite(y) & np.isfinite(yhat)
    if m.mean() < 0.5:
        return float("inf")
    return float(np.sqrt(np.mean((y[m] - yhat[m]) ** 2)))


def _aic(n: int, k: int, sse: float) -> float:
    # basic Gaussian AIC proxy
    n = max(1, int(n))
    sse = max(1e-12, float(sse))
    return float(n * np.log(sse / n) + 2 * k)


def _fit_linear_ode(t: np.ndarray, y: np.ndarray) -> Dict[str, Any]:
    # dy/dt = a*y + b
    y = np.asarray(y, dtype=float)
    t = np.asarray(t, dtype=float)
    dy = np.gradient(y, t if np.unique(t).size > 2 else None)

    X = np.column_stack([y, np.ones_like(y)])
    m = np.isfinite(dy) & np.isfinite(X).all(axis=1)
    X2 = X[m]
    dy2 = dy[m]
    if X2.shape[0] < 50:
        return {"ok": False, "error": "not_enough_rows"}

    beta, *_ = np.linalg.lstsq(X2, dy2, rcond=None)
    a = float(beta[0]); b = float(beta[1])
    pred_dy = X2 @ beta
    resid = dy2 - pred_dy
    sse = float(np.sum(resid**2))

    # predict y one-step Euler for scoring
    dt = float(np.median(np.diff(t))) if np.unique(t).size > 2 else 1.0
    yhat = [y[0]]
    for i in range(1, len(y)):
        yhat.append(yhat[-1] + dt * (a * yhat[-1] + b))
    yhat = np.asarray(yhat, dtype=float)

    return {
        "ok": True,
        "name": "linear_ode",
        "model": "dy/dt = a*y + b",
        "params": {"a": a, "b": b, "dt": dt},
        "yhat": yhat,
        "rmse": _rmse(y, yhat),
        "aic": _aic(len(y), 2, sse),
        "k": 2,
    }


def _fit_logistic(t: np.ndarray, y: np.ndarray) -> Dict[str, Any]:
    # dy/dt = r*y*(1 - y/K)
    # quick-and-robust grid fit (no scipy dependency)
    y = np.asarray(y, dtype=float)
    t = np.asarray(t, dtype=float)
    dy = np.gradient(y, t if np.unique(t).size > 2 else None)

    m = np.isfinite(y) & np.isfinite(dy)
    y2 = y[m]; dy2 = dy[m]
    if y2.size < 80:
        return {"ok": False, "error": "not_enough_rows"}

    # reasonable K candidates around upper quantiles
    y_pos = y2[np.isfinite(y2)]
    if y_pos.size < 80:
        return {"ok": False, "error": "bad_y"}
    q90 = float(np.nanpercentile(y_pos, 90))
    q99 = float(np.nanpercentile(y_pos, 99))
    K_grid = np.linspace(max(1e-6, q90), max(q90 + 1e-6, q99 * 1.5), 12)

    best = None
    for K in K_grid:
        f = y2 * (1.0 - (y2 / (K + 1e-9)))
        # fit dy = r*f
        denom = float(np.sum(f*f) + 1e-12)
        r = float(np.sum(dy2 * f) / denom)
        pred_dy = r * f
        resid = dy2 - pred_dy
        sse = float(np.sum(resid**2))
        aic = _aic(int(y2.size), 2, sse)

        if best is None or aic < best["aic"]:
            best = {"K": float(K), "r": float(r), "aic": aic}

    if best is None:
        return {"ok": False, "error": "no_fit"}

    # simulate forward (Euler)
    dt = float(np.median(np.diff(t))) if np.unique(t).size > 2 else 1.0
    K = best["K"]; r = best["r"]
    yhat = [y[0]]
    for i in range(1, len(y)):
        y_prev = yhat[-1]
        yhat.append(y_prev + dt * (r * y_prev * (1.0 - y_prev / (K + 1e-9))))
    yhat = np.asarray(yhat, dtype=float)

    return {
        "ok": True,
        "name": "logistic",
        "model": "dy/dt = r*y*(1-y/K)",
        "params": {"r": r, "K": K, "dt": dt},
        "yhat": yhat,
        "rmse": _rmse(y, yhat),
        "aic": float(best["aic"]),
        "k": 2,
    }


def _fit_damped_oscillator(t: np.ndarray, y: np.ndarray) -> Dict[str, Any]:
    # y'' + c*y' + k*y = 0 (simple linear oscillator proxy)
    y = np.asarray(y, dtype=float)
    t = np.asarray(t, dtype=float)
    dt = float(np.median(np.diff(t))) if np.unique(t).size > 2 else 1.0

    y1 = np.gradient(y, dt)
    y2 = np.gradient(y1, dt)

    # Fit y2 = -c*y1 - k*y
    X = np.column_stack([y1, y, np.ones_like(y)])
    m = np.isfinite(y2) & np.isfinite(X).all(axis=1)
    X2 = X[m]; y2_ = y2[m]
    if X2.shape[0] < 80:
        return {"ok": False, "error": "not_enough_rows"}

    beta, *_ = np.linalg.lstsq(X2, y2_, rcond=None)
    c = float(-beta[0])
    k = float(-beta[1])

    pred = X2 @ beta
    resid = y2_ - pred
    sse = float(np.sum(resid**2))
    aic = _aic(X2.shape[0], 2, sse)

    # crude simulate using Euler for (y, y')
    yhat = [y[0]]
    v = 0.0
    for i in range(1, len(y)):
        a = -c * v - k * yhat[-1]
        v = v + dt * a
        yhat.append(yhat[-1] + dt * v)
    yhat = np.asarray(yhat, dtype=float)

    return {
        "ok": True,
        "name": "damped_oscillator",
        "model": "y'' + c*y' + k*y = 0",
        "params": {"c": c, "k": k, "dt": dt},
        "yhat": yhat,
        "rmse": _rmse(y, yhat),
        "aic": float(aic),
        "k": 2,
    }


def candidate_models() -> List[Callable[[np.ndarray, np.ndarray], Dict[str, Any]]]:
    return [_fit_linear_ode, _fit_logistic, _fit_damped_oscillator]
