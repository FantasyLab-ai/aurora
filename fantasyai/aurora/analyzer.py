# fantasyai/aurora/analyzer.py
from __future__ import annotations
from typing import Dict, Any, List
from dataclasses import dataclass
import logging
import json
import numpy as np

from .data_collector import RawDataset
from .stat_models import (
    TimeSeriesAnomaly,
    robust_zscore_anomalies,
    isolation_forest_anomalies,
)
from .advanced_models import stl_decompose, detect_changepoints
from .forecast_models import forecast_residual_anomalies
from .multivar import detect_multivariate_states
from ..db.storage import insert_aurora_anomalies

logger = logging.getLogger(__name__)


@dataclass
class Anomaly:
    domain: str         # "climate", "market", "astronomy", "system"
    source: str         # "open_meteo", "coingecko", "nasa_apod", "multivar"
    description: str
    score: float        # magnitude of anomaly
    metadata: Dict[str, Any]


def _from_ts_anomaly(
    ts_a: TimeSeriesAnomaly,
    *,
    domain: str,
    source: str,
    value_label: str,
    meta: Dict[str, Any],
) -> Anomaly:
    desc = (
        f"{value_label}={ts_a.value:.4f} at index={ts_a.index} time={ts_a.time} "
        f"is anomalous by {ts_a.method} (score={ts_a.score:.2f})."
    )
    metadata = {
        "time": ts_a.time,
        "index": ts_a.index,
        "value": ts_a.value,
        "score": ts_a.score,
        "method": ts_a.method,
        "value_label": value_label,
        **meta,
        **ts_a.extra,
    }
    return Anomaly(
        domain=domain,
        source=source,
        description=desc,
        score=ts_a.score,
        metadata=metadata,
    )


# ---------- CLIMATE ----------

def _detect_climate_anomalies(ds: RawDataset) -> List[Anomaly]:
    payload = ds.payload
    times = payload.get("time", [])
    temps = payload.get("temperature_2m_max", [])

    if not times or not temps:
        return []

    anoms: List[Anomaly] = []

    # 1) robust z-score on raw temps
    base_anoms = robust_zscore_anomalies(
        times=times,
        values=temps,
        threshold=2.5,
        min_points=10,
    )
    anoms.extend(
        _from_ts_anomaly(
            ts_a,
            domain="climate",
            source="open_meteo",
            value_label="daily_max_temp_C",
            meta={**ds.meta, "series": "raw_temp"},
        )
        for ts_a in base_anoms
    )

    # 2) STL residual anomalies
    if len(temps) >= 30:
        decomp = stl_decompose(temps, period=7)
        resid = decomp.resid
        ts_anoms_resid = robust_zscore_anomalies(
            times=times,
            values=resid,
            threshold=2.5,
            min_points=10,
        )
        anoms.extend(
            _from_ts_anomaly(
                ts_a,
                domain="climate",
                source="open_meteo",
                value_label="residual_temp",
                meta={**ds.meta, "series": "residual"},
            )
            for ts_a in ts_anoms_resid
        )

    # 3) change-points
    if len(temps) >= 30:
        from .advanced_models import detect_changepoints

        cp = detect_changepoints(temps, model="rbf", penalty=5.0)
        for idx in cp.indices:
            if idx >= len(times):
                continue
            desc = (
                f"Potential structural change in climate series at index={idx}, "
                f"time={times[idx]} (model={cp.model}, pen={cp.penalty})."
            )
            anoms.append(
                Anomaly(
                    domain="climate",
                    source="open_meteo",
                    description=desc,
                    score=1.0,
                    metadata={
                        "time": times[idx],
                        "index": idx,
                        "model": cp.model,
                        "penalty": cp.penalty,
                        **ds.meta,
                        "series": "change_point",
                    },
                )
            )

    # 4) forecast residual anomalies
    frc = forecast_residual_anomalies(
        times=times,
        values=temps,
        ar_lags=3,
        min_points=20,
        z_threshold=2.5,
    )
    for fa in frc:
        desc = (
            f"Forecast residual anomaly at time={fa.time}: "
            f"value={fa.value:.3f}, forecast={fa.forecast:.3f}, "
            f"residual={fa.residual:.3f}, z={fa.z_score:.2f}"
        )
        anoms.append(
            Anomaly(
                domain="climate",
                source="open_meteo",
                description=desc,
                score=float(abs(fa.z_score)),
                metadata={
                    "time": fa.time,
                    "index": fa.index,
                    "value": fa.value,
                    "forecast": fa.forecast,
                    "residual": fa.residual,
                    "z_score": fa.z_score,
                    **ds.meta,
                    "series": "forecast_residual",
                },
            )
        )

    return anoms


# ---------- MARKET ----------

