from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, Any, Optional, Tuple, List

import numpy as np
import pandas as pd


@dataclass
class IntervalResult:
    point: np.ndarray
    lower: np.ndarray
    upper: np.ndarray
    level: float
    method: str
    meta: Dict[str, Any]


def _rng(seed: Optional[int]) -> np.random.Generator:
    return np.random.default_rng(seed)


def bootstrap_statistic(
    x: np.ndarray,
    stat_fn: Callable[[np.ndarray], float],
    n_boot: int = 1000,
    level: float = 0.95,
    seed: Optional[int] = 7,
) -> IntervalResult:
    x = np.asarray(x)
    g = _rng(seed)
    boots = np.empty(n_boot, dtype=float)
    n = len(x)
    for i in range(n_boot):
        samp = x[g.integers(0, n, size=n)]
        boots[i] = float(stat_fn(samp))

    alpha = (1.0 - level) / 2.0
    lower = np.quantile(boots, alpha)
    upper = np.quantile(boots, 1 - alpha)
    point = float(stat_fn(x))
    return IntervalResult(
        point=np.array([point]),
        lower=np.array([lower]),
        upper=np.array([upper]),
        level=level,
        method="bootstrap",
        meta={"n_boot": n_boot, "seed": seed},
    )


def bootstrap_linear_regression_ci(
    X: np.ndarray,
    y: np.ndarray,
    fit_fn: Callable[[np.ndarray, np.ndarray], Tuple[np.ndarray, float]],
    predict_fn: Callable[[np.ndarray, np.ndarray, float], np.ndarray],
    X_pred: np.ndarray,
    n_boot: int = 500,
    level: float = 0.95,
    seed: Optional[int] = 7,
) -> Dict[str, Any]:
    """
    Generic bootstrap wrapper:
      - fit_fn returns (coef, intercept)
      - predict_fn(coef, X_pred, intercept) returns yhat
    """
    X = np.asarray(X)
    y = np.asarray(y).reshape(-1)
    X_pred = np.asarray(X_pred)

    g = _rng(seed)
    n = len(y)

    # point fit
    coef0, b0 = fit_fn(X, y)
    yhat0 = predict_fn(coef0, X_pred, b0)

    preds = np.zeros((n_boot, len(yhat0)), dtype=float)
    coefs = np.zeros((n_boot, len(coef0)), dtype=float)
    intercepts = np.zeros((n_boot,), dtype=float)

    for i in range(n_boot):
        idx = g.integers(0, n, size=n)
        Xb = X[idx]
        yb = y[idx]
        coef, b = fit_fn(Xb, yb)
        coefs[i] = coef
        intercepts[i] = b
        preds[i] = predict_fn(coef, X_pred, b)

    alpha = (1.0 - level) / 2.0
    lower = np.quantile(preds, alpha, axis=0)
    upper = np.quantile(preds, 1 - alpha, axis=0)

    return {
        "prediction": {
            "point": yhat0.tolist(),
            "lower": lower.tolist(),
            "upper": upper.tolist(),
            "level": level,
            "method": "bootstrap",
            "meta": {"n_boot": n_boot, "seed": seed},
        },
        "coef_ci": {
            "point": coef0.tolist(),
            "lower": np.quantile(coefs, alpha, axis=0).tolist(),
            "upper": np.quantile(coefs, 1 - alpha, axis=0).tolist(),
            "level": level,
            "method": "bootstrap",
        },
        "intercept_ci": {
            "point": float(b0),
            "lower": float(np.quantile(intercepts, alpha)),
            "upper": float(np.quantile(intercepts, 1 - alpha)),
            "level": level,
            "method": "bootstrap",
        },
    }


def robust_zscores(df: pd.DataFrame, cols: List[str]) -> pd.DataFrame:
    """
    Robust z = (x - median) / (1.4826 * MAD)
    """
    out = df.copy()
    for c in cols:
        x = pd.to_numeric(out[c], errors="coerce")
        med = np.nanmedian(x)
        mad = np.nanmedian(np.abs(x - med))
        denom = 1.4826 * mad if mad and mad > 0 else np.nanstd(x) or 1.0
        out[c + "_rz"] = (x - med) / (denom if denom != 0 else 1.0)
    return out
