from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple, Any

import numpy as np
import pandas as pd


# -----------------------------
# Utilities
# -----------------------------

def _safe_float(x) -> float:
    try:
        v = float(x)
        if math.isfinite(v):
            return v
    except Exception:
        pass
    return float("nan")


def _to_numeric_series(s: pd.Series) -> pd.Series:
    if pd.api.types.is_numeric_dtype(s):
        return s.astype(float)
    # Remove commas, spaces
    x = s.astype(str).str.replace(",", "", regex=False).str.strip()
    return pd.to_numeric(x, errors="coerce")


def _infer_time_axis_seconds(
    df: pd.DataFrame,
    time_col: Optional[str] = None,
) -> Tuple[np.ndarray, Dict[str, Any]]:
    """
    Returns (t_seconds, meta).
    If time_col missing/unusable, returns evenly spaced index seconds.
    """
    meta: Dict[str, Any] = {"time_col": time_col, "dt_seconds": None, "time_axis": None}

    if time_col and time_col in df.columns:
        s = df[time_col]
        # If already datetime-like
        if np.issubdtype(s.dtype, np.datetime64):
            dt = pd.to_datetime(s, errors="coerce")
        else:
            dt = pd.to_datetime(s, errors="coerce", infer_datetime_format=True)
        dt = dt.dropna()
        if len(dt) >= max(10, int(0.5 * len(df))):
            # align to full df index by coercing again, then compute seconds
            dt_full = pd.to_datetime(df[time_col], errors="coerce", infer_datetime_format=True)
            # forward fill only for timing; preserves order
            dt_full = dt_full.fillna(method="ffill")
            dt_full = dt_full.fillna(method="bfill")
            t0 = dt_full.iloc[0]
            t_seconds = (dt_full - t0).dt.total_seconds().astype(float).to_numpy()
            # dt estimate
            dts = np.diff(t_seconds)
            dts = dts[np.isfinite(dts)]
            if len(dts) > 0:
                dt_med = float(np.median(dts[dts > 0])) if np.any(dts > 0) else float(np.median(dts))
            else:
                dt_med = 1.0
            meta["dt_seconds"] = dt_med
            meta["time_axis"] = f"datetime:{time_col}"
            return t_seconds, meta

    # Fallback: index-based seconds
    n = len(df)
    t_seconds = np.arange(n, dtype=float)
    meta["dt_seconds"] = 1.0
    meta["time_axis"] = "fallback_index_seconds"
    return t_seconds, meta


def _finite_difference(y: np.ndarray, t: np.ndarray) -> np.ndarray:
    """
    dy/dt using central differences where possible.
    """
    y = np.asarray(y, dtype=float)
    t = np.asarray(t, dtype=float)
    n = len(y)
    if n < 3:
        return np.full(n, np.nan, dtype=float)

    dy = np.empty(n, dtype=float)
    dy[:] = np.nan

    # forward/backward for ends
    dt0 = t[1] - t[0]
    dtn = t[-1] - t[-2]
    if dt0 != 0:
        dy[0] = (y[1] - y[0]) / dt0
    if dtn != 0:
        dy[-1] = (y[-1] - y[-2]) / dtn

    # central for interior
    dt = t[2:] - t[:-2]
    num = y[2:] - y[:-2]
    with np.errstate(divide="ignore", invalid="ignore"):
        dy[1:-1] = num / dt
    return dy


def _standardize(X: np.ndarray) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    mu = np.nanmean(X, axis=0)
    sd = np.nanstd(X, axis=0)
    sd = np.where(sd == 0, 1.0, sd)
    Xs = (X - mu) / sd
    return Xs, mu, sd


def _ols_fit(X: np.ndarray, y: np.ndarray) -> Tuple[np.ndarray, float, float]:
    """
    Returns (beta, rmse, r2)
    """
    mask = np.isfinite(y) & np.all(np.isfinite(X), axis=1)
    X2 = X[mask]
    y2 = y[mask]
    if len(y2) < max(20, X2.shape[1] * 5):
        raise ValueError("not_enough_samples")

    beta, *_ = np.linalg.lstsq(X2, y2, rcond=None)
    pred = X2 @ beta
    resid = y2 - pred
    rmse = float(np.sqrt(np.mean(resid**2)))
    ss_tot = float(np.sum((y2 - np.mean(y2))**2))
    ss_res = float(np.sum(resid**2))
    r2 = float(1.0 - ss_res / ss_tot) if ss_tot > 0 else float("nan")
    return beta, rmse, r2


def _aic(n: int, rss: float, k: int) -> float:
    # AIC = n*ln(RSS/n) + 2k
    if n <= 0 or rss <= 0:
        return float("inf")
    return float(n * np.log(rss / n) + 2 * k)


# -----------------------------
# Tier 1: Coupled ODE Systems
# -----------------------------

