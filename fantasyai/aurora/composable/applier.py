"""
Apply a prior pack to the current run's findings.

The state_builder calls ``apply_prior_pack_to_findings`` after it
assembles findings but before they ship in the state response. We
walk every finding and decorate ones that align with a prior:

    finding["prior_source"] = {
        "run_id":   "<source run>",
        "kind":     "physics" | "regimes" | "baseline" | "anomaly_floor",
        "alignment": "matches" | "drifts" | "novel",
        "note":     "Logistic law refit; K within 8% of prior",
    }

The frontend reads ``prior_source`` and stamps a small PRIOR badge on
the card. The synthesis engine can also pick this up to mention
prior alignment in narrative ("…within 0.3 of the prior baseline").

Comparison policy:
    * **physics**:  same law name → "matches"; rmse within 2× and same
      law → "matches"; different law → "drifts".
    * **regimes**:  same K → "matches"; ±1 → "drifts"; otherwise novel.
    * **baseline**: target mean within (prior_span * 0.1) → "matches";
      within 0.3 → "drifts"; otherwise "novel".
    * **anomaly_floor**: not used for finding tagging — applied as a
      threshold the detector sees before running.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Dict, List, Optional


def _safe_float(x: Any) -> Optional[float]:
    try:
        v = float(x)
        if math.isnan(v) or math.isinf(v):
            return None
        return v
    except Exception:
        return None


def load_prior_pack(run_dir: Any) -> Optional[Dict[str, Any]]:
    """Read ``priors_pack.json`` from a run dir (or any path containing it).

    Returns None if the file's absent or malformed. The shape mirrors
    what ``PriorPack.to_dict()`` produces.
    """
    rd = Path(run_dir)
    candidates = [rd / "priors_pack.json", rd / "prior_pack.json"]
    for p in candidates:
        if not p.exists():
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8", errors="replace"))
            if isinstance(data, dict):
                return data
        except Exception:
            continue
    return None


# ----------------------------------------------------------------------
# Per-prior comparison logic
# ----------------------------------------------------------------------

def _compare_physics(finding: Dict[str, Any],
                       prior_physics: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not isinstance(prior_physics, dict):
        return None
    prior_law = str(prior_physics.get("law") or "").lower()
    if not prior_law:
        return None
    ev = finding.get("evidence") or {}
    # Aurora physics-discovery findings carry the matched law in the
    # title and in evidence; check both. Method name is `physics_discovery`.
    method = str(finding.get("method") or "").lower()
    title  = str(finding.get("title") or "").lower()
    cur_law = str(ev.get("law") or ev.get("best_law") or "").lower()
    if not cur_law and "physics" in method:
        # Best-effort: scrape the law name from the title.
        for cand in ("logistic", "linear_ode", "exponential",
                     "damped_oscillator", "sigmoid", "polynomial",
                     "power_law", "saturation"):
            if cand in title:
                cur_law = cand
                break
    if not cur_law:
        return None
    if cur_law == prior_law:
        prior_rmse = _safe_float(prior_physics.get("rmse"))
        cur_rmse   = _safe_float(ev.get("rmse") or ev.get("rmse_test"))
        note = "Same law as prior"
        if prior_rmse and cur_rmse:
            ratio = cur_rmse / prior_rmse if prior_rmse else 1.0
            if ratio <= 1.25:
                note = f"Same law as prior (RMSE {ratio:.2f}× prior)"
                alignment = "matches"
            elif ratio <= 2.0:
                note = f"Same law as prior but RMSE {ratio:.2f}× — drifting"
                alignment = "drifts"
            else:
                note = f"Same law as prior but RMSE {ratio:.2f}× — fit degraded"
                alignment = "drifts"
        else:
            alignment = "matches"
        return {"kind": "physics", "alignment": alignment, "note": note}
    return {
        "kind": "physics",
        "alignment": "novel",
        "note": f"Different law than prior ({prior_law} → {cur_law})",
    }


def _compare_regimes(finding: Dict[str, Any],
                      prior_regimes: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not isinstance(prior_regimes, dict):
        return None
    prior_k = prior_regimes.get("k")
    if prior_k is None:
        return None
    method = str(finding.get("method") or "").lower()
    if "regime" not in method and "hmm" not in method:
        return None
    ev = finding.get("evidence") or {}
    cur_k = ev.get("k") or ev.get("regime_count") or ev.get("best_K")
    if cur_k is None:
        return None
    try:
        diff = abs(int(cur_k) - int(prior_k))
    except Exception:
        return None
    if diff == 0:
        return {"kind": "regimes", "alignment": "matches",
                "note": f"K={cur_k} matches prior (K={prior_k})"}
    if diff <= 1:
        return {"kind": "regimes", "alignment": "drifts",
                "note": f"K={cur_k} drifted from prior (K={prior_k})"}
    return {"kind": "regimes", "alignment": "novel",
            "note": f"K={cur_k} differs sharply from prior (K={prior_k})"}


def _compare_baseline(finding: Dict[str, Any],
                       prior_baseline: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not isinstance(prior_baseline, dict):
        return None
    prior_col = str(prior_baseline.get("col") or "").lower()
    prior_min = _safe_float(prior_baseline.get("min"))
    prior_max = _safe_float(prior_baseline.get("max"))
    if not prior_col or prior_min is None or prior_max is None:
        return None
    span = prior_max - prior_min
    if span <= 0:
        return None
    # Anomaly findings sometimes carry the value the row took.
    ev = finding.get("evidence") or {}
    col = str(ev.get("column") or ev.get("col") or "").lower()
    if col and col != prior_col:
        return None
    val = _safe_float(ev.get("value") or ev.get("y") or ev.get("mean"))
    if val is None:
        return None
    # Distance from the prior range, normalised by span.
    if prior_min <= val <= prior_max:
        return {"kind": "baseline", "alignment": "matches",
                "note": f"Value within prior range [{prior_min:.2f}, {prior_max:.2f}]"}
    dist = min(abs(val - prior_min), abs(val - prior_max))
    rel = dist / span
    if rel <= 0.3:
        return {"kind": "baseline", "alignment": "drifts",
                "note": f"Value {dist:.2f} outside prior range ({rel*100:.0f}% drift)"}
    return {"kind": "baseline", "alignment": "novel",
            "note": f"Value {dist:.2f} outside prior range (>{rel*100:.0f}% drift)"}


def _best_tag_for(finding: Dict[str, Any],
                   pack: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Try each comparator; return the first that fires."""
    priors_dict = pack.get("priors") or pack  # accept either shape
    for fn, key in (
        (_compare_physics, "physics"),
        (_compare_regimes, "regimes"),
        (_compare_baseline, "baseline"),
    ):
        prior = priors_dict.get(key) if isinstance(priors_dict, dict) else None
        if not prior:
            continue
        tag = fn(finding, prior)
        if tag:
            tag["run_id"] = pack.get("source_run_id")
            return tag
    return None


