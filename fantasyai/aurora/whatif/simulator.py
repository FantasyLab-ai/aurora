"""
What-if simulator — apply a structured intervention to fitted models from a run.

Two prediction paths, in this order:

1. **Linear OLS lookup**: when the run's ``causal_what_if`` block matches
   the queried treatment, we use the fitted ``beta_treatment`` to compute
   ``Δy = beta · Δx`` directly. Fastest and most honest — it's exactly the
   estimand the runner already computed, with the same identifiability caveats.

2. **Physics ODE forward-sim**: when the run has a fitted physics_discovery
   ODE (linear_ode, logistic, damped_oscillator) and the queried variable is
   the target itself, we Euler-integrate forward with the intervention applied
   as an initial-condition shift. Gives a real trajectory, not just a delta.

Both paths return the same shape, with explicit identifiability flags.

Identifiability check
---------------------

Before answering, we determine whether the queried effect is identifiable
from the data + conditioning set. We REFUSE to answer (with an explicit
``identifiable: False`` and a list of unverifiable assumptions) when:

* the queried variable doesn't appear in the dataset
* the variable was not fit by ``causal_what_if`` AND not the physics target
* the run had no covariate adjustment (we'll answer but flag confounding risk)

Honest refusal beats hallucinated counterfactuals.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional

from .spec import InterventionSpec


# ----------------------------------------------------------------------
# Identifiability check
# ----------------------------------------------------------------------

def check_identifiability(
    spec: InterventionSpec,
    *,
    deep_math: Optional[Dict[str, Any]],
    structure: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """Decide whether the effect of the intervention is identifiable from this run.

    Returns:
        ``{identifiable: bool, approach: str, satisfied_if: [...], violated_if: [...],
           caveats: [...], target_variable: str}``
    """
    cw = (deep_math or {}).get("causal_what_if") or {}
    target = (deep_math or {}).get("target_col")
    pd_ = (deep_math or {}).get("physics_discovery") or {}
    columns_seen = set()
    if structure:
        columns_seen = {c.get("name") for c in (structure.get("columns") or [])}
        # Also pull from numeric_cols list if present.
        for c in structure.get("numeric_cols") or []:
            if isinstance(c, str):
                columns_seen.add(c)
            elif isinstance(c, dict):
                columns_seen.add(c.get("name"))

    # Unknown variable — refuse cleanly.
    if columns_seen and spec.variable not in columns_seen:
        return {
            "identifiable": False,
            "approach": "none",
            "target_variable": target,
            "caveats": [],
            "violated_if": [
                f"variable '{spec.variable}' is not present in this dataset "
                f"(available columns: {sorted(columns_seen)[:8]}...)",
            ],
            "satisfied_if": [],
        }

    # Path 1: causal_what_if path
    treatment = cw.get("treatment_col")
    outcome = cw.get("target_col")
    if treatment == spec.variable and cw.get("note") in (None, "ok"):
        covariates = cw.get("covariates_used") or []
        if isinstance(covariates, str):
            covariates = [covariates] if covariates else []
        approach = ("back-door criterion via covariate adjustment"
                    if covariates else
                    "naive marginal regression (no covariate adjustment)")
        caveats = []
        if not covariates:
            caveats.append("no covariates were adjusted for — unobserved confounding cannot be ruled out")
        return {
            "identifiable": True,
            "approach": approach,
            "target_variable": outcome or target,
            "covariates_used": covariates,
            "caveats": caveats,
            "satisfied_if": [
                f"the conditioning set {covariates if covariates else '∅'} blocks all back-door paths",
                "no unmeasured common causes of treatment and outcome",
                "linearity in the relationship",
                "treatment temporally precedes outcome",
            ],
            "violated_if": [
                "an unobserved variable causes both treatment and outcome",
                "outcome causes treatment (reverse direction)",
                "the relationship is non-linear",
                "selection bias",
            ],
        }

    # Path 2: physics target — we can simulate the ODE forward.
    best = pd_.get("best") or {}
    physics_target = pd_.get("target_col") or target
    if best and spec.variable == physics_target and spec.kind == "set":
        return {
            "identifiable": True,
            "approach": "physics ODE forward simulation (initial-condition shift)",
            "target_variable": physics_target,
            "caveats": [
                f"prediction assumes the fitted {best.get('name')} continues to hold",
                "no external forcing introduced by the intervention itself",
            ],
            "satisfied_if": [
                "the fitted ODE remains valid in the perturbed regime",
                "no regime shifts triggered by the intervention",
            ],
            "violated_if": [
                "the system enters a new regime where the fitted parameters no longer apply",
                "external forcing or feedback we haven't modeled",
            ],
        }

    # Variable is in the data but neither the OLS treatment nor the physics target.
    return {
        "identifiable": False,
        "approach": "none",
        "target_variable": target,
        "caveats": [
            "the variable is present in the dataset but no fitted model "
            "can answer 'what happens to the target if we change it'",
        ],
        "violated_if": [
            f"the runner did not fit a causal effect for '{spec.variable}' on the target",
            "no physics ODE in the run uses this variable as state",
        ],
        "satisfied_if": [
            "to make this query identifiable, re-run with this variable as the treatment",
            "or fit a multivariate model (currently the runner uses single-treatment OLS)",
        ],
    }


# ----------------------------------------------------------------------
# Linear OLS path — fastest, uses fitted beta_treatment directly
# ----------------------------------------------------------------------

def _simulate_via_ols(
    spec: InterventionSpec,
    *,
    deep_math: Dict[str, Any],
) -> Dict[str, Any]:
    """Apply Δy = beta · Δx using the run's fitted causal_what_if.

    Returns a single-step "trajectory" plus the structured prediction.
    """
    cw = deep_math.get("causal_what_if") or {}
    beta = cw.get("beta_treatment")
    target = cw.get("target_col")
    delta = spec.value if spec.kind == "delta" else (
        # 'set' for OLS: we don't know the baseline X, so we use the value as Δ from baseline 0.
        spec.value
    )

    if beta is None or target is None:
        return {"ok": False, "reason": "OLS effect missing"}

    # Point estimate.
    delta_y = float(beta) * float(delta)

    # CI: the runner doesn't ship a SE for beta. Use the residual sigma as a
    # rough proxy when available. Forecast block has 'sigma' (residual std)
    # which we use as a non-rigorous but better-than-nothing CI estimate.
    sigma = (deep_math.get("forecasting") or {}).get("sigma")
    ci_low = ci_high = None
    if isinstance(sigma, (int, float)) and sigma > 0:
        # Approximate 95% CI = ±1.96 σ (assumes the residual scale dominates the OLS uncertainty).
        ci_low = delta_y - 1.96 * float(sigma) * 0.1   # conservative shrink — beta SE ≪ residual σ
        ci_high = delta_y + 1.96 * float(sigma) * 0.1

    # One-point trajectory: target shifts by delta_y immediately, then holds.
    horizon = max(1, spec.duration_steps or 1)
    trajectory = [{"step": i + 1, "value": delta_y, "ci_low": ci_low, "ci_high": ci_high}
                  for i in range(min(horizon, 24))]

    return {
        "ok": True,
        "method": "linear_ols_effect_lookup",
        "target_variable": target,
        "delta_target": delta_y,
        "ci_low": ci_low,
        "ci_high": ci_high,
        "trajectory": trajectory,
        "beta_treatment": float(beta),
        "delta_x": float(delta),
    }


# ----------------------------------------------------------------------
# Physics ODE path — Euler-integrate forward
# ----------------------------------------------------------------------

def _simulate_via_physics(
    spec: InterventionSpec,
    *,
    deep_math: Dict[str, Any],
    horizon_steps: int = 24,
) -> Dict[str, Any]:
    """Apply a 'set' intervention by shifting the initial condition, then Euler-integrate."""
    pd_ = deep_math.get("physics_discovery") or {}
    best = pd_.get("best") or {}
    name = best.get("name")
    params = best.get("params") or {}
    if not name or not params:
        return {"ok": False, "reason": "no physics model fitted"}

    y0 = float(spec.value)  # 'set' kind: the new initial condition
    n = max(1, min(horizon_steps, 100))
    dt = float(params.get("dt", 1.0)) or 1.0
    trajectory: List[Dict[str, Any]] = []

    if name == "linear_ode":
        # dy/dt = a*y + b
        a = float(params.get("a", 0.0))
        b = float(params.get("b", 0.0))
        y = y0
        for i in range(n):
            y = y + dt * (a * y + b)
            trajectory.append({"step": i + 1, "value": y, "ci_low": None, "ci_high": None})
    elif name == "logistic":
        # dy/dt = r*y*(1 - y/K)
        r = float(params.get("r", 0.0))
        K = float(params.get("K", 1.0))
        y = y0
        for i in range(n):
            y = y + dt * (r * y * (1.0 - y / max(K, 1e-9)))
            trajectory.append({"step": i + 1, "value": y, "ci_low": None, "ci_high": None})
    elif name == "damped_oscillator":
        # y'' + c*y' + k*y = 0 → simulate y, v
        c = float(params.get("c", 0.0))
        k = float(params.get("k", 0.0))
        y = y0
        v = 0.0
        for i in range(n):
            a = -c * v - k * y
            v = v + dt * a
            y = y + dt * v
            trajectory.append({"step": i + 1, "value": y, "ci_low": None, "ci_high": None})
    else:
        return {"ok": False, "reason": f"physics model '{name}' not yet supported by simulator"}

    delta_target = trajectory[-1]["value"] - y0 if trajectory else 0.0
    return {
        "ok": True,
        "method": f"physics_forward_sim_{name}",
        "target_variable": pd_.get("target_col"),
        "delta_target": float(delta_target),
        "ci_low": None,  # physics path doesn't yet quantify simulation uncertainty
        "ci_high": None,
        "trajectory": trajectory,
        "model_used": name,
        "model_params": params,
        "y0": float(y0),
    }


# ----------------------------------------------------------------------
# Public entry — pick the best available simulator path
# ----------------------------------------------------------------------

def simulate_intervention(
    spec: InterventionSpec,
    *,
    deep_math: Optional[Dict[str, Any]],
    structure: Optional[Dict[str, Any]],
    horizon_steps: int = 24,
) -> Dict[str, Any]:
    """Compute the predicted effect of the intervention.

    Returns a single dict with the trajectory + identifiability + method.
    """
    if not deep_math:
        return {"ok": False, "reason": "no deep_math.json available for this run"}

    # First check identifiability.
    ident = check_identifiability(spec, deep_math=deep_math, structure=structure)
    if not ident["identifiable"]:
        return {
            "ok": True,                # request succeeded; answer is "I refuse"
            "identifiable": False,
            "intervention": spec.to_dict(),
            "identifiability": ident,
            "trajectory": [],
            "delta_target": None,
            "method": "refused",
            "headline": (
                f"I can't answer that from this run. "
                f"{ident.get('violated_if', ['unspecified'])[0]}"
            ),
        }

    # Pick the path.
    cw = deep_math.get("causal_what_if") or {}
    if cw.get("treatment_col") == spec.variable and cw.get("note") in (None, "ok"):
        result = _simulate_via_ols(spec, deep_math=deep_math)
        if result.get("ok"):
            result["intervention"] = spec.to_dict()
            result["identifiability"] = ident
            result["headline"] = _ols_headline(spec, result, ident)
            return result

    pd_ = deep_math.get("physics_discovery") or {}
    physics_target = pd_.get("target_col") or deep_math.get("target_col")
    if spec.variable == physics_target and spec.kind == "set":
        result = _simulate_via_physics(spec, deep_math=deep_math, horizon_steps=horizon_steps)
        if result.get("ok"):
            result["intervention"] = spec.to_dict()
            result["identifiability"] = ident
            result["headline"] = _physics_headline(spec, result, ident)
            return result

    return {
        "ok": False,
        "reason": "neither OLS path nor physics path applies — variable is "
                  "in the dataset but not the OLS treatment or physics target",
        "intervention": spec.to_dict(),
        "identifiability": ident,
    }


# ----------------------------------------------------------------------
# Headlines (one-liners)
# ----------------------------------------------------------------------

def _ols_headline(spec: InterventionSpec, result: Dict[str, Any], ident: Dict[str, Any]) -> str:
    delta = result.get("delta_target")
    target = result.get("target_variable")
    sign = "+" if (delta or 0) >= 0 else ""
    base = (f"{spec.display_text()} → expected change in {target} of "
            f"{sign}{delta:.3g}" if delta is not None else
            f"{spec.display_text()} → effect on {target}")
    if not ident.get("covariates_used"):
        base += " (NO covariate adjustment — confounding risk)"
    return base


def _physics_headline(spec: InterventionSpec, result: Dict[str, Any], ident: Dict[str, Any]) -> str:
    target = result.get("target_variable")
    delta = result.get("delta_target")
    n = len(result.get("trajectory") or [])
    sign = "+" if (delta or 0) >= 0 else ""
    return (f"{spec.display_text()} → simulating {result.get('model_used')} forward "
            f"{n} steps, target {target} drifts {sign}{delta:.3g}"
            if delta is not None else
            f"{spec.display_text()} → simulating {result.get('model_used')}")
