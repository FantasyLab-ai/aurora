# fantasyai/aurora/storycards.py
from __future__ import annotations
from dataclasses import dataclass
from typing import List, Dict, Any, Optional, Tuple

import numpy as np

from .motifs import MotifLibrary, MotifInstance
from .relationships import RelationshipGraph
from .multivar import build_daily_feature_matrix
from .data_collector import RawDataset
from .motif_pipeline import build_aurora_motif_library
from .stat_models import robust_zscore_anomalies
from .relationships_pipeline import build_relationship_graph_from_raw
from ..fusion.motif_world_mapper import motif_to_world_constraints


@dataclass
class Storycard:
    id: str
    kind: str  # "motif" / "anomaly"
    title: str
    summary: str
    domains_involved: List[str]
    stats: Dict[str, Any]
    hooks: Dict[str, str]  # seeds for video / game / narrative
    extra: Dict[str, Any]


def _domain_from_feature(name: str) -> str:
    return name.split(":", 1)[0] if ":" in name else "unknown"


def _extract_motif_instances(
    lib: MotifLibrary,
    motif_id: int,
) -> List[MotifInstance]:
    return [inst for inst in lib.instances if inst.motif_id == motif_id]


def _compute_motif_feature_stats(
    lib: MotifLibrary,
    motif_id: int,
) -> Dict[str, Dict[str, float]]:
    """
    Aggregate mean/std over all instances of a motif across features.
    """
    feature_names = lib.feature_names
    if not feature_names:
        return {}

    instances = _extract_motif_instances(lib, motif_id)
    if not instances:
        return {}

    window = lib.window
    D = len(feature_names)
    # Collect all windows for this motif
    collected = []
    for inst in instances:
        vec = inst.features
        if vec.shape[0] != window * D:
            continue
        mat = vec.reshape(window, D)
        collected.append(mat)
    if not collected:
        return {}

    stacked = np.stack(collected, axis=0)  # [N, window, D]
    # Average over N*window
    flat = stacked.reshape(-1, D)
    stats: Dict[str, Dict[str, float]] = {}
    for j, fname in enumerate(feature_names):
        col = flat[:, j]
        stats[fname] = {
            "mean": float(col.mean()),
            "std": float(col.std()),
            "min": float(col.min()),
            "max": float(col.max()),
        }
    return stats


def _derive_domains_involved(stats: Dict[str, Dict[str, float]]) -> List[str]:
    doms = {_domain_from_feature(name) for name in stats.keys()}
    return sorted(list(doms))


def _short_summary_from_stats(
    motif_id: int,
    stats: Dict[str, Dict[str, float]],
) -> str:
    """
    Lightweight, non-LLM summary: detect a few obvious things.
    """
    # Example heuristics
    climate_spikes = []
    market_vols = []

    for name, s in stats.items():
        if name.startswith("climate:") and name.endswith(":temp"):
            if s["std"] > 5:  # arbitrary
                climate_spikes.append(name)
        if name.startswith("market:") and name.endswith(":return"):
            if s["std"] > 0.03:
                market_vols.append(name)

    parts = [f"Motif #{motif_id} captures a recurring multi-day pattern."]
    if climate_spikes:
        parts.append(f"It includes pronounced climate variability in {len(climate_spikes)} locations.")
    if market_vols:
        parts.append(f"It also includes elevated market volatility across {len(market_vols)} assets.")
    if not climate_spikes and not market_vols:
        parts.append("The pattern is subtle across climate and markets.")

    return " ".join(parts)


def _build_video_seed_from_motif(
    motif_id: int,
    domains: List[str],
) -> str:
    """
    Text seed you can feed into a Veo/Sora prompt builder.
    """
    dom_str = ", ".join(domains)
    return (
        f"Aurora motif #{motif_id} — a multi-domain pattern involving {dom_str}. "
        "Imagine a short cinematic sequence where visual elements representing these domains "
        "intertwine and change over several days, hinting at hidden connections."
    )


def _build_game_seed_from_motif(
    motif_id: int,
    domains: List[str],
) -> str:
    return (
        f"Use motif #{motif_id} as a blueprint for a game scenario: map each domain "
        f"({', '.join(domains)}) to a faction or resource and design a world where their "
        "interactions mirror the motif's ebb and flow."
    )


def build_motif_storycard(
    lib: MotifLibrary,
    motif_id: int,
    rel_graph: Optional[RelationshipGraph] = None,
) -> Storycard:
    stats = _compute_motif_feature_stats(lib, motif_id)
    domains = _derive_domains_involved(stats)
    summary = _short_summary_from_stats(motif_id, stats)

    extra: Dict[str, Any] = {
        "feature_stats_sample": {k: v for k, v in list(stats.items())[:10]},
    }
    if rel_graph is not None:
        extra["relationships_summary"] = {
            "num_relationships": len(rel_graph.relationships),
        }

    hooks = {
        "video_seed": _build_video_seed_from_motif(motif_id, domains),
        "game_seed": _build_game_seed_from_motif(motif_id, domains),
        "narrative_seed": (
            f"A recurring pattern (motif #{motif_id}) where phenomena from domains "
            f"{', '.join(domains)} align in non-obvious ways. Use this as the basis for "
            "a narrative about hidden forces tying these systems together."
        ),
    }

    return Storycard(
        id=f"motif_{motif_id}",
        kind="motif",
        title=f"Aurora Motif #{motif_id}",
        summary=summary,
        domains_involved=domains,
        stats={
            "num_features": len(stats),
            "window": lib.window,
            "num_instances": len(_extract_motif_instances(lib, motif_id)),
        },
        hooks=hooks,
        extra=extra,
    ).__dict__  # convert to dict for easier JSON usage


def build_latest_motif_storycard_with_world(
    raw_datasets: list[RawDataset],
    window: int = 7,
    n_motifs: int = 5,
) -> Dict[str, Any]:
    """
    End-to-end helper:
      - Build motif library
      - Build relationship graph
      - Find motif most associated with latest window
      - Build storycard
      - Map motif -> Mythos WorldConstraints (via fusion)
    """
    from .multivar import build_daily_feature_matrix
    from .motif_pipeline import build_aurora_motif_library
    from .relationships_pipeline import build_relationship_graph_from_raw
    from ..fusion.motif_world_pipeline import _select_top_motif_for_latest_window
    from ..fusion.motif_world_mapper import motif_to_world_constraints

    dates, X, feature_names = build_daily_feature_matrix(raw_datasets)
    if X.size == 0:
        return {"error": "no_data"}

    # Motif library
    lib = build_aurora_motif_library(raw_datasets, window=window, n_motifs=n_motifs)

    # Relationship graph
    rel_result = build_relationship_graph_from_raw(raw_datasets, max_lag=5, min_strength="weak")
    rel_graph = rel_result.get("relationship_graph")

    # Pick motif tied to latest window
    motif_id = _select_top_motif_for_latest_window(lib, dates, X)

    card = build_motif_storycard(lib, motif_id, rel_graph=rel_graph)

    # Map motif -> world constraints
    spec = motif_to_world_constraints(lib, motif_id)
    world_cons = spec.constraints.__dict__

    return {
        "motif_id": motif_id,
        "storycard": card,
        "world_constraints": world_cons,
        "motif_diagnostics": spec.diagnostics,
    }
