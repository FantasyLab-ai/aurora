"""
Aurora method-coverage expansion (Stream 1.2 in the 12-month plan).

This package houses *new* analytical methods added to round out Aurora's
existing 17-method library. They live here rather than in the existing
``deep_math`` / ``mathstack_v2`` files because:

  1. Each method is self-contained — easier to test, easier to audit,
     easier to swap implementations.
  2. The community-contributed-methods plugin SDK (roadmap item 1.8)
     will land here too, with the same contract: each method exposes
     ``fit(df) -> finding_dict`` and gets its own file.
  3. Adding to the legacy files risks touching code that downstream
     state_builder logic depends on.

Each module here provides at minimum:

  * One callable ``fit_X(df, **kwargs) -> dict`` that returns an
    Aurora-shaped finding (severity, confidence, title, description,
    method, claim_id, fabricated=False).
  * A short ``KB_CITATIONS`` list so the RAG layer can ground the
    finding's narrative.
  * A precondition check so the method skips cleanly when the data
    shape doesn't fit (e.g., VAR needs ≥ 2 numeric columns + a time
    axis).

Public API:

    from fantasyai.aurora.math.methods import fit_var, fit_dtw, fit_bocpd
    result = fit_var(df, target_cols=["close", "volume"])
"""
from __future__ import annotations

from .var import fit_var, VAR_KB_CITATIONS
from .dtw import fit_dtw, dtw_distance, DTW_KB_CITATIONS
from .bocpd import fit_bocpd, BOCPD_KB_CITATIONS
from .robust_pca import fit_robust_pca, ROBUST_PCA_KB_CITATIONS
from .emd import fit_emd, EMD_KB_CITATIONS
from .kalman import fit_kalman, KALMAN_KB_CITATIONS
from .spectral_entropy import fit_spectral_entropy, SPECTRAL_ENTROPY_KB_CITATIONS

__all__ = [
    # Methods shipped in v1.2 (Stream 1.2 of the 12-month plan).
    "fit_var",
    "fit_dtw",
    "dtw_distance",
    "fit_bocpd",
    "fit_robust_pca",
    "fit_emd",
    "fit_kalman",
    "fit_spectral_entropy",
    # Citation lists (used by the RAG layer + research kit).
    "VAR_KB_CITATIONS",
    "DTW_KB_CITATIONS",
    "BOCPD_KB_CITATIONS",
    "ROBUST_PCA_KB_CITATIONS",
    "EMD_KB_CITATIONS",
    "KALMAN_KB_CITATIONS",
    "SPECTRAL_ENTROPY_KB_CITATIONS",
]
