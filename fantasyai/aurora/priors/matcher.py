"""
Prior matcher — checks a fitted physics_models candidate against the prior catalogue.

Matching strategy (v0)
----------------------

Two gates per prior:

1. **Structural form**: the fitted candidate's runner form must match the
   prior's ``matches_runner_form`` (e.g. "linear_ode" → only Newton's cooling
   and exponential are eligible).

2. **Parameter range + sign constraints**: each free parameter must fall
   within the prior's expected range AND respect any sign constraints.

We compute a **match confidence** in [0, 1]:

  * 1.0 if all params are within range AND all sign constraints satisfied
  * decreases linearly as params drift outside range (capped at 0)
  * 0.0 if any sign constraint is violated outright (a hard fail)

We also surface **why a prior didn't match** so the user can see what would
need to be true for it to apply (e.g. "Newton's cooling expects a < 0 but
fitted a = +0.34"). That negative information is itself trust-building.

For each fitted candidate we return up to 3 best-matching priors. If no
prior is plausible (confidence < 0.5), we return an empty list — better
to say "no known law matched" than to false-positive on irrelevant priors.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional

from .registry import Prior, list_priors_for_runner_form


_HARD_FAIL_THRESHOLD = 0.5  # below this, we don't even surface the match


@dataclass
class PriorMatch:
    prior_id: str
    display_name: str
    domain: str
    confidence: float                          # 0..1
    structural_form: str
    citation: str
    matched_params: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    # ↑ {"k": {"fitted": 0.034, "expected_range": [1e-6, 1.0], "in_range": True, "interpretation": "..."}}
    sign_constraints_satisfied: bool = True
    why_not: List[str] = field(default_factory=list)
    falsification_conditions: List[str] = field(default_factory=list)
    identifiability_conditions: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Sign-constraint and range checks
# ---------------------------------------------------------------------------

def _sign_ok(value: float, constraint: str, tolerance: Optional[float] = None) -> bool:
    """Sign / near-zero check. ``tolerance`` only applies for ``near-zero``."""
    constraint = (constraint or "").lower()
    if constraint == "positive":     return value > 0
    if constraint == "non-negative": return value >= 0
    if constraint == "negative":     return value < 0
    if constraint == "non-positive": return value <= 0
    if constraint == "near-zero":
        # Default tolerance: |b| must be small relative to typical magnitudes.
        # Without a reference, accept |b| < 1.0; that's permissive but rules
        # out gross source terms.
        tol = tolerance if tolerance is not None else 1.0
        return abs(value) < tol
    return True  # unknown → don't fail


def _range_score(value: float, lo: float, hi: float) -> float:
    """Linear penalty as we drift outside [lo, hi]. Returns score in [0, 1]."""
    if lo is None or hi is None or lo >= hi:
        return 1.0
    if lo <= value <= hi:
        return 1.0
    span = hi - lo
    drift = max(lo - value, value - hi) if value < lo or value > hi else 0
    if span <= 0:
        return 0.0
    # Beyond 100% span outside, score is 0.
    return max(0.0, 1.0 - drift / span)


# ---------------------------------------------------------------------------
# Per-prior parameter mapping
# ---------------------------------------------------------------------------

def _map_runner_params_to_prior(prior: Prior, fitted_params: Dict[str, Any]) -> Dict[str, float]:
    """Translate physics_models.py output params → the prior's parameter names.

    The runner emits raw ODE params (a, b, c, k, r, K, dt). Each prior defines
    its own parameter set. Map runner→prior here. Unknown maps return {}.
    """
    pid = prior.id
    fp = fitted_params or {}
    out: Dict[str, float] = {}

    if pid == "newton_cooling":
        # linear_ode: dy/dt = a*y + b   ↔   k = -a, T_inf = -b/a
        a = float(fp.get("a")) if fp.get("a") is not None else None
        b = float(fp.get("b")) if fp.get("b") is not None else None
        if a is not None:
            out["k"] = -a
            if a != 0 and b is not None:
                out["T_inf"] = -b / a
    elif pid == "exponential":
        # linear_ode: dy/dt = a*y + b   when b ≈ 0  → exponential
        a = float(fp.get("a")) if fp.get("a") is not None else None
        if a is not None:
            out["a"] = a
    elif pid == "radioactive_decay":
        # First-order decay: dN/dt = -λ·N (no source). λ = -a.
        a = float(fp.get("a")) if fp.get("a") is not None else None
        if a is not None:
            out["lambda_decay"] = -a
    elif pid == "beer_lambert":
        # dI/dx = -ε·c·I (no source). absorption_coefficient = -a.
        a = float(fp.get("a")) if fp.get("a") is not None else None
        if a is not None:
            out["absorption_coefficient"] = -a
    elif pid == "verhulst_logistic":
        # logistic candidate emits r and K directly.
        if fp.get("r") is not None: out["r"] = float(fp["r"])
        if fp.get("K") is not None: out["K"] = float(fp["K"])
    elif pid == "gompertz":
        # Gompertz shares (r, K) with the logistic runner output.
        if fp.get("r") is not None: out["r"] = float(fp["r"])
        if fp.get("K") is not None: out["K"] = float(fp["K"])
    elif pid == "bass_diffusion":
        # Bass uses an effective rate ≈ r and market potential ≈ K.
        if fp.get("r") is not None: out["r"] = float(fp["r"])
        if fp.get("K") is not None: out["K"] = float(fp["K"])
    elif pid == "damped_oscillator":
        # y'' + c*y' + k*y = 0; runner emits c and k directly.
        if fp.get("c") is not None: out["c"] = float(fp["c"])
        if fp.get("k") is not None: out["k"] = float(fp["k"])
    elif pid == "rlc_circuit":
        # Same form as mechanical oscillator (R↔c, 1/(LC)↔k).
        if fp.get("c") is not None: out["c"] = float(fp["c"])
        if fp.get("k") is not None: out["k"] = float(fp["k"])
    elif pid == "pharmacokinetics_1st_order":
        # First-order drug elimination: dC/dt = -k_e · C  (b ≈ 0)
        a = float(fp.get("a")) if fp.get("a") is not None else None
        if a is not None:
            out["k_elimination"] = -a
    elif pid == "rc_circuit":
        # RC discharge: dV/dt = -V/τ  → τ = -1/a  (b ≈ 0)
        a = float(fp.get("a")) if fp.get("a") is not None else None
        if a is not None and a != 0:
            out["tau"] = -1.0 / a
    elif pid == "compound_interest":
        # Continuously compounded: dV/dt = r·V  → r = a  (b ≈ 0)
        a = float(fp.get("a")) if fp.get("a") is not None else None
        if a is not None:
            out["r"] = a
    elif pid == "pendulum_small_angle":
        # θ'' + (g/L)θ = 0 — undamped, only k matters.
        if fp.get("k") is not None:
            out["k"] = float(fp["k"])
    elif pid == "lc_circuit":
        # LC oscillation: undamped (c ≈ 0), only k matters.
        if fp.get("k") is not None:
            out["k"] = float(fp["k"])
    # ---- v2.0 algebraic forms ----
    elif pid == "stefan_boltzmann":
        # Power law y = a · t^b   →   exponent b should be ≈ 4.
        if fp.get("b") is not None:
            out["exponent"] = float(fp["b"])
    elif pid == "kepler_third":
        # Power law with b ≈ 1.5
        if fp.get("b") is not None:
            out["exponent"] = float(fp["b"])
    elif pid == "kleibers_law":
        # Power law with b ≈ 0.75
        if fp.get("b") is not None:
            out["exponent"] = float(fp["b"])
    elif pid == "michaelis_menten":
        # saturation y = Vmax · t / (K + t)
        if fp.get("Vmax") is not None: out["Vmax"] = float(fp["Vmax"])
        if fp.get("K") is not None:    out["K"] = float(fp["K"])
    elif pid == "sigmoid_adoption":
        # sigmoid y = L / (1 + exp(-k·(t - t₀)))
        if fp.get("L") is not None: out["L"] = float(fp["L"])
        if fp.get("k") is not None: out["k"] = float(fp["k"])
    elif pid == "ballistic_motion":
        # polynomial_2 y = a·t² + b·t + c   →   a should be ≈ -g/2 (negative for falling)
        if fp.get("a") is not None: out["a"] = float(fp["a"])
        if fp.get("b") is not None: out["b"] = float(fp["b"])
    return out


def _runner_param_for_sign(prior: Prior, fitted_params: Dict[str, Any], signed_param: str) -> Optional[float]:
    """Resolve which runner-emitted value corresponds to a prior's sign-constrained param.

    For Newton cooling, the prior says ``sign_constraints={"a": "negative"}``.
    Here ``a`` refers to the *runner's* a (the coefficient in dy/dt = a*y + b),
    not the prior's k. So we read it directly from fitted_params.
    """
    val = fitted_params.get(signed_param)
    if val is None:
        return None
    try:
        return float(val)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Public entry
# ---------------------------------------------------------------------------

def match_physics_fit(fitted_candidate: Optional[Dict[str, Any]],
                      max_results: int = 3) -> List[PriorMatch]:
    """Match a fitted physics_models candidate against the prior catalogue.

    Args:
        fitted_candidate: ``physics_discovery.best`` (or any runner_up) — a dict
            with at minimum ``name`` (the runner's form) and ``params``.
        max_results: cap on the number of matches returned.

    Returns:
        List of ``PriorMatch`` sorted by confidence descending. Empty if no
        prior reaches the hard-fail threshold.
    """
    if not isinstance(fitted_candidate, dict):
        return []
    runner_form = fitted_candidate.get("name")
    if not runner_form:
        return []
    fitted_params = fitted_candidate.get("params") or {}

    candidates: List[PriorMatch] = []
    for prior in list_priors_for_runner_form(runner_form):
        # Map runner params → prior params
        mapped = _map_runner_params_to_prior(prior, fitted_params)

        # Sign constraints — checked against runner's params (since that's
        # what's directly fitted). Values must clear ALL constraints.
        signs_ok = True
        why_not: List[str] = []
        for signed_p, constraint in (prior.sign_constraints or {}).items():
            v = _runner_param_for_sign(prior, fitted_params, signed_p)
            if v is None:
                continue  # missing param ≠ violation; skip
            if not _sign_ok(v, constraint):
                signs_ok = False
                why_not.append(
                    f"expected {signed_p} {constraint} but fitted {signed_p}={v:.4g}"
                )

        # Parameter range scoring (over the prior's own free_parameters).
        param_blocks: Dict[str, Dict[str, Any]] = {}
        if not prior.free_parameters:
            range_score = 1.0
        else:
            scores: List[float] = []
            for pname, spec in prior.free_parameters.items():
                rng = spec.get("range") or [None, None]
                lo, hi = rng[0], rng[1]
                fitted_v = mapped.get(pname)
                if fitted_v is None:
                    # Can't evaluate; treat as neutral 0.7 so the match isn't dismissed
                    # purely because the runner didn't expose this exact name.
                    param_blocks[pname] = {
                        "fitted": None, "expected_range": [lo, hi],
                        "in_range": None, "interpretation": spec.get("interpretation", ""),
                    }
                    scores.append(0.7)
                    continue
                in_range = (lo is None or fitted_v >= lo) and (hi is None or fitted_v <= hi)
                s = _range_score(fitted_v, lo if lo is not None else float("-inf"),
                                 hi if hi is not None else float("inf"))
                param_blocks[pname] = {
                    "fitted": fitted_v,
                    "expected_range": [lo, hi],
                    "in_range": bool(in_range),
                    "interpretation": spec.get("interpretation", ""),
                }
                if not in_range:
                    why_not.append(
                        f"{pname}={fitted_v:.4g} is outside expected range "
                        f"[{lo}, {hi}]"
                    )
                scores.append(s)
            range_score = sum(scores) / len(scores) if scores else 1.0

        # Compose confidence: hard fail if any sign constraint violated.
        if not signs_ok:
            confidence = 0.0
        else:
            confidence = range_score

        if confidence >= _HARD_FAIL_THRESHOLD:
            candidates.append(PriorMatch(
                prior_id=prior.id,
                display_name=prior.display_name,
                domain=prior.domain,
                confidence=round(confidence, 3),
                structural_form=prior.structural_form,
                citation=prior.citation,
                matched_params=param_blocks,
                sign_constraints_satisfied=signs_ok,
                why_not=why_not,
                falsification_conditions=list(prior.falsification_conditions or []),
                identifiability_conditions=list(prior.identifiability_conditions or []),
            ))

    candidates.sort(key=lambda m: m.confidence, reverse=True)
    return candidates[:max_results]
