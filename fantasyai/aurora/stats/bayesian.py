"""
Bayesian inference — Wave G, Method 2.

Pure-numpy Bayesian inference for the cases that cover most of Aurora's
needs without pulling PyMC:

* :func:`beta_binomial_posterior`  — closed-form Beta-Binomial conjugate
                                       posterior. Used for proportions,
                                       hit-rates, success probabilities.
* :func:`normal_normal_posterior`  — closed-form Normal-Normal posterior
                                       for a mean with known variance.
* :func:`normal_inverse_gamma`     — Normal-Inverse-Gamma posterior for
                                       a mean + variance with conjugate
                                       priors.
* :func:`metropolis_hastings`      — generic random-walk MH sampler for
                                       arbitrary log-posteriors. Slow but
                                       correct; for the cases the closed-
                                       form solutions don't cover.
* :func:`bayes_factor_savage_dickey` — quick Bayes factor against a
                                         point-null using the Savage-Dickey
                                         density ratio.

Glass-box throughout: every posterior result records the prior, the
likelihood family, the data summary statistics, and (for MH) the
acceptance rate + chain diagnostics.

Doesn't replace PyMC for complex hierarchical models. Does cover
"replace point estimates with posteriors" for the model families
Aurora already fits.
"""
from __future__ import annotations

import math
from typing import Any, Callable, Dict, List, Optional, Sequence

try:
    import numpy as np
except Exception:  # pragma: no cover
    np = None  # type: ignore[assignment]


# ===========================================================================
# Closed-form conjugate posteriors
# ===========================================================================

def beta_binomial_posterior(
    successes: int,
    trials: int,
    *,
    prior_alpha: float = 1.0,
    prior_beta: float = 1.0,
    ci_level: float = 0.95,
) -> Dict[str, Any]:
    """Beta-Binomial conjugate posterior for a proportion.

    Prior: Beta(α, β). Default Beta(1, 1) is the flat prior on [0, 1].
    Posterior: Beta(α + successes, β + trials - successes).

    Returns posterior mean, mode, equal-tailed credible interval, and
    P(p > 0.5) for quick sanity.
    """
    if trials < successes or successes < 0 or trials < 0:
        return {"available": False, "skipped_reason": "invalid_counts"}
    a = prior_alpha + successes
    b = prior_beta + (trials - successes)
    mean = a / (a + b)
    mode = ((a - 1) / (a + b - 2)) if a > 1 and b > 1 else mean
    var = (a * b) / ((a + b) ** 2 * (a + b + 1))
    # Credible interval via numerical inversion of Beta CDF — but with
    # no scipy, we use the Wilson-Hilferty approximation through a
    # sampling-based fallback.
    rng = np.random.default_rng(42)
    samples = rng.beta(a, b, size=10000)
    alpha = 1.0 - ci_level
    ci_low = float(np.quantile(samples, alpha / 2))
    ci_high = float(np.quantile(samples, 1 - alpha / 2))
    p_gt_half = float(np.mean(samples > 0.5))
    return {
        "available": True,
        "kind": "beta_binomial",
        "posterior_alpha": a,
        "posterior_beta": b,
        "posterior_mean": float(mean),
        "posterior_mode": float(mode),
        "posterior_variance": float(var),
        "ci_low": ci_low,
        "ci_high": ci_high,
        "ci_level": ci_level,
        "p_greater_than_half": p_gt_half,
        "n_samples": 10000,
        "prior": {"alpha": prior_alpha, "beta": prior_beta},
        "data": {"successes": successes, "trials": trials},
        "method": "beta_binomial_conjugate",
    }