def coupled_ode_linear(
    df: pd.DataFrame,
    cols: List[str],
    time_col: Optional[str] = None,
    add_quadratic: bool = True,
) -> Dict[str, Any]:
    """
    Fits dx/dt = A x + b (optionally with quadratic terms).
    Returns best system fit + metrics.
    """
    t, tmeta = _infer_time_axis_seconds(df, time_col=time_col)

    # Build state matrix
    Xraw = []
    used_cols = []
    for c in cols:
        if c in df.columns:
            s = _to_numeric_series(df[c])
            Xraw.append(s.to_numpy(dtype=float))
            used_cols.append(c)
    if len(used_cols) < 2:
        return {"note": "skipped", "error": "need_2plus_numeric_cols", "used_cols": used_cols}

    Xstate = np.vstack(Xraw).T  # n x m
    # Derivatives for each state component
    dX = np.vstack([_finite_difference(Xstate[:, j], t) for j in range(Xstate.shape[1])]).T

    # Feature expansion: [1, x1..xm, x1^2..xm^2, x_i x_j]
    base = Xstate.copy()
    feats = [np.ones((len(df), 1), dtype=float), base]
    feat_names = ["1"] + used_cols

    if add_quadratic:
        sq = base**2
        feats.append(sq)
        feat_names += [f"{c}^2" for c in used_cols]
        # cross terms
        m = base.shape[1]
        cross_list = []
        cross_names = []
        for i in range(m):
            for j in range(i + 1, m):
                cross_list.append((base[:, i] * base[:, j]).reshape(-1, 1))
                cross_names.append(f"{used_cols[i]}*{used_cols[j]}")
        if cross_list:
            feats.append(np.hstack(cross_list))
            feat_names += cross_names

    Phi = np.hstack(feats)

    systems = []
    total_fail = 0
    for j, yname in enumerate(used_cols):
        try:
            beta, rmse, r2 = _ols_fit(Phi, dX[:, j])
            # RSS for AIC
            mask = np.isfinite(dX[:, j]) & np.all(np.isfinite(Phi), axis=1)
            resid = dX[mask, j] - (Phi[mask] @ beta)
            rss = float(np.sum(resid**2))
            aic = _aic(int(np.sum(mask)), rss, int(len(beta)))
            systems.append({
                "state": yname,
                "beta": beta.tolist(),
                "rmse": rmse,
                "r2": r2,
                "aic": aic,
            })
        except Exception as e:
            total_fail += 1
            systems.append({"state": yname, "error": f"{type(e).__name__}:{e}"})

    ok = [s for s in systems if "beta" in s]
    if not ok:
        return {"note": "failed", "error": "all_state_fits_failed", "time": tmeta, "used_cols": used_cols, "per_state": systems}

    # Aggregate
    agg_rmse = float(np.mean([s["rmse"] for s in ok]))
    agg_r2 = float(np.nanmean([s["r2"] for s in ok]))
    agg_aic = float(np.mean([s["aic"] for s in ok]))
    return {
        "note": "ok",
        "model": "coupled_ode_linear",
        "time": tmeta,
        "used_cols": used_cols,
        "feature_names": feat_names,
        "per_state": systems,
        "aggregate": {"rmse": agg_rmse, "r2": agg_r2, "aic": agg_aic},
    }


# -----------------------------
# Tier 1: Delay dynamics (DDE-lite)
# -----------------------------

def delay_dynamics_linear(
    df: pd.DataFrame,
    ycol: str,
    time_col: Optional[str] = None,
    lag_steps: int = 5,
) -> Dict[str, Any]:
    """
    DDE-lite: dy/dt = a*y + b*y(t-lag) + c
    We approximate y(t-lag) via shift(lag_steps).
    """
    if ycol not in df.columns:
        return {"note": "skipped", "error": "ycol_missing"}

    t, tmeta = _infer_time_axis_seconds(df, time_col=time_col)
    y = _to_numeric_series(df[ycol]).to_numpy(dtype=float)
    dy = _finite_difference(y, t)

    ylag = np.roll(y, lag_steps)
    ylag[:lag_steps] = np.nan

    Phi = np.column_stack([np.ones(len(y)), y, ylag])
    try:
        beta, rmse, r2 = _ols_fit(Phi, dy)
        mask = np.isfinite(dy) & np.all(np.isfinite(Phi), axis=1)
        resid = dy[mask] - Phi[mask] @ beta
        rss = float(np.sum(resid**2))
        aic = _aic(int(np.sum(mask)), rss, 3)
        return {
            "note": "ok",
            "model": "dde_lite_linear",
            "time": tmeta,
            "ycol": ycol,
            "lag_steps": int(lag_steps),
            "params": {"a": float(beta[1]), "b": float(beta[2]), "c": float(beta[0])},
            "rmse": rmse,
            "r2": r2,
            "aic": aic,
        }
    except Exception as e:
        return {"note": "failed", "error": f"{type(e).__name__}:{e}", "time": tmeta, "ycol": ycol, "lag_steps": int(lag_steps)}


