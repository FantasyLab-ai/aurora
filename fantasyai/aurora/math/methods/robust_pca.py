"""
Robust PCA — low-rank + sparse decomposition (Candès et al. 2011).

Classical PCA assumes the data matrix decomposes into low-rank
structure + Gaussian noise. When the noise is **structured outliers**
(a few rows or cells with huge errors) classical PCA gets ruined — the
outliers dominate the top components. Robust PCA solves the right
problem: decompose X = L + S where L is low-rank and S is sparse
(few non-zero entries, but arbitrary magnitude).

Aurora uses Robust PCA to:

  * Separate "signal subspace" (L) from "anomaly mask" (S)
  * Surface the rows/columns where S has non-trivial mass — these are
    the outliers the rest of the pipeline should look at first
  * Provide a complement to Mahalanobis / Isolation Forest / LOF when
    those methods get confused by joint outliers

Algorithm: Inexact ALM (Augmented Lagrangian Method) from Lin, Chen,
& Ma (2010). It alternates between SVD soft-thresholding (for L) and
element-wise soft-thresholding (for S), with a multiplier update.
Converges in ~50 iterations for the matrix sizes Aurora handles
(< 10K rows × 100 columns).

Pure NumPy. No new dependencies.
"""
from __future__ import annotations

import logging
import math
from typing import Any, Dict, List, Optional, Tuple

LOG = logging.getLogger(__name__)


ROBUST_PCA_KB_CITATIONS = [
    {
        "seed_id": "seed:candes_2011_robust_pca",
        "title": "Robust Principal Component Analysis?",
        "authors": "Candès, E.J., Li, X., Ma, Y., and Wright, J.",
        "year": 2011,
        "venue": "Journal of the ACM 58(3)",
        "summary": (
            "Foundational paper proving that a matrix can be decomposed into "
            "low-rank + sparse components by convex optimisation (Principal "
            "Component Pursuit). Establishes the theoretical guarantee that "
            "Robust PCA recovers the true low-rank structure under mild "
            "assumptions about sparsity."
        ),
        "doi": "10.1145/1970392.1970395",
    },
    {
        "seed_id": "seed:lin_2010_inexact_alm",
        "title": "The Augmented Lagrange Multiplier Method for Exact Recovery of Corrupted Low-Rank Matrices",
        "authors": "Lin, Z., Chen, M., and Ma, Y.",
        "year": 2010,
        "venue": "arXiv:1009.5055",
        "summary": (
            "Practical Inexact ALM algorithm for Robust PCA. Faster than the "
            "exact APG method by an order of magnitude, with the same "
            "recovery guarantees. This is the algorithm Aurora implements."
        ),
        "url": "https://arxiv.org/abs/1009.5055",
    },
]


def fit_robust_pca(
    df: Any,
    *,
    target_cols: Optional[List[str]] = None,
    lam: Optional[float] = None,
    max_iter: int = 100,
    tol: float = 1e-7,
    claim_seq: int = 0,
) -> Dict[str, Any]:
    """Run Robust PCA on the numeric columns of ``df`` and report the
    sparse component's row magnitudes (outlier scores).

    Args:
        df:           pandas DataFrame.
        target_cols:  Columns to decompose. None → all numeric.
        lam:          Sparsity regulariser. None → Candès's default
                      1/sqrt(max(n_rows, n_cols)).
        max_iter:     Maximum ALM iterations.
        tol:          Convergence tolerance on ||X - L - S||_F / ||X||_F.
        claim_seq:    Index for the claim_id.

    Returns:
        Aurora-shaped finding with the per-row outlier scores from S.
    """
    try:
        import numpy as np
    except ImportError:
        return _skip("numpy not available", claim_seq)

    if target_cols is None:
        target_cols = [c for c in df.columns if _is_numeric(df[c])]
    if len(target_cols) < 2:
        return _skip(
            f"Robust PCA requires ≥2 numeric columns; found {len(target_cols)}",
            claim_seq,
        )

    sub = df[target_cols].dropna()
    if len(sub) < 10:
        return _skip(
            f"Robust PCA requires ≥10 rows after dropna; got {len(sub)}",
            claim_seq,
        )

    X = sub.astype(float).values
    n, p = X.shape

    # Standardise per column so the regulariser scales evenly. Avoid
    # division-by-zero on constant columns.
    means = X.mean(axis=0)
    stds = X.std(axis=0)
    stds[stds < 1e-12] = 1.0
    Xn = (X - means) / stds

    try:
        L, S, n_iter, converged = _inexact_alm(Xn, lam=lam,
                                                 max_iter=max_iter, tol=tol)
    except Exception as e:
        LOG.exception("Robust PCA failed")
        return {
            "severity": "info", "confidence": 0.0,
            "title": "Robust PCA failed",
            "description": f"{type(e).__name__}: {e}",
            "method": "robust_pca",
            "actions": [],
            "kind_hint": "robust_pca_failed",
            "claim_id": f"robust_pca-{claim_seq:04d}",
            "fabricated": False,
            "evidence": {"status": "failed", "error": str(e)},
        }

    # Per-row L2 norm of S → outlier score.
    row_scores = np.linalg.norm(S, axis=1)
    # Flag rows whose score is > 3 MAD above the median.
    med = float(np.median(row_scores))
    mad = float(np.median(np.abs(row_scores - med)))
    threshold = med + 3.0 * 1.4826 * mad  # 1.4826 ≈ Φ^-1(0.75), makes MAD ~ σ
    flagged = [int(i) for i in np.where(row_scores > threshold)[0]]
    flagged.sort(key=lambda i: -float(row_scores[i]))

    # Rank of the low-rank component (singular values above 1e-6).
    try:
        _u, s, _v = np.linalg.svd(L, full_matrices=False)
        rank_est = int(np.sum(s > 1e-6))
    except Exception:
        rank_est = -1

    top_flagged = [
        {"row_idx": i, "score": round(float(row_scores[i]), 4)}
        for i in flagged[:10]
    ]

    description = (
        f"Decomposed {n} rows × {p} cols into a rank-{rank_est} low-rank "
        f"component plus a sparse component. Flagged {len(flagged)} rows as "
        f"structured outliers (||S_row||₂ above 3-MAD). "
        f"ALM converged in {n_iter} iteration(s)."
        + (" (did not reach tol within max_iter)" if not converged else "")
    )

    severity = "warn" if len(flagged) >= 5 else "info"

    return {
        "severity": severity,
        "confidence": 0.85 if converged else 0.6,
        "title": (
            f"Robust PCA: rank-{rank_est} subspace + {len(flagged)} structured "
            f"outlier row(s)"
        ),
        "description": description,
        "method": "robust_pca",
        "actions": ["VIEW EVIDENCE"],
        "kind_hint": "low_rank_plus_sparse",
        "claim_id": f"robust_pca-{claim_seq:04d}",
        "fabricated": False,
        "kb_citations": ROBUST_PCA_KB_CITATIONS,
        "evidence": {
            "status": "fit",
            "n_obs": int(n),
            "n_vars": int(p),
            "low_rank_estimate": rank_est,
            "n_outlier_rows": len(flagged),
            "lam_used": float(lam if lam is not None else 1.0 / math.sqrt(max(n, p))),
            "iterations": int(n_iter),
            "converged": bool(converged),
            "top_outlier_rows": top_flagged,
        },
    }


