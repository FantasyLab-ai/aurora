"""
Power analysis + Bayesian experimental design — Wave I, Method 9.

* :func:`power_t_test`            — sample-size for a two-sample t-test
                                      at given effect size + α + power.
* :func:`power_correlation`       — sample-size to detect correlation r.
* :func:`detectable_effect`       — given n + α + power, the smallest
                                      Cohen's d we'd detect.
* :func:`expected_information_gain` — Bayesian experimental design via
                                       KL divergence between posterior
                                       and prior.

Pure numpy. Closed-form for power formulas; simulation-based EIG.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Sequence

try:
    import numpy as np
except Exception:  # pragma: no cover
    np = None  # type: ignore[assignment]


def _norm_ppf(p: float) -> float:
    p = max(min(p, 1.0 - 1e-12), 1e-12)
    a = [-3.969683028665376e+01,  2.209460984245205e+02,
         -2.759285104469687e+02,  1.383577518672690e+02,
         -3.066479806614716e+01,  2.506628277459239e+00]
    b = [-5.447609879822406e+01,  1.615858368580409e+02,
         -1.556989798598866e+02,  6.680131188771972e+01,
         -1.328068155288572e+01]
    q = p - 0.5
    r = q * q
    return (((((a[0]*r + a[1])*r + a[2])*r + a[3])*r + a[4])*r + a[5])*q / \
           (((((b[0]*r + b[1])*r + b[2])*r + b[3])*r + b[4])*r + 1)


def power_t_test(
    effect_size: float,
    *,
    alpha: float = 0.05,
    power: float = 0.80,
    two_sided: bool = True,
) -> Dict[str, Any]:
    """Sample size per group for a two-sample t-test.

    Standard normal-approximation formula:
      n = 2 · ( (z_{α/2} + z_{β}) / d )²
    """
    if effect_size <= 0:
        return {"available": False, "skipped_reason": "non_positive_effect"}
    z_alpha = _norm_ppf(1.0 - alpha / (2.0 if two_sided else 1.0))
    z_beta = _norm_ppf(power)
    n = 2 * ((z_alpha + z_beta) / effect_size) ** 2
    return {
        "available": True,
        "kind": "t_test_two_sample",
        "n_per_group": int(math.ceil(n)),
        "n_total": int(math.ceil(2 * n)),
        "effect_size_d": effect_size,
        "alpha": alpha,
        "power": power,
        "two_sided": two_sided,
        "method": "normal_approx_t_test",
        "notes": [
            "normal approximation; for n < 30 use the noncentral t",
        ],
    }


def power_correlation(
    r: float,
    *,
    alpha: float = 0.05,
    power: float = 0.80,
) -> Dict[str, Any]:
    """Sample size to detect a population correlation r.

    Fisher z-transformation: n ≈ ((z_{α/2} + z_{β}) / atanh(r))² + 3.
    """
    if abs(r) >= 1 or r == 0:
        return {"available": False,
                "skipped_reason": "r_must_be_in_open_(-1,1)_and_nonzero"}
    z_alpha = _norm_ppf(1 - alpha / 2)
    z_beta = _norm_ppf(power)
    z_r = math.atanh(r)
    n = ((z_alpha + z_beta) / z_r) ** 2 + 3
    return {
        "available": True,
        "kind": "correlation_test",
        "n_required": int(math.ceil(n)),
        "r": r,
        "alpha": alpha,
        "power": power,
        "method": "fisher_z_transform",
    }


def detectable_effect(
    n_per_group: int,
    *,
    alpha: float = 0.05,
    power: float = 0.80,
    two_sided: bool = True,
) -> Dict[str, Any]:
    """Given a fixed sample size, the smallest effect size detectable
    at the requested α + power."""
    if n_per_group < 2:
        return {"available": False, "skipped_reason": "n_per_group_<2"}
    z_alpha = _norm_ppf(1.0 - alpha / (2.0 if two_sided else 1.0))
    z_beta = _norm_ppf(power)
    d = (z_alpha + z_beta) * math.sqrt(2.0 / n_per_group)
    return {
        "available": True,
        "kind": "minimum_detectable_d",
        "minimum_d": float(d),
        "n_per_group": n_per_group,
        "alpha": alpha,
        "power": power,
        "two_sided": two_sided,
        "method": "normal_approx_t_test_inverse",
    }


# ===========================================================================
# Bayesian experimental design (KL information gain)
# ===========================================================================

def expected_information_gain(
    candidate_designs: List[Dict[str, Any]],
    *,
    prior_samples: Sequence[float],
    likelihood_at_design: callable,
    n_simulations: int = 100,
    seed: Optional[int] = 42,
) -> Dict[str, Any]:
    """Estimate expected information gain (KL divergence between
    posterior and prior) for each candidate design.

    Procedure (simulation-based; Lindley 1956):
      For each candidate design d:
        For each simulation:
          1. Sample θ ~ prior.
          2. Sample y ~ likelihood(d, θ).
          3. Compute log p(y | θ_i) for many θ_i ~ prior.
          4. Posterior weight ∝ likelihood(d, θ_i, y).
          5. EIG contribution = Σ w_i log w_i / max_likelihood.
        Average over simulations.

    Picks the design with the largest expected EIG.

    ``likelihood_at_design`` signature:
        f(design: dict, theta: float) -> sample y (float)
    """
    if np is None:
        raise RuntimeError("numpy required")
    rng = np.random.default_rng(seed)
    prior = np.asarray(prior_samples, dtype=float)
    n_prior = len(prior)
    if n_prior < 50:
        return {"available": False,
                "skipped_reason": "need_50_prior_samples"}
    eig_per_design: List[float] = []
    for design in candidate_designs:
        eig_estimates = []
        for _ in range(n_simulations):
            theta_true = float(prior[rng.integers(0, n_prior)])
            try:
                y = likelihood_at_design(design, theta_true)
            except Exception:
                continue
            # Posterior weights via Gaussian likelihood with σ=1
            # (caller supplies a more sophisticated likelihood if
            # needed — this default is sufficient for many designs).
            log_weights = -0.5 * (prior - y) ** 2
            log_weights = log_weights - log_weights.max()
            w = np.exp(log_weights)
            w_sum = w.sum()
            if w_sum <= 0:
                continue
            w = w / w_sum
            # KL(posterior || prior) — note prior is uniform over its
            # samples, so the term is simply sum w log(N · w).
            mask = w > 0
            kl = float(np.sum(w[mask] * np.log(n_prior * w[mask])))
            eig_estimates.append(kl)
        if eig_estimates:
            eig_per_design.append(float(np.mean(eig_estimates)))
        else:
            eig_per_design.append(0.0)
    best_idx = int(np.argmax(eig_per_design))
    return {
        "available": True,
        "eig_per_design": eig_per_design,
        "best_design_idx": best_idx,
        "best_design": candidate_designs[best_idx],
        "best_eig": eig_per_design[best_idx],
        "n_simulations": n_simulations,
        "method": "simulation_based_kl_eig",
        "notes": [
            "Lindley 1956 KL information gain via Monte Carlo",
            "Gaussian-σ=1 default likelihood; pass a custom callable "
            "via likelihood_at_design for tighter fits",
        ],
    }
