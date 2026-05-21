"""
Matrix Profile method wrapper for the v1.2 extended_runner.

The core algorithm already exists at `fantasyai.aurora.matrix_profile` —
this module just wraps it in the canonical fit_*() shape so it slots
into the extended_runner orchestrator alongside VAR / DTW / BOCPD /
Robust PCA / EMD / Kalman / Spectral Entropy.

For quantitative trading workflows this is high-value signal:

    * **Motifs** — recurring patterns. "Has this 50-bar setup occurred
      before? Where? What followed?"
    * **Discords** — uniquely-different windows. The classic anomaly
      surface for price/volume series.

Algorithm: FFT-accelerated MASS (Mueen's Algorithm for Similarity
Search) with z-normalised Euclidean distance. O(n² log n) overall;
n=10K runs in well under a second.

Skip conditions:
    * Series too short for the heuristic window (n < window + 4)
    * No numeric target column resolvable

Citations attached for the bundle's KB layer to surface.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

LOG = logging.getLogger(__name__)


MATRIX_PROFILE_KB_CITATIONS = [
    {
        "id": "yeh_matrix_profile_2016",
        "authors": "Yeh, C-C. M., Zhu, Y., Ulanova, L., et al.",
        "year": 2016,
        "title": "Matrix Profile I: All Pairs Similarity Joins for Time Series",
        "venue": "ICDM",
        "domain": "time_series_motifs",
    },
    {
        "id": "mueen_mass_2014",
        "authors": "Mueen, A.",
        "year": 2014,
        "title": "Mueen's Algorithm for Similarity Search (MASS)",
        "venue": "Technical Report",
        "domain": "time_series_motifs",
    },
]


def fit_matrix_profile(
    df: Any,
    *,
    target_col: Optional[str] = None,
    window: Optional[int] = None,
    n_motifs: int = 3,
    n_discords: int = 3,
    claim_seq: int = 0,
) -> Dict[str, Any]:
    """Compute matrix profile motifs + discords on a numeric column.

    Returns an Aurora-shaped finding dict (same shape every method
    in extended_runner emits).
    """
    # Lazy imports — keep cold-start cheap.
    import math
    try:
        import pandas as pd
        import numpy as np
    except ImportError as e:
        return _failed(claim_seq, f"missing dep: {e}")

    try:
        from fantasyai.aurora.matrix_profile import compute_motifs_and_discords
    except Exception as e:
        return _failed(claim_seq, f"matrix_profile module unavailable: {e}")

    # Resolve target column.
    try:
        numeric_cols = [c for c in df.columns
                         if pd.api.types.is_numeric_dtype(df[c].dtype)]
    except Exception as e:
        return _failed(claim_seq, f"could not enumerate columns: {e}")

    if not numeric_cols:
        return _skipped(claim_seq, "no numeric columns")

    col = target_col if (target_col and target_col in numeric_cols) \
           else numeric_cols[0]

    series_obj = df[col].dropna()
    if series_obj.empty:
        return _skipped(claim_seq, f"target column {col!r} is empty")

    series = series_obj.astype(float).values
    n = len(series)
    if n < 30:
        return _skipped(claim_seq, f"series too short (n={n}; need ≥30)")

    # Default window = 5% of series, clamped [10, 200]. Same heuristic
    # compute_motifs_and_discords uses internally.
    try:
        result = compute_motifs_and_discords(
            series, window=window,
            n_motifs=int(n_motifs), n_discords=int(n_discords),
        )
    except Exception as e:
        LOG.exception("matrix_profile crashed")
        return _failed(claim_seq, str(e))

    if not result.get("available"):
        return _skipped(claim_seq, str(result.get("reason") or "unknown"))

    motifs = result.get("motifs") or []
    discords = result.get("discords") or []
    stats = result.get("profile_stats") or {}

    title_bits: List[str] = []
    if motifs:
        m0 = motifs[0]
        title_bits.append(
            f"top motif: rows {m0.get('start_a')}↔{m0.get('start_b')} "
            f"(d={m0.get('distance')})"
        )
    if discords:
        d0 = discords[0]
        title_bits.append(
            f"top discord: row {d0.get('start')} (d={d0.get('distance')})"
        )
    title = (
        f"Matrix profile on {col!r}: {len(motifs)} motifs, {len(discords)} discords"
        + (f" — {title_bits[0]}" if title_bits else "")
    )

    desc_parts = [
        f"Computed the matrix profile (Yeh et al. 2016) on column {col!r} "
        f"with window size {result.get('window')} over {n} samples."
    ]
    if motifs:
        desc_parts.append(
            f"Found {len(motifs)} recurring motif(s); the strongest is "
            f"at positions {motifs[0].get('start_a')} and "
            f"{motifs[0].get('start_b')} (z-normalised distance "
            f"{motifs[0].get('distance')})."
        )
    if discords:
        desc_parts.append(
            f"Found {len(discords)} discord(s); the most unusual window "
            f"begins at position {discords[0].get('start')} "
            f"(distance {discords[0].get('distance')})."
        )

    return {
        "severity": "warn" if len(discords) >= 2 else "info",
        "confidence": 0.85,
        "title": title,
        "description": " ".join(desc_parts),
        "method": "matrix_profile",
        "actions": ["VIEW EVIDENCE"],
        "kind_hint": "motif_discovery",
        "claim_id": f"matrix_profile-{claim_seq:04d}",
        "fabricated": False,
        "kb_citations": MATRIX_PROFILE_KB_CITATIONS,
        "evidence": {
            "status":        "fit",
            "target_col":    col,
            "n_obs":         int(n),
            "window":        int(result.get("window") or 0),
            "motifs":        list(motifs),
            "discords":      list(discords),
            "profile_stats": dict(stats),
            "n_motifs":      len(motifs),
            "n_discords":    len(discords),
        },
    }


def _skipped(claim_seq: int, reason: str) -> Dict[str, Any]:
    return {
        "severity": "info", "confidence": 1.0,
        "title": "Matrix profile skipped",
        "description": reason,
        "method": "matrix_profile",
        "actions": [], "kind_hint": "method_skipped",
        "claim_id": f"matrix_profile-skipped-{claim_seq:04d}",
        "fabricated": False,
        "evidence": {"status": "skipped", "reason": reason},
    }


def _failed(claim_seq: int, error: str) -> Dict[str, Any]:
    return {
        "severity": "info", "confidence": 0.0,
        "title": "Matrix profile failed",
        "description": error,
        "method": "matrix_profile",
        "actions": [], "kind_hint": "method_failed",
        "claim_id": f"matrix_profile-failed-{claim_seq:04d}",
        "fabricated": False,
        "evidence": {"status": "failed", "error": error},
    }
