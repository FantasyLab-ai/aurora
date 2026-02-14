from __future__ import annotations

from typing import Dict, Iterable

import numpy as np
import pandas as pd


def compute_univariate_anomaly_scores(
    series: pd.Series,
    *,
    z_threshold: float | None = None,
) -> pd.Series:
    """
    Simple univariate anomaly helper:
      - fill NaNs with median
      - z-score
      - return |z| as anomaly magnitude

    If z_threshold is provided, caller can later flag scores >= threshold.
    """
    s = series.copy()
    if s.isna().any():
        s = s.fillna(s.median())

    mu = s.mean()
    sigma = s.std()

    if sigma == 0 or np.isnan(sigma):
        # No variance, no anomalies
        z = pd.Series(0.0, index=s.index)
    else:
        z = (s - mu) / sigma

    scores = z.abs()
    return scores


def compute_multimetric_anomaly_scores(
    X: pd.DataFrame,
    weights: Dict[str, float],
) -> pd.Series:
    """
    Multi-metric anomaly scoring.

    Inputs
    ------
    X : pd.DataFrame
        Rows = observations (days, trips, patients, etc.).
        Columns = metrics to consider (e.g. temperature, precip, cost_per_mile).
    weights : dict
        Mapping from column name -> relative importance. Only columns present in
        X and in `weights` are used.

    Output
    -------
    pd.Series
        One anomaly score per row in X, aligned to X.index.

    Method
    ------
    1) Select columns that exist in X and in `weights`.
    2) Fill NaNs via median imputation per column.
    3) Z-score each column.
    4) Normalize weights to sum to 1.
    5) Compute a weighted Euclidean norm of the z-scores:

           score_i = sqrt( sum_c (w_c * z_{i,c})^2 )

       Larger scores = more unusual combination of metrics.
    """
    if not isinstance(X, pd.DataFrame):
        X = pd.DataFrame(X)

    # Keep only columns that appear both in X and in weights
    cols: list[str] = [c for c in X.columns if c in weights]
    if not cols:
        raise ValueError(
            f"compute_multimetric_anomaly_scores: no overlapping columns between "
            f"X.columns={list(X.columns)} and weights={list(weights.keys())}"
        )

    df = X[cols].copy()

    # Median impute
    for c in cols:
        if df[c].isna().any():
            df[c] = df[c].fillna(df[c].median())

    # Z-score columns
    z = pd.DataFrame(index=df.index)
    for c in cols:
        mu = df[c].mean()
        sigma = df[c].std()
        if sigma == 0 or np.isnan(sigma):
            z[c] = 0.0
        else:
            z[c] = (df[c] - mu) / sigma

    # Normalize weights to sum to 1 over the used columns
    w_used: Dict[str, float] = {}
    for c in cols:
        w_used[c] = float(weights.get(c, 0.0))

    total_w = sum(w_used.values()) or 1.0
    for c in cols:
        w_used[c] = w_used[c] / total_w

    # Weighted Euclidean norm per row
    contrib = pd.DataFrame(index=z.index)
    for c in cols:
        w = w_used[c]
        contrib[c] = (w * z[c]) ** 2

    score = np.sqrt(contrib.sum(axis=1))
    score.name = "aurora_anomaly_score"
    return score
