"""
Bootstrap confidence intervals — credibility infrastructure.

Three flavours ship:

* :func:`bootstrap_ci`        — standard non-parametric percentile bootstrap.
                                 Default for IID data.
* :func:`block_bootstrap_ci`  — moving-block bootstrap. For autocorrelated
                                 time-series data (resamples blocks of length
                                 ``block_size`` to preserve dependence).
* :func:`bca_bootstrap_ci`    — bias-corrected accelerated bootstrap.
                                 Tighter intervals when the statistic is
                                 skewed; slightly more expensive.

All three return the same shape:

    {
        "point": float,             # statistic on the original sample
        "ci_low": float,             # lower bound of the CI
        "ci_high": float,            # upper bound of the CI
        "ci_low_99": float | None,   # tighter inner bound at 99%
        "ci_high_99": float | None,
        "method": str,               # "bootstrap_percentile" | "bca" | "block"
        "n_resamples": int,
        "ci_level": float,           # e.g. 0.95
        "block_size": int | None,    # set for block bootstrap
        "n_observations": int,
        "seed": int | None,
    }

Glass-box: the seed is recorded so any reviewer can re-run the same
resamples and reproduce the CI exactly.

No external dependencies beyond numpy.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, Optional, Sequence, Union

import math

try:
    import numpy as np
except Exception:  # pragma: no cover — numpy is a hard dep elsewhere
    np = None  # type: ignore[assignment]


ArrayLike = Union[Sequence[float], "np.ndarray"]
Statistic = Callable[["np.ndarray"], float]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _coerce_1d(values: ArrayLike) -> "np.ndarray":
    if np is None:
        raise RuntimeError("numpy required for bootstrap_ci")
    arr = np.asarray(values, dtype=float)
    if arr.ndim != 1:
        arr = arr.reshape(-1)
    arr = arr[~np.isnan(arr)]
    return arr


def _percentile_ci(samples: "np.ndarray", level: float) -> tuple:
    """Two-sided percentile CI from bootstrap distribution."""
    alpha = 1.0 - level
    lo = float(np.quantile(samples, alpha / 2.0))
    hi = float(np.quantile(samples, 1.0 - alpha / 2.0))
    return lo, hi


# ---------------------------------------------------------------------------
# Standard non-parametric percentile bootstrap
# ---------------------------------------------------------------------------

def bootstrap_ci(
    values: ArrayLike,
    statistic: Statistic = None,
    *,
    n_resamples: int = 1000,
    ci_level: float = 0.95,
    seed: Optional[int] = 42,
    also_99: bool = True,
) -> Dict[str, Any]:
    """Standard non-parametric percentile bootstrap.

    Parameters
    ----------
    values
        1-D array of observations.
    statistic
        Callable ``f(np.ndarray) -> float`` computing the point estimate.
        Default: :func:`numpy.mean`.
    n_resamples
        Number of bootstrap resamples. 1000 is the conventional default
        for percentile intervals; bump to 5000 for tighter tail estimates.
    ci_level
        Confidence level for the primary interval. ``0.95`` produces a
        95% CI.
    seed
        RNG seed. Recorded in the return dict so anyone can reproduce
        the same resamples.
    also_99
        When True, additionally returns 99% bounds in
        ``ci_low_99`` / ``ci_high_99`` (cheap to compute from the same
        bootstrap distribution).

    Returns
    -------
    dict
        See module docstring for the exact shape.
    """
    if statistic is None:
        statistic = lambda x: float(np.mean(x))
    arr = _coerce_1d(values)
    n = arr.shape[0]
    if n == 0:
        return {
            "point": None, "ci_low": None, "ci_high": None,
            "ci_low_99": None, "ci_high_99": None,
            "method": "bootstrap_percentile",
            "n_resamples": 0, "ci_level": ci_level,
            "block_size": None, "n_observations": 0,
            "seed": seed,
            "skipped_reason": "empty_input",
        }
    point = float(statistic(arr))
    rng = np.random.default_rng(seed)
    samples = np.empty(n_resamples, dtype=float)
    # Pre-draw indices in a single block — avoids per-iteration RNG overhead.
    idx = rng.integers(0, n, size=(n_resamples, n))
    for i in range(n_resamples):
        samples[i] = float(statistic(arr[idx[i]]))
    lo, hi = _percentile_ci(samples, ci_level)
    out: Dict[str, Any] = {
        "point": point,
        "ci_low": lo,
        "ci_high": hi,
        "ci_low_99": None,
        "ci_high_99": None,
        "method": "bootstrap_percentile",
        "n_resamples": n_resamples,
        "ci_level": ci_level,
        "block_size": None,
        "n_observations": n,
        "seed": seed,
    }
    if also_99:
        lo99, hi99 = _percentile_ci(samples, 0.99)
        out["ci_low_99"] = lo99
        out["ci_high_99"] = hi99
    return out


# ---------------------------------------------------------------------------
# Block bootstrap (autocorrelated data)
# ---------------------------------------------------------------------------

def _auto_block_size(n: int) -> int:
    """Politis–White rule-of-thumb fallback: l ≈ n^{1/3}.

    Good enough when the user doesn't supply a block size and we don't
    want to fit an autocorrelation function. Reviewer can override.
    """
    if n <= 1:
        return 1
    return max(2, int(round(n ** (1.0 / 3.0))))


def block_bootstrap_ci(
    values: ArrayLike,
    statistic: Statistic = None,
    *,
    block_size: Optional[int] = None,
    n_resamples: int = 1000,
    ci_level: float = 0.95,
    seed: Optional[int] = 42,
    also_99: bool = True,
) -> Dict[str, Any]:
    """Moving-block bootstrap for autocorrelated (time-series) data.

    Standard percentile bootstrap assumes IID resamples — wrong for
    time series, where consecutive observations are dependent. The
    moving-block variant resamples *blocks* of consecutive observations
    so within-block dependence is preserved.

    Default block_size = ⌈n^(1/3)⌉ (Politis–White heuristic).
    """
    if statistic is None:
        statistic = lambda x: float(np.mean(x))
    arr = _coerce_1d(values)
    n = arr.shape[0]
    if n == 0:
        return {
            "point": None, "ci_low": None, "ci_high": None,
            "ci_low_99": None, "ci_high_99": None,
            "method": "block_bootstrap",
            "n_resamples": 0, "ci_level": ci_level,
            "block_size": None, "n_observations": 0,
            "seed": seed,
            "skipped_reason": "empty_input",
        }
    bs = block_size or _auto_block_size(n)
    bs = max(1, min(bs, n))
    n_blocks = max(1, math.ceil(n / bs))
    point = float(statistic(arr))
    rng = np.random.default_rng(seed)
    # Pre-draw block start indices; reconstruct each resample by stitching.
    starts = rng.integers(0, n - bs + 1 if n - bs + 1 > 0 else 1,
                            size=(n_resamples, n_blocks))
    samples = np.empty(n_resamples, dtype=float)
    for i in range(n_resamples):
        pieces = [arr[s:s + bs] for s in starts[i]]
        joined = np.concatenate(pieces)[:n]  # truncate to original length
        samples[i] = float(statistic(joined))
    lo, hi = _percentile_ci(samples, ci_level)
    out: Dict[str, Any] = {
        "point": point,
        "ci_low": lo, "ci_high": hi,
        "ci_low_99": None, "ci_high_99": None,
        "method": "block_bootstrap",
        "n_resamples": n_resamples,
        "ci_level": ci_level,
        "block_size": bs,
        "n_observations": n,
        "seed": seed,
    }
    if also_99:
        lo99, hi99 = _percentile_ci(samples, 0.99)
        out["ci_low_99"] = lo99
        out["ci_high_99"] = hi99
    return out


# ---------------------------------------------------------------------------
# BCa bootstrap (bias-corrected accelerated)
# ---------------------------------------------------------------------------

def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_ppf(p: float) -> float:
    """Inverse normal CDF (Beasley-Springer-Moro). Good enough for
    BCa — we don't need 9 decimal places, and we avoid pulling scipy."""
    p = max(min(p, 1.0 - 1e-12), 1e-12)
    # Acklam's approximation.
    a = [-3.969683028665376e+01,  2.209460984245205e+02,
         -2.759285104469687e+02,  1.383577518672690e+02,
         -3.066479806614716e+01,  2.506628277459239e+00]
    b = [-5.447609879822406e+01,  1.615858368580409e+02,
         -1.556989798598866e+02,  6.680131188771972e+01,
         -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01,
         -2.400758277161838e+00, -2.549732539343734e+00,
          4.374664141464968e+00,  2.938163982698783e+00]
    d = [ 7.784695709041462e-03,  3.224671290700398e-01,
          2.445134137142996e+00,  3.754408661907416e+00]
    plow  = 0.02425
    phigh = 1 - plow
    if p < plow:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0]*q + c[1])*q + c[2])*q + c[3])*q + c[4])*q + c[5]) / \
               ((((d[0]*q + d[1])*q + d[2])*q + d[3])*q + 1)
    if p <= phigh:
        q = p - 0.5
        r = q * q
        return (((((a[0]*r + a[1])*r + a[2])*r + a[3])*r + a[4])*r + a[5])*q / \
               (((((b[0]*r + b[1])*r + b[2])*r + b[3])*r + b[4])*r + 1)
    q = math.sqrt(-2 * math.log(1 - p))
    return -(((((c[0]*q + c[1])*q + c[2])*q + c[3])*q + c[4])*q + c[5]) / \
            ((((d[0]*q + d[1])*q + d[2])*q + d[3])*q + 1)


