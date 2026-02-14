# fantasyai/aurora/advanced_models.py
from __future__ import annotations
from dataclasses import dataclass
from typing import Sequence, List

import numpy as np
from statsmodels.tsa.seasonal import STL
import ruptures as rpt


@dataclass
class DecompositionResult:
    trend: np.ndarray
    seasonal: np.ndarray
    resid: np.ndarray


@dataclass
class ChangePointResult:
    indices: List[int]
    model: str
    penalty: float


def stl_decompose(
    values: Sequence[float],
    period: int,
) -> DecompositionResult:
    """
    Use STL (Seasonal-Trend decomposition) to separate signal into:
      trend + seasonal + residual.
    This is ideal for climate or markets with strong periodic behavior.
    """
    arr = np.asarray(values, dtype=float)
    stl = STL(arr, period=period, robust=True)
    res = stl.fit()
    return DecompositionResult(
        trend=res.trend,
        seasonal=res.seasonal,
        resid=res.resid,
    )


def detect_changepoints(
    values: Sequence[float],
    model: str = "rbf",
    penalty: float = 5.0,
) -> ChangePointResult:
    """
    Use 'ruptures' to detect change-points in a 1D series using offline algorithms.
    model="rbf" works well for many smooth time series.
    """
    arr = np.asarray(values, dtype=float)
    algo = rpt.Pelt(model=model).fit(arr)
    # 'pen' is a regularization term controlling number of change points
    change_indices = algo.predict(pen=penalty)
    # ruptures returns end indices; we convert to start indices except 0
    indices = sorted(set(int(i) for i in change_indices if i < len(arr)))
    return ChangePointResult(indices=indices, model=model, penalty=penalty)
