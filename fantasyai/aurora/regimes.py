# fantasyai/aurora/regimes.py
from __future__ import annotations
from dataclasses import dataclass
from typing import List, Dict, Any

import numpy as np
from sklearn.cluster import KMeans


@dataclass
class RegimeLabel:
    index: int
    label: int
    features: Dict[str, float]


def rolling_feature_matrix(
    values: List[float],
    window: int = 10,
) -> np.ndarray:
    """
    Build a rolling-window feature matrix for a 1D series.
    Each row is a window of 'window' consecutive values.
    """
    arr = np.asarray(values, dtype=float)
    if len(arr) < window:
        return np.empty((0, window), dtype=float)
    n_rows = len(arr) - window + 1
    X = np.empty((n_rows, window), dtype=float)
    for i in range(n_rows):
        X[i, :] = arr[i : i + window]
    return X


def cluster_regimes(
    values: List[float],
    n_regimes: int = 3,
    window: int = 10,
    random_state: int = 42,
) -> List[int]:
    """
    Cluster rolling windows into 'regimes' using KMeans.
    Returns a label per time index (same length as 'values'),
    with first (window-1) points set to -1 (no label).
    """
    X = rolling_feature_matrix(values, window=window)
    if X.size == 0:
        return [-1] * len(values)

    kmeans = KMeans(
        n_clusters=n_regimes,
        random_state=random_state,
        n_init=10,
    )
    labels_win = kmeans.fit_predict(X)

    labels = [-1] * len(values)
    for i, lab in enumerate(labels_win):
        idx = i + window - 1
        labels[idx] = int(lab)
    return labels
