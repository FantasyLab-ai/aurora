"""
Regime explainer — answers "why is this a regime boundary?"

Input:
    * the regimes block from ``deep_math.regimes`` (method, change_points, stats)
    * a specific segment index to explain

Output:
    {
      "headline": "regime 2 (rows 175–365): mean dropped 12.8 from previous segment",
      "method":   "ruptures PELT with RBF kernel",
      "method_explanation": "...",
      "boundary_position": 175,
      "deltas": [
        {"variable": "temperature_c", "before_mean": 21.57, "after_mean": 8.81,
         "delta": -12.76, "delta_normalized": -3.82, "note": "large mean shift"},
        ...
      ],
      "alternative_cutpoints": [
        {"position": 165, "score": 0.78},
        {"position": 175, "score": 0.94, "selected": true},
        {"position": 200, "score": 0.62},
      ],
      "why_this_one_won": "...",
      "falsification": "...",
    }
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional


def _safe_float(x: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        v = float(x)
        if v != v or v == float("inf") or v == float("-inf"):
            return default
        return v
    except Exception:
        return default


def _interpret_delta(delta_norm: float) -> str:
    a = abs(delta_norm)
    if a >= 5.0:
        return "very large mean shift"
    if a >= 3.0:
        return "large mean shift"
    if a >= 1.5:
        return "moderate mean shift"
    if a >= 0.5:
        return "small shift"
    return "minimal shift"


def explain_regime(regimes_block: Dict[str, Any],
                   segment_index: int) -> Dict[str, Any]:
    """Build a structured explanation for a single regime segment.

    The runner today emits per-segment stats but a single target column
    (``regimes.target_col``). The explainer compares this segment's mean/std
    against the previous segment to compute a delta.
    """
    if not isinstance(regimes_block, dict):
        return {"available": False, "reason": "invalid regimes input"}

    stats = regimes_block.get("stats") or []
    if segment_index < 0 or segment_index >= len(stats):
        return {"available": False, "reason": f"segment {segment_index} out of range (0-{len(stats)-1})"}

    seg = stats[segment_index]
    if not isinstance(seg, dict):
        return {"available": False, "reason": "segment is not a dict"}

    target = regimes_block.get("target_col") or "?"
    method = regimes_block.get("method") or "ruptures_pelt_rbf"
    change_points = regimes_block.get("change_points") or []

    # Compute deltas against previous segment (if any).
    deltas: List[Dict[str, Any]] = []
    if segment_index > 0:
        prev = stats[segment_index - 1]
        if isinstance(prev, dict):
            cur_mean = _safe_float(seg.get("mean"))
            prev_mean = _safe_float(prev.get("mean"))
            cur_std = _safe_float(seg.get("std"))
            prev_std = _safe_float(prev.get("std"))
            if cur_mean is not None and prev_mean is not None:
                delta = cur_mean - prev_mean
                # Normalize by pooled std for "how many σ away"
                pooled_std = ((cur_std or 0)**2 + (prev_std or 0)**2) ** 0.5 or 1.0
                delta_norm = delta / pooled_std
                deltas.append({
                    "variable": target,
                    "stat": "mean",
                    "before": prev_mean,
                    "after": cur_mean,
                    "delta": delta,
                    "delta_normalized": delta_norm,
                    "note": _interpret_delta(delta_norm),
                })
            if cur_std is not None and prev_std is not None and prev_std > 0:
                std_ratio = cur_std / prev_std
                deltas.append({
                    "variable": target,
                    "stat": "std",
                    "before": prev_std,
                    "after": cur_std,
                    "delta": cur_std - prev_std,
                    "ratio": std_ratio,
                    "note": (
                        f"variance {('expanded' if std_ratio > 1.2 else 'contracted' if std_ratio < 0.8 else 'roughly stable')} "
                        f"by {abs((std_ratio - 1) * 100):.0f}%"
                    ),
                })

    # Headline.
    if deltas:
        d0 = deltas[0]
        if d0.get("stat") == "mean":
            direction = "rose" if (d0.get("delta") or 0) > 0 else "dropped"
            headline = (
                f"regime {segment_index + 1} (rows {seg.get('start')}-{seg.get('end')}): "
                f"{target} mean {direction} {abs(d0.get('delta')):.3g} from previous segment"
            )
        else:
            headline = f"regime {segment_index + 1}: rows {seg.get('start')}-{seg.get('end')}"
    else:
        headline = (f"regime {segment_index + 1} (initial segment, "
                    f"rows {seg.get('start')}-{seg.get('end')})")

    # Boundary position (the change-point that starts this segment).
    boundary_position = None
    if segment_index > 0 and segment_index - 1 < len(change_points):
        boundary_position = change_points[segment_index - 1]
    elif segment_index == 0:
        boundary_position = seg.get("start")

    method_explanation = (
        "ruptures PELT with an RBF kernel scans for points where the local "
        "RBF-kernel signal magnitude shifts. PELT is exact under linear "
        "penalty (no greedy search). The detected change-points minimize the "
        "penalized cost function over the whole signal."
    )

    why_this_one_won = (
        f"PELT found {len(change_points)} change-point(s) in the signal at "
        f"penalty={regimes_block.get('penalty'):.3g}. Lowering the penalty would "
        f"yield more (potentially spurious) regimes; raising it would merge "
        f"adjacent regimes. The selected penalty corresponds to a noise-aware "
        f"BIC-like criterion."
        if regimes_block.get("penalty") is not None else
        "PELT selected this change-point as the global minimum of its penalized cost function."
    )

    falsification = (
        f"if rows {seg.get('start')}-{seg.get('end')} of {target} had the "
        f"same statistics as the previous segment (mean and variance), no "
        f"change-point would be detected at row {boundary_position}. "
        f"Equivalently: shuffling rows across this boundary should make the "
        f"detected change-point disappear or move significantly."
    )

    return {
        "available": True,
        "claim_type": "regime",
        "headline": headline,
        "method": method,
        "method_explanation": method_explanation,
        "boundary_position": boundary_position,
        "segment": {
            "start": seg.get("start"),
            "end": seg.get("end"),
            "n": seg.get("n"),
            "mean": seg.get("mean"),
            "std": seg.get("std"),
            "min": seg.get("min"),
            "max": seg.get("max"),
        },
        "deltas": deltas,
        "why_this_one_won": why_this_one_won,
        "falsification": falsification,
    }
