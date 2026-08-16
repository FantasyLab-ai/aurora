"""
Regime fingerprints — the measurable shape of a series.

A calibration table is only usable if the same descriptor can be computed
from (a) the synthetic nulls the corpus was built on and (b) the user's
actual data at analysis time. This module is that descriptor. The corpus
build and the runtime lookup both call ``fingerprint_series`` — one code
path, so the two sides are always comparable.

Estimator notes (these choices are load-bearing):

  * ``phi_hat`` uses the lag-1 sample autocorrelation with the Kendall
    bias correction: r1 + (1 + 3*r1)/n. The naive estimator is biased
    toward zero on short series (E[r1] ~ phi - (1+3*phi)/n; Kendall 1954,
    Marriott & Pope 1954). Understating phi means understating the false
    discovery rate — the exact failure mode this engine exists to
    prevent — so the correction is not optional.
  * The corpus records the *measured mean fingerprint* of its null draws
    using this same estimator, so any residual estimator bias appears
    identically on both sides of the lookup and cancels in the match.
  * ``seasonal_period`` is a deliberately conservative periodogram
    heuristic: it only names a period when a single frequency dominates
    (peak power > 8x the median spectral power, at least 3 full cycles
    observed). A missed weak seasonality degrades to "no period", which
    keeps the lookup inside the non-seasonal cells — the safe direction.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any, Dict, Optional

import numpy as np


@dataclass(frozen=True)
class RegimeFingerprint:
    """The measurable properties of one series that plausibly move
    detector error rates. Computable from data alone — no labels."""
    n: int                      # observations after dropping missing
    phi_hat: float              # bias-corrected lag-1 autocorrelation
    missingness: float          # fraction of the raw series that was NaN
    sampling_regular: bool      # uniform time deltas (True if no timestamps)
    excess_kurtosis: float      # Fisher definition; 0 for a normal
    skewness: float
    seasonal_period: Optional[int]  # detected dominant period, else None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _phi_hat_kendall(x: np.ndarray) -> float:
    """Bias-corrected lag-1 autocorrelation (Kendall 1954)."""
    n = len(x)
    if n < 3:
        return 0.0
    xc = x - x.mean()
    denom = float(np.dot(xc, xc))
    if denom <= 0.0:
        return 0.0
    r1 = float(np.dot(xc[:-1], xc[1:])) / denom
    corrected = r1 + (1.0 + 3.0 * r1) / n
    return float(np.clip(corrected, -0.99, 0.99))


def _seasonal_period(x: np.ndarray) -> Optional[int]:
    """Dominant-period heuristic: periodogram candidate + ACF confirmation.

    The periodogram alone is not enough — autocorrelated (red-noise)
    series concentrate power at low frequencies and would fake a period.
    A genuine seasonal cycle also produces a *local maximum* in the
    autocorrelation function at the period lag; red noise decays
    monotonically and cannot. Both tests must pass.
    """
    n = len(x)
    if n < 12:
        return None
    xc = x - x.mean()
    power = np.abs(np.fft.rfft(xc)) ** 2
    if len(power) < 4:
        return None
    # Skip the DC bin. Candidate periods: 3 samples .. n/3 (>= 3 cycles).
    body = power[1:]
    med = float(np.median(body))
    if med <= 0.0:
        return None
    k_peak = int(np.argmax(body)) + 1
    period = int(round(n / k_peak))
    if period < 3 or period > n // 3:
        return None
    if float(power[k_peak]) < 8.0 * med:
        return None
    # ACF confirmation at the candidate lag.
    denom = float(np.dot(xc, xc))
    if denom <= 0.0:
        return None

    def _acf(lag: int) -> float:
        if lag <= 0 or lag >= n:
            return 0.0
        return float(np.dot(xc[:-lag], xc[lag:])) / denom

    a_p = _acf(period)
    if a_p < 0.2:
        return None
    if not (a_p > _acf(period - 2) and a_p > _acf(period + 2)):
        return None
    return period


def _sampling_regular(timestamps: Optional[np.ndarray]) -> bool:
    """True when time deltas are uniform (or no timestamps exist — a
    row-indexed series is regular by definition)."""
    if timestamps is None:
        return True
    ts = np.asarray(timestamps, dtype=float)
    ts = ts[np.isfinite(ts)]
    if len(ts) < 3:
        return True
    deltas = np.diff(ts)
    if np.any(deltas <= 0):
        return False
    m = float(np.median(deltas))
    if m <= 0:
        return False
    # Regular = 95% of gaps within 1% of the median gap.
    within = np.abs(deltas - m) <= 0.01 * m
    return bool(np.mean(within) >= 0.95)


def fingerprint_series(values: Any, timestamps: Any = None) -> RegimeFingerprint:
    """Compute the regime fingerprint of a single series.

    Args:
        values:     1-D array-like; NaN/inf entries count as missing.
        timestamps: optional 1-D array-like of observation times, used
                    only for the sampling-regularity flag.

    Returns:
        RegimeFingerprint. Moment statistics are computed on the
        non-missing values in original order (the same view Aurora's
        detectors see after their own dropna).
    """
    raw = np.asarray(values, dtype=float).ravel()
    n_raw = len(raw)
    finite = np.isfinite(raw)
    x = raw[finite]
    n = len(x)
    missingness = float(1.0 - n / n_raw) if n_raw else 0.0

    if n < 3 or float(np.std(x)) <= 0.0:
        return RegimeFingerprint(
            n=n, phi_hat=0.0, missingness=round(missingness, 4),
            sampling_regular=_sampling_regular(
                np.asarray(timestamps, dtype=float)[finite]
                if timestamps is not None else None
            ),
            excess_kurtosis=0.0, skewness=0.0, seasonal_period=None,
        )

    from scipy import stats as _st
    kurt = float(_st.kurtosis(x, fisher=True, bias=False))
    skew = float(_st.skew(x, bias=False))

    ts = None
    if timestamps is not None:
        ts_all = np.asarray(timestamps, dtype=float).ravel()
        ts = ts_all[finite] if len(ts_all) == n_raw else ts_all

    return RegimeFingerprint(
        n=n,
        phi_hat=round(_phi_hat_kendall(x), 4),
        missingness=round(missingness, 4),
        sampling_regular=_sampling_regular(ts),
        excess_kurtosis=round(kurt, 4),
        skewness=round(skew, 4),
        seasonal_period=_seasonal_period(x),
    )
