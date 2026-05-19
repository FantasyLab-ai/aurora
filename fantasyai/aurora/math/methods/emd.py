"""
Empirical Mode Decomposition (EMD) — non-stationary signal decomposition.

Where Aurora's wavelet methods (Morlet CWT) decompose a signal in
fixed-frequency bands, EMD adaptively decomposes a signal into
**Intrinsic Mode Functions (IMFs)** — local oscillations whose
frequency content changes over time. This is the right tool for:

  * Non-stationary signals (frequency drift, regime-dependent cycles)
  * Signals with sharp transients that wavelets smear
  * Mode separation when the user wants to see "the fast cycle"
    separated from "the slow trend" without choosing frequency bands
    a priori

Algorithm: Huang et al. (1998). For each iteration the "sifting"
process:
  1. Identifies upper + lower envelopes via local extrema
  2. Subtracts the mean of the envelopes
  3. Repeats until the residual is "monotonic enough" (Standard
     Deviation criterion < 0.3)
The IMF is extracted, the residual becomes the next signal, and the
process repeats until the residual has fewer than 2 extrema.

Pure NumPy + scipy (already a dep via statsmodels). No new deps.
"""
from __future__ import annotations

import logging
import math
from typing import Any, Dict, List, Optional, Tuple

LOG = logging.getLogger(__name__)


EMD_KB_CITATIONS = [
    {
        "seed_id": "seed:huang_1998_emd",
        "title": "The empirical mode decomposition and the Hilbert spectrum for nonlinear and non-stationary time series analysis",
        "authors": "Huang, N.E., Shen, Z., Long, S.R., et al.",
        "year": 1998,
        "venue": "Proc. Royal Society A 454(1971)",
        "summary": (
            "The original EMD paper. Defines Intrinsic Mode Functions, the "
            "sifting process, and the Hilbert-Huang Transform that EMD "
            "underpins."
        ),
        "doi": "10.1098/rspa.1998.0193",
    },
    {
        "seed_id": "seed:rilling_2003_emd_practical",
        "title": "On Empirical Mode Decomposition and its Algorithms",
        "authors": "Rilling, G., Flandrin, P., and Gonçalves, P.",
        "year": 2003,
        "venue": "IEEE-EURASIP Workshop on Nonlinear Signal and Image Processing",
        "summary": (
            "Practical refinements to the original EMD algorithm: cubic-spline "
            "envelope interpolation, the standard-deviation stopping "
            "criterion, and boundary-condition handling. Aurora follows "
            "this formulation."
        ),
        "url": "https://hal.science/hal-02541985",
    },
]


def fit_emd(
    df: Any,
    *,
    target_col: Optional[str] = None,
    max_imfs: int = 5,
    sd_threshold: float = 0.3,
    max_sift_iter: int = 50,
    claim_seq: int = 0,
) -> Dict[str, Any]:
    """Decompose a column into Intrinsic Mode Functions.

    Args:
        df:            pandas DataFrame.
        target_col:    Column to decompose. None → first numeric
                       column with variance.
        max_imfs:      Stop after extracting this many IMFs.
        sd_threshold:  SD-stopping threshold for the sifting process.
                       0.2–0.3 is Huang's recommended range.
        max_sift_iter: Maximum sifting iterations per IMF.
        claim_seq:     Index for the claim_id.

    Returns:
        Aurora-shaped finding summarising the IMFs (count, energy per
        mode, dominant frequency per mode).
    """
    try:
        import numpy as np
    except ImportError:
        return _skip("numpy not available", claim_seq)

    if target_col is None:
        for c in df.columns:
            try:
                series = df[c].dropna().astype(float)
                if len(series) >= 30 and float(series.var()) > 1e-12:
                    target_col = c
                    break
            except (TypeError, ValueError):
                continue
    if target_col is None or target_col not in df.columns:
        return _skip("EMD couldn't find a usable numeric column.", claim_seq)

    x = df[target_col].dropna().astype(float).values
    n = len(x)
    if n < 30:
        return _skip(f"EMD needs ≥30 observations; got {n}", claim_seq)

    try:
        imfs, residual, sift_iters = _sift_to_imfs(
            x, max_imfs=max_imfs, sd_threshold=sd_threshold,
            max_sift_iter=max_sift_iter,
        )
    except Exception as e:
        LOG.exception("EMD failed")
        return {
            "severity": "info", "confidence": 0.0,
            "title": "EMD failed",
            "description": f"{type(e).__name__}: {e}",
            "method": "emd",
            "actions": [],
            "kind_hint": "emd_failed",
            "claim_id": f"emd-{claim_seq:04d}",
            "fabricated": False,
            "evidence": {"status": "failed", "error": str(e)},
        }

    # Per-IMF stats: energy + estimated period via zero crossings.
    imf_stats = []
    for i, imf in enumerate(imfs):
        energy = float(np.sum(imf ** 2))
        zc = _zero_crossings(imf)
        # Each full cycle has 2 zero crossings → period ≈ 2N/zc.
        period = float(2 * n / zc) if zc > 0 else float("inf")
        imf_stats.append({
            "imf_index": i,
            "energy": round(energy, 4),
            "estimated_period_samples": round(period, 2)
                if math.isfinite(period) else None,
            "zero_crossings": int(zc),
        })

    description = (
        f"Decomposed {target_col!r} into {len(imfs)} IMF(s) plus a residual. "
        f"Periods (samples): "
        + ", ".join(
            f"{s['estimated_period_samples']:.0f}" if s['estimated_period_samples']
            else "monotonic"
            for s in imf_stats[:5]
        )
    )

    return {
        "severity": "info",
        "confidence": 0.7,
        "title": f"EMD on {target_col!r}: {len(imfs)} intrinsic mode(s)",
        "description": description,
        "method": "emd",
        "actions": ["VIEW EVIDENCE"],
        "kind_hint": "adaptive_decomposition",
        "claim_id": f"emd-{claim_seq:04d}",
        "fabricated": False,
        "kb_citations": EMD_KB_CITATIONS,
        "evidence": {
            "status": "fit",
            "target_col": target_col,
            "n_obs": int(n),
            "n_imfs": len(imfs),
            "sift_iterations": list(sift_iters),
            "imf_stats": imf_stats,
            "residual_range": [float(np.min(residual)), float(np.max(residual))],
        },
    }


