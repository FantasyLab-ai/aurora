from __future__ import annotations

from typing import Dict, Any

import numpy as np


def time_series_metrics(y_true, y_pred) -> Dict[str, float]:
    """
    Basic time-series metrics for Aurora:
    - RMSE
    - MAE
    - MAPE (safe, ignoring zero denominators)
    """
    yt = np.asarray(y_true, dtype=float)
    yp = np.asarray(y_pred, dtype=float)
    if yt.shape != yp.shape:
        raise ValueError("y_true and y_pred must have the same shape.")

    diff = yt - yp
    rmse = float(np.sqrt(np.mean(diff ** 2)))
    mae = float(np.mean(np.abs(diff)))
    with np.errstate(divide="ignore", invalid="ignore"):
        mape_arr = np.where(yt != 0, np.abs(diff / yt), np.nan)
    mape = float(np.nanmean(mape_arr))

    return {
        "rmse": rmse,
        "mae": mae,
        "mape": mape,
    }


def risk_tail_metrics(returns, alpha: float = 0.95) -> Dict[str, Any]:
    """
    Risk-tail metrics:
    - VaR
    - CVaR
    - mean/std of returns
    """
    r = np.sort(np.asarray(returns, dtype=float))
    if r.ndim != 1 or r.size == 0:
        raise ValueError("returns must be a non-empty 1D array.")

    # alpha=0.95 -> 5% lower tail
    q_idx = int((1 - alpha) * len(r))
    q_idx = max(0, min(q_idx, len(r) - 1))
    var_val = float(r[q_idx])
    tail = r[: q_idx + 1]
    cvar_val = float(tail.mean())

    return {
        "mean": float(r.mean()),
        "std": float(r.std(ddof=1)),
        "var": var_val,
        "cvar": cvar_val,
        "alpha": alpha,
        "tail_count": int(len(tail)),
        "total_count": int(len(r)),
    }
