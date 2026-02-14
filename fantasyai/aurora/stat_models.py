# fantasyai/aurora/stat_models.py
from __future__ import annotations
from dataclasses import dataclass
from typing import List, Sequence, Optional

import numpy as np
from sklearn.ensemble import IsolationForest
from sklearn.preprocessing import StandardScaler


@dataclass
class TimeSeriesAnomaly:
    index: int
    time: str
    value: float
    score: float      # magnitude of anomaly (higher = more anomalous)
    method: str       # "robust_zscore", "isolation_forest"
    extra: dict       # any extra info (median, std, etc.)


def robust_zscore_anomalies(
    times: Sequence[str],
    values: Sequence[float],
    threshold: float = 2.5,
    min_points: int = 10,
) -> List[TimeSeriesAnomaly]:
    """
    Robust anomaly detector using median and MAD-based z-score.
    Good for climate/time-series where mean/std can be skewed.

    z_robust = 0.6745 * (x - median) / MAD
    """
    times_arr = np.asarray(times)
    vals = np.asarray(values, dtype=float)

    if len(vals) < min_points or len(vals) != len(times_arr):
        return []

    median = np.median(vals)
    mad = np.median(np.abs(vals - median))  # Median Absolute Deviation

    if mad == 0:
        # All points essentially identical; nothing stands out statistically.
        return []

    robust_z = 0.6745 * (vals - median) / mad
    anomalies: List[TimeSeriesAnomaly] = []

    for idx, (t, x, z) in enumerate(zip(times_arr, vals, robust_z)):
        if abs(z) >= threshold:
            anomalies.append(
                TimeSeriesAnomaly(
                    index=idx,
                    time=str(t),
                    value=float(x),
                    score=float(abs(z)),
                    method="robust_zscore",
                    extra={
                        "median": float(median),
                        "mad": float(mad),
                        "z_robust": float(z),
                    },
                )
            )

    return anomalies


def isolation_forest_anomalies(
    times: Sequence[str],
    values: Sequence[float],
    contamination: float = 0.05,
    random_state: Optional[int] = 42,
    min_points: int = 20,
    use_time_feature: bool = True,
) -> List[TimeSeriesAnomaly]:
    """
    Anomaly detector using IsolationForest (unsupervised).
    We can treat time-series as points in 1D (value) or 2D (time, value).

    Higher flexibility and can capture non-Gaussian weirdness.
    """
    times_arr = np.asarray(times)
    vals = np.asarray(values, dtype=float)

    if len(vals) < min_points or len(vals) != len(times_arr):
        return []

    indices = np.arange(len(vals), dtype=float)

    if use_time_feature:
        X = np.column_stack([indices, vals])
    else:
        X = vals.reshape(-1, 1)

    # Standardize for better IF behavior
    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)

    # Fit IsolationForest
    clf = IsolationForest(
        contamination=contamination,
        random_state=random_state,
        n_estimators=200,
        n_jobs=-1,
    )
    clf.fit(X_scaled)

    # decision_function: higher = more normal; lower = more anomalous
    scores = -clf.decision_function(X_scaled)  # flip so larger = more anomalous
    labels = clf.predict(X_scaled)             # -1 = anomaly, 1 = normal

    anomalies: List[TimeSeriesAnomaly] = []
    for idx, (t, x, s, label) in enumerate(zip(times_arr, vals, scores, labels)):
        if label == -1:
            anomalies.append(
                TimeSeriesAnomaly(
                    index=idx,
                    time=str(t),
                    value=float(x),
                    score=float(s),
                    method="isolation_forest",
                    extra={},
                )
            )

    # sort by score descending
    anomalies.sort(key=lambda a: a.score, reverse=True)
    return anomalies
