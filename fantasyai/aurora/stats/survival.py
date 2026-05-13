"""
Survival analysis — Wave D.

Three classical methods, pure numpy:

* :func:`kaplan_meier`            — non-parametric survival curve with
                                      Greenwood-formula confidence band.
* :func:`log_rank_test`           — two-group test for differential
                                      survival; chi-square one degree of
                                      freedom.
* :func:`cox_proportional_hazards` — Cox PH model fit by Newton-Raphson on
                                      the partial likelihood (Breslow ties).
                                      Returns hazard ratios with Wald CIs.
* :func:`schoenfeld_residuals_test` — checks the proportional-hazards
                                       assumption. When the test rejects,
                                       the synthesis layer must flag the
                                       Cox results as suspect.

Glass-box: every function records its assumptions and the parameters
used so reviewers can audit. No external survival-analysis libraries.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Sequence, Tuple

try:
    import numpy as np
except Exception:  # pragma: no cover
    np = None  # type: ignore[assignment]


# ===========================================================================
# Kaplan-Meier
# ===========================================================================

def kaplan_meier(
    durations: Sequence[float],
    events: Sequence[int],
    *,
    ci_level: float = 0.95,
) -> Dict[str, Any]:
    """Kaplan-Meier non-parametric estimator.

    Parameters
    ----------
    durations
        Time-to-event for each subject.
    events
        1 = event observed at duration, 0 = censored at duration.
    ci_level
        Pointwise CI level; uses Greenwood's formula for the variance.

    Returns
    -------
    dict::

        {
          "available":  True,
          "times":      list[float],   # ordered unique event times
          "n_at_risk":  list[int],
          "n_events":   list[int],
          "survival":   list[float],   # S(t) at each event time
          "ci_low":     list[float],
          "ci_high":    list[float],
          "median_survival":     float | None,
          "n_subjects":          int,
          "n_events_total":      int,
          "n_censored":          int,
          "method":     "kaplan_meier_greenwood",
        }
    """
    if np is None:
        raise RuntimeError("numpy required for kaplan_meier")
    d = np.asarray(durations, dtype=float)
    e = np.asarray(events, dtype=int)
    if d.shape != e.shape:
        raise ValueError("durations and events must be same length")
    n = d.shape[0]
    if n == 0:
        return {"available": False, "skipped_reason": "empty_input"}
    # Sort by duration ascending.
    order = np.argsort(d, kind="stable")
    d = d[order]
    e = e[order]
    unique_times = sorted(set(d[e == 1].tolist()))
    n_at_risk: List[int] = []
    n_events: List[int] = []
    survival: List[float] = []
    var_log_s: List[float] = []
    s = 1.0
    cum_var = 0.0
    for t in unique_times:
        at_risk = int(np.sum(d >= t))
        died = int(np.sum((d == t) & (e == 1)))
        if at_risk == 0:
            continue
        s = s * (1.0 - died / at_risk)
        if at_risk - died > 0:
            cum_var += died / (at_risk * (at_risk - died))
        n_at_risk.append(at_risk)
        n_events.append(died)
        survival.append(s)
        var_log_s.append(cum_var)
    # Greenwood pointwise CI on log scale.
    z = _norm_ppf(1.0 - (1.0 - ci_level) / 2.0)
    ci_low: List[float] = []
    ci_high: List[float] = []
    for s_val, v in zip(survival, var_log_s):
        if s_val <= 0 or s_val >= 1 or v <= 0:
            ci_low.append(s_val); ci_high.append(s_val)
            continue
        # Wald CI on log(-log(S)).
        log_s = math.log(s_val)
        # Variance of log(S) ≈ cum_var; log-log delta gives variance of
        # log(-log(S)) ≈ var(log_s) / log_s².
        v_loglog = v / (log_s ** 2) if log_s != 0 else v
        sd_loglog = math.sqrt(v_loglog)
        x_lo = math.log(-log_s) - z * sd_loglog
        x_hi = math.log(-log_s) + z * sd_loglog
        ci_low.append(math.exp(-math.exp(x_hi)))
        ci_high.append(math.exp(-math.exp(x_lo)))
    # Median survival = first time S(t) ≤ 0.5.
    median_t: Optional[float] = None
    for t, s_val in zip(unique_times, survival):
        if s_val <= 0.5:
            median_t = float(t)
            break
    return {
        "available": True,
        "times": [float(t) for t in unique_times],
        "n_at_risk": n_at_risk,
        "n_events": n_events,
        "survival": survival,
        "ci_low": ci_low,
        "ci_high": ci_high,
        "median_survival": median_t,
        "n_subjects": n,
        "n_events_total": int(e.sum()),
        "n_censored": int(n - e.sum()),
        "ci_level": ci_level,
        "method": "kaplan_meier_greenwood",
    }


# ===========================================================================
# Log-rank test
# ===========================================================================

def log_rank_test(
    durations_a: Sequence[float], events_a: Sequence[int],
    durations_b: Sequence[float], events_b: Sequence[int],
) -> Dict[str, Any]:
    """Two-sample log-rank test (Mantel 1966).

    Tests H0: the two groups have the same survival distribution.
    Returns chi-square statistic, df=1, and the p-value via the
    chi-square survival function.
    """
    if np is None:
        raise RuntimeError("numpy required for log_rank_test")
    da = np.asarray(durations_a, dtype=float)
    db = np.asarray(durations_b, dtype=float)
    ea = np.asarray(events_a, dtype=int)
    eb = np.asarray(events_b, dtype=int)
    times = sorted(set(da[ea == 1].tolist()) | set(db[eb == 1].tolist()))
    O_minus_E = 0.0
    V = 0.0
    for t in times:
        n_a = int(np.sum(da >= t))
        n_b = int(np.sum(db >= t))
        d_a = int(np.sum((da == t) & (ea == 1)))
        d_b = int(np.sum((db == t) & (eb == 1)))
        n = n_a + n_b
        d = d_a + d_b
        if n == 0 or d == 0:
            continue
        e_a = d * n_a / n
        v_a = (d * n_a * n_b * (n - d)) / (n * n * max(1, n - 1))
        O_minus_E += d_a - e_a
        V += v_a
    if V <= 0:
        return {"available": False,
                "skipped_reason": "zero_variance_in_test"}
    chi2 = O_minus_E ** 2 / V
    p_value = _chi2_sf_df1(chi2)
    return {
        "available": True,
        "chi_square": chi2,
        "df": 1,
        "p_value": p_value,
        "n_a": len(da),
        "n_b": len(db),
        "events_a": int(ea.sum()),
        "events_b": int(eb.sum()),
        "method": "log_rank_mantel",
    }


# ===========================================================================
# Cox proportional hazards
# ===========================================================================

def cox_proportional_hazards(
    durations: Sequence[float],
    events: Sequence[int],
    covariates: Sequence[Sequence[float]],
    *,
    max_iter: int = 30,
    tol: float = 1e-6,
) -> Dict[str, Any]:
    """Cox PH fit via Newton-Raphson on the Breslow partial likelihood.

    Parameters
    ----------
    durations, events
        Same as :func:`kaplan_meier`.
    covariates
        2-D array (n × p) of covariates.

    Returns
    -------
    dict with hazard ratios, log-HR coefficients, Wald CIs, p-values,
    and the partial-log-likelihood at the solution.
    """
    if np is None:
        raise RuntimeError("numpy required for cox_proportional_hazards")
    d = np.asarray(durations, dtype=float)
    e = np.asarray(events, dtype=int)
    X = np.asarray(covariates, dtype=float)
    if X.ndim == 1:
        X = X.reshape(-1, 1)
    n, p = X.shape
    if d.shape[0] != n or e.shape[0] != n:
        raise ValueError("durations / events / covariates length mismatch")
    if n < p + 5:
        return {"available": False,
                "skipped_reason": f"need_at_least_{p + 5}_subjects"}

    # Sort by duration descending so risk sets are easy: at each event
    # time t, risk set = subjects with duration >= t.
    order = np.argsort(d, kind="stable")
    d_s = d[order]; e_s = e[order]; X_s = X[order]
    beta = np.zeros(p)
    last_ll = -np.inf

    for _ in range(max_iter):
        Xb = X_s @ beta
        exp_Xb = np.exp(Xb)
        # For each event time, compute risk-set sums.
        # Walk descending duration so we accumulate from the largest
        # times. (We sorted ascending; reverse-cumulative sum.)
        cum_exp = np.cumsum(exp_Xb[::-1])[::-1]
        cum_X_exp = np.cumsum((X_s * exp_Xb[:, None])[::-1], axis=0)[::-1]
        # Score and information matrix.
        score = np.zeros(p)
        info = np.zeros((p, p))
        log_lik = 0.0
        for i in range(n):
            if e_s[i] != 1:
                continue
            S0 = cum_exp[i]
            if S0 <= 0:
                continue
            S1 = cum_X_exp[i] / S0
            log_lik += Xb[i] - math.log(S0)
            score += X_s[i] - S1
            # Information: E[XX^T | risk] - S1 S1^T.
            mask = slice(i, n)
            w = exp_Xb[mask] / S0
            S2 = np.einsum("ij,ik,i->jk", X_s[mask], X_s[mask], w)
            info += S2 - np.outer(S1, S1)
        try:
            delta = np.linalg.solve(info, score)
        except np.linalg.LinAlgError:
            break
        beta_new = beta + delta
        # Step halving on divergence.
        if math.isnan(log_lik):
            break
        if log_lik < last_ll - 1e-3:
            beta_new = beta + 0.5 * delta
        if np.max(np.abs(delta)) < tol:
            beta = beta_new
            break
        beta = beta_new
        last_ll = log_lik
    # Final pass for SE and HR.
    Xb = X_s @ beta
    exp_Xb = np.exp(Xb)
    cum_exp = np.cumsum(exp_Xb[::-1])[::-1]
    info = np.zeros((p, p))
    for i in range(n):
        if e_s[i] != 1:
            continue
        S0 = cum_exp[i]
        if S0 <= 0:
            continue
        mask = slice(i, n)
        w = exp_Xb[mask] / S0
        cum_X_exp_i = (X_s[mask] * w[:, None]).sum(axis=0)
        S2 = np.einsum("ij,ik,i->jk", X_s[mask], X_s[mask], w)
        info += S2 - np.outer(cum_X_exp_i, cum_X_exp_i)
    try:
        cov_beta = np.linalg.inv(info)
    except np.linalg.LinAlgError:
        cov_beta = np.eye(p)
    se = np.sqrt(np.diag(cov_beta))
    z_stats = beta / np.where(se > 0, se, 1)
    p_values = [_normal_two_sided_p(z) for z in z_stats]
    hr = np.exp(beta)
    ci_low = np.exp(beta - 1.96 * se)
    ci_high = np.exp(beta + 1.96 * se)
    return {
        "available": True,
        "n": n, "n_events": int(e.sum()), "p": p,
        "coefficients": beta.tolist(),
        "hazard_ratios": hr.tolist(),
        "se": se.tolist(),
        "ci_low": ci_low.tolist(),
        "ci_high": ci_high.tolist(),
        "z_statistics": z_stats.tolist(),
        "p_values": list(p_values),
        "log_partial_likelihood": last_ll,
        "method": "cox_ph_breslow_newton",
        "notes": [
            "Breslow ties; Newton-Raphson on partial likelihood",
            "Wald CIs on log-HR scale",
        ],
    }


# ===========================================================================
# Schoenfeld residuals — proportional-hazards check
# ===========================================================================

def schoenfeld_residuals_test(
    durations: Sequence[float],
    events: Sequence[int],
    covariates: Sequence[Sequence[float]],
    coefficients: Sequence[float],
) -> Dict[str, Any]:
    """Test the proportional-hazards assumption by correlating
    Schoenfeld residuals with rank(time).

    For each covariate, returns a chi-square test statistic and p-value.
    Significant correlation → PH assumption violated → Cox results
    suspect.
    """
    if np is None:
        raise RuntimeError("numpy required for schoenfeld_residuals_test")
    d = np.asarray(durations, dtype=float)
    e = np.asarray(events, dtype=int)
    X = np.asarray(covariates, dtype=float)
    if X.ndim == 1:
        X = X.reshape(-1, 1)
    beta = np.asarray(coefficients, dtype=float)
    n, p = X.shape
    order = np.argsort(d, kind="stable")
    d_s = d[order]; e_s = e[order]; X_s = X[order]
    Xb = X_s @ beta
    exp_Xb = np.exp(Xb)
    # Compute residual at each event time.
    residuals: List[Tuple[float, "np.ndarray"]] = []
    for i in range(n):
        if e_s[i] != 1:
            continue
        risk = slice(i, n)
        S0 = float(exp_Xb[risk].sum())
        if S0 <= 0:
            continue
        weights = exp_Xb[risk] / S0
        x_bar = (X_s[risk] * weights[:, None]).sum(axis=0)
        residuals.append((d_s[i], X_s[i] - x_bar))
    if len(residuals) < 5:
        return {"available": False,
                "skipped_reason": "fewer_than_5_event_residuals"}
    times = np.array([r[0] for r in residuals])
    resid = np.array([r[1] for r in residuals])
    # Use rank(time) — robust to time-scale.
    ranks = np.argsort(np.argsort(times)).astype(float)
    ranks_centred = ranks - ranks.mean()
    per_covariate: List[Dict[str, Any]] = []
    for j in range(p):
        rj = resid[:, j]
        rj_centred = rj - rj.mean()
        num = float(np.sum(rj_centred * ranks_centred))
        den_r = float(np.sum(ranks_centred ** 2))
        var_resid = float(np.var(rj, ddof=1))
        if den_r <= 0 or var_resid <= 0:
            chi2 = 0.0
            p_val = 1.0
        else:
            chi2 = num ** 2 / (den_r * var_resid)
            p_val = _chi2_sf_df1(chi2)
        per_covariate.append({
            "covariate_index": j,
            "chi_square": chi2,
            "p_value": p_val,
            "violates_ph": p_val < 0.05,
        })
    global_chi2 = sum(p_["chi_square"] for p_ in per_covariate)
    return {
        "available": True,
        "global_chi_square": global_chi2,
        "global_df": p,
        "per_covariate": per_covariate,
        "n_residuals": len(residuals),
        "any_violation": any(p_["violates_ph"] for p_ in per_covariate),
        "method": "schoenfeld_rank_correlation",
    }


# ===========================================================================
# Tiny p-value / norm helpers (no scipy)
# ===========================================================================

def _norm_ppf(p: float) -> float:
    """Inverse normal — same approximation we ship in bootstrap.py."""
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


def _normal_two_sided_p(z: float) -> float:
    """Two-sided p-value for a standard-normal z statistic."""
    return float(math.erfc(abs(z) / math.sqrt(2.0)))


def _chi2_sf_df1(x: float) -> float:
    """Chi-square survival function for df=1.

    For df=1, χ² = Z² where Z is standard normal, so::

        P(χ² > x)  =  P(|Z| > √x)  =  erfc(√x / √2).
    """
    if x <= 0:
        return 1.0
    return float(math.erfc(math.sqrt(x) / math.sqrt(2.0)))
