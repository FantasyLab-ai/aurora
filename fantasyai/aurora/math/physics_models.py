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


# ============================================================
# Algebraic / static candidates — added v2.0
# These don't solve ODEs forward; they fit closed-form algebraic
# relationships of y(t). Mathematically simpler than the ODE candidates
# but cover shapes the ODE candidates can't (e.g. Stefan-Boltzmann's T⁴).
# ============================================================

def _fit_power_law(t: np.ndarray, y: np.ndarray) -> Dict[str, Any]:
    """y = a · t^b — recovers Stefan-Boltzmann (b≈4), Kepler's 3rd (b≈3/2),
    allometry (b≈3/4). Requires t > 0 and y > 0 (works on positive series)."""
    import math
    t = np.asarray(t, dtype=float)
    y = np.asarray(y, dtype=float)
    # Shift t so it's positive (use rank-based time if t starts at 0).
    t_eff = t.copy()
    if t_eff.min() <= 0:
        t_eff = t_eff - t_eff.min() + 1.0
    mask = (t_eff > 0) & (y > 0) & np.isfinite(t_eff) & np.isfinite(y)
    if mask.sum() < 30:
        return {"ok": False, "error": "not_enough_positive_rows"}
    log_t = np.log(t_eff[mask])
    log_y = np.log(y[mask])
    try:
        b, log_a = np.polyfit(log_t, log_y, 1)
        a = math.exp(log_a)
    except Exception as e:
        return {"ok": False, "error": f"polyfit_failed: {e}"}
    yhat = a * np.power(t_eff, b)
    yhat = np.where(np.isfinite(yhat), yhat, np.nan)
    sse = float(np.nansum((y - yhat) ** 2))
    return {
        "ok": True,
        "name": "power_law",
        "model": "y = a · t^b",
        "params": {"a": float(a), "b": float(b)},
        "yhat": yhat,
        "rmse": _rmse(y, yhat),
        "aic": _aic(int(mask.sum()), 2, sse),
        "k": 2,
    }


def _fit_exponential_static(t: np.ndarray, y: np.ndarray) -> Dict[str, Any]:
    """y = a · exp(b·t) + c — closed-form exponential with explicit asymptote.

    Distinct from linear_ode: linear_ode integrates dy/dt = a*y + b numerically.
    This one is the algebraic solution. Fitted via scipy.curve_fit when available;
    falls back to least-squares on log(y - c) when not."""
    t = np.asarray(t, dtype=float)
    y = np.asarray(y, dtype=float)
    mask = np.isfinite(t) & np.isfinite(y)
    if mask.sum() < 30:
        return {"ok": False, "error": "not_enough_rows"}
    t_use = t[mask]
    y_use = y[mask]
    span = float(t_use.max() - t_use.min()) or 1.0
    t_norm = (t_use - t_use.min()) / span
    try:
        from scipy.optimize import curve_fit
        c0 = float(np.median(y_use))
        a0 = float(np.max(y_use) - c0) or 1.0
        popt, _ = curve_fit(
            lambda x, a, b, c: a * np.exp(b * x) + c,
            t_norm, y_use, p0=[a0, 0.1, c0], maxfev=2000,
        )
        a_, b_, c_ = (float(v) for v in popt)
    except Exception:
        # Fall back: assume c = min(y), fit log(y-c) linearly.
        c_ = float(np.min(y_use)) - 0.01 * float(np.std(y_use) or 1.0)
        diff = y_use - c_
        if (diff > 0).sum() < 20:
            return {"ok": False, "error": "exponential_fit_failed"}
        log_diff = np.log(diff[diff > 0])
        t_for_log = t_norm[diff > 0]
        try:
            b_, log_a_ = np.polyfit(t_for_log, log_diff, 1)
            import math as _m
            a_ = _m.exp(log_a_)
        except Exception as e:
            return {"ok": False, "error": f"fallback_exponential_failed: {e}"}
        b_, a_ = float(b_), float(a_)
    t_norm_full = (t - t_use.min()) / span
    yhat = a_ * np.exp(b_ * t_norm_full) + c_
    sse = float(np.nansum((y - yhat) ** 2))
    return {
        "ok": True,
        "name": "exponential",
        "model": "y = a · exp(b·t) + c",
        "params": {"a": float(a_), "b": float(b_), "c": float(c_),
                   "t_span": span, "t_min": float(t_use.min())},
        "yhat": yhat,
        "rmse": _rmse(y, yhat),
        "aic": _aic(int(mask.sum()), 3, sse),
        "k": 3,
    }


