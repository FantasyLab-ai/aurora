"""
Physics explainer — answers "why this fitted law, and what does it not explain?"

Input:
    * the physics_discovery block from ``deep_math.physics_discovery``
    * the physics block from ``deep_math.physics`` (diagnostic stats)

Output:
    {
      "headline": "best fit linear_ode (RMSE 5.83), beats logistic by 14% & oscillator by 41%",
      "method":   "ODE candidate library + grid search",
      "discovered_law": {...},
      "candidates_tried": [{name, rmse, aic, k, beats_best_by}],
      "residual_diagnostics": {...},
      "what_this_law_cannot_explain": [...],
      "method_explanation": "...",
      "falsification": "...",
    }
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional


def _safe_float(x: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        v = float(x)
        if v != v or v == float("inf") or v == float("-inf"):
            return default
        return v
    except Exception:
        return default


def _rel_diff(a: Optional[float], b: Optional[float]) -> Optional[float]:
    """Relative difference (b - a) / a, returned as a percentage."""
    if a is None or b is None or a == 0:
        return None
    return (b - a) / abs(a) * 100.0


def _match_priors_silently(best: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Run the prior matcher; tolerate import or runtime failure."""
    try:
        from fantasyai.aurora.priors import match_physics_fit
        return [m.to_dict() for m in match_physics_fit(best, max_results=3)]
    except Exception:
        return []