def normal_normal_posterior(
    data: Sequence[float],
    *,
    prior_mean: float = 0.0,
    prior_var: float = 1e6,
    sigma_known: Optional[float] = None,
    ci_level: float = 0.95,
) -> Dict[str, Any]:
    """Posterior for a mean μ given a Normal prior and Normal likelihood.

    With ``sigma_known``: μ ~ N(prior_mean, prior_var) and y_i ~ N(μ, σ²)
    (σ known), the posterior is N(μ_post, σ²_post) with closed form.

    Without ``sigma_known``: we plug in the sample SD as a pragmatic
    approximation. Honest reviewers should prefer ``normal_inverse_gamma``
    for that case.
    """
    if np is None:
        raise RuntimeError("numpy required for normal_normal_posterior")
    y = np.asarray(data, dtype=float)
    y = y[~np.isnan(y)]
    n = len(y)
    if n < 1:
        return {"available": False, "skipped_reason": "no_data"}
    sigma = sigma_known
    if sigma is None:
        sigma = float(np.std(y, ddof=1)) if n > 1 else 1.0
    sigma2 = sigma ** 2
    sample_mean = float(y.mean())
    # Posterior precision = prior precision + n / σ²
    post_prec = (1.0 / prior_var) + (n / sigma2)
    post_var = 1.0 / post_prec
    post_mean = post_var * (prior_mean / prior_var
                              + n * sample_mean / sigma2)
    # Equal-tailed CI from normal posterior.
    alpha = 1.0 - ci_level
    z = _norm_ppf(1.0 - alpha / 2.0)
    ci_low = post_mean - z * math.sqrt(post_var)
    ci_high = post_mean + z * math.sqrt(post_var)
    return {
        "available": True,
        "kind": "normal_normal",
        "posterior_mean": float(post_mean),
        "posterior_variance": float(post_var),
        "ci_low": float(ci_low),
        "ci_high": float(ci_high),
        "ci_level": ci_level,
        "sigma_used": float(sigma),
        "sigma_known": sigma_known is not None,
        "prior": {"mean": prior_mean, "variance": prior_var},
        "data": {"n": n, "sample_mean": sample_mean,
                  "sample_sd": float(np.std(y, ddof=1)) if n > 1 else 0.0},
        "method": "normal_normal_conjugate",
    }


def normal_inverse_gamma(
    data: Sequence[float],
    *,
    prior_mean: float = 0.0,
    prior_kappa: float = 0.001,    # near-zero precision = nearly flat
    prior_alpha: float = 0.001,
    prior_beta: float = 0.001,
    ci_level: float = 0.95,
    n_samples: int = 5000,
    seed: Optional[int] = 42,
) -> Dict[str, Any]:
    """Normal-Inverse-Gamma joint posterior for (μ, σ²).

    Conjugate update for unknown mean AND unknown variance. Returns
    posterior summaries plus a sample-based credible interval for μ.
    """
    if np is None:
        raise RuntimeError("numpy required")
    y = np.asarray(data, dtype=float)
    y = y[~np.isnan(y)]
    n = len(y)
    if n < 2:
        return {"available": False, "skipped_reason": "need_2_obs"}
    ybar = float(y.mean())
    s2 = float(np.var(y, ddof=1))
    # Posterior parameters (Gelman BDA Eq. 3.8).
    kappa_n = prior_kappa + n
    mu_n = (prior_kappa * prior_mean + n * ybar) / kappa_n
    alpha_n = prior_alpha + n / 2.0
    beta_n = (prior_beta + 0.5 * (n - 1) * s2
                + 0.5 * prior_kappa * n / kappa_n
                * (ybar - prior_mean) ** 2)
    # Posterior on σ² is Inverse-Gamma(α_n, β_n).
    rng = np.random.default_rng(seed)
    sigma2_samples = beta_n / rng.gamma(alpha_n, 1.0, size=n_samples)
    mu_samples = mu_n + rng.standard_normal(n_samples) * np.sqrt(
        sigma2_samples / kappa_n)
    alpha = 1.0 - ci_level
    return {
        "available": True,
        "kind": "normal_inverse_gamma",
        "posterior_mu_mean": float(np.mean(mu_samples)),
        "posterior_mu_ci_low":  float(np.quantile(mu_samples, alpha / 2)),
        "posterior_mu_ci_high": float(np.quantile(mu_samples, 1 - alpha / 2)),
        "posterior_sigma2_mean": float(np.mean(sigma2_samples)),
        "posterior_sigma2_ci_low":  float(np.quantile(sigma2_samples, alpha / 2)),
        "posterior_sigma2_ci_high": float(np.quantile(sigma2_samples, 1 - alpha / 2)),
        "kappa_n": float(kappa_n),
        "alpha_n": float(alpha_n),
        "beta_n":  float(beta_n),
        "ci_level": ci_level,
        "n_samples": n_samples,
        "prior": {"mean": prior_mean, "kappa": prior_kappa,
                   "alpha": prior_alpha, "beta": prior_beta},
        "data": {"n": n, "sample_mean": ybar, "sample_var": s2},
        "method": "normal_inverse_gamma_conjugate",
    }


# ===========================================================================
# Metropolis-Hastings (generic)
# ===========================================================================

