from __future__ import annotations

import re
import shutil
from pathlib import Path
import py_compile

ROOT = Path(__file__).resolve().parents[1]
MATH_DIR = ROOT / "fantasyai" / "aurora" / "math"

PHYSICS_PY = MATH_DIR / "physics.py"
MODELS_PY  = MATH_DIR / "physics_models.py"
DISCOV_PY  = MATH_DIR / "physics_discovery.py"

def backup(p: Path, tag: str) -> Path:
    b = p.with_suffix(p.suffix + f".bak_{tag}")
    shutil.copy2(p, b)
    return b

def write_file(p: Path, content: str):
    p.write_text(content, encoding="utf-8", newline="\n")

PHYSICS_CONTENT = r'''from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Dict, Any, Tuple
import numpy as np
import pandas as pd


def _is_mostly_numeric(s: pd.Series, thresh: float = 0.90) -> bool:
    """True if values parse as numeric at high rate."""
    if s is None or len(s) == 0:
        return False
    x = pd.to_numeric(s, errors="coerce")
    return float(np.isfinite(x).mean()) >= thresh


def _try_parse_datetime(s: pd.Series) -> pd.Series:
    """Parse datetime with coercion, safely."""
    # IMPORTANT: do NOT force dateutil on clearly numeric columns
    dt = pd.to_datetime(s, errors="coerce", utc=False)
    return dt


def _infer_time_col(df: pd.DataFrame) -> Optional[str]:
    """
    Choose a time column if one exists.
    Heuristic:
      - must parse to datetime at high success rate
      - must NOT be mostly numeric (avoid columns like CO(GT))
    """
    best = None
    best_rate = 0.0
    for c in df.columns:
        s = df[c]
        if s is None:
            continue
        # skip obvious numeric columns to avoid the "CO(GT) treated as datetime" trap
        if _is_mostly_numeric(s):
            continue
        dt = _try_parse_datetime(s)
        rate = float(dt.notna().mean())
        if rate > best_rate and rate >= 0.70:
            best_rate = rate
            best = c
    return best


def _build_time_axis_seconds(df: pd.DataFrame, time_col: Optional[str]) -> Tuple[np.ndarray, str, float]:
    """
    Returns (t_seconds, time_axis_note, dt_seconds).
    If time_col is None or unusable -> fallback to uniform index.
    """
    n = len(df)
    if n <= 1:
        t = np.arange(n, dtype=float)
        return t, "fallback_index", 1.0

    if time_col is None or time_col not in df.columns:
        t = np.arange(n, dtype=float)
        return t, "fallback_index", 1.0

    dt = _try_parse_datetime(df[time_col])
    ok = dt.notna()
    if ok.mean() < 0.70:
        t = np.arange(n, dtype=float)
        return t, "datetime_unusable_fallback_index", 1.0

    # Use only valid times; but keep length aligned by interpolating missing times
    dt2 = dt.copy()
    # forward-fill then back-fill remaining
    dt2 = dt2.ffill().bfill()
    # convert to seconds relative to start
    t_seconds = (dt2 - dt2.iloc[0]).dt.total_seconds().to_numpy(dtype=float)

    # estimate dt_seconds robustly
    diffs = np.diff(t_seconds)
    diffs = diffs[np.isfinite(diffs)]
    diffs = diffs[diffs > 0]
    if len(diffs) == 0:
        return np.arange(n, dtype=float), "datetime_flat_fallback_index", 1.0
    dt_seconds = float(np.median(diffs))
    return t_seconds, "datetime", dt_seconds


def physics_constraints(y: np.ndarray) -> Dict[str, Any]:
    """
    Phase 4: constraints/invariants layer.
    These checks are intentionally generic so they can run on any numeric signal.
    """
    y = np.asarray(y, dtype=float)
    finite = np.isfinite(y)
    fin_rate = float(finite.mean())

    y2 = y[finite]
    if y2.size < 20:
        return {
            "note": "insufficient_data",
            "n": int(y2.size),
            "finite_rate": fin_rate,
            "physics_consistency_score": 0.0,
            "checks": {},
        }

    # generic invariants / sanity checks
    pos_rate = float((y2 >= 0).mean())
    # boundedness: 99% should be within [p0.5, p99.5] range margin
    lo = float(np.nanpercentile(y2, 0.5))
    hi = float(np.nanpercentile(y2, 99.5))
    inside = float(((y2 >= lo) & (y2 <= hi)).mean())

    # smoothness: large jumps are suspicious (scale-normalized)
    dy = np.diff(y2)
    scale = float(np.nanstd(y2) + 1e-9)
    jump_rate = float((np.abs(dy) > (10.0 * scale)).mean())

    # monotonicity tendency: Spearman-ish using ranks (no scipy)
    ranks = pd.Series(y2).rank().to_numpy()
    t = np.arange(ranks.size, dtype=float)
    # corr of ranks with time (approx monotonicity)
    corr = float(np.corrcoef(t, ranks)[0, 1])

    # score aggregation (0..1)
    # reward finite/inside/low jump, mild reward for positivity
    score = (
        0.35 * fin_rate +
        0.30 * inside +
        0.20 * (1.0 - min(1.0, jump_rate)) +
        0.15 * pos_rate
    )
    score = float(max(0.0, min(1.0, score)))

    return {
        "note": "ok",
        "n": int(y2.size),
        "finite_rate": fin_rate,
        "physics_consistency_score": score,
        "checks": {
            "positivity_rate": pos_rate,
            "boundedness_inside_p0_5_p99_5": inside,
            "jump_rate_gt_10sigma": jump_rate,
            "rank_time_corr": corr,
            "p0_5": lo,
            "p99_5": hi,
        },
    }


def physics_diagnostics(df: pd.DataFrame, target_col: Optional[str] = None) -> Dict[str, Any]:
    """
    Phase 2 baseline physics: fit linear ODE dy/dt = a*y + b using least squares.
    Uses a best-effort time axis inference, but will fallback safely.
    """
    out: Dict[str, Any] = {"note": "ok", "error": None}

    # pick target
    num_cols = [c for c in df.columns if _is_mostly_numeric(df[c], 0.80)]
    if not num_cols:
        return {"note": "error", "error": "no_numeric_columns"}

    if target_col is None or target_col not in df.columns:
        # heuristic: pick a numeric column with decent variance
        best = None
        best_var = -1.0
        for c in num_cols:
            x = pd.to_numeric(df[c], errors="coerce").to_numpy(dtype=float)
            v = float(np.nanvar(x))
            if np.isfinite(v) and v > best_var:
                best_var = v
                best = c
        target_col = best or num_cols[0]

    y = pd.to_numeric(df[target_col], errors="coerce").to_numpy(dtype=float)
    finite = np.isfinite(y)
    if finite.mean() < 0.50:
        return {"note": "error", "error": "target_mostly_missing", "target_col": target_col}

    # infer time
    time_col = _infer_time_col(df)
    t, time_axis, dt_seconds = _build_time_axis_seconds(df, time_col)

    # align using finite mask
    y2 = y.copy()
    # simple fill for differences
    s = pd.Series(y2)
    y2 = s.interpolate(limit_direction="both").to_numpy(dtype=float)

    # derivative (finite difference)
    dy = np.gradient(y2, t if time_axis == "datetime" else None)

    # fit dy = a*y + b
    X = np.column_stack([y2, np.ones_like(y2)])
    # remove any NaNs
    m = np.isfinite(dy) & np.isfinite(X).all(axis=1)
    X = X[m]
    dy_fit = dy[m]
    if X.shape[0] < 50:
        return {"note": "error", "error": "not_enough_rows", "target_col": target_col}

    beta, *_ = np.linalg.lstsq(X, dy_fit, rcond=None)
    a = float(beta[0])
    b = float(beta[1])

    pred = X @ beta
    resid = dy_fit - pred
    rmse = float(np.sqrt(np.mean(resid**2)))

    # r2
    var = float(np.var(dy_fit))
    r2 = float(1.0 - (np.var(resid) / (var + 1e-12)))

    # stability for dy/dt = a*y + b: stable if a < 0
    stable = bool(a < 0.0)
    tau = float(abs(1.0 / (a + 1e-12)))  # "time constant" in seconds if time_axis=datetime, else in index units

    out.update({
        "model": "dy_dt = a*y + b",
        "a": a,
        "b": b,
        "stable": stable,
        "time_constant_seconds": tau,
        "r2": r2,
        "rmse": rmse,
        "n": int(X.shape[0]),
        "time_axis": time_axis,
        "dt_seconds": float(dt_seconds),
        "time_col": time_col,
        "target_col": target_col,
    })
    return out
'''

