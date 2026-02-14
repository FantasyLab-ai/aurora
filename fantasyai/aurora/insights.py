# fantasyai/aurora/insights.py
from __future__ import annotations
from dataclasses import dataclass
from typing import Dict, Any, List, Tuple
import json
import logging

import numpy as np

from .data_collector import RawDataset
from .analyzer import Anomaly
from ..db.storage import insert_aurora_insights

logger = logging.getLogger(__name__)


@dataclass
class Insight:
    domain: str          # "climate", "market", "astronomy"
    insight_key: str     # e.g. "climate:Nashville", "market:BTC", "astronomy:APOD"
    label: str           # human-readable label
    metrics: Dict[str, Any]


# ---------- CLIMATE INSIGHTS ----------

def _build_climate_insights(
    raw_datasets: List[RawDataset],
    anomalies: List[Anomaly],
) -> List[Insight]:
    """
    Build per-location climate risk indices + cross-location correlations.
    """

    # Map datasets by location
    climate_datasets: Dict[str, RawDataset] = {}
    for ds in raw_datasets:
        if ds.domain != "climate":
            continue
        loc = ds.meta.get("location_name", "unknown")
        climate_datasets[loc] = ds

    # Group anomalies by location
    anomalies_by_loc: Dict[str, List[Anomaly]] = {}
    for a in anomalies:
        if a.domain != "climate":
            continue
        loc = a.metadata.get("location_name", "unknown")
        anomalies_by_loc.setdefault(loc, []).append(a)

    insights: List[Insight] = []

    # 1) Per-location risk index
    for loc, ds in climate_datasets.items():
        payload = ds.payload
        times = payload.get("time", [])
        temps = payload.get("temperature_2m_max", [])
        n_days = len(times)

        loc_anoms = anomalies_by_loc.get(loc, [])
        n_anoms = len(loc_anoms)
        avg_score = float(
            sum(a.score for a in loc_anoms) / n_anoms
        ) if n_anoms > 0 else 0.0
        max_score = float(max((a.score for a in loc_anoms), default=0.0))

        # Simple risk index: frequency * (avg_severity + max_severity) normalized
        freq = n_anoms / n_days if n_days > 0 else 0.0
        risk_index = freq * (0.5 * avg_score + 0.5 * max_score)

        metrics = {
            "location_name": loc,
            "num_days": n_days,
            "num_anomalies": n_anoms,
            "avg_score": avg_score,
            "max_score": max_score,
            "frequency": freq,
            "risk_index": risk_index,
        }

        insights.append(
            Insight(
                domain="climate",
                insight_key=f"climate:{loc}",
                label=f"Climate risk – {loc}",
                metrics=metrics,
            )
        )

    # 2) Cross-location correlation (based on temp series)
    loc_names = sorted(climate_datasets.keys())
    if len(loc_names) >= 2:
        # assume same number of points per dataset for now
        temp_matrix = []
        for loc in loc_names:
            ds = climate_datasets[loc]
            temps = ds.payload.get("temperature_2m_max", [])
            temp_matrix.append(np.asarray(temps, dtype=float))

        try:
            arr = np.vstack(temp_matrix)
            corr = np.corrcoef(arr)
            corr_list = corr.tolist()
            metrics = {
                "locations": loc_names,
                "temperature_correlation_matrix": corr_list,
            }
            insights.append(
                Insight(
                    domain="climate",
                    insight_key="climate:cross_location_correlation",
                    label="Climate cross-location temperature correlations",
                    metrics=metrics,
                )
            )
        except Exception as e:
            logger.warning("Failed to compute climate correlation matrix: %s", e)

    return insights


# ---------- MARKET INSIGHTS ----------

def _compute_returns(prices: List[float]) -> np.ndarray:
    arr = np.asarray(prices, dtype=float)
    if len(arr) < 2:
        return np.array([], dtype=float)
    log_prices = np.log(arr)
    returns = np.diff(log_prices)
    return returns