def tag_findings_with_prior(
    findings: List[Dict[str, Any]],
    pack: Optional[Dict[str, Any]],
) -> int:
    """Walk every finding; attach a ``prior_source`` decoration when
    it aligns with one of the priors. Returns the count tagged.

    Mutates findings in place. No-op when ``pack`` is None or empty.
    """
    if not pack or not isinstance(pack, dict):
        return 0
    n_tagged = 0
    for f in findings:
        if not isinstance(f, dict):
            continue
        tag = _best_tag_for(f, pack)
        if tag:
            f["prior_source"] = tag
            n_tagged += 1
    return n_tagged


def apply_prior_pack_to_findings(
    findings: List[Dict[str, Any]],
    pack: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """High-level convenience: tag findings + return a small report
    the state_builder can ship in ``state["composed_from"]``.
    """
    if not pack:
        return {"available": False, "n_tagged": 0}
    n_tagged = tag_findings_with_prior(findings, pack)
    return {
        "available":      True,
        "source_run_id":  pack.get("source_run_id"),
        "source_dataset": pack.get("source_dataset"),
        "target_col":     pack.get("target_col"),
        "extracted_at":   pack.get("extracted_at"),
        "n_priors":       int(pack.get("n_priors") or 0),
        "n_tagged":       int(n_tagged),
        "priors":         pack.get("priors") if "priors" in pack else {
            "physics":       pack.get("physics"),
            "regimes":       pack.get("regimes"),
            "baseline":      pack.get("baseline"),
            "anomaly_floor": pack.get("anomaly_floor"),
        },
    }
