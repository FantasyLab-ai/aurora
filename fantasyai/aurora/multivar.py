# fantasyai/aurora/multivar.py
from __future__ import annotations
from dataclasses import dataclass
from typing import Dict, Any, List, Tuple
import logging
from datetime import datetime

import numpy as np

from .data_collector import RawDataset

logger = logging.getLogger(__name__)


@dataclass
class MultivarState:
    date: str
    features: Dict[str, float]
    distance: float


def _ensure_float(x: Any) -> float:
    try:
        return float(x)
    except Exception:
        return float("nan")


def _ms_to_date_str(ms: Any) -> str:
    try:
        ts = int(ms) / 1000.0
        return datetime.utcfromtimestamp(ts).date().isoformat()
    except Exception:
        return "unknown"


def build_daily_feature_matrix(raw_datasets: List[RawDataset]) -> Tuple[List[str], np.ndarray, List[str]]:
    """
    Build a matrix X where each row is a date and columns are numeric features:
      climate:<loc>:temp
      market:<coin>:price
      market:<coin>:return
      astronomy:apod:non_image_fraction
    """
    # date -> feature dict
    day_features: Dict[str, Dict[str, float]] = {}

    # ---- climate ----
    for ds in raw_datasets:
        if ds.domain != "climate":
            continue
        loc = ds.meta.get("location_name", "unknown")
        payload = ds.payload
        times = payload.get("time", [])
        temps = payload.get("temperature_2m_max", [])
        for t, temp in zip(times, temps):
            date = str(t)
            day_features.setdefault(date, {})
            key = f"climate:{loc}:temp"
            day_features[date][key] = _ensure_float(temp)

    # ---- markets ----
    for ds in raw_datasets:
        if ds.domain != "market":
            continue
        coin = ds.meta.get("coin_id", "unknown")
        payload = ds.payload
        times = payload.get("time", [])
        prices = payload.get("price", [])
        if len(times) != len(prices) or len(times) < 2:
            continue

        # compute log returns for multivariate representation
        arr = np.asarray(prices, dtype=float)
        log_prices = np.log(arr)
        returns = np.diff(log_prices)

        for idx in range(len(prices)):
            date = _ms_to_date_str(times[idx])
            day_features.setdefault(date, {})
            pkey = f"market:{coin}:price"
            day_features[date][pkey] = _ensure_float(prices[idx])
            if idx > 0:
                rkey = f"market:{coin}:return"
                day_features[date][rkey] = _ensure_float(returns[idx - 1])

    # ---- astronomy (APOD) ----
    for ds in raw_datasets:
        if ds.domain != "astronomy" or ds.source != "nasa_apod":
            continue
        entries = ds.payload.get("entries", [])
        # For each date, encode whether media_type != image (1) or not (0)
        for e in entries:
            date = e.get("date", "unknown")
            media_type = (e.get("media_type") or "").lower()
            day_features.setdefault(date, {})
            key = "astronomy:apod:non_image"
            val = 1.0 if media_type and media_type != "image" else 0.0
            # if multiple entries per day, we can sum (but APOD usually 1/day)
            day_features[date][key] = day_features[date].get(key, 0.0) + val

    if not day_features:
        return [], np.empty((0, 0)), []

    # Collect all feature names
    feature_names: List[str] = sorted({k for feats in day_features.values() for k in feats.keys()})
    dates_sorted = sorted(day_features.keys())

    X = np.zeros((len(dates_sorted), len(feature_names)), dtype=float)
    X.fill(np.nan)

    for i, d in enumerate(dates_sorted):
        feats = day_features[d]
        for j, fname in enumerate(feature_names):
            if fname in feats:
                X[i, j] = feats[fname]

    # Impute NaNs with column means
    col_means = np.nanmean(X, axis=0)
    inds = np.where(np.isnan(X))
    X[inds] = np.take(col_means, inds[1])

    return dates_sorted, X, feature_names


def compute_mahalanobis_distances(X: np.ndarray, eps: float = 1e-6) -> np.ndarray:
    """
    Compute Mahalanobis distance of each row from the global mean/cov.
    """
    if X.shape[0] < 3:
        return np.zeros(X.shape[0], dtype=float)

    mu = X.mean(axis=0)
    diff = X - mu
    cov = np.cov(X, rowvar=False)
    # regularize covariance in case it's singular
    cov += eps * np.eye(cov.shape[0])
    inv_cov = np.linalg.inv(cov)
    d_sq = np.einsum("ij,jk,ik->i", diff, inv_cov, diff)
    d = np.sqrt(np.maximum(d_sq, 0.0))
    return d


def detect_multivariate_states(
    raw_datasets: List[RawDataset],
    threshold: float = 3.0,
) -> List[MultivarState]:
    """
    Build daily feature vectors across domains and flag days with large
    Mahalanobis distance as joint anomalies.
    """
    dates, X, feature_names = build_daily_feature_matrix(raw_datasets)
    if X.size == 0:
        return []

    d = compute_mahalanobis_distances(X)
    states: List[MultivarState] = []

    for date, dist, row in zip(dates, d, X):
        states.append(
            MultivarState(
                date=date,
                features={fname: float(val) for fname, val in zip(feature_names, row)},
                distance=float(dist),
            )
        )

    # We don't threshold here; analyzer decides what to store.
    return states
