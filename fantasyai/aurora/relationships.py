# fantasyai/aurora/relationships.py
from __future__ import annotations
from dataclasses import dataclass
from typing import List, Dict, Any, Tuple, Sequence, Optional

import numpy as np
from scipy.stats import pearsonr


@dataclass
class Relationship:
    source: str
    target: str
    lag: int  # positive lag means source leads target by 'lag' steps
    corr: float
    p_value: float
    category: str  # "strong", "medium", "weak"
    direction: str  # "positive" / "negative"


@dataclass
class RelationshipGraph:
    features: List[str]
    relationships: List[Relationship]
    max_lag: int


def _lagged_corr(
    a: np.ndarray,
    b: np.ndarray,
    lag: int,
) -> Tuple[float, float]:
    """
    Compute Pearson correlation between a(t) and b(t+lag).
    lag > 0: a leads b
    lag < 0: b leads a
    Returns (corr, p_value). If insufficient data, returns (0.0, 1.0).
    """
    if lag == 0:
        x = a
        y = b
    elif lag > 0:
        x = a[:-lag]
        y = b[lag:]
    else:  # lag < 0
        lag_abs = -lag
        x = a[lag_abs:]
        y = b[:-lag_abs]

    if len(x) < 5:
        return 0.0, 1.0

    if np.allclose(x, x[0]) or np.allclose(y, y[0]):
        return 0.0, 1.0

    try:
        corr, p = pearsonr(x, y)
        if np.isnan(corr) or np.isnan(p):
            return 0.0, 1.0
        return float(corr), float(p)
    except Exception:
        return 0.0, 1.0


def _score_strength(corr: float, p_value: float) -> str:
    abs_c = abs(corr)
    if p_value < 0.01 and abs_c >= 0.7:
        return "strong"
    if p_value < 0.05 and abs_c >= 0.4:
        return "medium"
    if p_value < 0.1 and abs_c >= 0.25:
        return "weak"
    return "none"


def compute_relationship_graph(
    feature_names: List[str],
    X: np.ndarray,
    max_lag: int = 5,
    min_strength: str = "weak",
    max_pairs: int = 200,
) -> RelationshipGraph:
    """
    Build a cross-domain relationship graph:
      - For each pair of features (i, j):
        - Scan lags in [-max_lag, ..., +max_lag]
        - Find lag with highest |corr|
        - Keep relationships with at least 'min_strength'
    """
    T, D = X.shape
    if D < 2:
        return RelationshipGraph(features=feature_names, relationships=[], max_lag=max_lag)

    strength_order = {"none": 0, "weak": 1, "medium": 2, "strong": 3}
    min_strength_level = strength_order.get(min_strength, 1)

    rels: List[Relationship] = []

    for i in range(D):
        for j in range(D):
            if i == j:
                continue

            a = X[:, i]
            b = X[:, j]

            best_corr = 0.0
            best_p = 1.0
            best_lag = 0

            for lag in range(-max_lag, max_lag + 1):
                corr, p = _lagged_corr(a, b, lag)
                if abs(corr) > abs(best_corr):
                    best_corr = corr
                    best_p = p
                    best_lag = lag

            strength = _score_strength(best_corr, best_p)
            if strength_order[strength] < min_strength_level:
                continue

            direction = "positive" if best_corr >= 0 else "negative"

            rels.append(
                Relationship(
                    source=feature_names[i],
                    target=feature_names[j],
                    lag=best_lag,
                    corr=best_corr,
                    p_value=best_p,
                    category=strength,
                    direction=direction,
                )
            )

    # Limit number of relationships for sanity: sort by |corr|
    rels.sort(key=lambda r: abs(r.corr), reverse=True)
    if len(rels) > max_pairs:
        rels = rels[:max_pairs]

    return RelationshipGraph(
        features=feature_names,
        relationships=rels,
        max_lag=max_lag,
    )


def summarize_relationships_by_domain(
    graph: RelationshipGraph,
) -> Dict[str, Any]:
    """
    Quick summary: how many cross-domain links, etc.
    We assume feature names like "domain:..." (e.g., "climate:Nashville:temp").
    """
    def get_domain(name: str) -> str:
        return name.split(":", 1)[0] if ":" in name else "unknown"

    domain_links: Dict[Tuple[str, str], int] = {}
    for r in graph.relationships:
        d_src = get_domain(r.source)
        d_tgt = get_domain(r.target)
        key = (d_src, d_tgt)
        domain_links[key] = domain_links.get(key, 0) + 1

    return {
        "num_relationships": len(graph.relationships),
        "domain_pairs_counts": [
            {"source_domain": k[0], "target_domain": k[1], "count": v}
            for k, v in sorted(domain_links.items(), key=lambda kv: kv[1], reverse=True)
        ],
    }
