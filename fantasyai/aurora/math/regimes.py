
from __future__ import annotations
from typing import Any, Dict, Optional, List
import numpy as np
import pandas as pd

def _x(df: pd.DataFrame, col: str) -> np.ndarray:
    v = pd.to_numeric(df[col], errors="coerce").replace([np.inf, -np.inf], np.nan).dropna().values.astype(float)
    return v

def _cusum(x: np.ndarray, threshold: float = 8.0) -> List[int]:
    if len(x) < 80:
        return []
    mu = float(np.nanmean(x))
    sd = float(np.nanstd(x) + 1e-12)
    k = 0.5 * sd
    s_pos = 0.0; s_neg = 0.0
    cps = []
    for i, xi in enumerate(x):
        s_pos = max(0.0, s_pos + (xi - mu - k) / sd)
        s_neg = min(0.0, s_neg + (xi - mu + k) / sd)
        if s_pos > threshold or abs(s_neg) > threshold:
            cps.append(int(i))
            mu = float(np.nanmean(x[max(0, i-80):i+1]))
            s_pos = 0.0; s_neg = 0.0
    # de-dupe
    out = []
    last = -999
    for c in cps:
        if c - last >= 30:
            out.append(c); last = c
    return out[:25]

def _ruptures(x: np.ndarray) -> Dict[str, Any]:
    try:
        import ruptures as rpt
    except Exception:
        return {"note": "ruptures_not_installed"}
    if len(x) < 120:
        return {"note": "too_few_points"}
    sig = x.reshape(-1, 1)
    algo = rpt.Pelt(model="rbf").fit(sig)
    bkps = algo.predict(pen=3.0)
    # bkps includes last index; remove it
    bkps = [int(b) for b in bkps if b < len(x)]
    return {"method": "ruptures_pelt_rbf", "changepoints": bkps[:25]}

def detect_regimes_worldclass(df: pd.DataFrame, target_col: str) -> Dict[str, Any]:
    if target_col not in df.columns:
        return {"error": f"missing_target_col:{target_col}"}
    x = _x(df, target_col)
    out: Dict[str, Any] = {"target_col": target_col, "n_used": int(len(x))}
    if len(x) < 80:
        out["note"] = "too_few_points"
        return out

    out["changepoints_cusum"] = _cusum(x, threshold=8.0)
    out["changepoints_ruptures"] = _ruptures(x)

    # top step changes
    steps = []
    for c in out["changepoints_cusum"][:10]:
        a0 = max(0, c-200); a1 = c
        b0 = c; b1 = min(len(x), c+200)
        if (a1-a0) < 60 or (b1-b0) < 60:
            continue
        m0 = float(np.nanmean(x[a0:a1])); m1 = float(np.nanmean(x[b0:b1]))
        sd0 = float(np.nanstd(x[a0:a1]) + 1e-12)
        delta = m1 - m0
        steps.append({"idx": int(c), "mean_before": m0, "mean_after": m1, "delta": float(delta), "delta_in_sd": float(delta/sd0)})
    out["step_changes"] = sorted(steps, key=lambda d: abs(d["delta_in_sd"]), reverse=True)[:10]
    return out

# --- AUTO-ADDED COMPAT LAYER: detect_regimes(df, target_col=...) ---

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

try:
    import pandas as pd
except Exception:  # pragma: no cover
    pd = None  # type: ignore

def _to_numeric_series(s):
    """Convert to float series safely."""
    if pd is not None:
        return pd.to_numeric(s, errors="coerce")
    # fallback
    try:
        return np.asarray(s, dtype=float)
    except Exception:
        return np.asarray([np.nan] * len(s), dtype=float)

def _regime_stats(y: np.ndarray, cps: List[int]) -> List[Dict[str, Any]]:
    """Compute per-regime summary stats."""
    n = len(y)
    cuts = [0] + [c for c in cps if 0 < c < n] + [n]
    out = []
    for i in range(len(cuts) - 1):
        a, b = cuts[i], cuts[i + 1]
        seg = y[a:b]
        seg = seg[np.isfinite(seg)]
        out.append({
            "start": int(a),
            "end": int(b),
            "n": int(b - a),
            "mean": float(np.nanmean(seg)) if seg.size else None,
            "std": float(np.nanstd(seg)) if seg.size else None,
            "min": float(np.nanmin(seg)) if seg.size else None,
            "max": float(np.nanmax(seg)) if seg.size else None,
        })
    return out

