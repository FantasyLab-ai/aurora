"""
Calculus signatures — numerical derivatives, integrals, and curvature stats.

This module computes the **calculus fingerprint** of a numeric series: how it
changes (1st derivative), how its rate-of-change changes (2nd derivative,
curvature), how much accumulates over time (integral), and where the
inflection points are. These are the engineer's first-pass diagnostics for
"what kind of process is this signal coming from."

Aurora already has some of these (in physics_diag) but they're scattered.
This module gives them a clean, named home + adds a few that are missing
(integral, curvature score, inflection-point count, monotonic stretches).

Public API
----------

    calculus_signature(t, y) -> CalculusSignature
        — numeric derivative/integral stats for a single (t, y) series

    smooth_central_diff(arr, dt=1.0) -> ndarray
        — utility: 2nd-order central differences, edge-aware
"""
from __future__ import annotations

import math
from dataclasses import dataclass, asdict
from typing import Any, Dict, Optional

import numpy as np


@dataclass
class CalculusSignature:
    """Numerical-calculus fingerprint of a series y(t)."""
    # 1st derivative (rate of change)
    dy_dt_mean: float
    dy_dt_std: float
    dy_dt_max_abs: float
    dy_dt_sign_changes: int          # how many times the rate flips sign
    monotonic_stretches: int         # longest run of constant sign in dy/dt

    # 2nd derivative (curvature / acceleration)
    d2y_dt2_mean: float
    d2y_dt2_std: float
    inflection_points: int           # count of d²y/dt² sign changes

    # Integral
    integral_total: float            # ∫y dt over the full window
    integral_mean: float             # average value (∫y dt / span)

    # Shape descriptors
    monotonic: bool                  # strictly monotonic in y
    dominant_frequency: Optional[float]  # peak FFT frequency (None if FFT n/a)

    # Interpretation hints (filled by classify_signature)
    likely_shape: str = ""           # e.g. "exponential decay", "oscillatory", "linear"
    confidence: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        for k, v in list(d.items()):
            if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
                d[k] = None
        return d


def smooth_central_diff(arr: np.ndarray, dt: float = 1.0) -> np.ndarray:
    """2nd-order central differences with one-sided edges.

    Equivalent to np.gradient(arr, dt) but explicit so we can return a
    consistent shape and handle non-uniform sampling later.
    """
    a = np.asarray(arr, dtype=float)
    if a.size < 2:
        return np.zeros_like(a)
    return np.gradient(a, dt)


def _count_sign_changes(arr: np.ndarray, eps: float = 1e-9) -> int:
    """Number of times the sign of `arr` flips."""
    a = np.asarray(arr, dtype=float)
    a = a[np.isfinite(a)]
    if a.size < 2:
        return 0
    signs = np.sign(a)
    # Mask near-zero entries to avoid spurious flips at noise level.
    signs[np.abs(a) < eps] = 0
    nonzero = signs[signs != 0]
    if nonzero.size < 2:
        return 0
    return int(np.sum(nonzero[1:] != nonzero[:-1]))


def _longest_monotonic_run(arr: np.ndarray) -> int:
    """Longest stretch where sign of arr stays constant."""
    a = np.asarray(arr, dtype=float)
    a = a[np.isfinite(a)]
    if a.size < 2:
        return int(a.size)
    signs = np.sign(a)
    longest = 1
    current = 1
    for i in range(1, signs.size):
        if signs[i] == signs[i - 1] and signs[i] != 0:
            current += 1
            longest = max(longest, current)
        else:
            current = 1
    return int(longest)


def _dominant_frequency(arr: np.ndarray, dt: float = 1.0) -> Optional[float]:
    """Peak FFT frequency in Hz. None if signal is too short."""
    a = np.asarray(arr, dtype=float)
    a = a[np.isfinite(a)]
    if a.size < 16:
        return None
    a_centered = a - np.mean(a)
    spectrum = np.abs(np.fft.rfft(a_centered))
    if spectrum.size < 2:
        return None
    peak_idx = int(np.argmax(spectrum[1:]) + 1)
    n = a.size
    freq = peak_idx / (n * dt)
    return float(freq)


