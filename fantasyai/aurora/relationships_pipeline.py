# fantasyai/aurora/relationships_pipeline.py
from __future__ import annotations
from typing import Dict, Any

from .data_collector import RawDataset
from .multivar import build_daily_feature_matrix
from .relationships import compute_relationship_graph, summarize_relationships_by_domain, RelationshipGraph


def build_relationship_graph_from_raw(
    raw_datasets: list[RawDataset],
    max_lag: int = 5,
    min_strength: str = "weak",
) -> Dict[str, Any]:
    dates, X, feature_names = build_daily_feature_matrix(raw_datasets)
    if X.size == 0:
        return {"error": "no_data"}

    graph: RelationshipGraph = compute_relationship_graph(
        feature_names=feature_names,
        X=X,
        max_lag=max_lag,
        min_strength=min_strength,
    )

    summary = summarize_relationships_by_domain(graph)

    return {
        "dates_span": (dates[0], dates[-1]),
        "num_days": len(dates),
        "num_features": len(feature_names),
        "relationship_graph": graph,
        "summary": summary,
    }
