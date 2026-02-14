from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Any

import numpy as np
import pandas as pd
from sklearn.cluster import KMeans
from sklearn.decomposition import PCA


@dataclass
class RegimeDetectionResult:
    labels: np.ndarray
    centers: np.ndarray
    pca_components: np.ndarray
    explained_variance_ratio: np.ndarray
    anomaly_scores: np.ndarray
    diagnostics: Dict[str, Any]


def detect_regimes_and_anomalies(
    X: pd.DataFrame | np.ndarray,
    n_clusters: int = 3,
) -> RegimeDetectionResult:
    """
    Multi-dimensional clustering + anomaly scoring:
    - PCA to compress & denoise
    - k-means to define regimes
    - anomaly score = distance to assigned cluster center in PCA space
    """
    if isinstance(X, pd.DataFrame):
        mat = X.values.astype(float)
        cols = list(X.columns)
    else:
        mat = np.asarray(X, dtype=float)
        cols = [f"feat_{i}" for i in range(mat.shape[1])]

    if mat.ndim != 2:
        raise ValueError("X must be 2D")

    # Simple 2D PCA for visualization-quality representation.
    pca = PCA(n_components=min(2, mat.shape[1]))
    Z = pca.fit_transform(mat)

    k = max(1, min(n_clusters, len(mat)))
    kmeans = KMeans(n_clusters=k, n_init=10, random_state=42)
    labels = kmeans.fit_predict(Z)
    centers = kmeans.cluster_centers_

    # Anomaly score = distance from point to its cluster center.
    dists = np.linalg.norm(Z - centers[labels], axis=1)

    diags: Dict[str, Any] = {
        "n_clusters": k,
        "feature_names": cols,
    }

    return RegimeDetectionResult(
        labels=labels,
        centers=centers,
        pca_components=pca.components_,
        explained_variance_ratio=pca.explained_variance_ratio_,
        anomaly_scores=dists,
        diagnostics=diags,
    )
