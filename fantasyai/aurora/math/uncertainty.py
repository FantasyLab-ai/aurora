
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
