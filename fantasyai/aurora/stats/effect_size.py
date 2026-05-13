"""
Effect size measures.

Significance answers "is there an effect?". Effect size answers "how
big is it?". Reporting both prevents the common error of treating
small p-values as evidence of large effects when sample size alone
drives the significance.

Aurora's primitives surface four families:

* Cohen's d           — standardised mean difference between two groups.
                          Conventional bands: |d| < 0.2 small, 0.2-0.5
                          medium, 0.5-0.8 large, > 0.8 very large.
* Eta-squared (η²)    — proportion of variance explained in ANOVA.
                          Bands: < 0.01 negligible, 0.01-0.06 small,
                          0.06-0.14 medium, > 0.14 large.
* Odds ratio (OR)     — for binary outcomes / 2x2 tables.
                          Bands by Chen-Cohen (2010): 1.0 = no effect,
                          1.5 small, 3.5 medium, 9.0 large.
* R²                  — coefficient of determination for regression.
                          We report ``r_squared`` for any regression
                          finding so the synthesis layer can phrase
                          model fit honestly.

Each function returns a uniform dict shape:

    {
        "kind": "cohens_d" | "eta_squared" | "odds_ratio" | "r_squared",
        "value": float,
        "interpretation": "small" | "medium" | "large" | ...,
        "n": int,
        "details": {...}     # kind-specific extras
    }

so the synthesis layer can read ``effect_size["interpretation"]``
without caring which family we used.
"""
from __future__ import annotations

import math
from typing import Any, Dict, Optional, Sequence

try:
    import numpy as np
except Exception:  # pragma: no cover
    np = None  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Cohen's d
# ---------------------------------------------------------------------------

def cohens_d(
    group_a: Sequence[float],
    group_b: Sequence[float],
    *,
    pooled: bool = True,
) -> Dict[str, Any]:
    """Standardised mean difference between two groups.

    With ``pooled=True`` we use the pooled standard deviation; with
    False we use group_b's SD (Glass's Δ — useful when one group is
    a 'control' baseline). Pooled is the conventional default.
    """
    if np is None:
        raise RuntimeError("numpy required for cohens_d")
    a = np.asarray(group_a, dtype=float)
    b = np.asarray(group_b, dtype=float)
    a = a[~np.isnan(a)]
    b = b[~np.isnan(b)]
    na, nb = len(a), len(b)
    if na < 2 or nb < 2:
        return {
            "kind": "cohens_d", "value": None,
            "interpretation": "insufficient_data",
            "n": na + nb,
            "details": {"n_a": na, "n_b": nb,
                         "skipped_reason": "need ≥2 obs per group"},
        }
    ma, mb = float(np.mean(a)), float(np.mean(b))
    va = float(np.var(a, ddof=1))
    vb = float(np.var(b, ddof=1))
    if pooled:
        sp2 = ((na - 1) * va + (nb - 1) * vb) / (na + nb - 2)
        sp = math.sqrt(sp2) if sp2 > 0 else 0.0
    else:
        sp = math.sqrt(vb) if vb > 0 else 0.0
    d = (ma - mb) / sp if sp > 0 else 0.0
    return {
        "kind": "cohens_d",
        "value": float(d),
        "interpretation": interpret_cohens_d(d),
        "n": na + nb,
        "details": {
            "n_a": na, "n_b": nb,
            "mean_a": ma, "mean_b": mb,
            "pooled_sd": sp,
            "pooled": pooled,
        },
    }


def interpret_cohens_d(d: Optional[float]) -> str:
    if d is None:
        return "insufficient_data"
    a = abs(float(d))
    if a < 0.2:    return "negligible"
    if a < 0.5:    return "small"
    if a < 0.8:    return "medium"
    if a < 1.2:    return "large"
    return "very_large"


# ---------------------------------------------------------------------------
# Eta-squared
# ---------------------------------------------------------------------------

def eta_squared(
    ss_between: float,
    ss_total: float,
    *,
    df_between: Optional[int] = None,
    df_within: Optional[int] = None,
    n: Optional[int] = None,
) -> Dict[str, Any]:
    """ANOVA effect size η² = SS_between / SS_total.

    When df_between/df_within are supplied, also returns ω² (omega-
    squared) — a slightly less biased estimator preferred when sample
    sizes are small.
    """
    if ss_total <= 0:
        return {
            "kind": "eta_squared", "value": None,
            "interpretation": "insufficient_data",
            "n": n or 0,
            "details": {"skipped_reason": "non-positive ss_total"},
        }
    eta2 = ss_between / ss_total
    details: Dict[str, Any] = {
        "ss_between": ss_between, "ss_total": ss_total,
    }
    if (df_between is not None and df_within is not None
            and df_within > 0 and ss_total > 0):
        ms_within = (ss_total - ss_between) / df_within
        denom = ss_total + ms_within
        omega2 = ((ss_between - df_between * ms_within) / denom
                   if denom > 0 else None)
        details["omega_squared"] = omega2
        details["df_between"] = df_between
        details["df_within"] = df_within
    return {
        "kind": "eta_squared",
        "value": float(eta2),
        "interpretation": interpret_eta_squared(eta2),
        "n": n or 0,
        "details": details,
    }


def interpret_eta_squared(e2: Optional[float]) -> str:
    if e2 is None:
        return "insufficient_data"
    if e2 < 0.01:  return "negligible"
    if e2 < 0.06:  return "small"
    if e2 < 0.14:  return "medium"
    return "large"