def detect_regimes(df, target_col: Optional[str] = None, *, max_breaks: int = 8, seed: int = 0) -> Dict[str, Any]:
    """
    Detect regime changes in a target numeric series. Returns a stable JSON-friendly dict.

    - If ruptures is available: uses PELT (rbf) on the target series (after NaN drop).
    - Else: uses a simple rolling-mean shift heuristic.
    """
    rng = np.random.default_rng(int(seed) if seed is not None else 0)

    if df is None:
        return {"error": "df is None"}

    # choose target
    ycol = target_col
    if ycol is None:
        # pick first numeric-ish col
        try:
            cols = list(df.columns)
        except Exception:
            return {"error": "df has no columns"}
        ycol = None
        for c in cols:
            try:
                v = _to_numeric_series(df[c])
                if np.isfinite(np.asarray(v, dtype=float)).sum() >= 50:
                    ycol = c
                    break
            except Exception:
                continue
        if ycol is None:
            return {"note": "no_numeric_target_found"}

    # extract series
    try:
        s = df[ycol]
    except Exception:
        return {"error": f"target_col_not_found:{ycol}"}

    v = _to_numeric_series(s)
    if pd is not None:
        v = v.to_numpy(dtype=float, copy=False)
    else:
        v = np.asarray(v, dtype=float)

    # drop NaNs but keep mapping back if needed
    idx = np.where(np.isfinite(v))[0]
    y = v[idx]
    n = int(y.size)
    if n < 80:
        return {"target_col": str(ycol), "n": n, "note": "too_few_points_for_regimes"}

    # normalize for stability
    y_mean = float(np.mean(y))
    y_std = float(np.std(y)) or 1.0
    yn = (y - y_mean) / y_std

    # --- preferred: ruptures ---
    try:
        import ruptures as rpt  # type: ignore

        X = yn.reshape(-1, 1)
        # penalty heuristic: grows with log(n); tuned for stability
        pen = float(3.0 * np.log(n) * np.std(yn))
        algo = rpt.Pelt(model="rbf").fit(X)
        bkps = algo.predict(pen=pen)

        # ruptures returns last breakpoint == n; convert to internal cps excluding end
        cps = [int(b) for b in bkps if int(b) != n]
        # clip count
        if len(cps) > max_breaks:
            cps = cps[:max_breaks]

        return {
            "method": "ruptures_pelt_rbf",
            "target_col": str(ycol),
            "n": n,
            "penalty": pen,
            "change_points": cps,
            "regime_count": int(len(cps) + 1),
            "stats": _regime_stats(y, cps),
            "note": "ok",
        }
    except Exception as e:
        # fall back below
        ruptures_err = f"{type(e).__name__}: {e}"

    # --- fallback heuristic ---
    w = max(20, min(200, n // 10))
    # rolling mean and std
    # (use convolution for speed)
    kern = np.ones(w) / w
    m = np.convolve(yn, kern, mode="same")
    resid = yn - m
    # robust-ish scale
    scale = float(np.nanmedian(np.abs(resid - np.nanmedian(resid))) * 1.4826) or 1.0

    # candidate points where mean shifts sharply
    dm = np.abs(np.diff(m))
    thresh = float(3.5 * scale)
    cand = np.where(dm > thresh)[0].tolist()

    # compress nearby candidates into single points
    cps = []
    last = -10**9
    gap = max(10, w // 2)
    for c in cand:
        if c - last >= gap:
            cps.append(int(c))
            last = c

    if len(cps) > max_breaks:
        cps = cps[:max_breaks]

    return {
        "method": "heuristic_rolling_mean_shift",
        "target_col": str(ycol),
        "n": n,
        "window": int(w),
        "threshold": thresh,
        "change_points": cps,
        "regime_count": int(len(cps) + 1),
        "stats": _regime_stats(y, cps),
        "ruptures_error": ruptures_err if "ruptures_err" in locals() else None,
        "note": "ok",
    }