# ---------------------------------------------------------------------------
# Internals: Inexact ALM solver
# ---------------------------------------------------------------------------

def _inexact_alm(
    X,
    *,
    lam: Optional[float] = None,
    max_iter: int = 100,
    tol: float = 1e-7,
) -> Tuple[Any, Any, int, bool]:
    """Inexact ALM solver for Robust PCA. Returns (L, S, n_iter, converged).

    Implements Algorithm 5 of Lin, Chen & Ma (2010). The key updates per
    iteration:

        L_{k+1} = D_{1/μ}(X - S_k + Y_k/μ)        # singular-value threshold
        S_{k+1} = S_{λ/μ}(X - L_{k+1} + Y_k/μ)    # element-wise threshold
        Y_{k+1} = Y_k + μ(X - L_{k+1} - S_{k+1})
        μ_{k+1} = min(μ_k * ρ, μ_max)

    With these the residual ||X - L - S||_F drops geometrically.
    """
    import numpy as np
    n, p = X.shape
    if lam is None:
        lam = 1.0 / math.sqrt(max(n, p))
    # Initialise per Lin et al. defaults.
    Y = X / max(np.linalg.norm(X, 2), np.linalg.norm(X, np.inf) / lam)
    mu = 1.25 / max(np.linalg.norm(X, 2), 1e-12)
    mu_max = mu * 1e7
    rho = 1.5
    L = np.zeros_like(X)
    S = np.zeros_like(X)
    converged = False
    X_norm = np.linalg.norm(X, "fro")

    for k in range(max_iter):
        # L-update: singular-value soft-thresholding.
        U, sigma, Vt = np.linalg.svd(X - S + Y / mu, full_matrices=False)
        sigma_thresh = np.maximum(sigma - 1.0 / mu, 0.0)
        L = (U * sigma_thresh) @ Vt
        # S-update: element-wise soft-thresholding.
        S = _soft_threshold(X - L + Y / mu, lam / mu)
        # Multiplier update.
        Z = X - L - S
        Y = Y + mu * Z
        mu = min(mu * rho, mu_max)
        err = np.linalg.norm(Z, "fro") / max(X_norm, 1e-12)
        if err < tol:
            converged = True
            return L, S, k + 1, converged
    return L, S, max_iter, converged


def _soft_threshold(X, tau):
    """Element-wise soft thresholding: sign(x) * max(|x| - τ, 0)."""
    import numpy as np
    return np.sign(X) * np.maximum(np.abs(X) - tau, 0.0)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_numeric(series: Any) -> bool:
    try:
        import pandas as pd
        return pd.api.types.is_numeric_dtype(series.dtype)
    except Exception:
        return False


def _skip(reason: str, claim_seq: int) -> Dict[str, Any]:
    return {
        "severity": "info", "confidence": 1.0,
        "title": "Robust PCA skipped",
        "description": reason,
        "method": "robust_pca",
        "actions": [], "kind_hint": "method_skipped",
        "claim_id": f"robust_pca-skipped",
        "fabricated": False,
        "evidence": {"status": "skipped", "reason": reason},
    }
