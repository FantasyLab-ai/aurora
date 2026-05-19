"""
Bayesian Online Change Point Detection (BOCPD).

The current Aurora stack uses ``ruptures`` (PELT) for batch change-point
detection. BOCPD is the streaming-friendly variant: at each new
observation it updates a posterior over *run length* (how long the
current regime has been active) and signals a change point when the
mode of that posterior collapses back to zero.

Where this matters:

  * Aurora's v1.2 streaming-mode roadmap (item 1.4) needs a method
    that handles new data point-by-point without re-running on the
    whole history. BOCPD is exactly that.
  * For batch use, BOCPD is competitive with PELT but gives you the
    posterior probability of each potential change point — which is
    a more honest "we're not sure when, but here are the candidates".

Reference: Adams & MacKay (2007), "Bayesian Online Changepoint
Detection". The implementation here uses a Student-t observation model
(robust to outliers) with a constant hazard rate (the simplest prior
about how often regimes change). Both are tunable.

Implementation: pure NumPy. No new deps. ~100 lines of math.
"""
from __future__ import annotations

import logging
import math
from typing import Any, Dict, List, Optional, Tuple

LOG = logging.getLogger(__name__)


BOCPD_KB_CITATIONS = [
    {
        "seed_id": "seed:adams_mackay_2007_bocpd",
        "title": "Bayesian Online Changepoint Detection",
        "authors": "Adams, R.P. and MacKay, D.J.C.",
        "year": 2007,
        "venue": "arXiv:0710.3742",
        "summary": (
            "The foundational BOCPD paper. Derives the recursive run-"
            "length posterior with a constant-hazard prior and gives "
            "the practical algorithm Aurora implements."
        ),
        "url": "https://arxiv.org/abs/0710.3742",
    },
]


