import os, sys, json, hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]  # .../FantasyAI
MATH_DIR = ROOT / "fantasyai" / "aurora" / "math"
RUNNER = ROOT / "scripts" / "run_aurora_dataset_runner.py"

def sha256_text(t: str) -> str:
    return hashlib.sha256(t.encode("utf-8")).hexdigest()[:12]

def write_text(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    print(f"[OK] wrote: {path} (sha={sha256_text(text)})")

def backup(path: Path):
    if not path.exists(): return
    bak = path.with_suffix(path.suffix + ".bak")
    i = 0
    while bak.exists():
        i += 1
        bak = path.with_suffix(path.suffix + f".bak{i}")
    bak.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    print(f"[OK] backup: {bak}")

# -------------------- MODULES --------------------

FORECASTING = r'''
from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple, List

import numpy as np
import pandas as pd

def _as_series(df: pd.DataFrame, col: str) -> pd.Series:
    s = df[col]
    if isinstance(s, pd.Series):
        return s
    return pd.Series(s)

def _to_time_index(df: pd.DataFrame, time_col: Optional[str]) -> Optional[pd.DatetimeIndex]:
    if not time_col or time_col not in df.columns:
        return None
    dt = pd.to_datetime(df[time_col], errors="coerce")
    if dt.notna().mean() < 0.6:
        return None
    return pd.DatetimeIndex(dt)

def _clean_numeric(y: pd.Series) -> pd.Series:
    v = pd.to_numeric(y, errors="coerce")
    return v.replace([np.inf, -np.inf], np.nan).dropna()

def _try_fit_statsmodels(y: np.ndarray, seasonal_periods: Optional[int], horizon: int) -> Dict[str, Any]:
    out: Dict[str, Any] = {"engine": None}
    try:
        from statsmodels.tsa.holtwinters import ExponentialSmoothing
        engine = "ETS"
        yy = pd.Series(y)
        trend = "add"
        seasonal = "add" if seasonal_periods and seasonal_periods >= 4 else None
        sp = seasonal_periods if seasonal else None
        model = ExponentialSmoothing(yy, trend=trend, seasonal=seasonal, seasonal_periods=sp, initialization_method="estimated")
        res = model.fit(optimized=True)
        fc = res.forecast(horizon)
        # approximate prediction bands via residual bootstrap
        resid = (yy - res.fittedvalues).dropna().values
        out.update({"engine": engine, "forecast": fc.values.tolist(), "resid_std": float(np.nanstd(resid))})
        return out
    except Exception as e:
        out["engine_error"] = f"{type(e).__name__}: {e}"

    # fallback: simple AR with numpy (very light)
    try:
        engine = "AR1_fallback"
        yy = y.astype(float)
        yy = yy[~np.isnan(yy)]
        if len(yy) < 20:
            return {"engine": engine, "error": "too_few_points"}
        x = yy[:-1]; z = yy[1:]
        a = float(np.cov(x, z, ddof=1)[0,1] / (np.var(x, ddof=1) + 1e-12))
        b = float(np.mean(z) - a*np.mean(x))
        last = float(yy[-1])
        fc = []
        for _ in range(horizon):
            last = a*last + b
            fc.append(float(last))
        out.update({"engine": engine, "forecast": fc, "a": a, "b": b})
        return out
    except Exception as e:
        return {"engine": "AR1_fallback", "error": f"{type(e).__name__}: {e}"}

def _bootstrap_pi(y: np.ndarray, point_fc: np.ndarray, resid_std: float, B: int = 300, alpha: float = 0.05) -> Dict[str, Any]:
    rng = np.random.default_rng(0)
    h = len(point_fc)
    sims = rng.normal(0.0, resid_std, size=(B, h)) + point_fc[None, :]
    lo = np.quantile(sims, alpha/2, axis=0)
    hi = np.quantile(sims, 1-alpha/2, axis=0)
    return {"method": "resid_normal", "alpha": alpha, "lo": lo.tolist(), "hi": hi.tolist()}

def _rolling_backtest(y: np.ndarray, seasonal_periods: Optional[int], horizon: int, step: int = 20) -> Dict[str, Any]:
    # rolling-origin: train up to t, forecast horizon, score
    n = len(y)
    if n < 200:
        return {"note": "skipped_backtest_small_n", "n": int(n)}
    rmses = []
    maes = []
    starts = list(range(max(50, n//3), n-horizon, step))
    for t in starts:
        train = y[:t]
        test = y[t:t+horizon]
        fit = _try_fit_statsmodels(train, seasonal_periods, horizon)
        if "forecast" not in fit:
            continue
        fc = np.asarray(fit["forecast"], dtype=float)
        if len(fc) != len(test):
            continue
        err = fc - test
        rmses.append(float(np.sqrt(np.nanmean(err**2))))
        maes.append(float(np.nanmean(np.abs(err))))
    if not rmses:
        return {"note": "backtest_failed_no_windows"}
    return {
        "windows": int(len(rmses)),
        "horizon": int(horizon),
        "rmse_mean": float(np.mean(rmses)),
        "rmse_p50": float(np.median(rmses)),
        "mae_mean": float(np.mean(maes)),
        "mae_p50": float(np.median(maes)),
    }

def forecast_with_bands(
    df: pd.DataFrame,
    target_col: str,
    time_col: Optional[str] = None,
    horizon: int = 24,
    seasonal_periods: Optional[int] = None,
    seed: int = 0,
) -> Dict[str, Any]:
    rng = np.random.default_rng(seed)
    out: Dict[str, Any] = {"target_col": target_col, "horizon": int(horizon)}

    if target_col not in df.columns:
        return {"error": f"missing_target_col:{target_col}"}

    # if time exists, sort by time
    dt = _to_time_index(df, time_col)
    work = df.copy()
    if dt is not None:
        work = work.assign(__dt__=dt).sort_values("__dt__")
    y = _clean_numeric(_as_series(work, target_col))
    if len(y) < 50:
        return {"error": "too_few_points", "n": int(len(y))}

    y_arr = y.values.astype(float)
    out["n_used"] = int(len(y_arr))

    fit = _try_fit_statsmodels(y_arr, seasonal_periods, horizon)
    out["model"] = {k: v for k, v in fit.items() if k not in ("forecast",)}
    if "forecast" not in fit:
        out["error"] = "fit_failed"
        return out

    point_fc = np.asarray(fit["forecast"], dtype=float)
    out["forecast"] = point_fc.tolist()

    resid_std = float(fit.get("resid_std", np.nan))
    if np.isfinite(resid_std) and resid_std > 0:
        out["prediction_interval"] = _bootstrap_pi(y_arr, point_fc, resid_std, B=400, alpha=0.05)
    else:
        out["prediction_interval"] = {"note": "no_residual_std"}

    out["backtest"] = _rolling_backtest(y_arr, seasonal_periods, horizon=horizon, step=max(10, horizon))
    return out
'''

REGIMES = r'''
from __future__ import annotations
from typing import Any, Dict, Optional, List, Tuple

import numpy as np
import pandas as pd

def _clean_numeric(s: pd.Series) -> np.ndarray:
    v = pd.to_numeric(s, errors="coerce").replace([np.inf, -np.inf], np.nan).dropna().values.astype(float)
    return v

def _cusum_changepoints(x: np.ndarray, threshold: float = 8.0) -> List[int]:
    # simple mean-shift detector (CUSUM). returns indices where change is likely.
    if len(x) < 60:
        return []
    x = x.copy()
    mu = np.nanmean(x)
    sd = np.nanstd(x) + 1e-12
    k = 0.5 * sd
    s_pos = 0.0
    s_neg = 0.0
    cps = []
    for i, xi in enumerate(x):
        s_pos = max(0.0, s_pos + (xi - mu - k) / sd)
        s_neg = min(0.0, s_neg + (xi - mu + k) / sd)
        if s_pos > threshold or abs(s_neg) > threshold:
            cps.append(i)
            # reset after detection
            mu = np.nanmean(x[max(0, i-50):i+1])
            s_pos = 0.0
            s_neg = 0.0
    # de-duplicate nearby
    out = []
    last = -999
    for c in cps:
        if c - last >= 25:
            out.append(int(c))
            last = c
    return out

def _drift_ks_windows(x: np.ndarray, win: int = 200, step: int = 50) -> Dict[str, Any]:
    try:
        from scipy.stats import ks_2samp
    except Exception as e:
        return {"error": f"scipy_missing:{e}"}
    n = len(x)
    if n < win*2:
        return {"note": "too_few_points", "n": int(n), "win": int(win)}
    stats = []
    for a in range(0, n - 2*win + 1, step):
        x1 = x[a:a+win]
        x2 = x[a+win:a+2*win]
        s = ks_2samp(x1, x2, alternative="two-sided", mode="auto")
        stats.append({"start": int(a), "mid": int(a+win), "D": float(s.statistic), "p": float(s.pvalue)})
    # rank by strongest drift (lowest p)
    stats_sorted = sorted(stats, key=lambda d: (d["p"], -d["D"]))
    return {"window": int(win), "step": int(step), "top_drifts": stats_sorted[:10]}

def detect_regimes(df: pd.DataFrame, target_col: str) -> Dict[str, Any]:
    if target_col not in df.columns:
        return {"error": f"missing_target_col:{target_col}"}
    x = _clean_numeric(df[target_col])
    out: Dict[str, Any] = {"target_col": target_col, "n_used": int(len(x))}
    if len(x) < 80:
        out["note"] = "too_few_points_for_regimes"
        return out

    cps = _cusum_changepoints(x, threshold=8.0)
    out["changepoints"] = cps[:20]

    # step-change summary: mean before vs after each cp
    steps = []
    for c in cps[:10]:
        a0 = max(0, c-200); a1 = c
        b0 = c; b1 = min(len(x), c+200)
        if (a1-a0) < 50 or (b1-b0) < 50:
            continue
        m0 = float(np.nanmean(x[a0:a1]))
        m1 = float(np.nanmean(x[b0:b1]))
        sd0 = float(np.nanstd(x[a0:a1]) + 1e-12)
        delta = m1 - m0
        steps.append({"idx": int(c), "mean_before": m0, "mean_after": m1, "delta": float(delta), "delta_in_sd": float(delta/sd0)})
    out["step_changes"] = sorted(steps, key=lambda d: abs(d["delta_in_sd"]), reverse=True)[:10]

    out["distribution_drift"] = _drift_ks_windows(x, win=min(300, max(120, len(x)//8)), step=max(40, len(x)//40))
    return out
'''

UNCERTAINTY = r'''
from __future__ import annotations
from typing import Any, Dict, Optional, Tuple, List

import numpy as np
import pandas as pd

def bootstrap_corr_ci(x: np.ndarray, y: np.ndarray, B: int = 600, alpha: float = 0.05, seed: int = 0) -> Dict[str, Any]:
    rng = np.random.default_rng(seed)
    m = np.isfinite(x) & np.isfinite(y)
    x = x[m]; y = y[m]
    n = len(x)
    if n < 40:
        return {"error": "too_few_points", "n": int(n)}
    idx = rng.integers(0, n, size=(B, n))
    corrs = []
    for b in range(B):
        xb = x[idx[b]]
        yb = y[idx[b]]
        if np.nanstd(xb) < 1e-12 or np.nanstd(yb) < 1e-12:
            corrs.append(np.nan)
        else:
            corrs.append(float(np.corrcoef(xb, yb)[0,1]))
    corrs = np.asarray(corrs, dtype=float)
    corrs = corrs[np.isfinite(corrs)]
    if len(corrs) < 50:
        return {"error": "bootstrap_failed"}
    lo = float(np.quantile(corrs, alpha/2))
    hi = float(np.quantile(corrs, 1-alpha/2))
    return {"method": "bootstrap", "B": int(B), "alpha": float(alpha), "lo": lo, "hi": hi, "median": float(np.median(corrs))}

def monte_carlo_risk_surface(series: np.ndarray, horizon: int = 24, n_sims: int = 2000, seed: int = 0) -> Dict[str, Any]:
    rng = np.random.default_rng(seed)
    x = series[np.isfinite(series)].astype(float)
    if len(x) < 80:
        return {"error": "too_few_points", "n": int(len(x))}
    # simple local volatility from residuals of AR(1)
    y = x
    a = float(np.cov(y[:-1], y[1:], ddof=1)[0,1] / (np.var(y[:-1], ddof=1) + 1e-12))
    b = float(np.mean(y[1:]) - a*np.mean(y[:-1]))
    resid = y[1:] - (a*y[:-1] + b)
    sigma = float(np.nanstd(resid) + 1e-12)

    last = float(y[-1])
    paths = np.zeros((n_sims, horizon), dtype=float)
    for i in range(n_sims):
        v = last
        for t in range(horizon):
            v = a*v + b + rng.normal(0.0, sigma)
            paths[i, t] = v

    # risk metrics
    p05 = np.quantile(paths, 0.05, axis=0).tolist()
    p50 = np.quantile(paths, 0.50, axis=0).tolist()
    p95 = np.quantile(paths, 0.95, axis=0).tolist()
    worst = float(np.min(paths[:, -1]))
    best = float(np.max(paths[:, -1]))
    return {
        "method": "AR1_MC",
        "horizon": int(horizon),
        "n_sims": int(n_sims),
        "bands": {"p05": p05, "p50": p50, "p95": p95},
        "terminal": {"min": worst, "max": best}
    }
'''

CAUSAL = r'''
from __future__ import annotations
from typing import Any, Dict, Optional, List

import numpy as np
import pandas as pd

def _clean_frame(df: pd.DataFrame, cols: List[str]) -> pd.DataFrame:
    out = df[cols].copy()
    for c in cols:
        out[c] = pd.to_numeric(out[c], errors="coerce")
    out = out.replace([np.inf, -np.inf], np.nan).dropna()
    return out

def causal_what_if_linear(
    df: pd.DataFrame,
    target_col: str,
    feature_col: str,
    controls: Optional[List[str]] = None,
    delta_x: float = 1.0,
) -> Dict[str, Any]:
    """
    Not “true causality” without design assumptions; this is an explainable counterfactual
    under a linear structural assumption with controls. We return effect + CI + diagnostics.
    """
    controls = controls or []
    cols = [target_col, feature_col] + controls
    missing = [c for c in cols if c not in df.columns]
    if missing:
        return {"error": f"missing_cols:{missing}"}

    try:
        import statsmodels.api as sm
    except Exception as e:
        return {"error": f"statsmodels_missing:{e}"}

    work = _clean_frame(df, cols)
    if len(work) < 80:
        return {"error": "too_few_points", "n": int(len(work))}
    y = work[target_col].values.astype(float)
    X = work[[feature_col] + controls].values.astype(float)
    X = sm.add_constant(X, has_constant="add")
    model = sm.OLS(y, X).fit()

    # coefficient for feature is index 1
    beta = float(model.params[1])
    se = float(model.bse[1])
    # 95% approx
    lo = float(beta - 1.96*se)
    hi = float(beta + 1.96*se)

    effect = beta * float(delta_x)
    effect_lo = lo * float(delta_x)
    effect_hi = hi * float(delta_x)

    return {
        "assumption": "linear_model_with_controls (not guaranteed causal without identification)",
        "n": int(len(work)),
        "target": target_col,
        "feature": feature_col,
        "controls": controls,
        "delta_x": float(delta_x),
        "beta": beta,
        "beta_ci95": [lo, hi],
        "expected_delta_y": effect,
        "expected_delta_y_ci95": [effect_lo, effect_hi],
        "r2": float(model.rsquared) if np.isfinite(model.rsquared) else None,
    }
'''

OPTIMIZATION = r'''
from __future__ import annotations
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

def solve_linear_program(c: List[float], A_ub: List[List[float]], b_ub: List[float], bounds: Optional[List[Tuple[float,float]]] = None) -> Dict[str, Any]:
    try:
        from scipy.optimize import linprog
    except Exception as e:
        return {"error": f"scipy_missing:{e}"}
    res = linprog(c=np.asarray(c, dtype=float),
                  A_ub=np.asarray(A_ub, dtype=float),
                  b_ub=np.asarray(b_ub, dtype=float),
                  bounds=bounds,
                  method="highs")
    return {
        "status": int(res.status),
        "success": bool(res.success),
        "message": str(res.message),
        "objective": float(res.fun) if res.success else None,
        "x": res.x.tolist() if res.success else None
    }

def minimize_nonlinear(fun_name: str = "quadratic_bowl", x0: Optional[List[float]] = None) -> Dict[str, Any]:
    """
    Placeholder “MATLAB-style” solver entry. You can wire your real objective later.
    """
    try:
        from scipy.optimize import minimize
    except Exception as e:
        return {"error": f"scipy_missing:{e}"}

    x0 = np.asarray(x0 or [0.0, 0.0], dtype=float)

    if fun_name == "quadratic_bowl":
        def f(x): return (x[0]-1.0)**2 + 2.0*(x[1]+2.0)**2
        def g(x): return np.array([2.0*(x[0]-1.0), 4.0*(x[1]+2.0)], dtype=float)
    else:
        def f(x): return float(np.sum(x**2))
        def g(x): return 2.0*x

    res = minimize(f, x0, jac=g, method="BFGS")
    return {
        "fun_name": fun_name,
        "success": bool(res.success),
        "message": str(res.message),
        "x": res.x.tolist(),
        "objective": float(res.fun),
        "nit": int(res.nit),
    }
'''

SIMULATION = r'''
from __future__ import annotations
from typing import Any, Dict, Optional
import numpy as np
import pandas as pd

def simulate_counterfactuals_from_regression(beta: float, beta_ci95: list, delta_x_grid: list) -> Dict[str, Any]:
    # deterministic projection over a grid of deltas
    out = []
    for dx in delta_x_grid:
        out.append({
            "delta_x": float(dx),
            "delta_y": float(beta * dx),
            "delta_y_ci95": [float(beta_ci95[0]*dx), float(beta_ci95[1]*dx)],
        })
    return {"method": "linear_projection", "curve": out}
'''

# deep_math orchestrator (single canonical entry + backward-compat aliases)
DEEP_MATH = r'''
from __future__ import annotations

from typing import Any, Dict, Optional, List
import numpy as np
import pandas as pd

# stdlib imports that have caused NameError in the past
import re
import math
import json

from .forecasting import forecast_with_bands
from .regimes import detect_regimes
from .uncertainty import bootstrap_corr_ci, monte_carlo_risk_surface
from .causal import causal_what_if_linear
from .optimization import solve_linear_program, minimize_nonlinear
from .simulation import simulate_counterfactuals_from_regression

def _numeric_cols(df: pd.DataFrame, max_cols: int = 30) -> List[str]:
    cols = []
    for c in df.columns:
        if len(cols) >= max_cols:
            break
        v = pd.to_numeric(df[c], errors="coerce")
        # keep if non-trivial numeric content
        if v.notna().mean() > 0.6 and (np.nanstd(v.values.astype(float)) if v.notna().any() else 0.0) > 1e-12:
            cols.append(c)
    return cols

def _infer_time_col(df: pd.DataFrame) -> Optional[str]:
    # prefer common names
    for cand in ["date", "datetime", "timestamp", "time", "ds"]:
        if cand in df.columns:
            dt = pd.to_datetime(df[cand], errors="coerce")
            if dt.notna().mean() > 0.6:
                return cand
    # scan first 40 columns
    for c in df.columns[:40]:
        dt = pd.to_datetime(df[c], errors="coerce")
        if dt.notna().mean() > 0.6:
            return c
    return None

def _infer_target(df: pd.DataFrame, numeric_cols: List[str]) -> Optional[str]:
    # heuristic: pick a numeric column with highest variance and low missingness
    best = None
    best_score = -1.0
    for c in numeric_cols:
        v = pd.to_numeric(df[c], errors="coerce")
        miss = 1.0 - float(v.notna().mean())
        if miss > 0.3:
            continue
        sd = float(np.nanstd(v.values.astype(float)))
        score = sd * (1.0 - miss)
        if score > best_score:
            best_score = score
            best = c
    return best

def compute_deep_math_v3(
    df: pd.DataFrame,
    seed: int = 0,
    target_col: Optional[str] = None,
    time_col: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Holy-shit level module:
    - forecasting + bands + backtests (if time series present)
    - regimes (changepoints + drift)
    - uncertainty (bootstrap CI + MC risk surface)
    - causal what-if (regression-based, explainable)
    - optimization + simulation demos (MATLAB-style hooks)
    """
    rng = np.random.default_rng(seed)
    out: Dict[str, Any] = {
        "version": "deep_math_v3",
        "seed": int(seed),
        "rows": int(df.shape[0]),
        "cols": int(df.shape[1]),
    }

    numeric_cols = _numeric_cols(df, max_cols=35)
    out["numeric_cols_used"] = numeric_cols
    out["numeric_cols_n"] = int(len(numeric_cols))

    if not numeric_cols:
        out["error"] = "no_numeric_cols"
        return out

    # choose time + target
    tcol = time_col or _infer_time_col(df)
    ycol = target_col or _infer_target(df, numeric_cols) or numeric_cols[0]
    out["time_col"] = tcol
    out["target_col"] = ycol

    # 1) Forecasting (only if time column looks real)
    if tcol is not None:
        try:
            out["forecasting"] = forecast_with_bands(df, target_col=ycol, time_col=tcol, horizon=24, seasonal_periods=None, seed=seed)
        except Exception as e:
            out["forecasting"] = {"error": f"{type(e).__name__}: {e}"}
    else:
        out["forecasting"] = {"note": "no_time_col_detected"}

    # 2) Regimes (works with or without time; uses target series)
    try:
        out["regimes"] = detect_regimes(df, target_col=ycol)
    except Exception as e:
        out["regimes"] = {"error": f"{type(e).__name__}: {e}"}

    # 3) Hypothesis + uncertainty: pick top few pairs and bootstrap their corr CI
    #    (lightweight, stable, no massive O(n^2) explosion)
    try:
        pairs = []
        cols = numeric_cols[:10]
        arr = {c: pd.to_numeric(df[c], errors="coerce").values.astype(float) for c in cols}
        for i in range(len(cols)):
            for j in range(i+1, len(cols)):
                x = arr[cols[i]]; y = arr[cols[j]]
                m = np.isfinite(x) & np.isfinite(y)
                if m.sum() < 120:
                    continue
                r = float(np.corrcoef(x[m], y[m])[0,1]) if (np.nanstd(x[m])>1e-12 and np.nanstd(y[m])>1e-12) else np.nan
                if np.isfinite(r):
                    pairs.append((cols[i], cols[j], abs(r), r, int(m.sum())))
        pairs.sort(key=lambda t: t[2], reverse=True)
        top = pairs[:8]

        boot = []
        for a,b,ab, r, n in top:
            ci = bootstrap_corr_ci(arr[a], arr[b], B=500, alpha=0.05, seed=seed)
            boot.append({"a": a, "b": b, "n": n, "pearson_r": float(r), "pearson_ci95": [ci.get("lo"), ci.get("hi")], "bootstrap": ci})
        out["uncertainty"] = {"top_corr_bootstrap": boot}
    except Exception as e:
        out["uncertainty"] = {"error": f"{type(e).__name__}: {e}"}

    # MC risk surface on target
    try:
        y = pd.to_numeric(df[ycol], errors="coerce").values.astype(float)
        out["risk_surface"] = monte_carlo_risk_surface(y, horizon=24, n_sims=2000, seed=seed)
    except Exception as e:
        out["risk_surface"] = {"error": f"{type(e).__name__}: {e}"}

    # 4) Causal what-if (explainable “counterfactual under assumptions”)
    try:
        # choose feature: strongest corr with target among numeric cols
        best_feat = None
        best_abs = -1.0
        y = pd.to_numeric(df[ycol], errors="coerce").values.astype(float)
        for c in numeric_cols:
            if c == ycol: 
                continue
            x = pd.to_numeric(df[c], errors="coerce").values.astype(float)
            m = np.isfinite(x) & np.isfinite(y)
            if m.sum() < 200:
                continue
            if np.nanstd(x[m]) < 1e-12 or np.nanstd(y[m]) < 1e-12:
                continue
            r = float(np.corrcoef(x[m], y[m])[0,1])
            if abs(r) > best_abs:
                best_abs = abs(r); best_feat = c
        if best_feat:
            cw = causal_what_if_linear(df, target_col=ycol, feature_col=best_feat, controls=[], delta_x=1.0)
            out["causal_what_if"] = cw
            if "beta" in cw and "beta_ci95" in cw:
                out["simulation"] = simulate_counterfactuals_from_regression(cw["beta"], cw["beta_ci95"], delta_x_grid=[-2,-1,-0.5,0.5,1,2])
        else:
            out["causal_what_if"] = {"note": "no_feature_found"}
    except Exception as e:
        out["causal_what_if"] = {"error": f"{type(e).__name__}: {e}"}

    # 5) Optimization hook (demo). In product, this becomes “solve under constraints”
    try:
        out["optimization_demo"] = {
            "lp_example": solve_linear_program(c=[1,2], A_ub=[[1,1],[-1,2]], b_ub=[3,2], bounds=[(0,None),(0,None)]),
            "nonlinear_example": minimize_nonlinear(fun_name="quadratic_bowl", x0=[0,0]),
        }
    except Exception as e:
        out["optimization_demo"] = {"error": f"{type(e).__name__}: {e}"}

    return out

# Backward compatible aliases so your runner never breaks again:
compute_deep_math_v2 = compute_deep_math_v3
compute_deep_math_v1 = compute_deep_math_v3
'''

def patch_runner():
    if not RUNNER.exists():
        print(f"[WARN] runner not found: {RUNNER}")
        return
    txt = RUNNER.read_text(encoding="utf-8")

    # ensure argparse has deep-math exactly once
    if 'add_argument("--deep-math"' not in txt:
        # find a good insertion point near other args
        needle = 'add_argument("--math-v2"'
        idx = txt.find(needle)
        if idx != -1:
            line_start = txt.rfind("\n", 0, idx) + 1
            line_end = txt.find("\n", idx)
            indent = ""
            # guess indent from that line
            for ch in txt[line_start:idx]:
                if ch in (" ", "\t"):
                    indent += ch
                else:
                    break
            insert = f'{indent}ap.add_argument("--deep-math", action="store_true", help="run deep math modules (forecasting/regimes/uncertainty/causal/opt/sim)")\n'
            txt = txt[:line_end+1] + insert + txt[line_end+1:]
        else:
            # fallback: append near parser creation; safe
            txt += '\n# deep-math flag injected\nap.add_argument("--deep-math", action="store_true")\n'

    # import block: ensure v3 imported (and avoid old v1/v2 names)
    if "compute_deep_math_v3" not in txt:
        # remove any existing deep_math imports to avoid conflicts
        lines = txt.splitlines()
        new_lines = []
        for ln in lines:
            if "from fantasyai.aurora.math.deep_math import" in ln:
                continue
            new_lines.append(ln)
        txt = "\n".join(new_lines) + "\n"
        # insert near other aurora imports
        anchor = "from fantasyai.aurora.mathstack_v2 import compute_math_stack_v2"
        if anchor in txt:
            txt = txt.replace(anchor, anchor + "\nfrom fantasyai.aurora.math.deep_math import compute_deep_math_v3\n")
        else:
            txt = "from fantasyai.aurora.math.deep_math import compute_deep_math_v3\n" + txt

    # ensure call writes deep_math.json and uses v3
    if 'deep_math.json' not in txt or "compute_deep_math_v3(" not in txt:
        # best-effort insertion: right after math_results.json write, add deep math call
        marker = '(run_dir / "math_results.json")'
        pos = txt.find(marker)
        if pos != -1:
            # insert after the line that writes math_results.json
            line_end = txt.find("\n", pos)
            line_end = txt.find("\n", line_end+1)  # next line end (covers .write_text(...) line)
            insertion = '''
        # --- deep math (worldclass) ---
        if getattr(args, "deep_math", False):
            try:
                deep_out = compute_deep_math_v3(df, seed=int(args.seed))
            except Exception as e:
                deep_out = {"error": f"deep_math_failed:{type(e).__name__}:{e}"}
            (run_dir / "deep_math.json").write_text(json.dumps(deep_out, indent=2), encoding="utf-8")
            math_results["_deep_math_file"] = "deep_math.json"
'''
            txt = txt[:line_end+1] + insertion + txt[line_end+1:]

    # sanity: avoid multiple deep-math flags created by earlier patches
    # (keep the first, remove subsequent exact duplicates)
    lines = txt.splitlines()
    seen = 0
    cleaned = []
    for ln in lines:
        if 'add_argument("--deep-math"' in ln:
            seen += 1
            if seen > 1:
                continue
        cleaned.append(ln)
    txt = "\n".join(cleaned) + "\n"

    backup(RUNNER)
    RUNNER.write_text(txt, encoding="utf-8")
    print(f"[OK] runner patched: {RUNNER} (deep-math flags now: {seen if seen else 1})")

def main():
    # write modules
    write_text(MATH_DIR / "forecasting.py", FORECASTING)
    write_text(MATH_DIR / "regimes.py", REGIMES)
    write_text(MATH_DIR / "uncertainty.py", UNCERTAINTY)
    write_text(MATH_DIR / "causal.py", CAUSAL)
    write_text(MATH_DIR / "optimization.py", OPTIMIZATION)
    write_text(MATH_DIR / "simulation.py", SIMULATION)

    # write deep_math orchestrator
    deep_math_path = MATH_DIR / "deep_math.py"
    backup(deep_math_path)
    write_text(deep_math_path, DEEP_MATH)

    # patch runner
    patch_runner()

    # compile check
    import py_compile
    py_compile.compile(str(deep_math_path), doraise=True)
    py_compile.compile(str(RUNNER), doraise=True)
    print("[DONE] holy-shit modules installed + runner wired + py_compile ok")

if __name__ == "__main__":
    main()
