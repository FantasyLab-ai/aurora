# fantasyai/aurora/motif_pipeline.py
from __future__ import annotations
from typing import Dict, Any, Optional
import logging

import numpy as np

from .data_collector import RawDataset
from .multivar import build_daily_feature_matrix
from .motifs import learn_motifs_from_matrix, MotifLibrary
from .similarity import most_similar_motifs, most_similar_instances

logger = logging.getLogger(__name__)


def build_aurora_motif_library(
    raw_datasets: list[RawDataset],
    window: int = 7,
    n_motifs: int = 5,
) -> MotifLibrary:
    """
    Build a motif library from daily multi-domain features.
    """
    dates, X, feature_names = build_daily_feature_matrix(raw_datasets)
    if X.size == 0:
        logger.warning("No data for motif learning.")
        return MotifLibrary(prototypes=[], instances=[], window=window, feature_names=[])

    lib = learn_motifs_from_matrix(
        dates=dates,
        X=X,
        feature_names=feature_names,
        window=window,
        n_motifs=n_motifs,
        random_state=42,
    )
    logger.info(
        "Built motif library with %d prototypes and %d instances (window=%d).",
        len(lib.prototypes),
        len(lib.instances),
        lib.window,
    )
    return lib


def motif_similarity_for_latest_window(
    raw_datasets: list[RawDataset],
    lib: Optional[MotifLibrary] = None,
    window: int = 7,
    n_motifs: int = 5,
    top_k_motifs: int = 3,
    top_k_instances: int = 5,
) -> Dict[str, Any]:
    """
    Convenience function:
      - Build library if not provided
      - Take latest window
      - Compute which motifs it belongs to / is closest to
      - Return structured summary
    """
    dates, X, feature_names = build_daily_feature_matrix(raw_datasets)
    if X.size == 0:
        return {"error": "no_data"}

    if X.shape[0] < window:
        return {"error": "not_enough_days", "num_days": X.shape[0]}

    if lib is None:
        lib = build_aurora_motif_library(raw_datasets, window=window, n_motifs=n_motifs)

    # last window
    T, D = X.shape
    window_slice = X[T - window : T, :]  # [window, D]
    query_vec = window_slice.reshape(-1)

    motif_sims = most_similar_motifs(query_vec, lib, top_k=top_k_motifs)
    inst_sims = most_similar_instances(query_vec, lib, top_k=top_k_instances)

    latest_dates = dates[T - window : T]

    return {
        "window": window,
        "latest_window_dates": latest_dates,
        "feature_names": feature_names,
        "motif_matches": [
            {"motif_id": m.motif_id, "distance": m.distance, "count": m.count}
            for m in motif_sims
        ],
        "instance_matches": [
            {
                "motif_id": s.motif_id,
                "start_date": s.start_date,
                "end_date": s.end_date,
                "distance": s.distance,
            }
            for s in inst_sims
        ],
    }
