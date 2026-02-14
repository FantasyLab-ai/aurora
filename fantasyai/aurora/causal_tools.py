from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Any, Iterable

import numpy as np
import pandas as pd
from sklearn.linear_model import LinearRegression


@dataclass
class LinearCausalEstimate:
    target: str
    features: list[str]
    coef_: Dict[str, float]
    intercept_: float
    diagnostics: Dict[str, Any]


def estimate_linear_effects(
    df: pd.DataFrame,
    target: str,
    features: Iterable[str],
) -> LinearCausalEstimate:
    """
    Lightweight causal-ish layer: fit a linear model
    target ~ features on de-meaned columns (to approximate partial effects).
    This is not a full causal engine, but gives directional sensitivity.
    """
    feats = list(features)
    data = df.dropna(subset=feats + [target]).copy()
    if data.empty:
        raise ValueError("No data available after dropping NaNs.")

    X = data[feats].values.astype(float)
    y = data[target].values.astype(float)

    # De-mean to stabilize intercept.
    X_mean = X.mean(axis=0, keepdims=True)
    y_mean = y.mean()
    X_centered = X - X_mean
    y_centered = y - y_mean

    reg = LinearRegression()
    reg.fit(X_centered, y_centered)

    coef_map = {name: float(c) for name, c in zip(feats, reg.coef_)}
    intercept = float(y_mean - float(reg.intercept_))

    diag = {
        "n_samples": int(len(data)),
        "r2": float(reg.score(X_centered, y_centered)),
        "X_mean": X_mean.squeeze().tolist(),
        "y_mean": float(y_mean),
    }

    return LinearCausalEstimate(
        target=target,
        features=feats,
        coef_=coef_map,
        intercept_=intercept,
        diagnostics=diag,
    )


def apply_counterfactual(
    estimate: LinearCausalEstimate,
    base_point: Dict[str, float],
    deltas: Dict[str, float],
) -> Dict[str, float]:
    """
    Given a fitted LinearCausalEstimate and a base configuration of features,
    apply "deltas" (e.g., +5 mutations) to get an approximated change in target.
    """
    # Compute predicted base value.
    base_val = estimate.intercept_
    for name in estimate.features:
        base_val += estimate.coef_.get(name, 0.0) * base_point.get(name, 0.0)

    # Compute new value with deltas.
    new_val = estimate.intercept_
    for name in estimate.features:
        x = base_point.get(name, 0.0) + deltas.get(name, 0.0)
        new_val += estimate.coef_.get(name, 0.0) * x

    return {
        "target": estimate.target,
        "base_value": float(base_val),
        "new_value": float(new_val),
        "delta": float(new_val - base_val),
    }
