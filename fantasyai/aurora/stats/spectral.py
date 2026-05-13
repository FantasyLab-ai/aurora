"""
Wavelet + Lomb-Scargle spectral analysis — Wave H, Method 8.

Two complementary capabilities:

* :func:`continuous_wavelet`     — Morlet continuous wavelet transform.
                                     Time-frequency localisation; catches
                                     non-stationary spectra (a chirp,
                                     a frequency that drifts over time).
* :func:`lomb_scargle`           — Lomb-Scargle periodogram for
                                     unevenly-sampled time series.
                                     Standard in astronomy and ecology.
* :func:`detect_nonstationary_period` — runs Morlet, identifies the
                                          dominant ridge in the
                                          time-frequency map, and reports
                                          whether/how the period drifts.

Pure numpy. No PyWavelets / astropy.timeseries dependency.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Sequence, Tuple

try:
    import numpy as np
except Exception:  # pragma: no cover
    np = None  # type: ignore[assignment]


# ===========================================================================
# Continuous Morlet wavelet transform
# ===========================================================================

def continuous_wavelet(
    signal: Sequence[float],
    *,
    dt: float = 1.0,
    scales: Optional[Sequence[float]] = None,
    omega0: float = 6.0,
) -> Dict[str, Any]:
    """Morlet continuous wavelet transform.

    The Morlet wavelet is a complex exponential modulated by a Gaussian:

        ψ(t) = π^{-1/4} · e^{i ω₀ t} · e^{-t²/2}

    For a given scale s, the relationship to Fourier period is
    P = 4π s / (ω₀ + √(2 + ω₀²)).

    Parameters
    ----------
    signal
        1-D time series, evenly sampled.
    dt
        Sample interval.
    scales
        Wavelet scales to evaluate. Default: 16 logarithmically-spaced
        scales spanning periods 2·dt to len(signal)·dt/4.
    omega0
        Morlet central frequency. 6 is the conventional default.

    Returns
    -------
    dict::

      {"available", "power": (n_scales × T) array,
       "scales", "periods",
       "global_power": mean over time at each scale,
       "dominant_period_per_t": list[float],
       "method"}
    """
    if np is None:
        raise RuntimeError("numpy required")
    y = np.asarray(signal, dtype=float)
    y = y - y.mean()
    n = y.shape[0]
    if n < 16:
        return {"available": False, "skipped_reason": "fewer_than_16_obs"}
    if scales is None:
        # Default scales: 16 log-spaced across resolvable periods.
        s_min = 2.0 * dt
        s_max = n * dt / 4.0
        scales = np.geomspace(s_min, s_max, 16)
    scales = np.asarray(scales, dtype=float)
    n_s = len(scales)
    # Convert wavelet "scale" to the gauss-modulated exponential's
    # window width in samples.
    period_factor = (4.0 * math.pi) / (omega0 + math.sqrt(2 + omega0 ** 2))
    periods = scales * period_factor
    power = np.zeros((n_s, n))
    t_axis = np.arange(n) * dt
    for i, s in enumerate(scales):
        # Build the Morlet wavelet centred at each t (slow but clear).
        # For efficiency, use convolution form.
        kernel_half = int(min(n // 2, 4 * s / dt))
        if kernel_half < 1:
            continue
        u = np.arange(-kernel_half, kernel_half + 1) * dt / s
        kernel = (math.pi ** -0.25) * np.exp(1j * omega0 * u) \
                  * np.exp(-0.5 * u ** 2)
        # Normalise: integral of |ψ|² over t = 1/√s.
        kernel = kernel / math.sqrt(s)
        # Same-mode convolution returns full-length output.
        conv = np.convolve(y, kernel, mode="same")
        # When the kernel is even-length, np.convolve(mode='same')
        # returns an array of length max(M, N) — pad/truncate to n.
        if conv.shape[0] != n:
            if conv.shape[0] > n:
                conv = conv[:n]
            else:
                conv = np.concatenate([conv,
                                          np.zeros(n - conv.shape[0],
                                                     dtype=conv.dtype)])
        power[i] = np.abs(conv) ** 2
    global_power = power.mean(axis=1).tolist()
    # Dominant period at each t.
    dominant_period_per_t = periods[np.argmax(power, axis=0)].tolist()
    return {
        "available": True,
        "power": power.tolist(),
        "scales": scales.tolist(),
        "periods": periods.tolist(),
        "global_power": global_power,
        "dominant_period_per_t": dominant_period_per_t,
        "n_scales": n_s,
        "n_obs": n,
        "method": "morlet_cwt",
        "omega0": omega0,
        "dt": dt,
    }


def detect_nonstationary_period(
    signal: Sequence[float],
    *,
    dt: float = 1.0,
    scales: Optional[Sequence[float]] = None,
) -> Dict[str, Any]:
    """Trace the dominant frequency ridge over time.

    Returns a verdict — "stationary" if the dominant period is constant
    across the time series, "drifting" if it changes monotonically,
    "switching" if it changes abruptly.
    """
    cwt = continuous_wavelet(signal, dt=dt, scales=scales)
    if not cwt.get("available"):
        return cwt
    dominant = np.asarray(cwt["dominant_period_per_t"])
    if len(dominant) < 10:
        return {"available": False, "skipped_reason": "too_few_t_points"}
    # Compare first and last quartile of dominant periods.
    q = len(dominant) // 4
    p_first = float(np.median(dominant[:q]))
    p_last  = float(np.median(dominant[-q:]))
    p_mid   = float(np.median(dominant[q:-q] if 2 * q < len(dominant)
                                else dominant))
    rel_drift = abs(p_last - p_first) / max(1e-9, (p_first + p_last) / 2)
    if rel_drift < 0.10:
        verdict = "stationary"
    elif (p_first <= p_mid <= p_last) or (p_first >= p_mid >= p_last):
        verdict = "drifting"
    else:
        verdict = "switching"
    return {
        "available": True,
        "verdict": verdict,
        "period_first_quartile": p_first,
        "period_middle_half": p_mid,
        "period_last_quartile": p_last,
        "relative_drift": rel_drift,
        "dominant_period_per_t": cwt["dominant_period_per_t"],
        "n_obs": cwt["n_obs"],
        "method": "morlet_cwt_ridge",
    }


# ===========================================================================
# Lomb-Scargle periodogram
# ===========================================================================

def lomb_scargle(
    times: Sequence[float],
    values: Sequence[float],
    *,
    frequencies: Optional[Sequence[float]] = None,
    n_freq: int = 200,
    normalisation: str = "standard",
) -> Dict[str, Any]:
    """Lomb-Scargle periodogram for unevenly-sampled time series.

    Standard formulation (Press & Rybicki 1989). Returns the spectrum
    on the supplied frequency grid + the dominant frequency / period.
    """
    if np is None:
        raise RuntimeError("numpy required")
    t = np.asarray(times, dtype=float)
    y = np.asarray(values, dtype=float)
    mask = ~(np.isnan(t) | np.isnan(y))
    t = t[mask]; y = y[mask]
    n = len(t)
    if n < 8:
        return {"available": False, "skipped_reason": "fewer_than_8_obs"}
    y_centred = y - y.mean()
    var_y = float(np.var(y, ddof=1))
    if var_y <= 0:
        return {"available": False, "skipped_reason": "zero_variance"}
    if frequencies is None:
        # Default frequency grid: from 1/T to Nyquist for the median dt.
        T_total = float(t.max() - t.min())
        dt_med = float(np.median(np.diff(np.sort(t))))
        f_min = 1.0 / T_total if T_total > 0 else 0.01
        f_max = 0.5 / max(1e-9, dt_med)
        frequencies = np.linspace(f_min, f_max, n_freq)
    freqs = np.asarray(frequencies, dtype=float)
    omega = 2 * math.pi * freqs
    P = np.empty(len(freqs))
    for k, w in enumerate(omega):
        sin2wt = np.sin(2 * w * t).sum()
        cos2wt = np.cos(2 * w * t).sum()
        if sin2wt == 0 and cos2wt == 0:
            tau = 0.0
        else:
            tau = 0.5 * math.atan2(sin2wt, cos2wt) / max(1e-12, w)
        coswt = np.cos(w * (t - tau))
        sinwt = np.sin(w * (t - tau))
        c2 = float(np.sum(coswt ** 2))
        s2 = float(np.sum(sinwt ** 2))
        c = float(np.sum(y_centred * coswt))
        s = float(np.sum(y_centred * sinwt))
        if c2 <= 0 or s2 <= 0:
            P[k] = 0.0; continue
        P[k] = 0.5 * (c ** 2 / c2 + s ** 2 / s2)
    if normalisation == "standard":
        P = P / var_y
    dominant_idx = int(np.argmax(P))
    dominant_freq = float(freqs[dominant_idx])
    dominant_period = 1.0 / dominant_freq if dominant_freq > 0 else None
    # False-alarm probability via Horne-Baliunas (Press 1992):
    # FAP = 1 - (1 - exp(-Pmax))^M where M ≈ N independent freqs.
    Pmax = float(P[dominant_idx])
    M = max(1, len(freqs))
    fap = 1.0 - (1.0 - math.exp(-Pmax)) ** M
    return {
        "available": True,
        "frequencies": freqs.tolist(),
        "power": P.tolist(),
        "dominant_frequency": dominant_freq,
        "dominant_period": dominant_period,
        "max_power": Pmax,
        "false_alarm_probability": float(fap),
        "n_obs": n,
        "method": "lomb_scargle_press_rybicki",
        "normalisation": normalisation,
    }
