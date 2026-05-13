"""
Causal explainer — answers "why this causal effect, and what would falsify it?"

Input:
    * the causal_what_if block from ``deep_math.causal_what_if``
    * the structure_contract.json (for column metadata)

Output:
    {
      "headline": "unit increase in precip_mm → temperature_c +1.35 (linear OLS)",
      "method": "linear OLS with covariate adjustment",
      "estimand": "E[Y | do(T=t+δ)] - E[Y | do(T=t)]",
      "conditioning_set": {
        "treatment": "precip_mm",
        "outcome":   "temperature_c",
        "covariates_used": [...],
        "n": 365
      },
      "identifiability": {
        "approach": "back-door criterion via covariate adjustment",
        "satisfied_if": [...],
        "violated_if": [...]
      },
      "method_explanation": "...",
      "falsification": "...",
      "limitations": [...]
    }

Cloud LLMs paraphrase causal claims. Aurora computes the conditioning set,
states what's identifiable, and tells you exactly what would falsify it.
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


def explain_causal(causal_block: Optional[Dict[str, Any]],
                   structure: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Build a structured explanation for a causal_what_if estimate.

    Args:
        causal_block: ``deep_math.causal_what_if``
        structure: optional ``structure_contract.json`` (for column metadata)
    """
    if not isinstance(causal_block, dict):
        return {"available": False, "reason": "invalid causal input"}
    if causal_block.get("note") in ("failed", "skipped"):
        return {"available": False,
                "reason": causal_block.get("error") or causal_block.get("note", "no causal output")}

    treatment = causal_block.get("treatment_col")
    outcome = causal_block.get("target_col")
    if not treatment or not outcome:
        return {"available": False, "reason": "missing treatment or target column"}

    covariates = causal_block.get("covariates_used") or []
    if isinstance(covariates, str):
        covariates = [covariates] if covariates else []
    n = causal_block.get("n")
    delta = _safe_float(causal_block.get("delta"), 1.0)
    beta = _safe_float(causal_block.get("beta_treatment"))
    effect = _safe_float(causal_block.get("estimated_effect"))
    method_name = (causal_block.get("method") or "linear_ols").lower()

    # Headline.
    if effect is not None:
        sign = "+" if effect >= 0 else ""
        headline = (f"unit increase in {treatment} → "
                    f"{outcome} changes by {sign}{effect:.3f} (n={n}, "
                    f"{method_name.replace('_',' ')})")
    else:
        headline = f"effect of {treatment} on {outcome} ({method_name.replace('_',' ')})"

    # Estimand notation.
    estimand = (
        f"E[{outcome} | do({treatment}={treatment}+{delta:.2f})] - "
        f"E[{outcome} | do({treatment}={treatment})]"
    )

    # Identifiability assessment.
    has_covariates = bool(covariates)
    if "linear" in method_name or "ols" in method_name:
        approach = ("back-door criterion via covariate adjustment"
                    if has_covariates else
                    "naive marginal regression (no covariate adjustment)")
        satisfied_if = [
            f"the conditioning set {covariates if has_covariates else '∅'} "
            f"blocks all back-door paths between {treatment} and {outcome}",
            "no unmeasured common causes (confounders) of treatment and outcome",
            "linearity in the relationship between treatment and outcome",
            "no reverse causation: treatment temporally precedes outcome",
        ]
        violated_if = [
            "an unobserved variable causes both treatment and outcome",
            f"{outcome} causes {treatment} (reverse direction)",
            "the relationship is non-linear (interactions, thresholds)",
            "selection bias in the sample",
        ]
    else:
        approach = method_name
        satisfied_if = ["assumptions specific to method " + method_name]
        violated_if = ["assumptions of " + method_name + " are unmet"]

    method_explanation = (
        f"Linear OLS regression of {outcome} on {treatment}"
        + (f" controlling for {len(covariates)} covariate(s)"
           if has_covariates else " (no covariate adjustment)")
        + f" on n={n} rows. The reported effect ({effect:+.3f}) is the "
        + f"coefficient on {treatment} in the fitted regression. This is a "
        + "marginal-causal estimate under the assumption that the conditioning "
        + "set blocks all back-door paths."
    ) if effect is not None else (
        f"OLS regression with the standard assumption set for {method_name}."
    )

    falsification = (
        f"if you randomized {treatment} (held all else equal) and observed the "
        f"average change in {outcome}, the effect should be ~{effect:+.3f}"
        f"{'±sampling error' if effect is not None else ''}. "
        f"Equivalently: residualize {outcome} on the covariates "
        f"{covariates if has_covariates else '∅'} and on {treatment}; "
        f"those two residual sets should be uncorrelated. If they are, the "
        f"effect estimate is biased."
    )

    limitations: List[str] = []
    if not has_covariates:
        limitations.append("No covariates were adjusted for. Unobserved confounding cannot be ruled out.")
    if n is not None and n < 100:
        limitations.append(f"Sample size n={n} is small; the effect estimate is unstable.")
    if effect is not None and abs(effect) < 0.05 and beta is not None and abs(beta) < 0.05:
        limitations.append("Effect magnitude is small relative to typical signal scales; may be noise.")
    limitations.append(
        "OLS assumes a linear relationship. Aurora has not (yet) tested for "
        "non-linearity, interactions, or threshold effects."
    )

    return {
        "available": True,
        "claim_type": "causal",
        "headline": headline,
        "method": method_name.replace("_", " "),
        "estimand": estimand,
        "conditioning_set": {
            "treatment": treatment,
            "outcome": outcome,
            "covariates_used": covariates,
            "n": n,
            "delta": delta,
        },
        "estimate": {
            "effect": effect,
            "beta_treatment": beta,
        },
        "identifiability": {
            "approach": approach,
            "satisfied_if": satisfied_if,
            "violated_if": violated_if,
        },
        "method_explanation": method_explanation,
        "falsification": falsification,
        "limitations": limitations,
    }
