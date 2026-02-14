# fantasyai/aurora/backtesting.py
from __future__ import annotations
from dataclasses import dataclass
from typing import List, Dict, Any, Sequence

import numpy as np

from .stat_models import robust_zscore_anomalies, TimeSeriesAnomaly


@dataclass
class ThresholdSweepResult:
    threshold: float
    num_anomalies: int
    coverage_fraction: float  # fraction of time steps flagged


def threshold_sweep(
    times: Sequence[str],
    values: Sequence[float],
    thresholds: List[float],
    min_points: int = 10,
) -> List[ThresholdSweepResult]:
    """
    Simple backtest: for many thresholds, count anomalies and coverage.
    """
    T = len(values)
    results: List[ThresholdSweepResult] = []

    for th in thresholds:
        anoms: List[TimeSeriesAnomaly] = robust_zscore_anomalies(
            times=times,
            values=values,
            threshold=th,
            min_points=min_points,
        )
        idxs = {a.index for a in anoms}
        coverage = len(idxs) / max(1, T)
        results.append(
            ThresholdSweepResult(
                threshold=th,
                num_anomalies=len(anoms),
                coverage_fraction=coverage,
            )
        )
    return results


def summarize_anomalies_by_month(anoms: List[TimeSeriesAnomaly]) -> Dict[str, int]:
    """
    Group anomaly counts by YYYY-MM month string.
    """
    from datetime import datetime

    counts: Dict[str, int] = {}
    for a in anoms:
        try:
            dt = datetime.fromisoformat(str(a.time))
            key = dt.strftime("%Y-%m")
        except Exception:
            key = "unknown"
        counts[key] = counts.get(key, 0) + 1
    return counts