# -----------------------------
# Tier 1: Stochastic dynamics (SDE-lite)
# -----------------------------

def sde_euler_maruyama_fit(
    df: pd.DataFrame,
    ycol: str,
    time_col: Optional[str] = None,
) -> Dict[str, Any]:
    """
    SDE-lite using Euler-Maruyama:
      y_{t+1} - y_t = a(y_t)*dt + b(y_t)*sqrt(dt)*eps
    We fit drift as linear: a(y)=a0 + a1*y
    Diffusion as constant sigma estimated from residuals.
    """
    if ycol not in df.columns:
        return {"note": "skipped", "error": "ycol_missing"}

    t, tmeta = _infer_time_axis_seconds(df, time_col=time_col)
    dt = float(tmeta.get("dt_seconds") or 1.0)
    y = _to_numeric_series(df[ycol]).to_numpy(dtype=float)

    if len(y) < 50:
        return {"note": "skipped", "error": "not_enough_rows"}

    dy = np.diff(y)
    y0 = y[:-1]

    # Regression target: dy/dt
    target = dy / dt
    Phi = np.column_stack([np.ones(len(y0)), y0])
    try:
        beta, rmse, r2 = _ols_fit(Phi, target)
        # residuals in dy space:
        mask = np.isfinite(target) & np.all(np.isfinite(Phi), axis=1)
        pred = Phi[mask] @ beta
        resid = target[mask] - pred
        # Convert to dy residuals
        resid_dy = resid * dt
        sigma = float(np.nanstd(resid_dy) / math.sqrt(max(dt, 1e-9)))

        return {
            "note": "ok",
            "model": "sde_em_linear_drift",
            "time": tmeta,
            "ycol": ycol,
            "drift": {"a0": float(beta[0]), "a1": float(beta[1])},
            "diffusion": {"sigma": sigma, "assumption": "constant"},
            "rmse": rmse,
            "r2": r2,
        }
    except Exception as e:
        return {"note": "failed", "error": f"{type(e).__name__}:{e}", "time": tmeta, "ycol": ycol}


# -----------------------------
# Tier 2: Conservation / invariants (multi-var)
# -----------------------------