def bca_bootstrap_ci(
    values: ArrayLike,
    statistic: Statistic = None,
    *,
    n_resamples: int = 2000,
    ci_level: float = 0.95,
    seed: Optional[int] = 42,
) -> Dict[str, Any]:
    """Bias-corrected accelerated (BCa) bootstrap.

    Tighter and more accurate than the percentile method when the
    statistic's bootstrap distribution is skewed. The cost is a single
    extra jackknife pass to estimate the acceleration factor — O(n)
    additional work, negligible relative to the resampling itself.

    Implementation follows Efron 1987 (DOI 10.2307/2289144).
    """
    if statistic is None:
        statistic = lambda x: float(np.mean(x))
    arr = _coerce_1d(values)
    n = arr.shape[0]
    if n < 4:
        # Fall back to percentile — BCa needs enough points for the
        # jackknife to be meaningful.
        return bootstrap_ci(values, statistic, n_resamples=n_resamples,
                             ci_level=ci_level, seed=seed, also_99=False)
    point = float(statistic(arr))
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, n, size=(n_resamples, n))
    samples = np.empty(n_resamples, dtype=float)
    for i in range(n_resamples):
        samples[i] = float(statistic(arr[idx[i]]))
    # Bias correction z0.
    p_lt = float(np.mean(samples < point))
    p_lt = min(max(p_lt, 1e-9), 1 - 1e-9)
    z0 = _norm_ppf(p_lt)
    # Acceleration via jackknife.
    jk = np.empty(n, dtype=float)
    for i in range(n):
        jk[i] = float(statistic(np.delete(arr, i)))
    jk_mean = float(np.mean(jk))
    num = float(np.sum((jk_mean - jk) ** 3))
    den = 6.0 * (float(np.sum((jk_mean - jk) ** 2)) ** 1.5)
    accel = (num / den) if den != 0 else 0.0
    alpha = 1.0 - ci_level
    z_lo = _norm_ppf(alpha / 2.0)
    z_hi = _norm_ppf(1.0 - alpha / 2.0)
    a1 = _norm_cdf(z0 + (z0 + z_lo) / (1.0 - accel * (z0 + z_lo)))
    a2 = _norm_cdf(z0 + (z0 + z_hi) / (1.0 - accel * (z0 + z_hi)))
    a1 = min(max(a1, 0.0), 1.0)
    a2 = min(max(a2, 0.0), 1.0)
    lo = float(np.quantile(samples, a1))
    hi = float(np.quantile(samples, a2))
    return {
        "point": point,
        "ci_low": lo,
        "ci_high": hi,
        "ci_low_99": None,
        "ci_high_99": None,
        "method": "bca",
        "n_resamples": n_resamples,
        "ci_level": ci_level,
        "block_size": None,
        "n_observations": n,
        "seed": seed,
        "z0": z0,
        "acceleration": accel,
    }