MODELS_CONTENT = r'''from __future__ import annotations

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
'''

DISCOV_CONTENT = r'''from __future__ import annotations

from typing import Dict, Any, Optional, List
import numpy as np
import pandas as pd

from .physics import _infer_time_col, _build_time_axis_seconds, _is_mostly_numeric, physics_constraints
from .physics_models import candidate_models


def discover_physics_models(df: pd.DataFrame, target_col: Optional[str] = None) -> Dict[str, Any]:
    """
    Phase 3: run multiple physics-inspired models, score them, return best + runner-ups.
    This works even if we fall back to index time.
    """
    # choose numeric target
    num_cols = [c for c in df.columns if _is_mostly_numeric(df[c], 0.80)]
    if not num_cols:
        return {"note": "error", "error": "no_numeric_columns"}

    if target_col is None or target_col not in df.columns:
        # pick highest variance numeric column
        best = None; best_var = -1.0
        for c in num_cols:
            y = pd.to_numeric(df[c], errors="coerce").to_numpy(dtype=float)
            v = float(np.nanvar(y))
            if np.isfinite(v) and v > best_var:
                best_var = v; best = c
        target_col = best or num_cols[0]

    y = pd.to_numeric(df[target_col], errors="coerce").to_numpy(dtype=float)
    s = pd.Series(y)
    y = s.interpolate(limit_direction="both").to_numpy(dtype=float)

    time_col = _infer_time_col(df)
    t, time_axis, dt_seconds = _build_time_axis_seconds(df, time_col)

    # train/test split for scoring
    n = len(y)
    if n < 200:
        return {"note": "error", "error": "not_enough_rows", "n": int(n), "target_col": target_col}

    split = int(n * 0.80)
    t_tr, y_tr = t[:split], y[:split]
    t_te, y_te = t[split:], y[split:]

    results: List[Dict[str, Any]] = []
    for fit_fn in candidate_models():
        try:
            r = fit_fn(t_tr, y_tr)
            if not r.get("ok"):
                results.append({"name": r.get("name", getattr(fit_fn, "__name__", "unknown")),
                                "ok": False, "error": r.get("error", "fit_failed")})
                continue

            # re-simulate full series using fitted params by re-calling on full
            # (cheap and keeps deps minimal)
            r_full = fit_fn(t, y)
            if not r_full.get("ok"):
                results.append({"name": r.get("name", "unknown"), "ok": False, "error": "full_fit_failed"})
                continue

            yhat = np.asarray(r_full.get("yhat", np.full_like(y, np.nan)), dtype=float)
            rmse_test = float(np.sqrt(np.mean((y_te - yhat[split:])**2)))

            results.append({
                "name": r_full.get("name", "unknown"),
                "model": r_full.get("model"),
                "params": r_full.get("params", {}),
                "rmse_test": rmse_test,
                "rmse_full": float(r_full.get("rmse", np.nan)),
                "aic": float(r_full.get("aic", np.nan)),
                "k": int(r_full.get("k", 0)),
            })
        except Exception as e:
            results.append({"name": getattr(fit_fn, "__name__", "unknown"), "ok": False, "error": str(e)})

    # select best by rmse_test primarily, then aic
    valid = [r for r in results if r.get("rmse_test") is not None and np.isfinite(r.get("rmse_test", np.nan))]
    if not valid:
        return {
            "note": "error",
            "error": "all_models_failed",
            "target_col": target_col,
            "time_axis": time_axis,
            "time_col": time_col,
            "dt_seconds": float(dt_seconds),
            "candidates": results,
        }

    valid.sort(key=lambda r: (r["rmse_test"], r.get("aic", float("inf"))))
    best = valid[0]
    runners = valid[1:3]

    # Phase 4 constraints score (generic invariants)
    constraints = physics_constraints(y)

    return {
        "note": "ok",
        "target_col": target_col,
        "time_axis": time_axis,
        "time_col": time_col,
        "dt_seconds": float(dt_seconds),
        "best": best,
        "runner_ups": runners,
        "all_candidates": results,
        "constraints": constraints,
        "physics_consistency_score": constraints.get("physics_consistency_score", 0.0),
    }
'''

def main():
    for p in [PHYSICS_PY, MODELS_PY, DISCOV_PY]:
        if not p.exists():
            raise SystemExit(f"ERROR: missing expected file: {p}")

    backup(PHYSICS_PY, "pre_phase34")
    backup(MODELS_PY,  "pre_phase34")
    backup(DISCOV_PY,  "pre_phase34")

    write_file(PHYSICS_PY, PHYSICS_CONTENT)
    write_file(MODELS_PY,  MODELS_CONTENT)
    write_file(DISCOV_PY,  DISCOV_CONTENT)

    # compile checks
    py_compile.compile(str(PHYSICS_PY), doraise=True)
    py_compile.compile(str(MODELS_PY), doraise=True)
    py_compile.compile(str(DISCOV_PY), doraise=True)

    print("[DONE] Phase 3/4 physics modules installed cleanly:")
    print(f" - {PHYSICS_PY}")
    print(f" - {MODELS_PY}")
    print(f" - {DISCOV_PY}")

if __name__ == "__main__":
    main()