def conservation_linear_invariant(
    df: pd.DataFrame,
    cols: List[str],
    time_col: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Finds weights w such that w^T x is approximately constant:
      minimize || d/dt (w^T x) || subject to ||w||=1
    Solve via SVD on derivative matrix.
    """
    t, tmeta = _infer_time_axis_seconds(df, time_col=time_col)

    Xraw = []
    used_cols = []
    for c in cols:
        if c in df.columns:
            s = _to_numeric_series(df[c])
            Xraw.append(s.to_numpy(dtype=float))
            used_cols.append(c)
    if len(used_cols) < 2:
        return {"note": "skipped", "error": "need_2plus_numeric_cols", "used_cols": used_cols}

    Xstate = np.vstack(Xraw).T  # n x m
    dX = np.vstack([_finite_difference(Xstate[:, j], t) for j in range(Xstate.shape[1])]).T

    # Mask finite rows
    mask = np.all(np.isfinite(dX), axis=1)
    dX2 = dX[mask]
    if len(dX2) < 50:
        return {"note": "skipped", "error": "not_enough_finite_rows"}

    # SVD: smallest singular vector gives w minimizing ||dX w||
    U, S, VT = np.linalg.svd(dX2, full_matrices=False)
    w = VT[-1, :]  # m
    w = w / (np.linalg.norm(w) + 1e-12)

    inv = Xstate @ w
    inv_fd = _finite_difference(inv, t)
    inv_mask = np.isfinite(inv_fd)
    stability = float(np.nanstd(inv_fd[inv_mask])) if np.any(inv_mask) else float("nan")

    return {
        "note": "ok",
        "model": "linear_invariant",
        "time": tmeta,
        "used_cols": used_cols,
        "weights": {used_cols[i]: float(w[i]) for i in range(len(used_cols))},
        "invariant_derivative_std": stability,
        "interpretation": "Lower derivative std suggests a stronger conservation-like invariant.",
    }


# -----------------------------
# Tier 2: Dimensional sanity (optional units map)
# -----------------------------

def dimensional_sanity_checks(
    equations: List[Dict[str, Any]],
    units: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """
    If units provided, checks whether additive terms in an equation share the same unit.
    This is intentionally conservative. If units missing, returns 'skipped'.
    """
    if not units:
        return {"note": "skipped", "error": "units_not_provided"}

    issues = []
    for eq in equations:
        # eq should carry 'state' and 'feature_names' and 'beta'
        state = eq.get("state")
        feat_names = eq.get("feature_names") or []
        beta = eq.get("beta") or []
        if not state or not feat_names or not beta:
            continue

        # Very conservative: only validate linear terms where feature is a raw variable.
        # Constant term has no unit (treated as same unit as derivative target).
        # For state derivative dy/dt, unit is units[state] / time.
        # We can only check that all raw-variable terms are compatible with units[state].
        target_unit = units.get(state)
        if not target_unit:
            continue

        # Any raw var term must resolve to target_unit (up to unknown time scaling)
        for name, b in zip(feat_names, beta):
            if name == "1":
                continue
            if "*" in name or "^" in name:
                # skip nonlinear dimensional validation unless you provide composed units
                continue
            if name in units and units[name] != target_unit:
                if abs(float(b)) > 1e-8:
                    issues.append({
                        "equation": state,
                        "term": name,
                        "coef": float(b),
                        "unit_term": units[name],
                        "unit_target": target_unit,
                        "note": "Potential unit mismatch for linear additive term.",
                    })

    if issues:
        return {"note": "warn", "issues": issues, "count": len(issues)}
    return {"note": "ok", "issues": [], "count": 0}


# -----------------------------
# Orchestrator
# -----------------------------

def run_physics_tier12(
    df: pd.DataFrame,
    candidate_cols: Optional[List[str]] = None,
    time_col: Optional[str] = None,
    units: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """
    Runs Tier 1 + Tier 2 passes:
      - Coupled ODE system fit (top 2–4 cols)
      - DDE-lite on best single ycol
      - SDE-lite on best single ycol
      - Conservation invariant discovery on same cols
      - Dimensional sanity checks (optional)
    """
    out: Dict[str, Any] = {"note": "ok"}

    # Pick numeric columns
    cols = candidate_cols or list(df.columns)
    numeric_cols = []
    for c in cols:
        if c == time_col:
            continue
        s = _to_numeric_series(df[c])
        frac = float(np.mean(np.isfinite(s.to_numpy(dtype=float))))
        if frac >= 0.9:
            numeric_cols.append(c)

    if len(numeric_cols) < 2:
        return {"note": "skipped", "error": "no_numeric_columns_for_tier12"}

    # Choose a small set for stability (top variance)
    var_list = []
    for c in numeric_cols:
        x = _to_numeric_series(df[c]).to_numpy(dtype=float)
        var_list.append((c, float(np.nanvar(x))))
    var_list.sort(key=lambda t: t[1], reverse=True)
    core = [c for c, _ in var_list[:4]]  # up to 4

    out["selected_cols"] = core
    out["time_col"] = time_col

    # Coupled ODE
    ode = coupled_ode_linear(df, cols=core, time_col=time_col, add_quadratic=True)
    out["coupled_ode"] = ode

    # Choose ycol for single-var fits: first selected
    ycol = core[0]
    out["primary_ycol"] = ycol

    out["delay_dynamics"] = delay_dynamics_linear(df, ycol=ycol, time_col=time_col, lag_steps=5)
    out["sde"] = sde_euler_maruyama_fit(df, ycol=ycol, time_col=time_col)
    out["conservation"] = conservation_linear_invariant(df, cols=core, time_col=time_col)

    # Optional dimensional checks (only linear raw terms)
    # Build "equations" view from coupled_ode if ok
    equations = []
    if ode.get("note") == "ok":
        feat_names = ode.get("feature_names") or []
        for st in ode.get("per_state", []):
            if "beta" in st:
                equations.append({"state": st.get("state"), "feature_names": feat_names, "beta": st.get("beta")})
    out["dimensional"] = dimensional_sanity_checks(equations, units=units)

    # A simple tier12 score (0..1) for UI tiles:
    # rewards: good ODE aggregate r2, low invariant derivative std, no dimensional issues
    score = 0.0
    denom = 0.0

    if ode.get("note") == "ok":
        r2 = ode.get("aggregate", {}).get("r2")
        if r2 is not None and math.isfinite(float(r2)):
            score += max(0.0, min(1.0, (float(r2) + 1.0) / 2.0))  # map -1..1 to 0..1
            denom += 1.0

    cons = out.get("conservation", {})
    if cons.get("note") == "ok":
        inv_std = cons.get("invariant_derivative_std")
        if inv_std is not None and math.isfinite(float(inv_std)):
            # lower std -> higher score. Use soft transform.
            score += float(1.0 / (1.0 + float(inv_std)))
            denom += 1.0

    dim = out.get("dimensional", {})
    if dim.get("note") == "ok":
        score += 1.0
        denom += 1.0
    elif dim.get("note") == "warn":
        score += 0.25
        denom += 1.0

    out["tier12_score"] = float(score / denom) if denom > 0 else None
    return out