def _detect_market_anomalies(ds: RawDataset) -> List[Anomaly]:
    payload = ds.payload
    times = payload.get("time", [])
    prices = payload.get("price", [])

    if not times or not prices or len(times) != len(prices):
        return []

    values = np.asarray(prices, dtype=float)

    anoms: List[Anomaly] = []

    # 1) return anomalies
    log_prices = np.log(values)
    returns = np.diff(log_prices)
    ret_times = times[1:]

    ts_anoms_returns = robust_zscore_anomalies(
        times=ret_times,
        values=returns,
        threshold=2.5,
        min_points=10,
    )
    anoms.extend(
        _from_ts_anomaly(
            ts_a,
            domain="market",
            source="coingecko",
            value_label="log_return",
            meta={**ds.meta, "series": "returns"},
        )
        for ts_a in ts_anoms_returns
    )

    # 2) IsolationForest on price
    ts_anoms_if = isolation_forest_anomalies(
        times=times,
        values=values,
        contamination=0.05,
        min_points=20,
        use_time_feature=True,
    )
    anoms.extend(
        _from_ts_anomaly(
            ts_a,
            domain="market",
            source="coingecko",
            value_label="price_usd",
            meta={**ds.meta, "series": "price"},
        )
        for ts_a in ts_anoms_if
    )

    # 3) forecast residual anomalies on prices
    frc = forecast_residual_anomalies(
        times=times,
        values=values,
        ar_lags=3,
        min_points=30,
        z_threshold=2.5,
    )
    for fa in frc:
        desc = (
            f"Price forecast residual anomaly at time={fa.time}: "
            f"value={fa.value:.3f}, forecast={fa.forecast:.3f}, "
            f"residual={fa.residual:.3f}, z={fa.z_score:.2f}"
        )
        anoms.append(
            Anomaly(
                domain="market",
                source="coingecko",
                description=desc,
                score=float(abs(fa.z_score)),
                metadata={
                    "time": fa.time,
                    "index": fa.index,
                    "value": fa.value,
                    "forecast": fa.forecast,
                    "residual": fa.residual,
                    "z_score": fa.z_score,
                    **ds.meta,
                    "series": "forecast_residual",
                },
            )
        )

    return anoms


# ---------- ASTRONOMY ----------

def _detect_apod_anomalies(ds: RawDataset) -> List[Anomaly]:
    payload = ds.payload
    entries = payload.get("entries", [])
    anomalies: List[Anomaly] = []

    for e in entries:
        date_str = e.get("date", "unknown")
        media_type = e.get("media_type", "unknown")
        title = e.get("title", "Untitled")
        explanation = e.get("explanation", "") or ""
        url = e.get("url", "")

        if media_type.lower() != "image":
            desc = f"APOD {date_str} '{title}' media_type='{media_type}' is unusual (not image)."
            anomalies.append(
                Anomaly(
                    domain="astronomy",
                    source="nasa_apod",
                    description=desc,
                    score=1.5,
                    metadata={
                        "date": date_str,
                        "media_type": media_type,
                        "title": title,
                        "url": url,
                        "reason": "non_image_media_type",
                    },
                )
            )

        if len(explanation) > 1200:
            desc = (
                f"APOD {date_str} '{title}' explanation length={len(explanation)} "
                "is unusually long (high-detail event)."
            )
            anomalies.append(
                Anomaly(
                    domain="astronomy",
                    source="nasa_apod",
                    description=desc,
                    score=1.2,
                    metadata={
                        "date": date_str,
                        "title": title,
                        "url": url,
                        "explanation_length": len(explanation),
                        "reason": "very_long_explanation",
                    },
                )
            )

    return anomalies


# ---------- MULTIVARIATE (ALL DOMAINS) ----------

def _detect_multivariate_anomalies(raw_datasets: List[RawDataset]) -> List[Anomaly]:
    states = detect_multivariate_states(raw_datasets)
    if not states:
        return []

    # simple threshold: top X% or distance > 3
    distances = np.array([s.distance for s in states], dtype=float)
    if len(distances) == 0:
        return []
    thresh = max(3.0, float(np.percentile(distances, 90)))

    anoms: List[Anomaly] = []
    for s in states:
        if s.distance < thresh:
            continue
        desc = (
            f"Multivariate joint anomaly on {s.date}: "
            f"Mahalanobis distance={s.distance:.2f} across climate/market/astronomy features."
        )
        anoms.append(
            Anomaly(
                domain="system",
                source="multivar",
                description=desc,
                score=float(s.distance),
                metadata={
                    "date": s.date,
                    "distance": s.distance,
                    "features": s.features,
                    "series": "multivariate_state",
                },
            )
        )
    return anoms


# ---------- PIPELINE STAGE ----------

def aurora_analyzer_stage(ctx: Dict[str, Any]) -> Dict[str, Any]:
    """
    Process RawDataset objects from multiple domains using
    robust statistical / ML / time-series models, then store anomalies.
    """
    raw_datasets: List[RawDataset] = ctx.get("raw_datasets", [])
    all_anomalies: List[Anomaly] = []

    for ds in raw_datasets:
        if ds.domain == "climate" and ds.source == "open_meteo":
            all_anomalies.extend(_detect_climate_anomalies(ds))
        elif ds.domain == "market" and ds.source == "coingecko":
            all_anomalies.extend(_detect_market_anomalies(ds))
        elif ds.domain == "astronomy" and ds.source == "nasa_apod":
            all_anomalies.extend(_detect_apod_anomalies(ds))

    # Multivariate joint anomalies across domains
    all_anomalies.extend(_detect_multivariate_anomalies(raw_datasets))

    logger.info(
        "Aurora analyzer produced %d anomalies across %d datasets.",
        len(all_anomalies),
        len(raw_datasets),
    )

    if all_anomalies:
        rows = []
        for a in all_anomalies:
            rows.append(
                {
                    "domain": a.domain,
                    "source": a.source,
                    "description": a.description,
                    "score": a.score,
                    "metadata_json": json.dumps(a.metadata),
                }
            )
        insert_aurora_anomalies(rows)

    ctx["anomalies"] = all_anomalies
    return ctx