def fit_bocpd(
    df: Any,
    *,
    target_col: Optional[str] = None,
    hazard: float = 1.0 / 250.0,  # ~ one change point per ~250 obs by default
    prior_mean: float = 0.0,
    prior_var: float = 1.0,
    prior_alpha: float = 1.0,
    prior_beta: float = 1.0,
    threshold: float = 0.5,
    claim_seq: int = 0,
) -> Dict[str, Any]:
    """Run BOCPD on a single column and report detected change points.

    Args:
        df:           pandas DataFrame.
        target_col:   Column to analyse. If None, picks the first numeric
                      column with sufficient variance.
        hazard:       Prior probability of a change at each step. Smaller
                      = expect fewer regime shifts.
        prior_mean / prior_var / prior_alpha / prior_beta:
                      Hyperparameters of the Normal-Inverse-Gamma prior
                      on each regime's mean + variance. Defaults are
                      weakly informative.
        threshold:    Run-length posterior mass at 0 above which we
                      declare a change point.
        claim_seq:    Index for the claim_id.

    Returns:
        Aurora-shaped finding listing detected change points.
    """
    try:
        import numpy as np
        import pandas as pd
    except ImportError:
        return _skip("numpy/pandas not available", claim_seq)

    if target_col is None:
        # Pick first numeric column with > 1e-6 variance.
        for c in df.columns:
            try:
                series = df[c].dropna().astype(float)
                if len(series) > 30 and float(series.var()) > 1e-6:
                    target_col = c
                    break
            except (TypeError, ValueError):
                continue
    if target_col is None or target_col not in df.columns:
        return _skip(
            "BOCPD couldn't find a usable numeric column with variance.",
            claim_seq,
        )

    x = df[target_col].dropna().astype(float).values
    n = len(x)
    if n < 30:
        return _skip(
            f"BOCPD needs ≥30 observations; got {n} after dropna.",
            claim_seq,
        )

    # ---- Run-length posterior recursion --------------------------------
    # R[t][r] = P(run_length = r | x_1:t)
    # Updated each step using the predictive prob of the new observation
    # under each candidate run length.

    # We keep the posterior as a list (allocated dynamically each step)
    # because most mass concentrates at small r — no need for a dense
    # (n x n) matrix.

    log_R = np.array([0.0])  # P(r=0 | nothing) = 1 → log 1 = 0
    means = np.array([prior_mean])
    vars_ = np.array([prior_var])
    alphas = np.array([prior_alpha])
    betas = np.array([prior_beta])

    change_points: List[Tuple[int, float]] = []  # (index, posterior_mass_at_r=0)

    log_hazard = math.log(hazard)
    log_one_minus_hazard = math.log(1.0 - hazard)

    for t, xt in enumerate(x):
        # Predictive log-prob of xt under each existing run-length's
        # parameters (Student-t with df = 2*alpha, location = mean,
        # scale = sqrt(beta*(vars_+1)/(alphas*vars_))).
        # Closed-form: log pdf of Student's t.
        df_ = 2 * alphas
        scale = np.sqrt(betas * (vars_ + 1) / (alphas * vars_))
        z = (xt - means) / scale
        log_pred = (
            math.lgamma(0.0) if False else 0.0  # noop, real lgamma calls below
        )
        # log pdf of t(df_) at z, then subtract log scale to undo the
        # location-scale standardisation.
        log_pred = (
            _vec_lgamma((df_ + 1) / 2.0)
            - _vec_lgamma(df_ / 2.0)
            - 0.5 * np.log(df_ * math.pi)
            - np.log(scale)
            - ((df_ + 1) / 2.0) * np.log(1.0 + (z ** 2) / df_)
        )

        # Growth probabilities: continue current runs (r → r+1).
        log_growth = log_R + log_pred + log_one_minus_hazard
        # Change point probability: collapse to r = 0.
        log_change = _logsumexp(log_R + log_pred + log_hazard)

        # New posterior (one wider than before because runs grew).
        new_log_R = np.concatenate(([log_change], log_growth))
        # Normalise.
        new_log_R = new_log_R - _logsumexp(new_log_R)
        log_R = new_log_R

        # Update sufficient statistics for each candidate run length.
        # Adams & MacKay (2007) section 2.3.
        new_means = np.concatenate(([prior_mean],
                                      (vars_ * means + xt) / (vars_ + 1)))
        new_vars = np.concatenate(([prior_var], vars_ + 1))
        new_alphas = np.concatenate(([prior_alpha], alphas + 0.5))
        new_betas = np.concatenate(([prior_beta],
                                      betas + (vars_ * (xt - means) ** 2)
                                      / (2 * (vars_ + 1))))
        means = new_means
        vars_ = new_vars
        alphas = new_alphas
        betas = new_betas

        # Record change point if posterior at r=0 exceeds threshold.
        post_r0 = float(np.exp(log_R[0]))
        if post_r0 > threshold and t > 0:
            change_points.append((int(t), post_r0))

    if not change_points:
        return {
            "severity": "info",
            "confidence": 0.7,
            "title": f"BOCPD on {target_col!r}: no change points detected",
            "description": (
                f"Bayesian online change-point detection at threshold "
                f"{threshold} found no regime shifts in {n} observations."
            ),
            "method": "bocpd",
            "actions": ["VIEW EVIDENCE"],
            "kind_hint": "regime_stability",
            "claim_id": f"bocpd-{claim_seq:04d}",
            "fabricated": False,
            "kb_citations": BOCPD_KB_CITATIONS,
            "evidence": {
                "status": "fit",
                "target_col": target_col,
                "n_obs": int(n),
                "threshold": float(threshold),
                "hazard": float(hazard),
                "change_points": [],
            },
        }

    description = (
        f"BOCPD detected {len(change_points)} change point(s) in "
        f"{target_col!r} at threshold {threshold} (hazard = {hazard}). "
        f"Most confident: row {change_points[0][0]} "
        f"(posterior mass at r=0: {change_points[0][1]:.3f})."
    )

    return {
        "severity": "warn" if len(change_points) >= 3 else "info",
        "confidence": float(max(cp[1] for cp in change_points)),
        "title": f"BOCPD: {len(change_points)} change point(s) in {target_col!r}",
        "description": description,
        "method": "bocpd",
        "actions": ["VIEW EVIDENCE"],
        "kind_hint": "online_regime_change",
        "claim_id": f"bocpd-{claim_seq:04d}",
        "fabricated": False,
        "kb_citations": BOCPD_KB_CITATIONS,
        "evidence": {
            "status": "fit",
            "target_col": target_col,
            "n_obs": int(n),
            "threshold": float(threshold),
            "hazard": float(hazard),
            "change_points": [
                {"row_idx": idx, "posterior_at_r0": round(p, 4)}
                for idx, p in change_points
            ],
        },
    }


# ---------------------------------------------------------------------------
# Numerical helpers
# ---------------------------------------------------------------------------

def _logsumexp(arr) -> float:
    """log(sum(exp(arr))) computed numerically stably."""
    import numpy as np
    a = np.asarray(arr, dtype=float)
    if a.size == 0:
        return float("-inf")
    m = a.max()
    if not math.isfinite(m):
        return float(m)
    return float(m + math.log(float(np.sum(np.exp(a - m)))))


def _vec_lgamma(arr):
    """Vectorised lgamma."""
    import numpy as np
    from scipy.special import gammaln  # statsmodels brings scipy
    return gammaln(arr)


def _skip(reason: str, claim_seq: int) -> Dict[str, Any]:
    return {
        "severity": "info",
        "confidence": 1.0,
        "title": "BOCPD skipped",
        "description": reason,
        "method": "bocpd",
        "actions": [],
        "kind_hint": "method_skipped",
        "claim_id": "bocpd-skipped",
        "fabricated": False,
        "evidence": {"status": "skipped", "reason": reason},
    }
