from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Dict, Any

import numpy as np
import pandas as pd

try:
    from statsmodels.tsa.arima.model import ARIMA  # type: ignore
    _HAS_ARIMA = True
except Exception:  # pragma: no cover - optional dependency
    ARIMA = None  # type: ignore
    _HAS_ARIMA = False


@dataclass
class ForecastResult:
    """Container for Aurora forecasting outputs."""
    horizon: int
    history_len: int
    point_forecast: np.ndarray
    lower: np.ndarray
    upper: np.ndarray
    model_type: str
    diagnostics: Dict[str, Any]


def _ensure_series(y: pd.Series | pd.DataFrame | np.ndarray) -> pd.Series:
    if isinstance(y, pd.DataFrame):
        if y.shape[1] != 1:
            raise ValueError("Expected a single-column DataFrame for forecasting.")
        return y.iloc[:, 0]
    if isinstance(y, np.ndarray):
        if y.ndim != 1:
            raise ValueError("Expected a 1D numpy array for forecasting.")
        return pd.Series(y)
    if not isinstance(y, pd.Series):
        raise TypeError("Expected Series, 1D ndarray, or single-column DataFrame.")
    return y


def rolling_mean_forecast(
    y,
    horizon: int = 14,
    window: int = 30,
    alpha: float = 0.05,
) -> ForecastResult:
    """
    Very robust, dependency-light forecaster:
    - Uses last `window` points to compute mean and std.
    - Produces a flat forecast with normal-theory intervals.
    """
    series = _ensure_series(y).dropna()
    if len(series) < 5:
        raise ValueError("Not enough data points for rolling_mean_forecast (need >= 5).")

    w = min(window, len(series))
    tail = series.iloc[-w:]
    mu = float(tail.mean())
    sigma = float(tail.std(ddof=1) if w > 1 else 0.0)

    # z for two-sided (approx, 1.96 for 95%)
    from math import sqrt
    if alpha <= 0 or alpha >= 1:
        alpha = 0.05
    z = 1.96  # good enough for this demo

    point = np.full(horizon, mu, dtype=float)
    # conservative: don't shrink intervals with horizon; keep same sigma
    lower = mu - z * sigma
    upper = mu + z * sigma
    lower_arr = np.full(horizon, lower, dtype=float)
    upper_arr = np.full(horizon, upper, dtype=float)

    return ForecastResult(
        horizon=horizon,
        history_len=len(series),
        point_forecast=point,
        lower=lower_arr,
        upper=upper_arr,
        model_type="rolling_mean",
        diagnostics={
            "window_used": w,
            "mean": mu,
            "std": sigma,
        },
    )


def arima_forecast(
    y,
    horizon: int = 14,
    order: tuple[int, int, int] = (2, 1, 1),
) -> ForecastResult:
    """
    ARIMA-based forecaster using statsmodels if available.
    Falls back to rolling_mean_forecast if ARIMA is not installed or fit fails.
    """
    series = _ensure_series(y).dropna()
    if len(series) < 10 or not _HAS_ARIMA:
        # Fallback
        return rolling_mean_forecast(series, horizon=horizon)

    try:
        model = ARIMA(series.values, order=order)
        fitted = model.fit()
        fc = fitted.get_forecast(steps=horizon)
        point = fc.predicted_mean
        conf = fc.conf_int(alpha=0.05)
        # conf is (horizon, 2)
        lower = conf[:, 0]
        upper = conf[:, 1]
        diagnostics = {
            "aic": float(getattr(fitted, "aic", np.nan)),
            "bic": float(getattr(fitted, "bic", np.nan)),
            "order": order,
        }
        return ForecastResult(
            horizon=horizon,
            history_len=len(series),
            point_forecast=np.asarray(point, dtype=float),
            lower=np.asarray(lower, dtype=float),
            upper=np.asarray(upper, dtype=float),
            model_type="arima",
            diagnostics=diagnostics,
        )
    except Exception as exc:
        # Be extremely robust – never fail the whole pipeline on a fit issue.
        return rolling_mean_forecast(series, horizon=horizon)
