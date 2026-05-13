"""
Cross-tier replication status.

Given two tier outputs (e.g., T1 + T2), compute per-finding replication
status: confirmed | strengthened | weakened | contradicted | novel | pending.

Operates on the assembled-state shape (the dict returned by
``state_builder.build_state``). Compares anomalies, regimes, motifs, the
discovered law, and the headline forecast.

Threshold bands (locked in v1):
    confirmed     — effect size delta within ±20%
    strengthened  — delta > +20%
    weakened      — delta in (-80%, -20%]  (kept, de-emphasised)
    contradicted  — delta ≤ -80% OR sign flipped
    novel         — only seen in the higher tier
    pending       — only seen in lower tier (waiting for higher tier)
"""
from __future__ import annotations

import math
import time
from typing import Any, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Band thresholds (per the spec)
# ---------------------------------------------------------------------------

CONFIRMED_BAND = 0.20    # ±20%
WEAKENED_FLOOR = 0.20    # (-80%, -20%]
CONTRADICTED_FLOOR = 0.80


def _delta_status(prev: Optional[float], curr: Optional[float]) -> str:
    """Compare two effect sizes; return status string."""
    if prev is None and curr is None:
        return "pending"
    if prev is None:
        return "novel"
    if curr is None:
        return "weakened"
    if prev == 0 and curr == 0:
        return "confirmed"
    # Sign flip is contradicted regardless of magnitude.
    if (prev > 0) != (curr > 0):
        return "contradicted"
    base = max(abs(prev), 1e-9)
    rel = (curr - prev) / base
    if rel >= CONFIRMED_BAND:
        return "strengthened"
    if rel >= -CONFIRMED_BAND:
        return "confirmed"
    if rel >= -CONTRADICTED_FLOOR:
        return "weakened"
    return "contradicted"


def _delta_summary(prev: Optional[float], curr: Optional[float],
                    metric: str = "effect") -> str:
    if prev is None and curr is not None:
        return f"first seen at higher tier ({metric}={_fmt(curr)})"
    if curr is None and prev is not None:
        return f"absent at higher tier ({metric} was {_fmt(prev)})"
    if prev is None or curr is None:
        return ""
    return f"{metric} {_fmt(prev)} → {_fmt(curr)}"


def _fmt(v: Optional[float]) -> str:
    if v is None:
        return "—"
    if isinstance(v, (int,)) and not isinstance(v, bool):
        return str(v)
    try:
        return f"{float(v):.2f}"
    except Exception:
        return str(v)


# ---------------------------------------------------------------------------
# Per-finding-type effect-size extractors
# ---------------------------------------------------------------------------

def _key_anomaly(a: Dict[str, Any]) -> str:
    return f"anom::{a.get('column','?')}::{a.get('when','?')}"


def _effect_anomaly(a: Dict[str, Any]) -> Optional[float]:
    z = a.get("z")
    if isinstance(z, (int, float)):
        return abs(float(z))
    s = a.get("score")
    return float(s) if isinstance(s, (int, float)) else None


def _key_regime(r: Dict[str, Any]) -> str:
    return f"reg::{r.get('label','?')}::{r.get('start','?')}::{r.get('end','?')}"


def _effect_regime(r: Dict[str, Any]) -> Optional[float]:
    # std of the regime is a stand-in for "effect size".
    return float(r["std"]) if isinstance(r.get("std"), (int, float)) else None


def _key_motif(m: Dict[str, Any]) -> str:
    return f"motif::{m.get('kind','?')}::{m.get('name','?')}"


def _effect_motif(m: Dict[str, Any]) -> Optional[float]:
    s = m.get("score")
    return float(s) if isinstance(s, (int, float)) else None


def _key_finding(f: Dict[str, Any]) -> str:
    return f"find::{f.get('method','?')}::{(f.get('title') or '')[:80]}"


def _effect_finding(f: Dict[str, Any]) -> Optional[float]:
    z = f.get("z")
    if isinstance(z, (int, float)):
        return abs(float(z))
    c = f.get("confidence")
    return float(c) if isinstance(c, (int, float)) else None


# ---------------------------------------------------------------------------
# Public: attach replication status across tiers
# ---------------------------------------------------------------------------

