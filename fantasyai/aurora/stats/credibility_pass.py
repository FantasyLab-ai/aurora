"""
Credibility pass — applies bootstrap CIs, multiple-comparisons corrections,
and effect sizes to an already-assembled state dict.

Runs after the standard primitives have populated ``state.findings``,
``state.anomalies``, ``state.forecast``, ``state.physics``, ``state.causal``,
``state.regimes`` etc., and BEFORE the synthesis engine — so synthesis
templates can interpolate the new ``q_value`` / ``effect_size`` /
``confidence_interval`` slots.

Glass-box: every computation is recorded in ``state.credibility`` with
the inputs / parameters / methods used. Everything we couldn't compute
is recorded under ``skipped`` with a human-readable reason — silence is
NOT an option in the credibility layer.

The pass is idempotent: re-running it on a state already augmented
just refreshes the numbers. It never invents data; if a needed input
is missing, the corresponding output is ``None`` with a documented
reason.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Sequence

try:
    import numpy as np
except Exception:  # pragma: no cover
    np = None  # type: ignore[assignment]

from .bootstrap import bootstrap_ci, block_bootstrap_ci
from .multiple_comparisons import correct_findings, summarize_correction
from .effect_size import (
    cohens_d, r_squared, attach_effect_size, eta_squared, interpret_cohens_d,
)
from .multivariate_outliers import detect_multivariate_outliers
from .sensitivity import sensitivity_z_threshold, attach_sensitivity_profile


# ---------------------------------------------------------------------------
# z → two-sided p-value
# ---------------------------------------------------------------------------

def _two_sided_p_from_z(z: float) -> Optional[float]:
    """Convert a z-score to a two-sided p-value via the normal CDF."""
    if z is None:
        return None
    try:
        zv = abs(float(z))
    except Exception:
        return None
    # 1 - Φ(z) using erfc.
    return float(math.erfc(zv / math.sqrt(2.0)))


# ---------------------------------------------------------------------------
# Per-section augmentations
# ---------------------------------------------------------------------------

def _augment_anomalies(state: Dict[str, Any]) -> Dict[str, Any]:
    """Attach two-sided p-values and bootstrap CIs to anomaly entries.

    Univariate anomaly z-scores ARE valid hypothesis-test statistics under
    the null "this point came from the same distribution as the rest";
    converting to a p-value lets BH/Bonferroni correct the family.
    """
    anomalies = state.get("anomalies") or []
    if not isinstance(anomalies, list) or not anomalies:
        return {"applied": False, "reason": "no_anomalies"}
    n_with_z = 0
    for a in anomalies:
        if not isinstance(a, dict):
            continue
        z = a.get("z")
        if z is None:
            continue
        p = _two_sided_p_from_z(z)
        if p is not None:
            a["p_value"] = p
            n_with_z += 1
    return {"applied": True, "n_anomalies": len(anomalies),
            "n_p_values_attached": n_with_z}


def _bootstrap_anomaly_score_summary(state: Dict[str, Any]) -> Dict[str, Any]:
    """Bootstrap a CI for the median anomaly score.

    Useful for the synthesis layer: "the typical anomaly scored 3.4
    (CI [3.1, 3.7])" reads dramatically more credibly than the bare
    median.
    """
    if np is None:
        return {"applied": False, "reason": "numpy_missing"}
    anomalies = state.get("anomalies") or []
    scores = []
    for a in anomalies:
        if not isinstance(a, dict):
            continue
        s = a.get("score")
        if s is None:
            continue
        try:
            scores.append(float(s))
        except Exception:
            continue
    if len(scores) < 5:
        return {"applied": False, "reason": "fewer_than_5_scores"}
    res = bootstrap_ci(scores, statistic=lambda x: float(np.median(x)),
                        n_resamples=1000, ci_level=0.95, seed=42)
    state.setdefault("anomaly_score_summary", {}).update({
        "median": res["point"],
        "ci_low": res["ci_low"],
        "ci_high": res["ci_high"],
        "ci_level": res["ci_level"],
        "method": res["method"],
        "n_resamples": res["n_resamples"],
        "n_observations": res["n_observations"],
    })
    return {"applied": True, **res}


def _augment_forecast(state: Dict[str, Any]) -> Dict[str, Any]:
    """Surface the forecast's existing point-CI as a finding-level
    ``confidence_interval`` block on the headline forecast value.

    The forecasting runner already produces upper/lower bands; the
    credibility pass just promotes them so the synthesis templates
    don't have to know which method emitted them.
    """
    fc = state.get("forecast") or {}
    if not fc.get("available"):
        return {"applied": False, "reason": "forecast_unavailable"}
    head = fc.get("headline_value")
    lo = fc.get("headline_ci_low")
    hi = fc.get("headline_ci_high")
    if head is None:
        return {"applied": False, "reason": "no_headline_value"}
    if lo is None or hi is None:
        # Fall back to the first/last forecast point's CI if present.
        pts = fc.get("points") or fc.get("series") or []
        if pts and isinstance(pts, list):
            first = pts[0] if isinstance(pts[0], dict) else None
            if first and "ci_low" in first and "ci_high" in first:
                lo = first.get("ci_low")
                hi = first.get("ci_high")
    if lo is None or hi is None:
        return {"applied": False, "reason": "no_ci_bounds_available"}
    fc["confidence_interval"] = {
        "point": head, "ci_low": lo, "ci_high": hi,
        "ci_level": 0.95,
        "method": "model_native",   # not bootstrap — the model itself produced it
        "source": fc.get("method"),
    }
    return {"applied": True, "point": head, "ci_low": lo, "ci_high": hi}


def _augment_physics_effect_size(state: Dict[str, Any]) -> Dict[str, Any]:
    """Attach R² to physics findings when y_true and y_pred are reachable.

    The runner emits RMSE; we can compute R² when SS_total is in scope
    (i.e., we have access to the target series). For now, the physics
    block stores ``r2`` directly when the runner produced it; we promote
    that to an ``effect_size`` finding-level field.
    """
    phys = state.get("physics") or {}
    if not phys.get("available"):
        return {"applied": False, "reason": "physics_unavailable"}
    law = phys.get("discovered_law") or {}
    r2 = law.get("r2")
    if r2 is None:
        # Sometimes runner stores r² on best-fit candidate.
        for k in ("best", "discovered_law", "physics_discovery"):
            block = phys.get(k) or {}
            if isinstance(block, dict) and block.get("r2") is not None:
                r2 = block["r2"]
                break
    if r2 is None:
        return {"applied": False, "reason": "no_r2_available"}
    try:
        r2v = float(r2)
    except Exception:
        return {"applied": False, "reason": "r2_not_numeric"}
    eff = {
        "kind": "r_squared",
        "value": r2v,
        "interpretation": (
            "very_large" if r2v >= 0.7 else
            "large"      if r2v >= 0.5 else
            "medium"     if r2v >= 0.3 else
            "small"      if r2v >= 0.1 else
            "negligible" if r2v >= 0   else "worse_than_mean"
        ),
        "n": 0,
        "details": {"source": "physics_discovery_runner"},
    }
    phys["effect_size"] = eff
    return {"applied": True, **eff}


def _augment_causal_effect_size(state: Dict[str, Any]) -> Dict[str, Any]:
    """When the causal block carries treatment / control summary stats,
    compute Cohen's d. The runner today emits ``beta_treatment`` plus
    sometimes group means/sigmas — we use whatever is present."""
    cw = state.get("causal") or {}
    if not cw.get("available"):
        return {"applied": False, "reason": "causal_unavailable"}
    items = cw.get("items") or []
    n_attached = 0
    for it in items:
        if not isinstance(it, dict):
            continue
        ma = it.get("mean_treated")
        mb = it.get("mean_control")
        sa = it.get("sd_treated")
        sb = it.get("sd_control")
        na = it.get("n_treated")
        nb = it.get("n_control")
        if None in (ma, mb, sa, sb, na, nb):
            continue
        # Pooled-SD Cohen's d from summary stats (no raw arrays needed).
        try:
            ma, mb = float(ma), float(mb)
            sa2, sb2 = float(sa) ** 2, float(sb) ** 2
            na, nb = int(na), int(nb)
        except Exception:
            continue
        if na < 2 or nb < 2:
            continue
        sp2 = ((na - 1) * sa2 + (nb - 1) * sb2) / max(1, na + nb - 2)
        sp = math.sqrt(sp2) if sp2 > 0 else 0.0
        d = (ma - mb) / sp if sp > 0 else 0.0
        it["effect_size"] = {
            "kind": "cohens_d", "value": d,
            "interpretation": interpret_cohens_d(d),
            "n": na + nb,
            "details": {"n_a": na, "n_b": nb, "pooled_sd": sp,
                         "source": "summary_stats"},
        }
        n_attached += 1
    return {"applied": True, "n_attached": n_attached,
            "n_items": len(items)}


def _correct_findings_p_values(state: Dict[str, Any]) -> Dict[str, Any]:
    """Apply BH + Bonferroni to all findings carrying a p_value.

    Anomaly findings inherit their p_value from the anomaly entry they
    were derived from. We mirror that here so the findings list is
    self-contained for the synthesis layer.
    """
    findings = state.get("findings") or []
    if not isinstance(findings, list) or not findings:
        return {"applied": False, "reason": "no_findings"}
    # Mirror p_value from anomaly entries onto the corresponding findings.
    anomalies = state.get("anomalies") or []
    anom_by_when = {a.get("when"): a for a in anomalies if isinstance(a, dict)}
    for f in findings:
        if not isinstance(f, dict):
            continue
        if f.get("p_value") is not None:
            continue
        title = str(f.get("title") or "")
        # Anomaly finding titles are "Anomaly in <col> at row <id>".
        if title.startswith("Anomaly in "):
            # Try to locate the matching anomaly by its `when` substring.
            for when, a in anom_by_when.items():
                if when and str(when) in title:
                    if a.get("p_value") is not None:
                        f["p_value"] = a["p_value"]
                        # While we're here, mirror z too — synthesis
                        # templates may want it.
                        if f.get("z") is None and a.get("z") is not None:
                            f["z"] = a["z"]
                    break
    summary = correct_findings(findings, alpha=0.05)
    return {"applied": True, "summary": summary}


# ---------------------------------------------------------------------------
# Public entrypoint
# ---------------------------------------------------------------------------

def apply_credibility_pass(state: Dict[str, Any]) -> Dict[str, Any]:
    """Run all credibility steps in order, mutating ``state``.

    Records what ran in ``state.credibility``::

        {
          "applied":    True,
          "anomalies":           {applied, n_anomalies, n_p_values_attached},
          "anomaly_score_ci":    {applied, point, ci_low, ci_high, ...},
          "forecast_ci":         {applied, point, ci_low, ci_high},
          "physics_effect_size": {applied, kind, value, interpretation},
          "causal_effect_size":  {applied, n_attached, n_items},
          "multiple_comparisons":{applied, summary: {n_tests, n_significant_bh, ...}},
        }

    Returns the credibility dict (also stored under ``state.credibility``).
    """
    if not isinstance(state, dict):
        return {"applied": False, "reason": "state_not_dict"}
    out: Dict[str, Any] = {"applied": True}

    # Order matters: anomalies must get p-values BEFORE we run BH on findings.
    out["anomalies"] = _safe(_augment_anomalies, state, "anomalies")
    out["anomaly_score_ci"] = _safe(_bootstrap_anomaly_score_summary,
                                       state, "anomaly_score_ci")
    out["forecast_ci"] = _safe(_augment_forecast, state, "forecast_ci")
    out["physics_effect_size"] = _safe(_augment_physics_effect_size,
                                         state, "physics_effect_size")
    out["causal_effect_size"] = _safe(_augment_causal_effect_size,
                                        state, "causal_effect_size")
    out["multiple_comparisons"] = _safe(_correct_findings_p_values,
                                          state, "multiple_comparisons")

    # Wave B — multivariate outliers + sensitivity profile.
    out["multivariate_outliers"] = _safe(_detect_multivariate_outliers,
                                            state, "multivariate_outliers")
    out["sensitivity_z"] = _safe(_z_threshold_sensitivity,
                                    state, "sensitivity_z")

    state["credibility"] = out
    return out


# ---------------------------------------------------------------------------
# Wave B steps — multivariate outliers + sensitivity profiles
# ---------------------------------------------------------------------------

def _detect_multivariate_outliers(state: Dict[str, Any]) -> Dict[str, Any]:
    """Run the three multivariate detectors when a numeric matrix is
    constructible from the state.

    Aurora's standard ``state.dataset_matrix`` (when present) gives us
    a clean (n × p) numeric array. When absent, we attempt to reconstruct
    a small matrix from the structure block's listed numeric columns —
    but this is only a fallback; the real source is the dataset connector.
    """
    if np is None:
        return {"applied": False, "reason": "numpy_missing"}
    matrix = state.get("dataset_matrix")
    columns: Optional[List[str]] = None
    if matrix is None:
        # No reconstructed matrix in scope — skip cleanly. The orchestrator
        # is responsible for providing dataset_matrix when it wants
        # multivariate detection to run.
        return {"applied": False, "reason": "no_dataset_matrix"}
    try:
        arr = np.asarray(matrix, dtype=float)
    except Exception as e:
        return {"applied": False, "reason": f"matrix_coerce_failed: {e}"}
    if arr.ndim != 2 or arr.shape[1] < 2 or arr.shape[0] < 10:
        return {"applied": False,
                "reason": f"shape_unsuitable: {arr.shape}"}
    columns = state.get("dataset_matrix_columns")
    res = detect_multivariate_outliers(arr, columns=columns)
    # Promote a compact summary onto state.multivariate_outliers so
    # frontend / synthesis can read it without traversing per_method.
    state["multivariate_outliers"] = {
        "available": True,
        "methods_run": res["methods_run"],
        "n_consensus_2of3": res["n_consensus_2of3"],
        "n_any_method": res["n_any_method"],
        "n_observations": res["n_observations"],
        "n_features": res["n_features"],
        "columns": res.get("columns"),
        "per_method_counts": {
            m: r.get("n_outliers", 0)
            for m, r in res["per_method"].items()
        },
        "consensus_points": [
            {"row_idx": p["row_idx"], "consensus": p["consensus"],
              "flagged_by": p["flagged_by"]}
            for p in res["points"] if p["consensus"] >= 2
        ][:50],  # cap so we never bloat the state
    }
    # Emit a finding when consensus outliers exist — gives the synthesis
    # matcher a routable kind. Single finding summarises the family;
    # individual rows are accessible via state.multivariate_outliers.
    if res["n_consensus_2of3"] > 0:
        findings = state.setdefault("findings", [])
        if isinstance(findings, list):
            findings.append({
                "severity": "warn",
                "confidence": min(0.95,
                                    0.5 + 0.1 * res["n_consensus_2of3"]),
                "title": (
                    f"Multivariate outliers: {res['n_consensus_2of3']} "
                    f"point{'s' if res['n_consensus_2of3'] != 1 else ''} "
                    f"flagged by ≥2 of 3 detectors"
                ),
                "description": (
                    f"Consensus across robust Mahalanobis, isolation forest, "
                    f"and LOF — {res['n_any_method']} flagged by any single "
                    f"method. Methods run: {', '.join(res['methods_run'])}."
                ),
                "method": "multivariate_outlier_consensus",
                "actions": ["VIEW EVIDENCE", "EXPLAIN"],
                "kind_hint": "multivariate_outlier",
            })
    return {
        "applied": True,
        "n_consensus_2of3": res["n_consensus_2of3"],
        "n_any_method": res["n_any_method"],
        "methods_run": res["methods_run"],
    }


def _z_threshold_sensitivity(state: Dict[str, Any]) -> Dict[str, Any]:
    """Sweep the anomaly z-threshold and attach the robustness profile
    to each anomaly finding."""
    anomalies = state.get("anomalies") or []
    z_values = []
    for a in anomalies:
        if not isinstance(a, dict):
            continue
        z = a.get("z")
        if z is not None:
            try:
                z_values.append(float(z))
            except Exception:
                continue
    if len(z_values) < 3:
        return {"applied": False, "reason": "fewer_than_3_z_values"}
    profile = sensitivity_z_threshold(z_values, canonical=3.0)
    state["sensitivity_z_threshold"] = {
        "available": True,
        "robustness": profile["robustness"],
        "present_range": profile["present_range"],
        "canonical": profile["canonical"],
        "n_values_swept": profile["n_values"],
    }
    # Attach the robustness verdict to anomaly findings.
    for f in (state.get("findings") or []):
        if isinstance(f, dict) and str(f.get("title", "")).startswith("Anomaly"):
            attach_sensitivity_profile(f, profile)
    return {"applied": True, "robustness": profile["robustness"]}


def _safe(fn, state: Dict[str, Any], label: str) -> Dict[str, Any]:
    """Run a step, swallow exceptions into a structured result so that
    a single failing step never blocks the rest of the pass."""
    try:
        return fn(state)
    except Exception as e:
        return {"applied": False, "reason": f"{label}_exception: {type(e).__name__}: {e}"}
