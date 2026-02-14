# fantasyai/aurora/model_selection.py

from __future__ import annotations

from typing import Dict, List, Tuple

import numpy as np
from sklearn.ensemble import RandomForestRegressor, GradientBoostingRegressor
from sklearn.linear_model import LinearRegression
from sklearn.metrics import mean_squared_error, r2_score
from sklearn.model_selection import train_test_split


def _regression_score(
    model,
    X_train: np.ndarray,
    X_test: np.ndarray,
    y_train: np.ndarray,
    y_test: np.ndarray,
) -> Dict:
    model.fit(X_train, y_train)
    y_pred = model.predict(X_test)
    mse = mean_squared_error(y_test, y_pred)
    r2 = r2_score(y_test, y_pred)
    return {
        "mse": float(mse),
        "rmse": float(np.sqrt(mse)),
        "r2": float(r2),
    }


def auto_select_regressor(
    X: np.ndarray,
    y: np.ndarray,
    test_size: float = 0.25,
    random_state: int = 42,
) -> Dict:
    """
    Evaluate a few standard regressors and pick the best on RMSE.
    """
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=test_size, random_state=random_state
    )

    candidates = {
        "linear_regression": LinearRegression(),
        "random_forest_regressor": RandomForestRegressor(
            n_estimators=200, random_state=random_state
        ),
        "gradient_boosting_regressor": GradientBoostingRegressor(
            random_state=random_state
        ),
    }

    scores = {}
    for name, model in candidates.items():
        scores[name] = _regression_score(
            model, X_train, X_test, y_train, y_test
        )

    # pick best by RMSE
    best_name = min(scores.keys(), key=lambda n: scores[n]["rmse"])
    best_model = candidates[best_name].fit(X_train, y_train)

    return {
        "scores": scores,
        "best_model_name": best_name,
        "best_model": best_model,
    }