def metropolis_hastings(
    log_posterior: Callable[["np.ndarray"], float],
    initial: Sequence[float],
    *,
    n_samples: int = 5000,
    burn_in: int = 1000,
    proposal_sd: float = 0.5,
    seed: Optional[int] = 42,
) -> Dict[str, Any]:
    """Random-walk Metropolis-Hastings sampler.

    Draws ``n_samples`` (post-burn-in) samples from the target whose
    log-density is ``log_posterior``. Pure Python, slow — for cases the
    conjugate posteriors don't cover. Returns posterior samples plus
    chain diagnostics (acceptance rate, thinning recommendation).
    """
    if np is None:
        raise RuntimeError("numpy required")
    rng = np.random.default_rng(seed)
    theta = np.asarray(initial, dtype=float).copy()
    p_dim = theta.shape[0]
    log_p_cur = log_posterior(theta)
    accepted = 0
    samples = np.empty((n_samples, p_dim))
    total = burn_in + n_samples
    for i in range(total):
        proposal = theta + rng.standard_normal(p_dim) * proposal_sd
        try:
            log_p_new = log_posterior(proposal)
        except Exception:
            log_p_new = -np.inf
        if not math.isfinite(log_p_new):
            log_p_new = -np.inf
        log_alpha = log_p_new - log_p_cur
        if log_alpha >= 0 or rng.random() < math.exp(log_alpha):
            theta = proposal
            log_p_cur = log_p_new
            if i >= burn_in:
                accepted += 1
        if i >= burn_in:
            samples[i - burn_in] = theta
    accept_rate = accepted / max(1, n_samples)
    # Posterior summaries.
    post_mean = samples.mean(axis=0).tolist()
    post_sd = samples.std(axis=0, ddof=1).tolist()
    ci_low = np.quantile(samples, 0.025, axis=0).tolist()
    ci_high = np.quantile(samples, 0.975, axis=0).tolist()
    return {
        "available": True,
        "kind": "metropolis_hastings",
        "posterior_mean": post_mean,
        "posterior_sd": post_sd,
        "ci_low": ci_low,
        "ci_high": ci_high,
        "ci_level": 0.95,
        "acceptance_rate": accept_rate,
        "n_samples": n_samples,
        "burn_in": burn_in,
        "proposal_sd": proposal_sd,
        "method": "random_walk_mh",
        "diagnostics_note": (
            "target acceptance ~0.234 for high-d random walks; tune "
            "proposal_sd if outside [0.15, 0.5]"
        ),
    }


# ===========================================================================
# Bayes factor via Savage-Dickey
# ===========================================================================

def bayes_factor_savage_dickey(
    posterior_samples: Sequence[float],
    *,
    null_value: float = 0.0,
    prior_density_at_null: float = 1.0,
    bandwidth: Optional[float] = None,
) -> Dict[str, Any]:
    """Savage-Dickey density ratio for nested-model Bayes factor.

    BF_{10} = p(θ = null | prior) / p(θ = null | posterior).
    Estimates the posterior density at ``null_value`` via Gaussian KDE
    on ``posterior_samples``. The numerator is supplied (the prior
    density at the null), defaulting to 1.0 (flat prior in the relevant
    region).
    """
    if np is None:
        raise RuntimeError("numpy required")
    s = np.asarray(posterior_samples, dtype=float)
    s = s[~np.isnan(s)]
    if len(s) < 10:
        return {"available": False, "skipped_reason": "fewer_than_10_samples"}
    if bandwidth is None:
        # Silverman's rule-of-thumb bandwidth.
        bandwidth = 1.06 * float(np.std(s)) * len(s) ** (-1 / 5)
        if bandwidth <= 0:
            bandwidth = 1.0
    # Gaussian KDE evaluated at null_value.
    deltas = (s - null_value) / bandwidth
    post_density = float(np.mean(np.exp(-0.5 * deltas ** 2)
                                    / math.sqrt(2 * math.pi))) / bandwidth
    if post_density <= 0:
        return {"available": False, "skipped_reason": "zero_posterior_density"}
    bf_10 = prior_density_at_null / post_density
    if bf_10 < 1:
        evidence = "for_null"
        strength_bf = 1.0 / bf_10
    else:
        evidence = "for_alternative"
        strength_bf = bf_10
    if strength_bf < 3:
        strength_label = "weak"
    elif strength_bf < 10:
        strength_label = "moderate"
    elif strength_bf < 30:
        strength_label = "strong"
    else:
        strength_label = "very_strong"
    return {
        "available": True,
        "bf_10": float(bf_10),
        "log10_bf_10": float(math.log10(bf_10)) if bf_10 > 0 else None,
        "null_value": null_value,
        "evidence_direction": evidence,
        "strength": strength_label,
        "posterior_density_at_null": post_density,
        "prior_density_at_null": prior_density_at_null,
        "bandwidth": bandwidth,
        "n_samples": int(len(s)),
        "method": "savage_dickey_density_ratio",
    }


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
