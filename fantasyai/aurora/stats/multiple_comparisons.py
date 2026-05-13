"""
Multiple-comparisons correction.

When Aurora reports 47 anomalies with raw p-values, some fraction are
false positives. Real research applies either Benjamini-Hochberg
(controls the false discovery rate) or Bonferroni (controls the
family-wise error rate). Without these corrections, statistically
literate users dismiss the findings as multiple-testing fishing.

Two functions matter:

* :func:`benjamini_hochberg`  — produces ``q_values`` (BH-adjusted p)
                                  and a significance mask at level α.
                                  Default and recommended for exploratory
                                  detection of many candidate findings.
* :func:`bonferroni`          — conservative FWER correction; useful
                                  when even one false positive matters.
* :func:`correct_findings`    — walks a list of finding dicts that carry
                                  a ``p_value`` field, attaches
                                  ``q_value`` / ``p_bonferroni`` /
                                  ``significant_after_bh`` /
                                  ``significant_after_bonferroni``.

Glass-box: the correction parameters and the per-finding rank are
all returned so any reviewer can audit the correction without re-running
the original tests.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

try:
    import numpy as np
except Exception:  # pragma: no cover
    np = None  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Core arithmetic
# ---------------------------------------------------------------------------

def benjamini_hochberg(
    p_values: Sequence[float],
    *,
    alpha: float = 0.05,
) -> Dict[str, Any]:
    """Benjamini-Hochberg step-up FDR correction.

    Parameters
    ----------
    p_values
        1-D sequence of raw p-values, one per test.
    alpha
        Target FDR. Default 0.05.

    Returns
    -------
    dict with:
        q_values        — BH-adjusted p-values, same order as input.
        significant     — boolean mask (q < alpha), same order as input.
        n_tests         — int
        n_significant   — int
        alpha           — float
        method          — "benjamini_hochberg"
    """
    if np is None:
        raise RuntimeError("numpy required for benjamini_hochberg")
    p = np.asarray(p_values, dtype=float)
    n = p.shape[0]
    if n == 0:
        return {
            "q_values": [], "significant": [],
            "n_tests": 0, "n_significant": 0,
            "alpha": alpha, "method": "benjamini_hochberg",
        }
    # Sort ascending, keep original positions.
    order = np.argsort(p, kind="stable")
    p_sorted = p[order]
    ranks = np.arange(1, n + 1)
    # Step-up adjustment: q_(i) = min_{k>=i}( p_(k) * n / k ).
    raw_adj = p_sorted * n / ranks
    # Take cumulative minimum from the largest p downwards, then clamp to ≤1.
    q_sorted = np.minimum.accumulate(raw_adj[::-1])[::-1]
    q_sorted = np.clip(q_sorted, 0.0, 1.0)
    # Restore original ordering.
    q = np.empty_like(q_sorted)
    q[order] = q_sorted
    significant = q < alpha
    return {
        "q_values": q.tolist(),
        "significant": significant.tolist(),
        "n_tests": n,
        "n_significant": int(significant.sum()),
        "alpha": alpha,
        "method": "benjamini_hochberg",
    }


def bonferroni(
    p_values: Sequence[float],
    *,
    alpha: float = 0.05,
) -> Dict[str, Any]:
    """Bonferroni FWER correction. Adjusted p = min(1, p * n)."""
    if np is None:
        raise RuntimeError("numpy required for bonferroni")
    p = np.asarray(p_values, dtype=float)
    n = p.shape[0]
    if n == 0:
        return {
            "p_corrected": [], "significant": [],
            "n_tests": 0, "n_significant": 0,
            "alpha": alpha, "method": "bonferroni",
        }
    p_adj = np.clip(p * n, 0.0, 1.0)
    significant = p_adj < alpha
    return {
        "p_corrected": p_adj.tolist(),
        "significant": significant.tolist(),
        "n_tests": n,
        "n_significant": int(significant.sum()),
        "alpha": alpha,
        "method": "bonferroni",
    }


# ---------------------------------------------------------------------------
# Findings-list integration
# ---------------------------------------------------------------------------

def correct_findings(
    findings: List[Dict[str, Any]],
    *,
    alpha: float = 0.05,
    p_field: str = "p_value",
    only_methods: Optional[Iterable[str]] = None,
) -> Dict[str, Any]:
    """Attach q_value / p_bonferroni / significance flags to each finding.

    Walks the findings list, collects all that carry ``p_field``, runs
    BH and Bonferroni in parallel, then writes ``q_value``,
    ``p_bonferroni``, ``significant_after_bh``,
    ``significant_after_bonferroni`` back onto each finding (in place).

    Parameters
    ----------
    findings
        List of finding dicts. Mutated in place.
    alpha
        Target significance level for both corrections.
    p_field
        Name of the per-finding key holding the raw p-value.
    only_methods
        Optional whitelist — only correct findings whose ``method``
        field matches. Useful when you want to correct just a subset
        (e.g. only the anomaly p-values, not the regime p-values).

    Returns
    -------
    summary dict — see :func:`summarize_correction`.
    """
    if not findings:
        return summarize_correction([], 0.0, 0, 0)
    indices: List[int] = []
    p_list: List[float] = []
    for i, f in enumerate(findings):
        p = f.get(p_field) if isinstance(f, dict) else None
        if p is None:
            continue
        try:
            p_val = float(p)
        except Exception:
            continue
        if only_methods is not None:
            method = (f.get("method") or "")
            if method not in only_methods:
                continue
        indices.append(i)
        p_list.append(p_val)

    if not p_list:
        return summarize_correction([], alpha, 0, 0)

    bh = benjamini_hochberg(p_list, alpha=alpha)
    bf = bonferroni(p_list, alpha=alpha)

    for k, fi_idx in enumerate(indices):
        f = findings[fi_idx]
        f["q_value"] = bh["q_values"][k]
        f["p_bonferroni"] = bf["p_corrected"][k]
        f["significant_after_bh"] = bool(bh["significant"][k])
        f["significant_after_bonferroni"] = bool(bf["significant"][k])
        # Keep an audit trail — which family was this corrected against,
        # and at what α. Reviewers need this to interpret the q-value.
        f["multiple_comparisons"] = {
            "family_size": len(p_list),
            "alpha": alpha,
            "rank_in_family": k + 1,
            "methods": ["benjamini_hochberg", "bonferroni"],
        }
    return summarize_correction(p_list, alpha,
                                  bh["n_significant"], bf["n_significant"])


def summarize_correction(
    p_values: Sequence[float],
    alpha: float,
    n_sig_bh: int,
    n_sig_bonferroni: int,
) -> Dict[str, Any]:
    """Audit summary suitable for the synthesis layer + the panel header."""
    n = len(p_values)
    return {
        "n_tests": n,
        "alpha": alpha,
        "n_significant_raw": int(sum(1 for p in p_values if p < alpha)),
        "n_significant_bh": n_sig_bh,
        "n_significant_bonferroni": n_sig_bonferroni,
        "fdr_method": "benjamini_hochberg",
        "fwer_method": "bonferroni",
    }
