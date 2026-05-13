"""
PDE detection — recover heat, wave, and advection equations from gridded data.

Aurora's earlier physics layer fits *ordinary* differential equations: y(t).
But many real systems are governed by **partial** differential equations:
u(x, t), where the field depends on both space and time.

Three canonical PDE families are detected here:

* **Heat / diffusion**:   ∂u/∂t = α · ∂²u/∂x²
    α is the thermal diffusivity (temperature spreading), or in chemistry the
    diffusion coefficient. Recovers heat conduction, ink-in-water, neutron
    transport.

* **Linear advection**:   ∂u/∂t + c · ∂u/∂x = 0
    Pure transport at speed c — the field translates without changing shape.
    Recovers contamination plumes in moving fluids, traffic flow models.

* **Wave equation**:      ∂²u/∂t² = c² · ∂²u/∂x²
    String, drum, electromagnetic wave, sound. c is the wave speed.

Input format
------------

The fitters take (t, x, U) where:

* ``t`` is a 1D array of time samples, shape (n_t,)
* ``x`` is a 1D array of spatial positions, shape (n_x,)
* ``U`` is a 2D array of field values, shape (n_t, n_x), so U[i, j] is the
  field at time ``t[i]`` and position ``x[j]``.

The DataFrame helper ``detect_pde_grid_in_dataframe`` heuristically extracts
this layout from common CSV shapes (wide: row=time, col=spatial position;
long: separate t/x/u columns).

Approach
--------

For each candidate PDE we compute the relevant derivatives by central
differences, then fit the single coefficient (α or c or c²) by
least-squares on the residual. Score by combined RMSE on the residual
across the full grid.

This is deterministic and pure-numpy; no scipy required.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple
import numpy as np


# ---------------------------------------------------------------------------
# Numerical derivatives on a 2D grid
# ---------------------------------------------------------------------------

def _du_dt(U: np.ndarray, dt: float) -> np.ndarray:
    """Central difference along axis 0 (time)."""
    return np.gradient(U, dt, axis=0)


def _du_dx(U: np.ndarray, dx: float) -> np.ndarray:
    """Central difference along axis 1 (space)."""
    return np.gradient(U, dx, axis=1)


def _d2u_dx2(U: np.ndarray, dx: float) -> np.ndarray:
    """2nd central difference along axis 1."""
    du = _du_dx(U, dx)
    return _du_dx(du, dx)


def _d2u_dt2(U: np.ndarray, dt: float) -> np.ndarray:
    du = _du_dt(U, dt)
    return _du_dt(du, dt)


def _rmse(residual: np.ndarray) -> float:
    finite = residual[np.isfinite(residual)]
    if finite.size == 0:
        return float("inf")
    return float(np.sqrt(np.mean(finite ** 2)))


# ---------------------------------------------------------------------------
# Heat equation:  ∂u/∂t = α · ∂²u/∂x²
# ---------------------------------------------------------------------------

def fit_heat_equation(t: np.ndarray, x: np.ndarray, U: np.ndarray) -> Dict[str, Any]:
    """Fit ∂u/∂t = α · ∂²u/∂x²; return α and residual RMSE."""
    t = np.asarray(t, dtype=float)
    x = np.asarray(x, dtype=float)
    U = np.asarray(U, dtype=float)
    if U.ndim != 2 or U.shape != (t.size, x.size):
        return {"ok": False, "name": "heat_equation",
                "error": f"shape mismatch: U={U.shape}, expected ({t.size},{x.size})"}
    if t.size < 4 or x.size < 4:
        return {"ok": False, "name": "heat_equation", "error": "grid_too_small"}
    dt = float(np.median(np.diff(t))) or 1.0
    dx = float(np.median(np.diff(x))) or 1.0

    ut = _du_dt(U, dt)        # shape (n_t, n_x)
    uxx = _d2u_dx2(U, dx)     # shape (n_t, n_x)

    # Trim 2-cell border on each axis to avoid edge artefacts.
    inner = (slice(2, -2), slice(2, -2))
    ut_i = ut[inner].ravel()
    uxx_i = uxx[inner].ravel()
    mask = np.isfinite(ut_i) & np.isfinite(uxx_i)
    if mask.sum() < 30:
        return {"ok": False, "name": "heat_equation", "error": "not_enough_inner_points"}

    # Single-parameter fit: ut = α · uxx
    denom = float(np.sum(uxx_i[mask] ** 2)) + 1e-12
    alpha = float(np.sum(ut_i[mask] * uxx_i[mask]) / denom)
    residual = ut_i[mask] - alpha * uxx_i[mask]

    # R² on the predicted ∂u/∂t.
    ss_res = float(np.sum(residual ** 2))
    ss_tot = float(np.sum((ut_i[mask] - np.mean(ut_i[mask])) ** 2))
    r2 = 1.0 - ss_res / max(ss_tot, 1e-12)

    return {
        "ok": True,
        "name": "heat_equation",
        "model": "∂u/∂t = α · ∂²u/∂x²",
        "params": {"alpha": alpha, "dt": dt, "dx": dx},
        "rmse": _rmse(residual),
        "r2": float(r2),
        "k": 1,
        "inner_points": int(mask.sum()),
    }


# ---------------------------------------------------------------------------
# Linear advection:  ∂u/∂t + c · ∂u/∂x = 0
# ---------------------------------------------------------------------------

def fit_advection_equation(t: np.ndarray, x: np.ndarray, U: np.ndarray) -> Dict[str, Any]:
    """Fit ∂u/∂t + c · ∂u/∂x = 0; return c."""
    t = np.asarray(t, dtype=float); x = np.asarray(x, dtype=float)
    U = np.asarray(U, dtype=float)
    if U.ndim != 2 or U.shape != (t.size, x.size):
        return {"ok": False, "name": "advection_equation",
                "error": f"shape mismatch: U={U.shape}"}
    if t.size < 4 or x.size < 4:
        return {"ok": False, "name": "advection_equation", "error": "grid_too_small"}
    dt = float(np.median(np.diff(t))) or 1.0
    dx = float(np.median(np.diff(x))) or 1.0

    ut = _du_dt(U, dt)
    ux = _du_dx(U, dx)
    inner = (slice(2, -2), slice(2, -2))
    ut_i = ut[inner].ravel()
    ux_i = ux[inner].ravel()
    mask = np.isfinite(ut_i) & np.isfinite(ux_i)
    if mask.sum() < 30:
        return {"ok": False, "name": "advection_equation", "error": "not_enough_inner_points"}

    # ut = -c · ux  →  c = -<ut, ux> / <ux, ux>
    denom = float(np.sum(ux_i[mask] ** 2)) + 1e-12
    c = float(-np.sum(ut_i[mask] * ux_i[mask]) / denom)
    residual = ut_i[mask] + c * ux_i[mask]
    ss_res = float(np.sum(residual ** 2))
    ss_tot = float(np.sum((ut_i[mask] - np.mean(ut_i[mask])) ** 2))
    r2 = 1.0 - ss_res / max(ss_tot, 1e-12)
    return {
        "ok": True,
        "name": "advection_equation",
        "model": "∂u/∂t + c · ∂u/∂x = 0",
        "params": {"c": c, "dt": dt, "dx": dx},
        "rmse": _rmse(residual),
        "r2": float(r2),
        "k": 1,
        "inner_points": int(mask.sum()),
    }


# ---------------------------------------------------------------------------
# Wave equation:  ∂²u/∂t² = c² · ∂²u/∂x²
# ---------------------------------------------------------------------------

def fit_wave_equation(t: np.ndarray, x: np.ndarray, U: np.ndarray) -> Dict[str, Any]:
    """Fit ∂²u/∂t² = c² · ∂²u/∂x²; return c (positive root)."""
    t = np.asarray(t, dtype=float); x = np.asarray(x, dtype=float)
    U = np.asarray(U, dtype=float)
    if U.ndim != 2 or U.shape != (t.size, x.size):
        return {"ok": False, "name": "wave_equation",
                "error": f"shape mismatch: U={U.shape}"}
    if t.size < 5 or x.size < 5:
        return {"ok": False, "name": "wave_equation", "error": "grid_too_small"}
    dt = float(np.median(np.diff(t))) or 1.0
    dx = float(np.median(np.diff(x))) or 1.0

    utt = _d2u_dt2(U, dt)
    uxx = _d2u_dx2(U, dx)
    inner = (slice(2, -2), slice(2, -2))
    utt_i = utt[inner].ravel()
    uxx_i = uxx[inner].ravel()
    mask = np.isfinite(utt_i) & np.isfinite(uxx_i)
    if mask.sum() < 30:
        return {"ok": False, "name": "wave_equation", "error": "not_enough_inner_points"}

    denom = float(np.sum(uxx_i[mask] ** 2)) + 1e-12
    c2 = float(np.sum(utt_i[mask] * uxx_i[mask]) / denom)
    c = float(np.sqrt(abs(c2)))
    sign = "+" if c2 >= 0 else "-"
    residual = utt_i[mask] - c2 * uxx_i[mask]
    ss_res = float(np.sum(residual ** 2))
    ss_tot = float(np.sum((utt_i[mask] - np.mean(utt_i[mask])) ** 2))
    r2 = 1.0 - ss_res / max(ss_tot, 1e-12)
    return {
        "ok": True,
        "name": "wave_equation",
        "model": "∂²u/∂t² = c² · ∂²u/∂x²",
        "params": {"c": c, "c2": c2, "c2_sign": sign, "dt": dt, "dx": dx},
        "rmse": _rmse(residual),
        "r2": float(r2),
        "k": 1,
        "inner_points": int(mask.sum()),
    }


# ---------------------------------------------------------------------------
# Top-level dispatcher
# ---------------------------------------------------------------------------

def discover_pde(t: np.ndarray, x: np.ndarray, U: np.ndarray) -> Dict[str, Any]:
    """Try heat, advection, and wave; return the best fit by R² (highest first).

    Returns: {best, runner_ups, all_candidates, note}
    """
    candidates = [fit_heat_equation(t, x, U),
                  fit_advection_equation(t, x, U),
                  fit_wave_equation(t, x, U)]
    valid = [c for c in candidates if c.get("ok") and np.isfinite(c.get("rmse", np.nan))]
    if not valid:
        return {"note": "error", "best": None, "runner_ups": [], "all_candidates": candidates}
    # Rank by R² (descending). Ties broken by RMSE (ascending).
    valid.sort(key=lambda c: (-c.get("r2", -1.0), c.get("rmse", float("inf"))))
    return {
        "note": "ok",
        "best": valid[0],
        "runner_ups": valid[1:],
        "all_candidates": candidates,
    }


# ---------------------------------------------------------------------------
# DataFrame → (t, x, U) extraction
# ---------------------------------------------------------------------------

def detect_pde_grid_in_dataframe(df, time_col: Optional[str] = None) -> Optional[Tuple[np.ndarray, np.ndarray, np.ndarray, List[str]]]:
    """Heuristically extract (t, x, U) from a DataFrame.

    Two layouts supported:

    1. **Wide**: rows = time samples; columns = spatial positions named like
       ``x_0.0``, ``x_0.5``, …, ``pos_10``, …, ``5.5m``, etc. We look for ≥5
       columns whose names contain a numeric token; if found, those are the
       spatial axis.

    2. **Long**: three columns (t, x, u). Detected via heuristic name matches
       (``time``/``t``, ``x``/``position``, ``u``/``value``/``field``).

    Returns (t, x, U, used_columns) on success, None when the data doesn't
    look gridded.
    """
    import re
    try:
        import pandas as pd
    except Exception:
        return None
    if df is None or len(df) < 4:
        return None

    # ---- Try wide layout ----
    spatial_re = re.compile(r"(-?\d+\.?\d*)")
    spatial_cols: List[Tuple[str, float]] = []
    for c in df.columns:
        if time_col and c == time_col:
            continue
        m = spatial_re.search(str(c))
        if not m:
            continue
        try:
            pos = float(m.group(1))
            spatial_cols.append((c, pos))
        except Exception:
            continue
    if len(spatial_cols) >= 5:
        spatial_cols.sort(key=lambda kv: kv[1])
        x = np.array([p for _, p in spatial_cols], dtype=float)
        # If positions are not strictly increasing or have duplicates, bail.
        if x.size != np.unique(x).size:
            return None
        used = [c for c, _ in spatial_cols]
        # Time axis: explicit column or row index.
        if time_col and time_col in df.columns:
            t = pd.to_numeric(df[time_col], errors="coerce").to_numpy(dtype=float)
        else:
            t = np.arange(len(df), dtype=float)
        # Build U.
        try:
            U = df[used].apply(lambda col: pd.to_numeric(col, errors="coerce")).to_numpy(dtype=float)
        except Exception:
            return None
        if U.shape != (t.size, x.size):
            return None
        if not np.isfinite(U).any():
            return None
        return t, x, U, used

    # ---- Try long layout: t, x, u columns ----
    cols_lower = {str(c).lower(): c for c in df.columns}
    t_col = next((cols_lower[k] for k in ("time", "t", "timestamp") if k in cols_lower), None)
    x_col = next((cols_lower[k] for k in ("x", "position", "location", "pos") if k in cols_lower), None)
    u_col = next((cols_lower[k] for k in ("u", "value", "field", "amplitude") if k in cols_lower), None)
    if t_col and x_col and u_col:
        try:
            wide = df.pivot_table(index=t_col, columns=x_col, values=u_col, aggfunc="mean")
            t = wide.index.to_numpy(dtype=float)
            x = wide.columns.to_numpy(dtype=float)
            U = wide.to_numpy(dtype=float)
            if t.size >= 4 and x.size >= 4:
                return t, x, U, [t_col, x_col, u_col]
        except Exception:
            pass

    return None
