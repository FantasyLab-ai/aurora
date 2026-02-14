# fantasyai/aurora/data_fusion.py

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Any, List, Optional

import numpy as np

try:
    import pandas as pd  # type: ignore
except ImportError:  # pragma: no cover
    pd = None

from sklearn.decomposition import PCA


@dataclass
class FusionResult:
    correlation_matrix: Optional[np.ndarray]
    correlation_labels: List[str]
    pca_components: Optional[np.ndarray]
    pca_explained_variance: Optional[np.ndarray]
    diagnostics: Dict[str, Any]


def fuse_timeseries_dict(
    series_dict: Dict[str, np.ndarray],
    *,
    n_components: int = 2,
) -> FusionResult:
    """
    Take a dict of {name: 1D np.array} and compute:
      - correlation matrix
      - PCA components (low-dimensional representation)
    """
    names = list(series_dict.keys())
    if not names:
        return FusionResult(
            correlation_matrix=None,
            correlation_labels=[],
            pca_components=None,
            pca_explained_variance=None,
            diagnostics={"error": "empty input"},
        )

    # Align lengths by truncating to min length
    min_len = min(len(v) for v in series_dict.values())
    X = np.vstack([series_dict[name][:min_len] for name in names]).T  # shape: (T, D)

    # Correlation
    corr = np.corrcoef(X, rowvar=False)

    # PCA
    pca = PCA(n_components=min(n_components, X.shape[1]))
    comps = pca.fit_transform(X)
    ev = pca.explained_variance_ratio_

    diag = {
        "n_samples": int(X.shape[0]),
        "n_features": int(X.shape[1]),
    }

    return FusionResult(
        correlation_matrix=corr,
        correlation_labels=names,
        pca_components=comps,
        pca_explained_variance=ev,
        diagnostics=diag,
    )
