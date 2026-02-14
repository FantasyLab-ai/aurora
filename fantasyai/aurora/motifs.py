# fantasyai/aurora/motifs.py
from __future__ import annotations
from dataclasses import dataclass
from typing import List, Dict, Any, Tuple, Optional
import logging

import numpy as np
from sklearn.cluster import KMeans

logger = logging.getLogger(__name__)


@dataclass
class MotifInstance:
    motif_id: int
    start_index: int
    end_index: int
    start_date: str
    end_date: str
    features: np.ndarray  # window vector


@dataclass
class MotifPrototype:
    motif_id: int
    centroid: np.ndarray
    count: int


@dataclass
class MotifLibrary:
    prototypes: List[MotifPrototype]
    instances: List[MotifInstance]
    window: int
    feature_names: List[str]


def build_rolling_windows(
    dates: List[str],
    X: np.ndarray,
    window: int,
) -> Tuple[List[Tuple[int, int]], np.ndarray]:
    """
    Given dates and feature matrix X (shape [T, D]),
    build rolling windows of length 'window'.
    Returns:
      - list of (start_idx, end_idx)
      - window_matrix of shape [N_windows, window * D]
    """
    T, D = X.shape
    if T < window:
        return [], np.empty((0, window * D), dtype=float)

    indices: List[Tuple[int, int]] = []
    W = []
    for start in range(T - window + 1):
        end = start + window  # exclusive
        segment = X[start:end, :]  # [window, D]
        W.append(segment.reshape(-1))
        indices.append((start, end - 1))  # inclusive start/end
    W_arr = np.vstack(W)
    return indices, W_arr


def learn_motifs_from_matrix(
    dates: List[str],
    X: np.ndarray,
    feature_names: List[str],
    window: int = 7,
    n_motifs: int = 5,
    random_state: int = 42,
) -> MotifLibrary:
    """
    Cluster rolling windows into 'motifs'.

    Each motif is represented by:
      - a centroid vector (window * D)
      - a list of instances with (start_date, end_date, features)
    """
    if X.ndim != 2:
        raise ValueError("X must be 2D [T, D].")

    T, D = X.shape
    if T < window:
        logger.warning("Not enough time steps (%d) for window=%d", T, window)
        return MotifLibrary(prototypes=[], instances=[], window=window, feature_names=feature_names)

    indices, W = build_rolling_windows(dates, X, window)
    if W.shape[0] < n_motifs:
        # reduce cluster count if we don't have enough windows
        n_motifs = max(1, W.shape[0])

    kmeans = KMeans(
        n_clusters=n_motifs,
        random_state=random_state,
        n_init=10,
    )
    labels = kmeans.fit_predict(W)
    centroids = kmeans.cluster_centers_

    prototypes: List[MotifPrototype] = []
    instances: List[MotifInstance] = []

    # count per motif
    counts = {m: 0 for m in range(n_motifs)}
    for lab in labels:
        counts[lab] = counts.get(lab, 0) + 1

    for motif_id in range(n_motifs):
        prototypes.append(
            MotifPrototype(
                motif_id=motif_id,
                centroid=centroids[motif_id],
                count=counts.get(motif_id, 0),
            )
        )

    for (start, end), lab, wvec in zip(indices, labels, W):
        instances.append(
            MotifInstance(
                motif_id=int(lab),
                start_index=start,
                end_index=end,
                start_date=dates[start],
                end_date=dates[end],
                features=wvec,
            )
        )

    return MotifLibrary(
        prototypes=prototypes,
        instances=instances,
        window=window,
        feature_names=feature_names,
    )