def explain_physics(physics_discovery: Optional[Dict[str, Any]],
                    physics_diag: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Build a structured physics explanation.

    Args:
        physics_discovery: ``deep_math.physics_discovery`` block with best, runner_ups, all_candidates
        physics_diag: optional ``deep_math.physics`` diagnostic block (y_min, dy_dt_mean, etc.)
    """
    if not isinstance(physics_discovery, dict):
        return {"available": False, "reason": "invalid physics input"}
    if physics_discovery.get("note") in ("failed", "skipped"):
        return {"available": False,
                "reason": physics_discovery.get("error") or "no physics fit"}

    best = physics_discovery.get("best") or {}
    if not best:
        return {"available": False, "reason": "no best-fit candidate"}

    runner_ups = physics_discovery.get("runner_ups") or []
    all_cands = physics_discovery.get("all_candidates") or []
    target = physics_discovery.get("target_col") or "?"
    time_col = physics_discovery.get("time_col")
    consistency = _safe_float(physics_discovery.get("physics_consistency_score"))

    best_rmse = _safe_float(best.get("rmse_test") or best.get("rmse_full") or best.get("rmse"))
    best_aic = _safe_float(best.get("aic"))

    # Build the candidates-tried list including beats_best_by deltas.
    candidates_tried: List[Dict[str, Any]] = []
    # Always include best first.
    candidates_tried.append({
        "name": best.get("name", "?"),
        "model": best.get("model", ""),
        "rmse": best_rmse,
        "aic": best_aic,
        "k": best.get("k"),
        "is_best": True,
        "params": best.get("params") or {},
    })
    # Then runner-ups.
    for ru in runner_ups:
        if not isinstance(ru, dict):
            continue
        rmse = _safe_float(ru.get("rmse_test") or ru.get("rmse_full") or ru.get("rmse"))
        delta_pct = _rel_diff(best_rmse, rmse)
        candidates_tried.append({
            "name": ru.get("name", "?"),
            "model": ru.get("model", ""),
            "rmse": rmse,
            "aic": _safe_float(ru.get("aic")),
            "k": ru.get("k"),
            "is_best": False,
            "rmse_pct_worse_than_best": delta_pct,
            "params": ru.get("params") or {},
        })
    # And any failed candidates (so user sees what was tried but didn't fit).
    seen_names = {c["name"] for c in candidates_tried}
    for c in all_cands:
        if not isinstance(c, dict):
            continue
        name = c.get("name", "?")
        if name in seen_names:
            continue
        if c.get("ok") is False:
            candidates_tried.append({
                "name": name,
                "ok": False,
                "error": c.get("error", "fit failed"),
            })

    # Headline.
    pieces = [f"best fit {best.get('name', '?')}"]
    if best_rmse is not None:
        pieces.append(f"RMSE {best_rmse:.3g}")
    if runner_ups:
        deltas = [
            f"{c['name']} +{c['rmse_pct_worse_than_best']:.0f}%"
            for c in candidates_tried[1:3]
            if c.get("rmse_pct_worse_than_best") is not None and c.get("rmse_pct_worse_than_best") > 0
        ]
        if deltas:
            pieces.append("vs " + ", ".join(deltas))
    headline = ", ".join(pieces)

    # Residual diagnostics — read from physics_diag where available.
    residual_diag: Dict[str, Any] = {}
    if isinstance(physics_diag, dict):
        for key in ("y_min", "y_max", "y_mean", "dy_dt_mean", "dy_dt_min",
                    "dy_dt_max", "d2y_dt2_mean", "dy_sign_changes",
                    "is_monotonic", "growth_type"):
            if key in physics_diag:
                residual_diag[key] = physics_diag[key]

    # What this law cannot explain — driven by residual heuristics.
    cannot_explain: List[str] = []
    if residual_diag.get("dy_sign_changes") and residual_diag["dy_sign_changes"] > 50:
        cannot_explain.append(
            f"high-frequency oscillation: derivative changed sign "
            f"{residual_diag['dy_sign_changes']}× — the fitted law is smooth and "
            f"won't capture rapid reversals"
        )
    if residual_diag.get("is_monotonic") is False and "monoton" in best.get("name", "").lower():
        cannot_explain.append(
            "the data is NOT monotonic but a monotonic-form law was fit — "
            "non-monotonic regions are residualized"
        )
    if best.get("name") == "linear_ode":
        cannot_explain.append(
            "saturation behavior: the linear ODE has no equilibrium / carrying-capacity term"
        )
        cannot_explain.append(
            "explicit oscillation: any sustained periodic signal is in the residuals"
        )
    if best.get("name") == "logistic":
        cannot_explain.append(
            "any deviation from a single sigmoidal regime; multi-regime data is forced into one curve"
        )
    if best.get("name") == "damped_oscillator":
        cannot_explain.append(
            "non-zero forcing: a driven oscillator (with external input) would need a forcing term"
        )

    # Method explanation.
    method_explanation = (
        "Aurora's physics_discovery layer fits a small library of candidate "
        "ordinary differential equations (linear ODE, logistic growth, damped "
        "harmonic oscillator) by least-squares on the derivative gradient and "
        "ranks them by RMSE on a held-out 20% test split, with AIC as a tie-breaker. "
        f"The target series ({target}) was used "
        + (f"with detected time axis '{time_col}'" if time_col else
           "as ordered observations (no valid time axis)")
        + ". Each ODE was simulated forward via Euler integration to produce "
        + "ŷ(t), and RMSE was computed against y(t)."
    )

    # Falsification — concrete & checkable.
    falsification = (
        f"if you generated synthetic data from the fitted "
        f"{best.get('name')} ({best.get('model','')}) with the reported parameters "
        f"{best.get('params', {})} and ran physics_discovery on it, "
        f"the same form should win again. Conversely: residualize the data "
        f"against this fit; the residuals should look like white noise. If they "
        f"have visible structure (autocorrelation, periodicity, trend), the law "
        f"is wrong or incomplete."
    )

    # Prior matching against the catalogue.
    prior_matches = _match_priors_silently(best)

    return {
        "available": True,
        "claim_type": "physics",
        "headline": headline,
        "method": "ODE candidate library + grid search",
        "method_explanation": method_explanation,
        "discovered_law": {
            "name": best.get("name"),
            "equation": best.get("model", ""),
            "params": best.get("params", {}),
            "rmse": best_rmse,
            "aic": best_aic,
            "k": best.get("k"),
        },
        "candidates_tried": candidates_tried,
        "residual_diagnostics": residual_diag,
        "consistency_score": consistency,
        "what_this_law_cannot_explain": cannot_explain,
        "prior_matches": prior_matches,
        "falsification": falsification,
    }
