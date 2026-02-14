# fantasyai/aurora/causal_policy.py

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.neighbors import NearestNeighbors


@dataclass
class PolicyConfig:
    outcome_col: str     # e.g., "Overall Survival (Months)" or "delay_minutes"
    treatment_col: str   # e.g., "intensive_protocol" or "expedite_flag"
    feature_cols: List[str]


def _standardize_features(X: np.ndarray) -> np.ndarray:
    mu = X.mean(axis=0)
    sigma = X.std(axis=0) + 1e-8
    return (X - mu) / sigma


def estimate_ate_ipw(
    df: pd.DataFrame,
    cfg: PolicyConfig,
) -> Dict:
    """
    Inverse-probability weighted ATE estimate:
      ATE = E[Y(1) - Y(0)] using propensity scores.

    Assumes treatment_col is 0/1.
    """
    sub = df[[cfg.outcome_col, cfg.treatment_col] + cfg.feature_cols].dropna()
    y = sub[cfg.outcome_col].values.astype(float)
    t = sub[cfg.treatment_col].values.astype(int)
    X = _standardize_features(sub[cfg.feature_cols].values)

    # Propensity model P(T=1|X)
    clf = LogisticRegression(max_iter=200)
    clf.fit(X, t)
    p = clf.predict_proba(X)[:, 1]
    p = np.clip(p, 1e-3, 1 - 1e-3)

    # IPW weights
    w_treated = t / p
    w_control = (1 - t) / (1 - p)

    # ATE = E[Y(1)] - E[Y(0)]
    ate = (w_treated * y).sum() / w_treated.sum() - (w_control * y).sum() / w_control.sum()

    return {
        "n": int(len(sub)),
        "ate_ipw": float(ate),
        "propensity_mean": float(p.mean()),
        "propensity_std": float(p.std()),
    }


def neighbor_uplift_policy(
    df: pd.DataFrame,
    cfg: PolicyConfig,
    top_fraction: float = 0.2,
) -> Dict:
    """
    Simple “who should we treat?” heuristic:
      - Build treated / control pools
      - For each treated point, find a nearest neighbor in control space
      - Estimate individual uplift y_treated - y_control_neighbor
      - Recommend treatment for the top fraction by uplift
    """
    sub = df[[cfg.outcome_col, cfg.treatment_col] + cfg.feature_cols].dropna()
    y = sub[cfg.outcome_col].values.astype(float)
    t = sub[cfg.treatment_col].values.astype(int)
    X = _standardize_features(sub[cfg.feature_cols].values)

    treated_idx = np.where(t == 1)[0]
    control_idx = np.where(t == 0)[0]

    if len(treated_idx) == 0 or len(control_idx) == 0:
        return {
            "n": int(len(sub)),
            "message": "Not enough treated or control samples to build policy.",
        }

    X_treat = X[treated_idx]
    X_ctrl = X[control_idx]
    y_treat = y[treated_idx]
    y_ctrl = y[control_idx]

    nn = NearestNeighbors(n_neighbors=1)
    nn.fit(X_ctrl)
    dist, idx_match = nn.kneighbors(X_treat, return_distance=True)

    match_ctrl_indices = control_idx[idx_match.flatten()]
    uplift = y_treat - y[match_ctrl_indices]

    k = max(1, int(len(uplift) * top_fraction))
    top_indices = np.argsort(-uplift)[:k]

    recommended_treated_rows = treated_idx[top_indices]
    recommended_uplift = uplift[top_indices]

    return {
        "n": int(len(sub)),
        "top_fraction": float(top_fraction),
        "avg_uplift_recommended": float(np.mean(recommended_uplift)),
        "num_recommended": int(len(recommended_treated_rows)),
        "recommended_indices": recommended_treated_rows.tolist(),
    }
