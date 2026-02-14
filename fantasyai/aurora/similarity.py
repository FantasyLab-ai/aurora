# fantasyai/aurora/similarity.py
from __future__ import annotations
from dataclasses import dataclass
from typing import List, Tuple

import numpy as np

from .motifs import MotifLibrary, MotifPrototype, MotifInstance


@dataclass
class SimilarMotif:
    motif_id: int
    distance: float
    count: int


@dataclass
class SimilarInstance:
    motif_id: int
    start_date: str
    end_date: str
    distance: float


def _euclidean(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.linalg.norm(a - b))


def most_similar_motifs(
    query_window: np.ndarray,
    library: MotifLibrary,
    top_k: int = 3,
) -> List[SimilarMotif]:
    """
    Given a query window vector (shape [window * D]),
    find the most similar motif centroids.
    """
    sims: List[SimilarMotif] = []
    for proto in library.prototypes:
        d = _euclidean(query_window, proto.centroid)
        sims.append(
            SimilarMotif(
                motif_id=proto.motif_id,
                distance=d,
                count=proto.count,
            )
        )
    sims.sort(key=lambda s: s.distance)
    return sims[:top_k]


def most_similar_instances(
    query_vec: np.ndarray,
    library: MotifLibrary,
    top_k: int = 5,
) -> List[SimilarInstance]:
    """
    Nearest-neighbor search over all instances.
    Query can be:
      - a flattened window [window * D]
      - a single day's feature vector broadcasted to match window size.
    """
    sims: List[SimilarInstance] = []

    for inst in library.instances:
        d = _euclidean(query_vec, inst.features)
        sims.append(
            SimilarInstance(
                motif_id=inst.motif_id,
                start_date=inst.start_date,
                end_date=inst.end_date,
                distance=d,
            )
        )

    sims.sort(key=lambda s: s.distance)
    return sims[:top_k]