# ---------------------------------------------------------------------------
# Odds ratio
# ---------------------------------------------------------------------------

def odds_ratio(
    a: int, b: int, c: int, d: int,
    *,
    haldane_anscombe: bool = True,
    ci_level: float = 0.95,
) -> Dict[str, Any]:
    """Odds ratio for the 2x2 contingency table::

                    outcome+   outcome-
        exposed+      a          b
        exposed-      c          d

    With ``haldane_anscombe=True`` (default), 0.5 is added to every
    cell when any cell is zero — avoids divide-by-zero and is the
    conventional small-cell adjustment.

    Returns the OR plus its log-OR Wald confidence interval.
    """
    cells = [a, b, c, d]
    if any(x < 0 for x in cells):
        return {"kind": "odds_ratio", "value": None,
                "interpretation": "insufficient_data", "n": 0,
                "details": {"skipped_reason": "negative cell"}}
    n = sum(cells)
    if n == 0:
        return {"kind": "odds_ratio", "value": None,
                "interpretation": "insufficient_data", "n": 0,
                "details": {"skipped_reason": "all-zero table"}}
    aa, bb, cc, dd = float(a), float(b), float(c), float(d)
    used_correction = False
    if haldane_anscombe and (a == 0 or b == 0 or c == 0 or d == 0):
        aa, bb, cc, dd = aa + 0.5, bb + 0.5, cc + 0.5, dd + 0.5
        used_correction = True
    if cc * bb == 0:
        return {"kind": "odds_ratio", "value": None,
                "interpretation": "insufficient_data", "n": n,
                "details": {"skipped_reason": "denominator zero"}}
    or_value = (aa * dd) / (bb * cc)
    log_or = math.log(or_value) if or_value > 0 else 0.0
    # Wald CI on the log scale.
    se = math.sqrt(1 / aa + 1 / bb + 1 / cc + 1 / dd)
    # Two-sided z for the requested level.
    alpha = 1.0 - ci_level
    # 95% → z=1.96; for general level, approximate via inverse-normal.
    from .bootstrap import _norm_ppf  # tiny shared helper
    z = _norm_ppf(1.0 - alpha / 2.0)
    ci_low = math.exp(log_or - z * se)
    ci_high = math.exp(log_or + z * se)
    return {
        "kind": "odds_ratio",
        "value": float(or_value),
        "interpretation": interpret_odds_ratio(or_value),
        "n": n,
        "details": {
            "table": {"a": a, "b": b, "c": c, "d": d},
            "log_or": log_or, "log_or_se": se,
            "ci_low": ci_low, "ci_high": ci_high,
            "ci_level": ci_level,
            "haldane_anscombe_correction": used_correction,
        },
    }


def interpret_odds_ratio(or_value: Optional[float]) -> str:
    """Chen-Cohen (2010) bands. Symmetric in log-OR space."""
    if or_value is None or or_value <= 0:
        return "insufficient_data"
    log_abs = abs(math.log(or_value))
    if log_abs < math.log(1.5):  return "negligible"
    if log_abs < math.log(3.5):  return "small"
    if log_abs < math.log(9.0):  return "medium"
    return "large"


# ---------------------------------------------------------------------------
# R²
# ---------------------------------------------------------------------------

def r_squared(
    y_true: Sequence[float],
    y_pred: Sequence[float],
) -> Dict[str, Any]:
    """Coefficient of determination. R² = 1 - SS_res / SS_tot."""
    if np is None:
        raise RuntimeError("numpy required for r_squared")
    yt = np.asarray(y_true, dtype=float)
    yp = np.asarray(y_pred, dtype=float)
    mask = (~np.isnan(yt)) & (~np.isnan(yp))
    yt = yt[mask]
    yp = yp[mask]
    n = yt.shape[0]
    if n < 2:
        return {"kind": "r_squared", "value": None,
                "interpretation": "insufficient_data", "n": n,
                "details": {"skipped_reason": "need ≥2 paired obs"}}
    ss_res = float(np.sum((yt - yp) ** 2))
    ss_tot = float(np.sum((yt - yt.mean()) ** 2))
    if ss_tot <= 0:
        return {"kind": "r_squared", "value": None,
                "interpretation": "insufficient_data", "n": n,
                "details": {"skipped_reason": "zero variance in y_true"}}
    r2 = 1.0 - ss_res / ss_tot
    return {
        "kind": "r_squared",
        "value": float(r2),
        "interpretation": _interpret_r_squared(r2),
        "n": n,
        "details": {"ss_res": ss_res, "ss_tot": ss_tot},
    }


def _interpret_r_squared(r2: Optional[float]) -> str:
    if r2 is None:
        return "insufficient_data"
    if r2 < 0:    return "worse_than_mean"
    if r2 < 0.1:  return "negligible"
    if r2 < 0.3:  return "small"
    if r2 < 0.5:  return "medium"
    if r2 < 0.7:  return "large"
    return "very_large"


# ---------------------------------------------------------------------------
# Findings integration helper
# ---------------------------------------------------------------------------

def attach_effect_size(
    finding: Dict[str, Any],
    effect: Dict[str, Any],
    *,
    key: str = "effect_size",
) -> Dict[str, Any]:
    """Attach an effect-size dict to a finding under ``key`` (default
    ``effect_size``). Returns the finding for chaining."""
    if not isinstance(finding, dict) or not isinstance(effect, dict):
        return finding
    finding[key] = effect
    return finding
