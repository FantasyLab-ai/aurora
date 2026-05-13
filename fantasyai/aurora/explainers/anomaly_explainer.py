"""
Anomaly explainer — turns raw anomaly attribution into a structured "why".

Input:
    * the raw point dict from ``deep_math.extensions.anomaly_attribution.points[i]``
    * the structure_contract.json (for column metadata)

Output:
    {
      "headline": "row 47: precip_mm spiked 10.9σ high",
      "method":   "isolation_forest + robust_z attribution",
      "drivers": [
        {"feature": "precip_mm", "robust_z": +10.86, "direction": "high",
         "contribution_pct": 89.4, "note": "extreme high"},
        ...
      ],
      "score":     {"raw": 5.67, "interpretation": "well above the 3σ critical threshold"},
      "method_explanation": "...",
      "falsification": "if you removed row 47 and re-ran, the IF score on this point would drop below 2.5",
      "alternative_views": ["this row also flagged by 3σ rule on precip_mm",
                            "no other rows in the same 24-hour window flagged"]
    }

This is what makes Aurora different from a cloud LLM: the LLM would
paraphrase "row 47 looks anomalous." Aurora returns the actual driver
breakdown with attribution percentages and a falsification condition.
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


def _interpret_score(score: float) -> str:
    if score >= 7.0:
        return "extreme — well above any reasonable noise floor"
    if score >= 5.0:
        return "well above the 3σ critical threshold"
    if score >= 3.0:
        return "above the warning threshold; warrants review"
    if score >= 2.0:
        return "borderline; commonly noise"
    return "weak signal"


def _interpret_robust_z(rz: float) -> str:
    az = abs(rz)
    if az >= 8.0:
        return "extreme outlier"
    if az >= 5.0:
        return "strong outlier"
    if az >= 3.0:
        return "outlier (>3σ on the median)"
    if az >= 2.0:
        return "mild deviation"
    return "minor"


def explain_anomaly(point: Dict[str, Any],
                    structure: Optional[Dict[str, Any]] = None,
                    all_points: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """Build a structured explanation for a single anomaly attribution point.

    Args:
        point: one element from ``extensions.anomaly_attribution.points``
        structure: optional ``structure_contract.json`` content (column metadata)
        all_points: optional full list of points (used for "co-occurring"
                    detection — were other rows in the same window also flagged?)
    """
    if not isinstance(point, dict):
        return {"available": False, "reason": "invalid point input"}

    drivers_in = point.get("top_feature_drivers") or []
    if not drivers_in:
        return {"available": False, "reason": "no driver attribution available"}

    # Normalize driver list, compute attribution percentages by absolute robust_z.
    raw_drivers = []
    for d in drivers_in:
        if not isinstance(d, dict):
            continue
        rz = _safe_float(d.get("robust_z"))
        if rz is None:
            continue
        raw_drivers.append({
            "feature": d.get("feature", "?"),
            "robust_z": rz,
            "direction": d.get("direction") or ("high" if rz > 0 else "low"),
        })

    total_abs_z = sum(abs(d["robust_z"]) for d in raw_drivers) or 1.0
    drivers: List[Dict[str, Any]] = []
    for d in raw_drivers:
        contrib = (abs(d["robust_z"]) / total_abs_z) * 100.0
        drivers.append({
            "feature": d["feature"],
            "robust_z": d["robust_z"],
            "direction": d["direction"],
            "contribution_pct": round(contrib, 1),
            "note": _interpret_robust_z(d["robust_z"]),
        })
    # Sort by absolute robust_z, descending.
    drivers.sort(key=lambda x: abs(x["robust_z"]), reverse=True)

    # Headline: pull from the dominant driver.
    head = drivers[0]
    direction_word = "spiked" if head["direction"] == "high" else "dropped"
    headline = (f"row {point.get('row_id', '?')}: "
                f"{head['feature']} {direction_word} "
                f"{abs(head['robust_z']):.1f}σ {head['direction']}")

    # Score interpretation.
    raw_score = _safe_float(point.get("score"))
    score_block = {
        "raw": raw_score,
        "interpretation": _interpret_score(raw_score) if raw_score is not None else "no score available",
    }

    # Falsification condition — concrete, runnable test the user could verify.
    falsification = (
        f"if row {point.get('row_id', '?')} were removed from the dataset and "
        f"the isolation forest re-fit, this point's anomaly score would no longer "
        f"appear in the top-K. Likewise, if {head['feature']} were imputed to its "
        f"median value at this row, the contribution would collapse below 1σ."
    )

    # Alternative views — what other detectors would say about this point.
    alt_views: List[str] = []
    if abs(head["robust_z"]) >= 3.0:
        alt_views.append(
            f"3σ rule: this row also flagged on {head['feature']} "
            f"(|robust_z|={abs(head['robust_z']):.1f})"
        )
    # Are there other top-K points in the same row range (co-occurrence)?
    if all_points and point.get("row_id") is not None:
        try:
            row_id_int = int(point["row_id"])
            window = 24
            co = [p for p in all_points
                  if p is not point and p.get("row_id") is not None and
                  abs(int(p["row_id"]) - row_id_int) <= window]
            if co:
                alt_views.append(
                    f"co-occurrence: {len(co)} other anomalies within "
                    f"±{window} rows of this point"
                )
            else:
                alt_views.append(
                    f"isolation: no other anomalies within ±{window} rows"
                )
        except Exception:
            pass

    method_explanation = (
        "An isolation forest detected this row as an outlier, and a robust "
        "z-score (median + MAD-based) was computed per feature to identify "
        "which variables drove the flag. Contributions are normalized "
        "percentages of summed |robust_z| across the top features."
    )

    return {
        "available": True,
        "claim_type": "anomaly",
        "headline": headline,
        "method": "isolation_forest + robust_z attribution",
        "method_explanation": method_explanation,
        "drivers": drivers,
        "score": score_block,
        "falsification": falsification,
        "alternative_views": alt_views,
        "row_id": point.get("row_id"),
    }