def _fit_sigmoid(t: np.ndarray, y: np.ndarray) -> Dict[str, Any]:
    """y = L / (1 + exp(-k·(t - t₀))) — algebraic sigmoid.

    Distinct from logistic ODE: logistic integrates dy/dt = r·y·(1-y/K).
    This is the closed-form solution. Used for adoption curves, dose-response,
    saturation kinetics."""
    t = np.asarray(t, dtype=float)
    y = np.asarray(y, dtype=float)
    mask = np.isfinite(t) & np.isfinite(y)
    if mask.sum() < 30:
        return {"ok": False, "error": "not_enough_rows"}
    try:
        from scipy.optimize import curve_fit
    except Exception:
        return {"ok": False, "error": "scipy_not_available"}
    t_use, y_use = t[mask], y[mask]
    L0 = float(np.max(y_use)) or 1.0
    t0 = float(np.median(t_use))
    k0 = 1.0 / (float(np.std(t_use)) or 1.0)
    try:
        popt, _ = curve_fit(
            lambda x, L, k, x0: L / (1.0 + np.exp(-k * (x - x0))),
            t_use, y_use, p0=[L0, k0, t0], maxfev=2000,
        )
        L_, k_, t0_ = (float(v) for v in popt)
    except Exception as e:
        return {"ok": False, "error": f"sigmoid_curve_fit_failed: {e!s}"}
    yhat = L_ / (1.0 + np.exp(-k_ * (t - t0_)))
    sse = float(np.nansum((y - yhat) ** 2))
    return {
        "ok": True,
        "name": "sigmoid",
        "model": "y = L / (1 + exp(-k·(t - t₀)))",
        "params": {"L": L_, "k": k_, "t0": t0_},
        "yhat": yhat,
        "rmse": _rmse(y, yhat),
        "aic": _aic(int(mask.sum()), 3, sse),
        "k": 3,
    }


def _fit_polynomial(t: np.ndarray, y: np.ndarray) -> Dict[str, Any]:
    """y = a·t² + b·t + c — quadratic polynomial. Catches deceleration /
    acceleration patterns the ODE candidates miss (e.g. ballistic motion h = ½gt²).

    For richer use cases we'd also offer cubic / quartic; v1 is just degree 2.
    """
    t = np.asarray(t, dtype=float)
    y = np.asarray(y, dtype=float)
    mask = np.isfinite(t) & np.isfinite(y)
    if mask.sum() < 30:
        return {"ok": False, "error": "not_enough_rows"}
    try:
        a, b, c = np.polyfit(t[mask], y[mask], 2)
    except Exception as e:
        return {"ok": False, "error": f"polyfit_failed: {e}"}
    yhat = a * t ** 2 + b * t + c
    sse = float(np.nansum((y - yhat) ** 2))
    return {
        "ok": True,
        "name": "polynomial_2",
        "model": "y = a·t² + b·t + c",
        "params": {"a": float(a), "b": float(b), "c": float(c)},
        "yhat": yhat,
        "rmse": _rmse(y, yhat),
        "aic": _aic(int(mask.sum()), 3, sse),
        "k": 3,
    }


def _fit_saturation_static(t: np.ndarray, y: np.ndarray) -> Dict[str, Any]:
    """y = Vmax · t / (K + t) — Michaelis-Menten / Hill (n=1) saturation.

    Hyperbolic approach to a maximum. Used for enzyme kinetics, dose-response,
    ad-spend ROI curves, and any "diminishing returns" pattern."""
    t = np.asarray(t, dtype=float)
    y = np.asarray(y, dtype=float)
    # Shift t to be non-negative (Michaelis-Menten requires t ≥ 0).
    t_eff = t.copy()
    if t_eff.min() < 0:
        t_eff = t_eff - t_eff.min()
    mask = np.isfinite(t_eff) & np.isfinite(y) & (t_eff >= 0) & (y >= 0)
    if mask.sum() < 30:
        return {"ok": False, "error": "not_enough_nonnegative_rows"}
    try:
        from scipy.optimize import curve_fit
    except Exception:
        return {"ok": False, "error": "scipy_not_available"}
    Vmax0 = float(np.max(y[mask])) or 1.0
    K0 = float(np.median(t_eff[mask])) or 1.0
    try:
        popt, _ = curve_fit(
            lambda x, V, K: V * x / (K + x),
            t_eff[mask], y[mask],
            p0=[Vmax0, K0],
            bounds=([0, 1e-9], [np.inf, np.inf]),
            maxfev=2000,
        )
        V_, K_ = (float(v) for v in popt)
    except Exception as e:
        return {"ok": False, "error": f"saturation_curve_fit_failed: {e!s}"}
    yhat = V_ * t_eff / (K_ + t_eff)
    sse = float(np.nansum((y - yhat) ** 2))
    return {
        "ok": True,
        "name": "saturation",
        "model": "y = Vmax · t / (K + t)",
        "params": {"Vmax": V_, "K": K_},
        "yhat": yhat,
        "rmse": _rmse(y, yhat),
        "aic": _aic(int(mask.sum()), 2, sse),
        "k": 2,
    }


def candidate_models() -> List[Callable[[np.ndarray, np.ndarray], Dict[str, Any]]]:
    """Return all 8 candidate physics models tried by physics_discovery.

    ODE candidates (integrated forward via Euler):
      * linear_ode             dy/dt = a·y + b
      * logistic               dy/dt = r·y·(1 - y/K)
      * damped_oscillator      y'' + c·y' + k·y = 0

    Algebraic / closed-form candidates (added v2.0):
      * power_law              y = a · t^b           (Stefan-Boltzmann, allometry)
      * exponential            y = a · exp(b·t) + c  (decay / growth toward asymptote)
      * sigmoid                y = L / (1 + exp(-k(t-t₀)))  (S-curve adoption)
      * polynomial_2           y = a·t² + b·t + c    (ballistic, deceleration)
      * saturation             y = Vmax·t / (K + t)  (Michaelis-Menten)
    """
    return [
        # ODE candidates
        _fit_linear_ode,
        _fit_logistic,
        _fit_damped_oscillator,
        # Algebraic candidates (v2.0)
        _fit_power_law,
        _fit_exponential_static,
        _fit_sigmoid,
        _fit_polynomial,
        _fit_saturation_static,
    ]
