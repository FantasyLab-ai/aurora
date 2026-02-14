from __future__ import annotations

from typing import Any, Dict, Optional, Tuple
import numpy as np
import pandas as pd


def _pick_time_column(df: pd.DataFrame) -> Optional[str]:
    # best effort: common names
    candidates = ["datetime", "timestamp", "time", "date"]
    for c in df.columns:
        if c.lower() in candidates:
            return c
    # fallback: any datetime dtype
    for c in df.columns:
        if pd.api.types.is_datetime64_any_dtype(df[c]):
            return c
    return None


def _pick_target_numeric(df: pd.DataFrame) -> Optional[str]:
    # pick a numeric col with highest variance
    nums = [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c])]
    if not nums:
        return None
    best = None
    bestv = -1.0
    for c in nums:
        x = pd.to_numeric(df[c], errors="coerce")
        v = float(x.var(skipna=True)) if x.notna().any() else 0.0
        if v > bestv:
            bestv = v
            best = c
    return best


def _fit_exponential_decay(t: np.ndarray, y: np.ndarray) -> Dict[str, Any]:
    # y ~= a * exp(-k t) + c  (very rough)
    # We do a log-linear fit ignoring c for simplicity: log(y) ~= log(a) - k t
    eps = 1e-9
    y2 = np.clip(y, eps, None)
    ly = np.log(y2)
    A = np.vstack([np.ones_like(t), -t]).T
    beta, *_ = np.linalg.lstsq(A, ly, rcond=None)
    loga, k = float(beta[0]), float(beta[1])
    return {"family": "exp_decay", "loga": loga, "k": k}


def try_mechanistic_fit(df: pd.DataFrame) -> Dict[str, Any]:
    """
    Phase 4 stub: attempt a simple mechanistic surrogate.
    Non-breaking: returns ok:false if not enough info.
    """
    time_col = _pick_time_column(df)
    target = _pick_target_numeric(df)

    if time_col is None or target is None:
        return {"ok": False, "reason": "missing_time_or_target", "time_col": time_col, "target": target}

    d = df[[time_col, target]].copy()
    d[time_col] = pd.to_datetime(d[time_col], errors="coerce")
    d[target] = pd.to_numeric(d[target], errors="coerce")
    d = d.dropna()
    if len(d) < 50:
        return {"ok": False, "reason": "not_enough_rows_after_clean", "n": int(len(d)), "time_col": time_col, "target": target}

    d = d.sort_values(time_col)
    t0 = d[time_col].iloc[0]
    t = (d[time_col] - t0).dt.total_seconds().to_numpy(dtype=float)
    y = d[target].to_numpy(dtype=float)

    # normalize time scale
    if np.nanmax(t) > 0:
        t = t / np.nanmax(t)

    fit = _fit_exponential_decay(t, y)

    # basic fit quality proxy: correlation of predicted vs actual
    yhat = np.exp(fit["loga"]) * np.exp(-fit["k"] * t)
    corr = float(np.corrcoef(y, yhat)[0, 1]) if np.isfinite(y).all() and np.isfinite(yhat).all() else float("nan")

    return {
        "ok": True,
        "time_col": time_col,
        "target": target,
        "fit": fit,
        "fit_quality": {"corr_y_yhat": corr},
        "note": "mechanistic stub; will expand into logistic / SIR / constrained state-space"
    }
