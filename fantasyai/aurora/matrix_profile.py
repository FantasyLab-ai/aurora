"""
Matrix profile — STUMPY-style motif and discord discovery in pure numpy.

The matrix profile is the distance from each window in a series to its
nearest non-trivial-match neighbour. It's the right tool for asking:

* Where are the **motifs** (recurring shapes) in this series? — minima of P
* Where are the **discords** (uniquely-shaped windows / anomalies)? — maxima

Why not use the ``stumpy`` library directly? Aurora's invariant is local-first
with minimal dependencies. The Mueen-Yeh STAMP algorithm is small enough
(<200 lines) to ship pure-numpy. We use FFT-based cross-correlation so the
implementation is O(N² log N) — fast enough for series up to ~50k samples.

Public API
----------

    matrix_profile(series, window) -> (P, P_idx)
        P : ndarray of shape (n - window + 1,) — distance to nearest neighbour
        P_idx : ndarray of shape (n - window + 1,) — index of that neighbour

    top_motifs(series, window, k=3, exclusion_factor=0.5) -> List[Motif]
        — top-k recurring patterns

    top_discords(series, window, k=3) -> List[Discord]
        — top-k uniquely-shaped windows (anomalies)

    compute_motifs_and_discords(series, window, …) -> dict
        — convenience: both at once + summary stats
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any, Dict, List, Optional, Tuple
import math

import numpy as np


@dataclass
class Motif:
    """A recurring pattern: two windows whose shapes are very similar."""
    rank: int
    start_a: int
    start_b: int
    distance: float
    window: int

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        for k, v in list(d.items()):
            if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
                d[k] = None
        return d


@dataclass
class Discord:
    """A window whose shape is uniquely different from anything else."""
    rank: int
    start: int
    distance: float
    window: int

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        for k, v in list(d.items()):
            if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
                d[k] = None
        return d


# ----------------------------------------------------------------------
# Core matrix-profile algorithm
# ----------------------------------------------------------------------

def _sliding_mean_std(x: np.ndarray, window: int) -> Tuple[np.ndarray, np.ndarray]:
    """O(n) mean and std for every window using cumulative sums."""
    x = np.asarray(x, dtype=float)
    cs = np.concatenate(([0.0], np.cumsum(x)))
    cs2 = np.concatenate(([0.0], np.cumsum(x * x)))
    s1 = cs[window:] - cs[:-window]                 # shape (n - w + 1,)
    s2 = cs2[window:] - cs2[:-window]
    m = s1 / window
    var = s2 / window - m * m
    var = np.maximum(var, 0.0)                       # numerical safety
    s = np.sqrt(var)
    # Avoid divide-by-zero downstream: where std is 0, leave it; we'll mask.
    return m, s


def _sliding_dot(x: np.ndarray, q: np.ndarray) -> np.ndarray:
    """FFT-accelerated sliding dot-product of x with the (reversed) query q.

    Returns z[i] = sum_{k=0}^{m-1} x[i+k] * q[k] for valid i.
    """
    n = x.size
    m = q.size
    n_fft = 1 << int(np.ceil(np.log2(n + m)))
    X = np.fft.rfft(x, n_fft)
    Qr = np.fft.rfft(q[::-1], n_fft)
    z = np.fft.irfft(X * Qr, n_fft)
    # The valid window-dot-products are at offsets m-1 .. m-1 + (n - m).
    return z[m - 1 : m - 1 + (n - m + 1)]


def _mass(x: np.ndarray, q: np.ndarray,
          mu: np.ndarray, sigma: np.ndarray) -> np.ndarray:
    """Z-normalized Euclidean distance from query q to every window in x.

    Args:
        x:     full series
        q:     query window (length m)
        mu:    sliding mean of x with window m
        sigma: sliding std of x with window m
    """
    m = q.size
    q_mu = float(np.mean(q))
    q_sig = float(np.std(q))
    if q_sig == 0:
        # Constant query — distance to a window is meaningful only when the
        # window is also constant. Return inf elsewhere so it doesn't pollute.
        out = np.where(sigma == 0, 0.0, np.inf)
        return out
    QT = _sliding_dot(x, q)
    # Pearson r = (QT - m·mu·q_mu) / (m · sigma · q_sig)
    denom = (m * sigma * q_sig)
    with np.errstate(divide="ignore", invalid="ignore"):
        r = (QT - m * mu * q_mu) / denom
    r = np.where(np.isfinite(r), r, 1.0)
    r = np.clip(r, -1.0, 1.0)
    # Z-normalized squared Euclidean distance = 2m(1 - r); take sqrt.
    d2 = 2 * m * (1 - r)
    d2 = np.where(d2 < 0, 0.0, d2)
    d = np.sqrt(d2)
    # Where the local std is 0, distance isn't well-defined → mark inf.
    d = np.where(sigma == 0, np.inf, d)
    return d


def matrix_profile(series: np.ndarray, window: int,
                   exclusion_zone: Optional[int] = None) -> Tuple[np.ndarray, np.ndarray]:
    """Compute the matrix profile of ``series`` with the given window length.

    Returns:
        P:     shape (n - window + 1,) — distance to nearest non-trivial neighbour
        P_idx: shape (n - window + 1,) — index of that neighbour
    """
    x = np.asarray(series, dtype=float)
    n = x.size
    m = int(window)
    if m <= 1 or n < m + 2:
        n_p = max(1, n - m + 1)
        return np.full(n_p, np.inf), np.full(n_p, -1, dtype=int)

    # Default exclusion zone is m/2 (Yeh et al.).
    ez = exclusion_zone if exclusion_zone is not None else m // 2

    mu, sigma = _sliding_mean_std(x, m)
    n_p = n - m + 1
    P = np.full(n_p, np.inf)
    P_idx = np.full(n_p, -1, dtype=int)

    # STAMP: for each i, compute MASS(query=x[i:i+m]) → distance vector D,
    # mask the trivial-match exclusion zone, update P and P_idx.
    for i in range(n_p):
        if not np.all(np.isfinite(x[i : i + m])):
            continue
        D = _mass(x, x[i : i + m], mu, sigma)
        # Exclusion zone around i.
        lo = max(0, i - ez)
        hi = min(n_p, i + ez + 1)
        D[lo:hi] = np.inf
        # Update P / P_idx where this is closer.
        better = D < P
        P[better] = D[better]
        P_idx[better] = i

    return P, P_idx


# ----------------------------------------------------------------------
# Motifs and discords
# ----------------------------------------------------------------------

def top_motifs(series: np.ndarray, window: int, k: int = 3,
               exclusion_factor: float = 0.5) -> List[Motif]:
    """Find the top-k motifs (recurring patterns) in the series."""
    P, P_idx = matrix_profile(series, window)
    return _extract_motifs(P, P_idx, window, k, exclusion_factor)


def top_discords(series: np.ndarray, window: int, k: int = 3,
                 exclusion_factor: float = 0.5) -> List[Discord]:
    """Find the top-k discords (uniquely-shaped windows / anomalies)."""
    P, _ = matrix_profile(series, window)
    return _extract_discords(P, window, k, exclusion_factor)


def _extract_motifs(P: np.ndarray, P_idx: np.ndarray, window: int,
                    k: int, exclusion_factor: float) -> List[Motif]:
    """Pull the k smallest profile values, with exclusion to avoid trivial overlap."""
    finite = np.isfinite(P)
    if not finite.any():
        return []
    # Order by ascending distance.
    order = np.argsort(P)
    motifs: List[Motif] = []
    used: List[Tuple[int, int]] = []
    excl = int(window * exclusion_factor)
    for idx in order:
        if not finite[idx] or len(motifs) >= k:
            break
        a, b = int(idx), int(P_idx[idx])
        if b < 0:
            continue
        # Skip if a or b overlap a previously-claimed motif region.
        if any(abs(a - ua) < excl or abs(a - ub) < excl or
               abs(b - ua) < excl or abs(b - ub) < excl
               for ua, ub in used):
            continue
        motifs.append(Motif(
            rank=len(motifs) + 1,
            start_a=a, start_b=b,
            distance=float(P[idx]),
            window=int(window),
        ))
        used.append((a, b))
    return motifs


def _extract_discords(P: np.ndarray, window: int, k: int,
                      exclusion_factor: float) -> List[Discord]:
    finite = np.isfinite(P)
    if not finite.any():
        return []
    # Largest matrix profile values = uniquely different windows.
    order = np.argsort(-np.where(finite, P, -np.inf))
    out: List[Discord] = []
    used: List[int] = []
    excl = int(window * exclusion_factor)
    for idx in order:
        if not finite[idx] or len(out) >= k:
            break
        if any(abs(int(idx) - u) < excl for u in used):
            continue
        out.append(Discord(
            rank=len(out) + 1,
            start=int(idx),
            distance=float(P[idx]),
            window=int(window),
        ))
        used.append(int(idx))
    return out


# ----------------------------------------------------------------------
# Convenience entry
# ----------------------------------------------------------------------

def compute_motifs_and_discords(series: np.ndarray,
                                 window: Optional[int] = None,
                                 n_motifs: int = 3,
                                 n_discords: int = 3,
                                 ) -> Dict[str, Any]:
    """One-shot: compute matrix profile, return motifs + discords + summary.

    Picks a sensible default window length when one isn't given (~5% of n).
    """
    x = np.asarray(series, dtype=float)
    n = x.size
    if window is None:
        # Heuristic: 5% of series, clamped to [10, 200].
        window = max(10, min(200, n // 20))
    if n < window + 4:
        return {"available": False, "reason": "series too short for window"}
    motifs = top_motifs(x, window, k=n_motifs)
    discords = top_discords(x, window, k=n_discords)
    P, _ = matrix_profile(x, window)
    finite = P[np.isfinite(P)]
    return {
        "available": True,
        "window": int(window),
        "n": int(n),
        "motifs": [m.to_dict() for m in motifs],
        "discords": [d.to_dict() for d in discords],
        "profile_stats": {
            "min": float(finite.min()) if finite.size else None,
            "max": float(finite.max()) if finite.size else None,
            "mean": float(finite.mean()) if finite.size else None,
            "std": float(finite.std()) if finite.size else None,
        },
    }