def _build_market_insights(
    raw_datasets: List[RawDataset],
    anomalies: List[Anomaly],
) -> List[Insight]:
    """
    Build per-asset risk indices + cross-asset return correlations.
    """
    market_datasets: Dict[str, RawDataset] = {}
    for ds in raw_datasets:
        if ds.domain != "market":
            continue
        coin = ds.meta.get("coin_id", "unknown")
        market_datasets[coin] = ds

    anomalies_by_coin: Dict[str, List[Anomaly]] = {}
    for a in anomalies:
        if a.domain != "market":
            continue
        coin = a.metadata.get("coin_id", "unknown")
        anomalies_by_coin.setdefault(coin, []).append(a)

    insights: List[Insight] = []

    # 1) Per-asset risk index & volatility
    for coin, ds in market_datasets.items():
        payload = ds.payload
        times = payload.get("time", [])
        prices = payload.get("price", [])
        n_points = len(times)

        ret = _compute_returns(prices)
        realized_vol = float(np.std(ret)) if len(ret) > 1 else 0.0

        coin_anoms = anomalies_by_coin.get(coin, [])
        n_anoms = len(coin_anoms)
        avg_score = float(
            sum(a.score for a in coin_anoms) / n_anoms
        ) if n_anoms > 0 else 0.0
        max_score = float(max((a.score for a in coin_anoms), default=0.0))

        # risk index ~ anomaly frequency * severity * volatility
        freq = n_anoms / n_points if n_points > 0 else 0.0
        risk_index = freq * (0.4 * avg_score + 0.6 * max_score) * (1.0 + realized_vol)

        metrics = {
            "coin_id": coin,
            "vs_currency": ds.meta.get("vs_currency", "usd"),
            "num_points": n_points,
            "num_anomalies": n_anoms,
            "avg_score": avg_score,
            "max_score": max_score,
            "frequency": freq,
            "realized_volatility": realized_vol,
            "risk_index": risk_index,
        }

        insights.append(
            Insight(
                domain="market",
                insight_key=f"market:{coin}",
                label=f"Market risk – {coin.upper()}",
                metrics=metrics,
            )
        )

    # 2) Cross-asset return correlations
    coins = sorted(market_datasets.keys())
    if len(coins) >= 2:
        returns_matrix = []
        min_len = None
        for coin in coins:
            ds = market_datasets[coin]
            prices = ds.payload.get("price", [])
            ret = _compute_returns(prices)
            if len(ret) == 0:
                continue
            if min_len is None or len(ret) < min_len:
                min_len = len(ret)
            returns_matrix.append((coin, ret))

        if min_len and len(returns_matrix) >= 2:
            try:
                # align lengths
                aligned = []
                labels = []
                for coin, r in returns_matrix:
                    aligned.append(r[-min_len:])
                    labels.append(coin)
                arr = np.vstack(aligned)
                corr = np.corrcoef(arr)
                metrics = {
                    "assets": labels,
                    "return_correlation_matrix": corr.tolist(),
                }
                insights.append(
                    Insight(
                        domain="market",
                        insight_key="market:return_correlations",
                        label="Market cross-asset return correlations",
                        metrics=metrics,
                    )
                )
            except Exception as e:
                logger.warning("Failed to compute market return correlation matrix: %s", e)

    return insights


# ---------- ASTRONOMY INSIGHTS ----------

def _build_astronomy_insights(
    raw_datasets: List[RawDataset],
    anomalies: List[Anomaly],
) -> List[Insight]:
    """
    Aurora Astronomy: summarize APOD anomaly patterns into an 'event intensity'
    style metric. You could later plug in other astronomy datasets here.
    """
    insights: List[Insight] = []

    # Count anomalies by reason
    reason_counts: Dict[str, int] = {}
    for a in anomalies:
        if a.domain != "astronomy":
            continue
        reason = a.metadata.get("reason", "unknown")
        reason_counts[reason] = reason_counts.get(reason, 0) + 1

    # Count total APOD entries in the latest dataset
    total_entries = 0
    for ds in raw_datasets:
        if ds.domain == "astronomy" and ds.source == "nasa_apod":
            entries = ds.payload.get("entries", [])
            total_entries += len(entries)

    intensity = 0.0
    if total_entries > 0:
        # simple fraction of "weird" events
        weird_events = sum(reason_counts.values())
        intensity = weird_events / total_entries

    metrics = {
        "total_entries": total_entries,
        "reason_counts": reason_counts,
        "event_intensity": intensity,
    }

    insights.append(
        Insight(
            domain="astronomy",
            insight_key="astronomy:apod_events",
            label="Astronomy – APOD event intensity",
            metrics=metrics,
        )
    )

    return insights


# ---------- PIPELINE STAGE ----------

def aurora_insights_stage(ctx: Dict[str, Any]) -> Dict[str, Any]:
    """
    Consume raw_datasets + anomalies, produce higher-level Insights across all
    domains and persist them.
    """
    raw_datasets: List[RawDataset] = ctx.get("raw_datasets", [])
    anomalies: List[Anomaly] = ctx.get("anomalies", [])

    climate_insights = _build_climate_insights(raw_datasets, anomalies)
    market_insights = _build_market_insights(raw_datasets, anomalies)
    astronomy_insights = _build_astronomy_insights(raw_datasets, anomalies)

    all_insights: List[Insight] = (
        climate_insights + market_insights + astronomy_insights
    )

    if all_insights:
        rows = []
        for ins in all_insights:
            rows.append(
                {
                    "domain": ins.domain,
                    "insight_key": ins.insight_key,
                    "label": ins.label,
                    "metrics_json": json.dumps(ins.metrics),
                }
            )
        insert_aurora_insights(rows)

    ctx["insights"] = all_insights
    logger.info("Aurora insights stage produced %d insights.", len(all_insights))
    return ctx
