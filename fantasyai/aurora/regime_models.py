from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Any, Optional, List, Tuple

import numpy as np
import pandas as pd
from sklearn.cluster import KMeans
from sklearn.decomposition import PCA
from sklearn.preprocessing import StandardScaler
from sklearn.linear_model import LinearRegression


@dataclass
class RegimeModel:
    scaler: StandardScaler
    pca: PCA
    kmeans: KMeans
    models: Dict[int, Any]
    feature_cols: List[str]
    target_col: str


def fit_regime_models(
    df: pd.DataFrame,
    feature_cols: List[str],
    target_col: str,
    n_regimes: int = 3,
    random_state: int = 7,
    min_points_per_regime: int = 15,
) -> Dict[str, Any]:
    tmp = df.copy()
    X = tmp[feature_cols].apply(pd.to_numeric, errors="coerce").astype(float).fillna(tmp[feature_cols].median(numeric_only=True))
    y = pd.to_numeric(tmp[target_col], errors="coerce").astype(float)

    # drop rows without target
    m = y.notna()
    X = X.loc[m]
    y = y.loc[m]

    scaler = StandardScaler()
    Xs = scaler.fit_transform(X.values)

    pca = PCA(n_components=min(2, Xs.shape[1]), random_state=random_state)
    Z = pca.fit_transform(Xs)

    kmeans = KMeans(n_clusters=n_regimes, random_state=random_state, n_init="auto")
    regimes = kmeans.fit_predict(Z)

    models: Dict[int, Any] = {}
    summaries = []

    for r in range(n_regimes):
        idx = np.where(regimes == r)[0]
        if len(idx) < min_points_per_regime:
            models[r] = None
            summaries.append({"regime_id": r, "count": int(len(idx)), "status": "too_few_points"})
            continue
        lr = LinearRegression()
        lr.fit(X.values[idx], y.values[idx])
        models[r] = lr
        summaries.append({
            "regime_id": r,
            "count": int(len(idx)),
            "status": "ok",
            "coef": {feature_cols[i]: float(lr.coef_[i]) for i in range(len(feature_cols))},
            "intercept": float(lr.intercept_),
        })

    rm = RegimeModel(
        scaler=scaler,
        pca=pca,
        kmeans=kmeans,
        models=models,
        feature_cols=feature_cols,
        target_col=target_col,
    )

    return {
        "model": rm,
        "meta": {
            "n_samples": int(len(X)),
            "n_regimes": n_regimes,
            "feature_cols": feature_cols,
            "target_col": target_col,
        },
        "regime_summaries": summaries,
        "explained_variance_ratio": [float(x) for x in pca.explained_variance_ratio_],
    }


def predict_regime_aware(
    rm: RegimeModel,
    df_new: pd.DataFrame,
) -> pd.DataFrame:
    tmp = df_new.copy()
    X = tmp[rm.feature_cols].apply(pd.to_numeric, errors="coerce").astype(float)
    X = X.fillna(X.median(numeric_only=True))

    Xs = rm.scaler.transform(X.values)
    Z = rm.pca.transform(Xs)
    regimes = rm.kmeans.predict(Z)

    preds = np.full(len(X), np.nan, dtype=float)
    for i, r in enumerate(regimes):
        model = rm.models.get(int(r))
        if model is None:
            continue
        preds[i] = float(model.predict(X.values[i:i+1])[0])

    out = tmp.copy()
    out["aurora_regime_id"] = regimes
    out["aurora_regime_pred"] = preds
    return out
