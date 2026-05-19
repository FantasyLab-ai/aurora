"""
Spectral entropy — Shannon entropy of a signal's power spectrum.

Aurora already runs Lomb-Scargle for periodicity detection and Morlet
wavelets for non-stationary oscillations. Spectral entropy is the
complementary single-number summary: **how concentrated** the signal's
energy is in frequency space.

  * Low spectral entropy (near 0) — signal is highly periodic; energy
    concentrates in a few frequencies. Examples: sine waves, clocks.
  * High spectral entropy (near 1, normalised) — energy is spread
    uniformly across frequencies. Examples: white noise, broadband
    chaos.
  * Intermediate — typical real-world signals: a few dominant
    frequencies + broad background.

When this number jumps between time windows it signals a structural
regime change in the signal's spectral character — often correlated
with regime changes the HMM / ruptures methods also detect, but via a
totally different mathematical route. Triangulation matters: when two
independent methods flag the same structural break, confidence is high.

Pure NumPy. No new deps.
"""
from __future__ import annotations

import logging
import math
from typing import Any, Dict, List, Optional, Tuple

LOG = logging.getLogger(__name__)


SPECTRAL_ENTROPY_KB_CITATIONS = [
    {
        "seed_id": "seed:shannon_1948",
        "title": "A Mathematical Theory of Communication",
        "authors": "Shannon, C.E.",
        "year": 1948,
        "venue": "Bell System Technical Journal 27(3)",
        "summary": (
            "Defines entropy as -Σ p log p, the foundation of information "
            "theory. Spectral entropy applies this to the power spectrum "
            "of a signal: a uniform spectrum has maximum entropy, a "
            "delta-function spectrum (pure tone) has zero entropy."
        ),
        "doi": "10.1002/j.1538-7305.1948.tb01338.x",
    },
    {
        "seed_id": "seed:inouye_1991_spectral_entropy",
        "title": "Quantification of EEG irregularity by use of the entropy of the power spectrum",
        "authors": "Inouye, T., Shinosaki, K., Sakamoto, H., et al.",
        "year": 1991,
        "venue": "Electroencephalography and Clinical Neurophysiology 79(3)",
        "summary": (
            "Early applied paper popularising spectral entropy as a "
            "complexity measure for biomedical time series. Aurora uses "
            "this normalised-Shannon framing."
        ),
        "doi": "10.1016/0013-4694(91)90138-T",
    },
]


def fit_spectral_entropy(
    df: Any,
    *,
    target_col: Optional[str] = None,
    window_size: Optional[int] = None,
    window_step: Optional[int] = None,
    claim_seq: int = 0,
) -> Dict[str, Any]:
    """Compute the spectral entropy of a column. When ``window_size`` is
    set, computes the entropy in rolling windows and reports the max
    window-to-window change as a "spectral regime shift" signal.

    Args:
        df:           pandas DataFrame.
        target_col:   Column to analyse. None → first variable numeric.
        window_size:  If set, run rolling spectral entropy across windows
                      of this size. Default None → single global value.
        window_step:  Step between windows. Default = window_size // 2
                      (50% overlap).
        claim_seq:    Index for the claim_id.

    Returns:
        Aurora-shaped finding with the global spectral entropy and (if
        windowed) the largest entropy delta + window indices.
    """
    try:
        import numpy as np
    except ImportError:
        return _skip("numpy not available", claim_seq)

    if target_col is None:
        for c in df.columns:
            try:
                series = df[c].dropna().astype(float)
                if len(series) >= 32 and float(series.var()) > 1e-12:
                    target_col = c
                    break
            except (TypeError, ValueError):
                continue
    if target_col is None or target_col not in df.columns:
        return _skip("Spectral entropy: no usable numeric column.", claim_seq)

    x = df[target_col].dropna().astype(float).values
    n = len(x)
    if n < 32:
        return _skip(f"Spectral entropy needs ≥32 observations; got {n}", claim_seq)

    global_h = _spectral_entropy(x)

    window_results: List[Dict[str, Any]] = []
    biggest_jump = None
    if window_size and window_size <= n:
        step = window_step or max(1, window_size // 2)
        for start in range(0, n - window_size + 1, step):
            sub = x[start:start + window_size]
            h = _spectral_entropy(sub)
            window_results.append({
                "start_row": int(start),
                "end_row": int(start + window_size - 1),
                "spectral_entropy": round(float(h), 4),
            })
        # Largest window-to-window delta.
        if len(window_results) >= 2:
            deltas = []
            for i in range(1, len(window_results)):
                a = window_results[i - 1]["spectral_entropy"]
                b = window_results[i]["spectral_entropy"]
                deltas.append((abs(b - a), i, a, b))
            deltas.sort(reverse=True)
            d, i, a, b = deltas[0]
            biggest_jump = {
                "between_windows": (i - 1, i),
                "entropy_change": round(d, 4),
                "from": a, "to": b,
                "row_range": [window_results[i - 1]["start_row"],
                               window_results[i]["end_row"]],
            }

    # Classify the global entropy.
    if global_h < 0.4:
        regime = "highly periodic"
        sev = "info"
    elif global_h < 0.7:
        regime = "mixed periodic+broadband"
        sev = "info"
    else:
        regime = "broadband / near-white"
        sev = "info"

    description = (
        f"Normalised spectral entropy of {target_col!r}: {global_h:.3f} "
        f"({regime}). "
        + (f"Strongest windowed jump: Δ{biggest_jump['entropy_change']:.3f} "
            f"between windows {biggest_jump['between_windows']}." if biggest_jump else "")
    )

    return {
        "severity": sev,
        "confidence": 0.8,
        "title": f"Spectral entropy of {target_col!r}: {global_h:.3f} ({regime})",
        "description": description,
        "method": "spectral_entropy",
        "actions": ["VIEW EVIDENCE"],
        "kind_hint": "signal_complexity",
        "claim_id": f"spectral_entropy-{claim_seq:04d}",
        "fabricated": False,
        "kb_citations": SPECTRAL_ENTROPY_KB_CITATIONS,
        "evidence": {
            "status": "fit",
            "target_col": target_col,
            "n_obs": int(n),
            "global_spectral_entropy": round(float(global_h), 4),
            "regime_class": regime,
            "window_results": window_results,
            "biggest_window_jump": biggest_jump,
        },
    }


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

def _spectral_entropy(x) -> float:
    """Normalised Shannon entropy of the power spectrum of ``x``.

    Returns a value in [0, 1]:
       0  = all power at one frequency (pure tone)
       1  = uniform power (white noise)
    """
    import numpy as np
    x = np.asarray(x, dtype=float)
    x = x - x.mean()
    if len(x) < 2 or np.all(x == 0):
        return 0.0
    # Power spectrum via FFT.
    fft = np.fft.rfft(x)
    psd = np.abs(fft) ** 2
    # Drop DC; we just mean-centred.
    psd = psd[1:]
    total = psd.sum()
    if total <= 0:
        return 0.0
    p = psd / total
    # Shannon entropy in nats, normalised by log(N).
    nonzero = p[p > 0]
    h = -float(np.sum(nonzero * np.log(nonzero)))
    return h / math.log(len(p)) if len(p) > 1 else 0.0


def _skip(reason: str, claim_seq: int) -> Dict[str, Any]:
    return {
        "severity": "info", "confidence": 1.0,
        "title": "Spectral entropy skipped",
        "description": reason,
        "method": "spectral_entropy",
        "actions": [], "kind_hint": "method_skipped",
        "claim_id": "spectral_entropy-skipped",
        "fabricated": False,
        "evidence": {"status": "skipped", "reason": reason},
    }
