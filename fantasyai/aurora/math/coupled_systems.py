"""
Coupled multi-target ODE systems — Lotka-Volterra, SIR, and friends.

Until now, ``physics_discovery`` only fit *single*-target dynamics y(t).
But many real systems are intrinsically coupled: predator/prey, susceptible/
infectious/recovered, two competing species, supply/demand, etc. The
governing equations are pairs (or triples) of ODEs that exchange information
between the variables.

This module fits four canonical coupled families:

* **Lotka-Volterra (predator-prey)** — classic ecology
      dx/dt =  α·x  − β·x·y
      dy/dt = −γ·y  + δ·x·y

* **Competitive Lotka-Volterra** — two species competing for the same resource
      dx/dt = r₁·x·(1 − (x + a·y)/K₁)
      dy/dt = r₂·y·(1 − (y + b·x)/K₂)

* **SIR epidemiology** — susceptible/infected/recovered
      dS/dt = −β·S·I/N
      dI/dt =  β·S·I/N − γ·I
      dR/dt =  γ·I

* **Linear coupled 2D** — generic baseline; recovers things like rotation,
  damped oscillation in phase space, two-tank models
      dx/dt = a₁₁·x + a₁₂·y
      dy/dt = a₂₁·x + a₂₂·y

The fit-then-score pattern matches ``physics_models.py``: derive numerical
derivatives via central differences, fit the parameters via least-squares
on a single-step Euler residual, simulate forward to compute RMSE.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Tuple
import numpy as np


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _rmse_pair(x: np.ndarray, y: np.ndarray,
               xhat: np.ndarray, yhat: np.ndarray) -> float:
    """Combined RMSE: average of x-channel and y-channel RMSEs."""
    a = np.asarray(x, dtype=float); b = np.asarray(xhat, dtype=float)
    c = np.asarray(y, dtype=float); d = np.asarray(yhat, dtype=float)
    m1 = np.isfinite(a) & np.isfinite(b)
    m2 = np.isfinite(c) & np.isfinite(d)
    if m1.sum() < 2 or m2.sum() < 2:
        return float("inf")
    e1 = np.sqrt(np.mean((a[m1] - b[m1]) ** 2))
    e2 = np.sqrt(np.mean((c[m2] - d[m2]) ** 2))
    return float(0.5 * (e1 + e2))


def _aic_pair(n: int, k: int, sse: float) -> float:
    n = max(2, int(n)); sse = max(1e-12, float(sse))
    return float(n * np.log(sse / n) + 2 * k)


def _gradient(arr: np.ndarray, dt: float) -> np.ndarray:
    return np.gradient(np.asarray(arr, dtype=float), dt)


# ---------------------------------------------------------------------------
# 1. Lotka-Volterra (classic predator-prey)
# ---------------------------------------------------------------------------

def fit_lotka_volterra(t: np.ndarray, x: np.ndarray, y: np.ndarray) -> Dict[str, Any]:
    """Fit dx/dt = α·x − β·x·y, dy/dt = −γ·y + δ·x·y.

    α (prey growth rate), β (predation rate), γ (predator death rate),
    δ (predator efficiency). All four must be positive for a "real"
    predator-prey reading; we don't enforce that in the fit but flag it.
    """
    t = np.asarray(t, dtype=float)
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    if t.size < 50 or x.size != t.size or y.size != t.size:
        return {"ok": False, "name": "lotka_volterra", "error": "insufficient_or_mismatched_input"}
    dt = float(np.median(np.diff(t))) if t.size > 1 else 1.0
    if dt <= 0:
        dt = 1.0

    dxdt = _gradient(x, dt)
    dydt = _gradient(y, dt)

    # dx/dt = α·x − β·(x·y)  →  dxdt = [x, -x*y] · [α, β]ᵀ
    Ax = np.column_stack([x, -x * y])
    # dy/dt = -γ·y + δ·(x·y)  →  dydt = [-y, x*y] · [γ, δ]ᵀ
    Ay = np.column_stack([-y, x * y])

    mx = np.isfinite(dxdt) & np.isfinite(Ax).all(axis=1)
    my = np.isfinite(dydt) & np.isfinite(Ay).all(axis=1)
    if mx.sum() < 30 or my.sum() < 30:
        return {"ok": False, "name": "lotka_volterra", "error": "not_enough_finite_rows"}

    try:
        beta_x, *_ = np.linalg.lstsq(Ax[mx], dxdt[mx], rcond=None)
        beta_y, *_ = np.linalg.lstsq(Ay[my], dydt[my], rcond=None)
    except Exception as e:
        return {"ok": False, "name": "lotka_volterra", "error": f"lstsq_failed: {e}"}

    alpha, beta = float(beta_x[0]), float(beta_x[1])
    gamma, delta = float(beta_y[0]), float(beta_y[1])

    # Forward-simulate via Euler from initial conditions.
    n = t.size
    xhat = np.zeros(n); yhat = np.zeros(n)
    xhat[0], yhat[0] = x[0], y[0]
    for i in range(1, n):
        dx = alpha * xhat[i-1] - beta * xhat[i-1] * yhat[i-1]
        dy = -gamma * yhat[i-1] + delta * xhat[i-1] * yhat[i-1]
        xhat[i] = xhat[i-1] + dt * dx
        yhat[i] = yhat[i-1] + dt * dy
        # Guardrails: blow-up / NaN means the parameters won't simulate cleanly.
        if not (np.isfinite(xhat[i]) and np.isfinite(yhat[i])):
            xhat[i:] = np.nan
            yhat[i:] = np.nan
            break

    rmse = _rmse_pair(x, y, xhat, yhat)
    sse = float(np.nansum((x - xhat) ** 2) + np.nansum((y - yhat) ** 2))
    return {
        "ok": True,
        "name": "lotka_volterra",
        "model": "dx/dt = α·x − β·x·y; dy/dt = −γ·y + δ·x·y",
        "params": {"alpha": alpha, "beta": beta, "gamma": gamma, "delta": delta, "dt": dt},
        "xhat": xhat,
        "yhat": yhat,
        "rmse": rmse,
        "aic": _aic_pair(2 * n, 4, sse),
        "k": 4,
        "warnings": [
            "negative parameter (α<0)" if alpha < 0 else "",
            "negative parameter (β<0)" if beta < 0 else "",
            "negative parameter (γ<0)" if gamma < 0 else "",
            "negative parameter (δ<0)" if delta < 0 else "",
        ],
    }


# ---------------------------------------------------------------------------
# 2. Competitive Lotka-Volterra (two species sharing a resource)
# ---------------------------------------------------------------------------

def fit_competitive_lv(t: np.ndarray, x: np.ndarray, y: np.ndarray) -> Dict[str, Any]:
    """Fit competitive LV: dx/dt = r₁·x·(1 − (x + a·y)/K₁), similarly for y.

    Slightly heavier than predator-prey because the parameter space couples
    multiplicatively. We fit r₁/K₁ and a in one regression after substitution.
    """
    t = np.asarray(t, dtype=float); x = np.asarray(x, dtype=float); y = np.asarray(y, dtype=float)
    if t.size < 50:
        return {"ok": False, "name": "competitive_lv", "error": "insufficient_input"}
    dt = float(np.median(np.diff(t))) or 1.0
    dxdt = _gradient(x, dt); dydt = _gradient(y, dt)

    # dx/dt = r₁·x − (r₁/K₁)·x² − (r₁·a/K₁)·x·y → linear in [x, x², x·y]
    Ax = np.column_stack([x, x * x, x * y])
    Ay = np.column_stack([y, y * y, x * y])
    mx = np.isfinite(dxdt) & np.isfinite(Ax).all(axis=1)
    my = np.isfinite(dydt) & np.isfinite(Ay).all(axis=1)
    if mx.sum() < 30 or my.sum() < 30:
        return {"ok": False, "name": "competitive_lv", "error": "not_enough_finite_rows"}

    try:
        bx, *_ = np.linalg.lstsq(Ax[mx], dxdt[mx], rcond=None)
        by, *_ = np.linalg.lstsq(Ay[my], dydt[my], rcond=None)
    except Exception as e:
        return {"ok": False, "name": "competitive_lv", "error": f"lstsq_failed: {e}"}

    r1 = float(bx[0])
    K1 = float(-r1 / bx[1]) if bx[1] != 0 else float("inf")
    a  = float((-bx[2] / bx[1]) if bx[1] != 0 else 0.0)
    r2 = float(by[0])
    K2 = float(-r2 / by[1]) if by[1] != 0 else float("inf")
    b  = float((-by[2] / by[1]) if by[1] != 0 else 0.0)

    # Simulate forward.
    n = t.size
    xhat = np.zeros(n); yhat = np.zeros(n)
    xhat[0], yhat[0] = x[0], y[0]
    for i in range(1, n):
        dx_ = r1 * xhat[i-1] * (1 - (xhat[i-1] + a * yhat[i-1]) / max(K1, 1e-9))
        dy_ = r2 * yhat[i-1] * (1 - (yhat[i-1] + b * xhat[i-1]) / max(K2, 1e-9))
        xhat[i] = xhat[i-1] + dt * dx_
        yhat[i] = yhat[i-1] + dt * dy_
        if not (np.isfinite(xhat[i]) and np.isfinite(yhat[i])):
            xhat[i:] = np.nan; yhat[i:] = np.nan; break

    rmse = _rmse_pair(x, y, xhat, yhat)
    sse = float(np.nansum((x - xhat) ** 2) + np.nansum((y - yhat) ** 2))
    return {
        "ok": True,
        "name": "competitive_lv",
        "model": "dx/dt = r₁·x·(1 − (x + a·y)/K₁); dy/dt = r₂·y·(1 − (y + b·x)/K₂)",
        "params": {"r1": r1, "K1": K1, "a": a, "r2": r2, "K2": K2, "b": b, "dt": dt},
        "xhat": xhat, "yhat": yhat,
        "rmse": rmse,
        "aic": _aic_pair(2 * n, 6, sse),
        "k": 6,
    }


# ---------------------------------------------------------------------------
# 3. SIR epidemiology
# ---------------------------------------------------------------------------

def fit_sir(t: np.ndarray, S: np.ndarray, I: np.ndarray,
            R: np.ndarray = None, N: float = None) -> Dict[str, Any]:
    """Fit dS/dt = −β·S·I/N, dI/dt = β·S·I/N − γ·I.

    R is recovered (if not provided, we compute R = N − S − I assuming
    constant population). N is the total population (defaults to S[0]+I[0]).
    """
    t = np.asarray(t, dtype=float)
    S = np.asarray(S, dtype=float); I = np.asarray(I, dtype=float)
    if t.size < 50:
        return {"ok": False, "name": "sir", "error": "insufficient_input"}
    if N is None:
        N = float(S[0] + I[0] + (R[0] if R is not None else 0.0))
    if N <= 0 or not np.isfinite(N):
        return {"ok": False, "name": "sir", "error": "invalid_population"}

    dt = float(np.median(np.diff(t))) or 1.0
    dSdt = _gradient(S, dt); dIdt = _gradient(I, dt)

    # dS/dt = −β·(S·I/N)  → 1-parameter regression
    SI_over_N = S * I / N
    mS = np.isfinite(dSdt) & np.isfinite(SI_over_N)
    if mS.sum() < 30:
        return {"ok": False, "name": "sir", "error": "not_enough_finite_rows"}

    # Linear fit: dSdt = -β · (S·I/N)
    denom = float(np.sum(SI_over_N[mS] ** 2)) + 1e-12
    beta = float(-np.sum(dSdt[mS] * SI_over_N[mS]) / denom)
    # Linear fit: dIdt = β·(S·I/N) − γ·I = a·(S·I/N) + b·I, with a=β fixed.
    # Solve for γ via least-squares on residual.
    resid = dIdt - beta * SI_over_N
    mI = np.isfinite(resid) & np.isfinite(I)
    if mI.sum() < 30:
        return {"ok": False, "name": "sir", "error": "not_enough_finite_rows_I"}
    denom2 = float(np.sum(I[mI] ** 2)) + 1e-12
    gamma = float(-np.sum(resid[mI] * I[mI]) / denom2)

    # Simulate forward.
    n = t.size
    Shat = np.zeros(n); Ihat = np.zeros(n); Rhat = np.zeros(n)
    Shat[0], Ihat[0] = S[0], I[0]
    Rhat[0] = (R[0] if R is not None else max(0.0, N - S[0] - I[0]))
    for i in range(1, n):
        si = Shat[i-1] * Ihat[i-1] / max(N, 1e-9)
        Shat[i] = Shat[i-1] + dt * (-beta * si)
        Ihat[i] = Ihat[i-1] + dt * (beta * si - gamma * Ihat[i-1])
        Rhat[i] = Rhat[i-1] + dt * (gamma * Ihat[i-1])
        if not (np.isfinite(Shat[i]) and np.isfinite(Ihat[i])):
            Shat[i:] = np.nan; Ihat[i:] = np.nan; Rhat[i:] = np.nan; break

    rmse = _rmse_pair(S, I, Shat, Ihat)
    sse = float(np.nansum((S - Shat) ** 2) + np.nansum((I - Ihat) ** 2))
    R0 = float(beta / gamma) if gamma != 0 else float("inf")
    return {
        "ok": True,
        "name": "sir",
        "model": "dS/dt = −β·S·I/N; dI/dt = β·S·I/N − γ·I",
        "params": {"beta": beta, "gamma": gamma, "N": N, "R0": R0, "dt": dt},
        "xhat": Shat, "yhat": Ihat, "Rhat": Rhat,
        "rmse": rmse,
        "aic": _aic_pair(2 * n, 2, sse),
        "k": 2,
    }


# ---------------------------------------------------------------------------
# 4. Linear 2D coupled — generic baseline
# ---------------------------------------------------------------------------

def fit_linear_coupled(t: np.ndarray, x: np.ndarray, y: np.ndarray) -> Dict[str, Any]:
    """Fit dx/dt = a₁₁·x + a₁₂·y, dy/dt = a₂₁·x + a₂₂·y.

    The simplest non-trivial coupled system. Recovers rotation, damped
    oscillation in phase space, two-tank diffusion. 4 parameters.
    """
    t = np.asarray(t, dtype=float); x = np.asarray(x, dtype=float); y = np.asarray(y, dtype=float)
    if t.size < 50:
        return {"ok": False, "name": "linear_coupled_2d", "error": "insufficient_input"}
    dt = float(np.median(np.diff(t))) or 1.0
    dxdt = _gradient(x, dt); dydt = _gradient(y, dt)
    A = np.column_stack([x, y])
    mx = np.isfinite(dxdt) & np.isfinite(A).all(axis=1)
    my = np.isfinite(dydt) & np.isfinite(A).all(axis=1)
    if mx.sum() < 30 or my.sum() < 30:
        return {"ok": False, "name": "linear_coupled_2d", "error": "not_enough_finite_rows"}
    try:
        bx, *_ = np.linalg.lstsq(A[mx], dxdt[mx], rcond=None)
        by, *_ = np.linalg.lstsq(A[my], dydt[my], rcond=None)
    except Exception as e:
        return {"ok": False, "name": "linear_coupled_2d", "error": f"lstsq_failed: {e}"}

    a11, a12 = float(bx[0]), float(bx[1])
    a21, a22 = float(by[0]), float(by[1])
    n = t.size
    xhat = np.zeros(n); yhat = np.zeros(n)
    xhat[0], yhat[0] = x[0], y[0]
    for i in range(1, n):
        xhat[i] = xhat[i-1] + dt * (a11 * xhat[i-1] + a12 * yhat[i-1])
        yhat[i] = yhat[i-1] + dt * (a21 * xhat[i-1] + a22 * yhat[i-1])
        if not (np.isfinite(xhat[i]) and np.isfinite(yhat[i])):
            xhat[i:] = np.nan; yhat[i:] = np.nan; break

    rmse = _rmse_pair(x, y, xhat, yhat)
    sse = float(np.nansum((x - xhat) ** 2) + np.nansum((y - yhat) ** 2))
    # Eigenvalues — useful for diagnosing rotation vs decay.
    M = np.array([[a11, a12], [a21, a22]])
    try:
        eigs = np.linalg.eigvals(M)
        eigs_str = [str(complex(e)) for e in eigs]
    except Exception:
        eigs_str = []
    return {
        "ok": True,
        "name": "linear_coupled_2d",
        "model": "dx/dt = a₁₁·x + a₁₂·y; dy/dt = a₂₁·x + a₂₂·y",
        "params": {"a11": a11, "a12": a12, "a21": a21, "a22": a22, "dt": dt,
                   "eigenvalues": eigs_str},
        "xhat": xhat, "yhat": yhat,
        "rmse": rmse,
        "aic": _aic_pair(2 * n, 4, sse),
        "k": 4,
    }


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

CoupledFitter = Callable[..., Dict[str, Any]]


def candidate_coupled_models() -> List[CoupledFitter]:
    """Return all coupled-system fitters tried by ``discover_coupled``."""
    return [fit_lotka_volterra, fit_competitive_lv, fit_linear_coupled, fit_sir]


def discover_coupled(t: np.ndarray, x: np.ndarray, y: np.ndarray,
                     R: np.ndarray = None) -> Dict[str, Any]:
    """Try every coupled candidate and return the best by RMSE / AIC.

    Args:
        t: time axis
        x, y: the two coupled series (e.g. prey, predator)
        R: optional third series (for SIR's R compartment)

    Returns:
        {"best": {...}, "runner_ups": [...], "all_candidates": [...]}
    """
    results: List[Dict[str, Any]] = []
    for fn in candidate_coupled_models():
        try:
            if fn is fit_sir:
                r = fn(t, x, y, R=R) if R is not None else fn(t, x, y)
            else:
                r = fn(t, x, y)
        except Exception as e:
            r = {"ok": False, "name": getattr(fn, "__name__", "unknown"),
                 "error": f"crashed: {e!s}"}
        results.append(r)
    valid = [r for r in results if r.get("ok") and np.isfinite(r.get("rmse", np.nan))]
    if not valid:
        return {"note": "error", "best": None, "runner_ups": [], "all_candidates": results}
    valid.sort(key=lambda r: (r["rmse"], r.get("aic", float("inf"))))
    return {
        "note": "ok",
        "best": valid[0],
        "runner_ups": valid[1:3],
        "all_candidates": results,
    }