# ---------------------------------------------------------------------------
# Sifting + IMF extraction
# ---------------------------------------------------------------------------

def _sift_to_imfs(
    signal,
    *,
    max_imfs: int,
    sd_threshold: float,
    max_sift_iter: int,
) -> Tuple[List, Any, List[int]]:
    """Repeatedly sift IMFs out of ``signal`` until either ``max_imfs``
    have been extracted or the residual is monotonic."""
    import numpy as np
    residual = signal.copy().astype(float)
    imfs: List[Any] = []
    iters: List[int] = []
    for _ in range(max_imfs):
        # Stop if residual has fewer than 2 extrema (trend remaining).
        if _count_extrema(residual) < 2:
            break
        imf, sift_count = _extract_one_imf(
            residual, sd_threshold=sd_threshold,
            max_sift_iter=max_sift_iter,
        )
        imfs.append(imf)
        iters.append(sift_count)
        residual = residual - imf
    return imfs, residual, iters


def _extract_one_imf(
    signal,
    *,
    sd_threshold: float,
    max_sift_iter: int,
) -> Tuple[Any, int]:
    """Sift one IMF out of ``signal``. Returns (imf, n_sift_iterations)."""
    import numpy as np
    h = signal.copy().astype(float)
    for k in range(max_sift_iter):
        upper, lower = _envelopes(h)
        m = (upper + lower) / 2.0
        h_new = h - m
        sd = float(np.sum((h - h_new) ** 2) / (np.sum(h ** 2) + 1e-12))
        h = h_new
        if sd < sd_threshold:
            return h, k + 1
    return h, max_sift_iter


def _envelopes(signal):
    """Compute upper + lower envelopes via cubic spline through the
    extrema. Boundary conditions: clamped to the first/last extremum."""
    import numpy as np
    from scipy.interpolate import CubicSpline

    n = len(signal)
    idx = np.arange(n)

    # Find local maxima + minima.
    maxima = _local_extrema(signal, kind="max")
    minima = _local_extrema(signal, kind="min")

    # Need at least 2 extrema of each type to spline.
    if len(maxima) < 2 or len(minima) < 2:
        # Degenerate signal — return zero envelopes so subtraction is a no-op.
        return np.zeros(n), np.zeros(n)

    # Extend with boundary anchors so the spline doesn't blow up at the ends.
    maxima = _extend_boundaries(maxima, signal, kind="max")
    minima = _extend_boundaries(minima, signal, kind="min")

    cs_u = CubicSpline(maxima[:, 0], maxima[:, 1])
    cs_l = CubicSpline(minima[:, 0], minima[:, 1])
    upper = cs_u(idx)
    lower = cs_l(idx)
    return upper, lower


def _local_extrema(signal, *, kind: str):
    """Return Nx2 array of (index, value) for local maxima or minima."""
    import numpy as np
    sig = signal.astype(float)
    cmp = (sig[1:-1] > sig[:-2]) & (sig[1:-1] > sig[2:]) if kind == "max" \
        else (sig[1:-1] < sig[:-2]) & (sig[1:-1] < sig[2:])
    indices = np.where(cmp)[0] + 1
    if len(indices) == 0:
        return np.zeros((0, 2))
    return np.column_stack((indices, sig[indices]))


def _extend_boundaries(extrema, signal, *, kind: str):
    """Add boundary anchors at 0 and len-1 by mirroring the first/last
    extremum. Prevents the cubic spline from oscillating wildly at edges."""
    import numpy as np
    n = len(signal)
    first_idx, first_val = extrema[0]
    last_idx, last_val = extrema[-1]
    # Reflect first extremum about index 0, last about n-1.
    left = np.array([[0.0, first_val]])
    right = np.array([[float(n - 1), last_val]])
    return np.vstack([left, extrema, right])


def _count_extrema(signal) -> int:
    import numpy as np
    sig = signal.astype(float)
    if len(sig) < 3:
        return 0
    is_max = (sig[1:-1] > sig[:-2]) & (sig[1:-1] > sig[2:])
    is_min = (sig[1:-1] < sig[:-2]) & (sig[1:-1] < sig[2:])
    return int(np.sum(is_max) + np.sum(is_min))


def _zero_crossings(signal) -> int:
    import numpy as np
    sig = np.asarray(signal, dtype=float)
    return int(np.sum(np.diff(np.sign(sig)) != 0))


def _skip(reason: str, claim_seq: int) -> Dict[str, Any]:
    return {
        "severity": "info", "confidence": 1.0,
        "title": "EMD skipped",
        "description": reason,
        "method": "emd",
        "actions": [], "kind_hint": "method_skipped",
        "claim_id": "emd-skipped",
        "fabricated": False,
        "evidence": {"status": "skipped", "reason": reason},
    }