def attach_replication(state_lower: Dict[str, Any],
                        state_higher: Dict[str, Any],
                        tier_lower: int,
                        tier_higher: int) -> None:
    """Walk ``state_higher`` and attach a ``replication_status`` block to
    each anomaly/regime/motif/finding by comparing to ``state_lower``.

    Mutates ``state_higher`` in place. Items present only in ``state_lower``
    are appended to ``state_higher`` as ``status=contradicted`` (so the
    user sees they didn't survive).
    """
    _attach_list(state_lower.get("anomalies") or [],
                 state_higher.setdefault("anomalies", []),
                 _key_anomaly, _effect_anomaly,
                 "z", tier_lower, tier_higher)
    _attach_list(state_lower.get("regimes") or [],
                 state_higher.setdefault("regimes", []),
                 _key_regime, _effect_regime,
                 "std", tier_lower, tier_higher)
    _attach_list(state_lower.get("motifs") or [],
                 state_higher.setdefault("motifs", []),
                 _key_motif, _effect_motif,
                 "score", tier_lower, tier_higher)
    _attach_list(state_lower.get("findings") or [],
                 state_higher.setdefault("findings", []),
                 _key_finding, _effect_finding,
                 "effect", tier_lower, tier_higher)

    # Aggregate blocks: forecast headline + physics law.
    _attach_aggregate(
        state_lower.get("forecast") or {},
        state_higher.get("forecast") or {},
        path_to_metric="headline_value",
        metric_label="peak",
        tier_lower=tier_lower, tier_higher=tier_higher,
    )
    _attach_aggregate(
        (state_lower.get("physics") or {}).get("discovered_law") or {},
        (state_higher.get("physics") or {}).get("discovered_law") or {},
        path_to_metric="rmse",
        metric_label="rmse",
        tier_lower=tier_lower, tier_higher=tier_higher,
        # Lower RMSE is BETTER, so flip semantics: a negative delta = strengthened.
        invert_direction=True,
    )

    # Aggregate replication summary on the state.
    _summarise(state_higher, tier_higher)


def _attach_list(prev_items: List[Dict[str, Any]],
                  curr_items: List[Dict[str, Any]],
                  key_fn,
                  effect_fn,
                  metric_label: str,
                  tier_lower: int,
                  tier_higher: int) -> None:
    """Match items by key; tag each with a replication_status block."""
    prev_by_key = {key_fn(x): x for x in prev_items}
    seen_keys: set = set()
    for item in curr_items:
        k = key_fn(item)
        seen_keys.add(k)
        prev = prev_by_key.get(k)
        e_prev = effect_fn(prev) if prev else None
        e_curr = effect_fn(item)
        status = _delta_status(e_prev, e_curr)
        item["replication_status"] = {
            "schema": "replication_v1",
            "first_seen_tier": (tier_lower if prev else tier_higher),
            "current_tier": tier_higher,
            "status": status,
            "delta_summary": _delta_summary(e_prev, e_curr, metric_label),
            "history": [
                {"tier": tier_lower, "value": e_prev},
                {"tier": tier_higher, "value": e_curr},
            ],
        }
    # Items in lower tier but absent from higher → append as contradicted.
    for k, prev in prev_by_key.items():
        if k in seen_keys:
            continue
        ghost = dict(prev)   # shallow copy
        ghost["replication_status"] = {
            "schema": "replication_v1",
            "first_seen_tier": tier_lower,
            "current_tier": tier_higher,
            "status": "contradicted",
            "delta_summary": (f"present at tier {tier_lower} ({metric_label}="
                              f"{_fmt(effect_fn(prev))}) but absent at tier "
                              f"{tier_higher}"),
            "history": [
                {"tier": tier_lower, "value": effect_fn(prev)},
                {"tier": tier_higher, "value": None},
            ],
        }
        curr_items.append(ghost)


def _attach_aggregate(prev: Dict[str, Any], curr: Dict[str, Any],
                      *, path_to_metric: str, metric_label: str,
                      tier_lower: int, tier_higher: int,
                      invert_direction: bool = False) -> None:
    if not curr:
        return
    p = prev.get(path_to_metric) if isinstance(prev, dict) else None
    c = curr.get(path_to_metric)
    if invert_direction and isinstance(p, (int, float)) and isinstance(c, (int, float)):
        # For RMSE-like metrics, lower is better; we compare on the inverted
        # signal so "strengthened" still means "the model got better".
        status = _delta_status(-float(p), -float(c))
    else:
        status = _delta_status(p, c)
    curr["replication_status"] = {
        "schema": "replication_v1",
        "first_seen_tier": (tier_lower if p is not None else tier_higher),
        "current_tier": tier_higher,
        "status": status,
        "delta_summary": _delta_summary(p, c, metric_label),
        "history": [
            {"tier": tier_lower, "value": p},
            {"tier": tier_higher, "value": c},
        ],
    }


def _summarise(state: Dict[str, Any], current_tier: int) -> None:
    """Roll the per-item replication into a single summary on the state."""
    counts = {"confirmed": 0, "strengthened": 0, "novel": 0, "pending": 0,
               "weakened": 0, "contradicted": 0}
    total = 0
    for bucket in ("anomalies", "regimes", "motifs", "findings"):
        for item in state.get(bucket) or []:
            r = (item or {}).get("replication_status")
            if not r:
                continue
            total += 1
            counts[r.get("status", "pending")] = counts.get(r.get("status"), 0) + 1
    replicated = counts["confirmed"] + counts["strengthened"]
    state["replication_summary"] = {
        "schema": "replication_summary_v1",
        "current_tier": current_tier,
        "total_items": total,
        "n_replicated": replicated,
        "fraction_replicated": (replicated / total) if total else 0.0,
        "counts": counts,
        "headline":
            (f"{replicated} of {total} findings replicated at tier "
             f"{current_tier}" if total else
             f"no findings to compare at tier {current_tier}"),
    }