def _classify_shape(sig: CalculusSignature) -> tuple[str, float]:
    """Heuristic classification — what kind of curve does this look like?

    Returns (label, confidence) where confidence ∈ [0, 1].
    """
    # Strict monotonicity → exponential / power / linear
    if sig.monotonic:
        if sig.d2y_dt2_std > 0 and abs(sig.d2y_dt2_mean) > 0.1 * sig.d2y_dt2_std:
            sign = "growth" if sig.dy_dt_mean > 0 else "decay"
            curving = "exponential-like" if sig.d2y_dt2_mean * sig.dy_dt_mean > 0 \
                else "decelerating"
            return f"{curving} {sign}", 0.7
        return "linear-monotonic", 0.85

    # Many inflection points + dominant frequency → oscillatory
    if sig.inflection_points >= 4 and sig.dominant_frequency:
        return "oscillatory", 0.8

    # High dy_dt sign changes but no clear frequency → noisy / random walk
    if sig.dy_dt_sign_changes > sig.monotonic_stretches * 4:
        return "noisy / non-stationary", 0.6

    # Few inflection points → likely a smooth shape (sigmoid, saturation, single peak)
    if 1 <= sig.inflection_points <= 3:
        return "saturation / sigmoid-like", 0.7

    return "complex", 0.4


def calculus_signature(t: np.ndarray, y: np.ndarray) -> CalculusSignature:
    """Compute the calculus fingerprint of (t, y).

    Args:
        t: independent variable (time or row index)
        y: target series

    Returns:
        CalculusSignature with first/second derivatives, integral, and a
        heuristic shape classification.
    """
    t = np.asarray(t, dtype=float)
    y = np.asarray(y, dtype=float)

    # Estimate dt; if non-uniform, use the median spacing.
    if t.size >= 2:
        dt_arr = np.diff(t)
        dt_arr = dt_arr[np.isfinite(dt_arr) & (dt_arr > 0)]
        dt = float(np.median(dt_arr)) if dt_arr.size else 1.0
    else:
        dt = 1.0

    # 1st derivative
    dy_dt = smooth_central_diff(y, dt)
    dy_dt_clean = dy_dt[np.isfinite(dy_dt)]

    # 2nd derivative
    d2y_dt2 = smooth_central_diff(dy_dt, dt)
    d2y_dt2_clean = d2y_dt2[np.isfinite(d2y_dt2)]

    # Integral via trapezoidal rule.
    y_clean = y[np.isfinite(y)]
    if y_clean.size >= 2:
        integral_total = float(np.trapezoid(y_clean, dx=dt)) \
            if hasattr(np, "trapezoid") else float(np.trapz(y_clean, dx=dt))
        integral_mean = integral_total / max((y_clean.size - 1) * dt, 1e-9)
    else:
        integral_total = 0.0
        integral_mean = 0.0

    # Monotonicity
    if y_clean.size >= 2:
        diffs = np.diff(y_clean)
        monotonic = bool(np.all(diffs >= -1e-9)) or bool(np.all(diffs <= 1e-9))
    else:
        monotonic = False

    sig = CalculusSignature(
        dy_dt_mean=float(np.nanmean(dy_dt_clean)) if dy_dt_clean.size else 0.0,
        dy_dt_std=float(np.nanstd(dy_dt_clean)) if dy_dt_clean.size else 0.0,
        dy_dt_max_abs=float(np.nanmax(np.abs(dy_dt_clean))) if dy_dt_clean.size else 0.0,
        dy_dt_sign_changes=_count_sign_changes(dy_dt_clean),
        monotonic_stretches=_longest_monotonic_run(dy_dt_clean),
        d2y_dt2_mean=float(np.nanmean(d2y_dt2_clean)) if d2y_dt2_clean.size else 0.0,
        d2y_dt2_std=float(np.nanstd(d2y_dt2_clean)) if d2y_dt2_clean.size else 0.0,
        inflection_points=_count_sign_changes(d2y_dt2_clean),
        integral_total=integral_total,
        integral_mean=integral_mean,
        monotonic=monotonic,
        dominant_frequency=_dominant_frequency(y_clean, dt),
    )
    label, conf = _classify_shape(sig)
    sig.likely_shape = label
    sig.confidence = conf
    return sig
