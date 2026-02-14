# fantasyai/aurora/forecast_models.py
from __future__ import annotations
from typing import Sequence, List
from dataclasses import dataclass

import numpy as np
from statsmodels.tsa.ar_model import AutoReg


@dataclass
class ForecastResidualAnomaly:
    index: int
    time: str
    value: float
    forecast: float
    residual: float
    z_score: float


def forecast_residual_anomalies(
    times: Sequence[str],
    values: Sequence[float],
    ar_lags: int = 3,
    min_points: int = 20,
    z_threshold: float = 2.5,
) -> List[ForecastResidualAnomaly]:
    """
    Fit an AutoReg (AR) model and use forecast residual z-scores to
    detect anomalies.

    Great for non-stationary series (climate temps, prices).
    """
    if len(values) < max(min_points, ar_lags + 5) or len(times) != len(values):
        return []

    arr = np.asarray(values, dtype=float)
    model = AutoReg(arr, lags=ar_lags, old_names=False)
    res = model.fit()
    # in-sample predictions
    preds = res.predict(start=ar_lags, end=len(arr) - 1)
    residuals = arr[ar_lags:] - preds

    mu = residuals.mean()
    std = residuals.std()
    if std == 0:
        return []

    z = (residuals - mu) / std

    anomalies: List[ForecastResidualAnomaly] = []
    for i, (time, val, f, r, zz) in enumerate(
        zip(times[ar_lags:], arr[ar_lags:], preds, residuals, z), start=ar_lags
    ):
        if abs(zz) >= z_threshold:
            anomalies.append(
                ForecastResidualAnomaly(
                    index=i,
                    time=str(time),
                    value=float(val),
                    forecast=float(f),
                    residual=float(r),
                    z_score=float(zz),
                )
            )

    return anomalies
