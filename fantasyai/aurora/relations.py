# fantasyai/aurora/relations.py
from __future__ import annotations
from dataclasses import dataclass
from typing import List

import numpy as np


@dataclass
class CrossCorrelationResult:
    lags: List[int]
    values: List[float]


def cross_correlation(
    x: List[float],
    y: List[float],
    max_lag: int = 5,
) -> CrossCorrelationResult:
    """
    Simple normalized cross-correlation up to +/- max_lag.
    """
    x_arr = np.asarray(x, dtype=float)
    y_arr = np.asarray(y, dtype=float)
    n = min(len(x_arr), len(y_arr))
    x_arr = x_arr[:n]
    y_arr = y_arr[:n]

    x_arr = (x_arr - x_arr.mean()) / (x_arr.std() + 1e-8)
    y_arr = (y_arr - y_arr.mean()) / (y_arr.std() + 1e-8)

    lags = list(range(-max_lag, max_lag + 1))
    vals: List[float] = []
    for lag in lags:
        if lag < 0:
            xx = x_arr[:lag]
            yy = y_arr[-lag:]
        elif lag > 0:
            xx = x_arr[lag:]
            yy = y_arr[:-lag]
        else:
            xx = x_arr
            yy = y_arr
        if len(xx) == 0:
            vals.append(0.0)
        else:
            vals.append(float(np.mean(xx * yy)))
    return CrossCorrelationResult(lags=lags, values=vals)
