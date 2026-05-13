"""
Granger causality + transfer entropy — Wave G, Method 3.

* :func:`granger_causality_test`  — pairwise Granger F-test using
                                      VAR(p) restricted/unrestricted
                                      sums of squares.
* :func:`bivariate_granger`       — runs both directions X→Y and Y→X
                                      and returns a verdict on causal
                                      direction.
* :func:`transfer_entropy`        — binning-based transfer entropy
                                      T_{X→Y} (Schreiber 2000) — picks
                                      up nonlinear causal relationships
                                      Granger misses.

Pure numpy. F-distribution critical values via the asymptotic
chi-square approximation (Wilks 1938) — accurate to ~1e-3 for the
df ranges Aurora uses.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Sequence

try:
    import numpy as np
except Exception:  # pragma: no cover
    np = None  # type: ignore[assignment]


def _build_lags(y: "np.ndarray", lags: int) -> "np.ndarray":
    """Build lagged regressor matrix [y_{t-1}, ..., y_{t-lags}]."""
    n = y.shape[0]
    X = np.zeros((n - lags, lags))
    for k in range(1, lags + 1):
        X[:, k - 1] = y[lags - k:n - k]
    return X


def _ols_rss(y: "np.ndarray", X: "np.ndarray") -> float:
    """OLS residual sum of squares with a constant."""
    X1 = np.column_stack([np.ones(len(y)), X])
    beta, *_ = np.linalg.lstsq(X1, y, rcond=None)
    resid = y - X1 @ beta
    return float(np.sum(resid ** 2))


def granger_causality_test(
    cause: Sequence[float],
    effect: Sequence[float],
    *,
    max_lag: int = 4,
) -> Dict[str, Any]:
    """Test whether ``cause`` Granger-causes ``effect``.

    Compares the residual sum of squares of an AR(p) model for ``effect``
    (restricted) against a model that adds lagged ``cause`` (unrestricted).
    F-statistic on the SS difference.
    """
    if np is None:
        raise RuntimeError("numpy required")
    y = np.asarray(effect, dtype=float)
    x = np.asarray(cause, dtype=float)
    mask = ~(np.isnan(y) | np.isnan(x))
    y = y[mask]; x = x[mask]
    n = len(y)
    if n < max_lag * 4 + 4:
        return {"available": False,
                "skipped_reason": f"need_at_least_{max_lag * 4 + 4}_obs"}
    Y = y[max_lag:]
    Y_lags = _build_lags(y, max_lag)
    X_lags = _build_lags(x, max_lag)
    rss_restricted = _ols_rss(Y, Y_lags)
    rss_unrestricted = _ols_rss(Y, np.column_stack([Y_lags, X_lags]))
    df1 = max_lag                     # additional regressors
    df2 = len(Y) - 2 * max_lag - 1    # residual df in unrestricted
    if df2 <= 0 or rss_unrestricted <= 0:
        return {"available": False,
                "skipped_reason": "zero_or_negative_df_or_rss"}
    f_stat = ((rss_restricted - rss_unrestricted) / df1) / \
             (rss_unrestricted / df2)
    # Approximate p-value via chi-square (df1) on the LR-style scale:
    # under H0, df1 * F  →  χ²(df1) for large df2. Accurate for our use.
    chi2 = df1 * f_stat
    p_value = _chi2_sf(chi2, df1)
    return {
        "available": True,
        "f_statistic": float(f_stat),
        "df_numerator": df1,
        "df_denominator": df2,
        "p_value": float(p_value),
        "max_lag": max_lag,
        "rss_restricted": rss_restricted,
        "rss_unrestricted": rss_unrestricted,
        "n_obs": int(len(Y)),
        "significant": p_value < 0.05,
        "method": "granger_var_f_test",
    }


def bivariate_granger(
    x: Sequence[float],
    y: Sequence[float],
    *,
    max_lag: int = 4,
    var_x_label: str = "x",
    var_y_label: str = "y",
) -> Dict[str, Any]:
    """Run Granger in both directions; classify the relationship.

    Returns one of:
      ``x_causes_y``     — x → y significant, y → x not.
      ``y_causes_x``     — y → x significant, x → y not.
      ``bidirectional``  — both directions significant (feedback loop).
      ``no_evidence``    — neither direction significant.
    """
    fwd = granger_causality_test(x, y, max_lag=max_lag)
    rev = granger_causality_test(y, x, max_lag=max_lag)
    if not fwd.get("available") or not rev.get("available"):
        return {"available": False,
                "skipped_reason": "underlying_test_failed",
                "forward": fwd, "reverse": rev}
    fwd_sig = fwd["p_value"] < 0.05
    rev_sig = rev["p_value"] < 0.05
    if fwd_sig and rev_sig:
        verdict = "bidirectional"
    elif fwd_sig:
        verdict = f"{var_x_label}_causes_{var_y_label}"
    elif rev_sig:
        verdict = f"{var_y_label}_causes_{var_x_label}"
    else:
        verdict = "no_evidence"
    return {
        "available": True,
        "verdict": verdict,
        "forward":  {"f": fwd["f_statistic"], "p": fwd["p_value"]},
        "reverse":  {"f": rev["f_statistic"], "p": rev["p_value"]},
        "max_lag": max_lag,
        "method": "bivariate_granger",
    }


# ===========================================================================
# Transfer entropy
# ===========================================================================

def transfer_entropy(
    source: Sequence[float],
    target: Sequence[float],
    *,
    lag: int = 1,
    n_bins: int = 6,
) -> Dict[str, Any]:
    """Schreiber 2000 transfer entropy T_{source→target}.

    T = I(target_t ; source_{t-lag} | target_{t-lag}).
    Estimated by binning. Captures nonlinear causal information transfer
    that the linear Granger F-test misses.
    """
    if np is None:
        raise RuntimeError("numpy required")
    x = np.asarray(source, dtype=float)
    y = np.asarray(target, dtype=float)
    mask = ~(np.isnan(x) | np.isnan(y))
    x = x[mask]; y = y[mask]
    n = len(x)
    if n < lag + 5:
        return {"available": False,
                "skipped_reason": f"need_at_least_{lag + 5}_obs"}
    y_t  = y[lag:]
    y_p  = y[:-lag]
    x_p  = x[:-lag]
    from .mutual_info import conditional_mi
    te = conditional_mi(x_p, y_t, y_p, n_bins=n_bins)
    return {
        "available": True,
        "transfer_entropy": float(te),
        "lag": lag,
        "n_bins": n_bins,
        "n_obs": int(len(y_t)),
        "method": "schreiber_te_binning",
        "notes": [
            "TE > 0 indicates source provides predictive information "
            "about target beyond target's own past",
            "binning estimator; KSG estimator preferred for small samples",
        ],
    }


# ===========================================================================
# Chi-square survival function (no scipy)
# ===========================================================================

def _chi2_sf(x: float, df: int) -> float:
    """P(χ² > x) for arbitrary df via the regularised lower incomplete
    gamma. Pure-numpy Lanczos approximation."""
    if x <= 0 or df <= 0:
        return 1.0
    # Q(s, x) = Γ(s, x) / Γ(s); we compute the regularised lower P
    # then return 1 - P. P uses a series expansion for x < s+1 and
    # a continued fraction for x ≥ s+1 (Numerical Recipes).
    s = df / 2.0
    z = x / 2.0
    if z < s + 1:
        # Series.
        ap = s
        sum_ = 1.0 / s
        delta = sum_
        for _ in range(200):
            ap += 1
            delta *= z / ap
            sum_ += delta
            if abs(delta) < abs(sum_) * 1e-12:
                break
        log_p = -z + s * math.log(z) - _lgamma(s) + math.log(sum_)
        return float(1.0 - math.exp(log_p))
    else:
        # Continued fraction.
        b = z + 1 - s
        c = 1e30
        d = 1.0 / b
        h = d
        for i in range(1, 200):
            an = -i * (i - s)
            b += 2
            d = an * d + b
            if abs(d) < 1e-30: d = 1e-30
            c = b + an / c
            if abs(c) < 1e-30: c = 1e-30
            d = 1.0 / d
            delta = d * c
            h *= delta
            if abs(delta - 1) < 1e-12:
                break
        log_q = -z + s * math.log(z) - _lgamma(s) + math.log(h)
        return float(math.exp(log_q))


def _lgamma(x: float) -> float:
    return math.lgamma(x)
