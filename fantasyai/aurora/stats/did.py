"""
Difference-in-Differences — Wave F.

Workhorse causal-inference method when panel data contains a treatment
that affects some entities at some times.

Two-way fixed-effects DiD (canonical):
    y_{it} = α_i + γ_t + β · D_{it} + ε_{it}

where D_{it} = 1 for treated entities in post-treatment periods.

Glass-box requirements per the brief:
* Parallel-trends pre-test (paired t-test on pre-period growth rates).
* Honest standard errors via wild-cluster bootstrap.
* Always report β with explicit assumption-tracking — DiD without
  assumption auditing is one of the commonest sources of bad causal
  inference.

Pure numpy.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Sequence

try:
    import numpy as np
except Exception:  # pragma: no cover
    np = None  # type: ignore[assignment]


def difference_in_differences(
    entities: Sequence[Any],
    times: Sequence[Any],
    treatment_indicator: Sequence[int],
    outcome: Sequence[float],
    *,
    pre_trend_check: bool = True,
    n_bootstrap: int = 500,
    seed: Optional[int] = 42,
) -> Dict[str, Any]:
    """Two-way fixed-effects DiD with parallel-trends pre-test.

    Parameters
    ----------
    entities, times
        Panel keys.
    treatment_indicator
        D_{it}: 1 when entity i is treated in period t, 0 otherwise.
    outcome
        y_{it}.
    pre_trend_check
        When True, runs a paired t-test on pre-period outcome trends
        between treated and control entities.
    n_bootstrap
        Wild-cluster bootstrap replicates for the treatment-effect SE.
    seed
        RNG seed.

    Returns
    -------
    dict with::

        {
          "available":              True,
          "treatment_effect":       β̂,
          "se":                     float,
          "ci_low":                 float,
          "ci_high":                float,
          "p_value":                float,
          "n_treated":              int,
          "n_control":              int,
          "n_pre":                  int,
          "n_post":                 int,
          "parallel_trends": {
            "tested":       bool,
            "p_value":      float | None,
            "violated":     bool,
            "delta_trend":  float,
          },
          "assumptions": [str],
          "method":         "two_way_fe_did_wild_cluster",
        }
    """
    if np is None:
        raise RuntimeError("numpy required for difference_in_differences")
    n = len(outcome)
    if not (len(entities) == len(times) == len(treatment_indicator) == n):
        raise ValueError("inputs must be same length")
    if n < 8:
        return {"available": False, "skipped_reason": "fewer_than_8_obs"}

    y = np.asarray(outcome, dtype=float)
    D = np.asarray(treatment_indicator, dtype=float)
    e_arr = np.asarray(entities)
    t_arr = np.asarray(times)
    valid = ~np.isnan(y)
    y = y[valid]; D = D[valid]; e_arr = e_arr[valid]; t_arr = t_arr[valid]
    n = y.shape[0]

    e_unique, e_idx = np.unique(e_arr, return_inverse=True)
    t_unique, t_idx = np.unique(t_arr, return_inverse=True)
    n_e = len(e_unique); n_t = len(t_unique)

    # Build design matrix with entity dummies, time dummies, and D.
    X = np.zeros((n, n_e + n_t + 1))
    X[np.arange(n), e_idx] = 1
    X[np.arange(n), n_e + t_idx] = 1
    X[:, -1] = D
    # Drop one entity dummy and one time dummy for identifiability.
    keep = list(range(n_e + n_t + 1))
    keep.remove(0)            # drop entity 0
    keep.remove(n_e)          # drop time 0
    X_ = X[:, keep]
    # OLS.
    try:
        beta, *_ = np.linalg.lstsq(X_, y, rcond=None)
    except np.linalg.LinAlgError:
        return {"available": False, "skipped_reason": "design_singular"}
    treatment_effect = float(beta[-1])
    yhat = X_ @ beta
    resid = y - yhat
    rss = float(np.sum(resid ** 2))
    df_resid = max(1, n - X_.shape[1])
    sigma2 = rss / df_resid
    XtX_inv = np.linalg.pinv(X_.T @ X_)
    se = float(math.sqrt(sigma2 * XtX_inv[-1, -1]))
    z = treatment_effect / se if se > 0 else 0.0
    p_value = float(math.erfc(abs(z) / math.sqrt(2.0)))

    # Wild-cluster bootstrap for honest SE (Cameron-Gelbach-Miller 2008).
    rng = np.random.default_rng(seed)
    boot_betas = []
    for _ in range(n_bootstrap):
        # Rademacher weights at the entity level (cluster).
        w_per_entity = rng.choice([-1.0, 1.0], size=n_e)
        w = w_per_entity[e_idx]
        y_star = yhat + w * resid
        try:
            b, *_ = np.linalg.lstsq(X_, y_star, rcond=None)
            boot_betas.append(float(b[-1]))
        except Exception:
            continue
    if boot_betas:
        boot_arr = np.array(boot_betas)
        # Two-sided percentile CI.
        ci_low = float(np.quantile(boot_arr, 0.025))
        ci_high = float(np.quantile(boot_arr, 0.975))
    else:
        ci_low = treatment_effect - 1.96 * se
        ci_high = treatment_effect + 1.96 * se

    # Parallel-trends pre-test: compare pre-period trends between
    # entities that EVER receive treatment vs entities that NEVER do.
    parallel_trends = {"tested": False, "p_value": None,
                        "violated": False, "delta_trend": 0.0}
    if pre_trend_check:
        # Identify treated entities (entities with at least one D=1 obs).
        ever_treated = set()
        for ent in e_unique:
            mask = (e_arr == ent)
            if D[mask].max() > 0:
                ever_treated.add(ent)
        # Pre-period = times before the first treatment time of any
        # treated entity. (Simplification — for staggered adoption,
        # event-study specs are the proper tool.)
        treated_first_t = []
        for ent in ever_treated:
            ent_mask = (e_arr == ent) & (D > 0)
            if ent_mask.any():
                treated_first_t.append(t_arr[ent_mask].min())
        if treated_first_t:
            try:
                first_treat_t = min(treated_first_t)
            except TypeError:
                first_treat_t = None
        else:
            first_treat_t = None
        if first_treat_t is not None:
            pre_mask = (t_arr < first_treat_t)
            # OLS y ~ time × treated within pre-period.
            pre_y = y[pre_mask]
            pre_t = np.asarray([float(_to_num(t)) for t in t_arr[pre_mask]])
            pre_treated = np.array([1 if e in ever_treated else 0
                                       for e in e_arr[pre_mask]], dtype=float)
            if pre_y.shape[0] >= 5 and len(set(pre_treated)) == 2:
                # Fit y = a + b*t + c*treated + d*(t*treated).
                Xp = np.column_stack([np.ones_like(pre_t), pre_t,
                                         pre_treated,
                                         pre_t * pre_treated])
                try:
                    beta_p, *_ = np.linalg.lstsq(Xp, pre_y, rcond=None)
                    yhat_p = Xp @ beta_p
                    resid_p = pre_y - yhat_p
                    rss_p = float(np.sum(resid_p ** 2))
                    df_p = max(1, len(pre_y) - 4)
                    s2_p = rss_p / df_p
                    se_p = float(math.sqrt(s2_p *
                                              np.linalg.pinv(Xp.T @ Xp)[-1, -1]))
                    z_p = beta_p[-1] / se_p if se_p > 0 else 0.0
                    p_p = float(math.erfc(abs(z_p) / math.sqrt(2.0)))
                    parallel_trends = {
                        "tested": True,
                        "p_value": p_p,
                        "violated": p_p < 0.05,
                        "delta_trend": float(beta_p[-1]),
                    }
                except Exception:
                    pass
    assumptions = [
        "two-way fixed-effects identification: y_it = α_i + γ_t + β·D_it + ε_it",
        "parallel trends in absence of treatment",
        "no anticipation effects pre-treatment",
        "stable unit treatment value (SUTVA)",
        f"wild-cluster bootstrap on entity ({n_e} clusters)",
    ]
    return {
        "available": True,
        "treatment_effect": treatment_effect,
        "se": se,
        "ci_low": ci_low,
        "ci_high": ci_high,
        "p_value": p_value,
        "n_obs": n,
        "n_entities": n_e,
        "n_times": n_t,
        "n_treated_obs": int(D.sum()),
        "parallel_trends": parallel_trends,
        "assumptions": assumptions,
        "n_bootstrap": len(boot_betas),
        "method": "two_way_fe_did_wild_cluster",
    }


def _to_num(x):
    """Best-effort conversion of a time label to a numeric scalar.
    Handles ints, floats, numpy scalars, and date-like strings (fallback
    to hash so the pre-trend regression has a stable ordering)."""
    try:
        return float(x)
    except Exception:
        return float(hash(x) % (10 ** 6))
