from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, Any, Optional, List, Tuple

import numpy as np
import pandas as pd


def train_test_split_time(df: pd.DataFrame, frac_train: float = 0.8) -> Tuple[pd.DataFrame, pd.DataFrame]:
    n = len(df)
    k = max(1, int(n * frac_train))
    return df.iloc[:k].copy(), df.iloc[k:].copy()


def metric_rmse(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    y_true = np.asarray(y_true, dtype=float)
    y_pred = np.asarray(y_pred, dtype=float)
    return float(np.sqrt(np.mean((y_true - y_pred) ** 2)))


def metric_mae(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    y_true = np.asarray(y_true, dtype=float)
    y_pred = np.asarray(y_pred, dtype=float)
    return float(np.mean(np.abs(y_true - y_pred)))


def backtest_forecast(
    df: pd.DataFrame,
    value_col: str,
    fit_forecast_fn: Callable[[pd.Series, int], Dict[str, Any]],
    horizon: int = 14,
    frac_train: float = 0.8,
) -> Dict[str, Any]:
    train, test = train_test_split_time(df, frac_train=frac_train)
    series_train = pd.to_numeric(train[value_col], errors="coerce").astype(float).dropna()
    series_test = pd.to_numeric(test[value_col], errors="coerce").astype(float).dropna()

    h = min(horizon, len(series_test))
    fc = fit_forecast_fn(series_train, h)
    yhat = np.asarray(fc["point"][:h], dtype=float)
    ytrue = np.asarray(series_test.values[:h], dtype=float)

    return {
        "horizon": int(h),
        "metrics": {
            "rmse": metric_rmse(ytrue, yhat),
            "mae": metric_mae(ytrue, yhat),
        },
        "first5": {
            "y_true": ytrue[:5].tolist(),
            "y_pred": yhat[:5].tolist(),
        },
        "diagnostics": fc.get("diagnostics", {}),
    }


def stress_test(
    df: pd.DataFrame,
    numeric_cols: List[str],
    run_fn: Callable[[pd.DataFrame], Dict[str, Any]],
    shocks: Dict[str, float],
) -> Dict[str, Any]:
    tmp = df.copy()
    for c, mult in shocks.items():
        if c in tmp.columns:
            x = pd.to_numeric(tmp[c], errors="coerce").astype(float)
            tmp[c] = x * float(mult)
    base = run_fn(df)
    shocked = run_fn(tmp)
    return {"base": base, "shocked": shocked, "shocks": shocks}


def falsification_shuffle_target(
    df: pd.DataFrame,
    target_col: str,
    run_supervised_fn: Callable[[pd.DataFrame], Dict[str, Any]],
    seed: int = 7,
) -> Dict[str, Any]:
    g = np.random.default_rng(seed)
    tmp = df.copy()
    y = tmp[target_col].values
    g.shuffle(y)
    tmp[target_col] = y
    res = run_supervised_fn(tmp)
    res["note"] = "Target shuffled: model should degrade materially if signal is real."
    res["seed"] = seed
    return res
