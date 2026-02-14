from __future__ import annotations

from typing import Any, Dict, List, Tuple
import numpy as np
import pandas as pd


def _num_cols(df: pd.DataFrame) -> List[str]:
    return [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c])]


def build_physics_features(df: pd.DataFrame, max_cols: int = 12) -> Dict[str, Any]:
    """
    Domain-agnostic physics-inspired features:
      - first difference (velocity analog)
      - second difference (acceleration analog)
      - z-score normalized version
      - simple lags (t-1)
      - ratios between top-variance columns (limited)
    Returns a SUMMARY ONLY (we do not mutate the main pipeline yet).
    """
    cols = _num_cols(df)
    if not cols:
        return {"ok": False, "reason": "no_numeric_columns"}

    # pick top variance numeric columns
    var = []
    for c in cols:
        x = pd.to_numeric(df[c], errors="coerce")
        v = float(x.var(skipna=True)) if x.notna().any() else 0.0
        var.append((v, c))
    var.sort(reverse=True)
    top = [c for _, c in var[:max_cols]]

    derived: Dict[str, Dict[str, Any]] = {}

    for c in top:
        x = pd.to_numeric(df[c], errors="coerce")

        # finite differences
        d1 = x.diff()
        d2 = d1.diff()

        # normalized
        mu = float(x.mean(skipna=True)) if x.notna().any() else 0.0
        sd = float(x.std(skipna=True)) if x.notna().any() else 0.0
        z = (x - mu) / (sd if sd > 1e-12 else 1.0)

        derived[c] = {
            "d1": {"name": f"{c}__d1", "non_null": int(d1.notna().sum())},
            "d2": {"name": f"{c}__d2", "non_null": int(d2.notna().sum())},
            "z":  {"name": f"{c}__z",  "non_null": int(z.notna().sum()), "mean": mu, "std": sd},
            "lag1": {"name": f"{c}__lag1", "non_null": int(x.shift(1).notna().sum())},
        }

    # ratios across top 6 (bounded count)
    ratio_pairs: List[Dict[str, Any]] = []
    ratio_cols = top[:6]
    for i in range(len(ratio_cols)):
        for j in range(i + 1, len(ratio_cols)):
            a, b = ratio_cols[i], ratio_cols[j]
            xa = pd.to_numeric(df[a], errors="coerce")
            xb = pd.to_numeric(df[b], errors="coerce")
            denom = xb.replace(0, np.nan)
            r = xa / denom
            ratio_pairs.append({
                "name": f"{a}__over__{b}",
                "non_null": int(r.notna().sum()),
                "notes": "ratio feature; denom zeros -> NaN"
            })

    return {
        "ok": True,
        "top_numeric_by_variance": top,
        "derived_features_summary": derived,
        "ratio_features_summary": ratio_pairs[:15],
    }


def discover_candidate_invariants(df: pd.DataFrame, max_cols: int = 10) -> Dict[str, Any]:
    """
    Candidate invariants discovery (domain-agnostic heuristics):
      - x + y ~ constant  (low CV)
      - x - y ~ constant
      - x * y ~ constant
      - x / y ~ constant  (where y != 0)
    We score each candidate by coefficient-of-variation (CV) on the derived series.
    """
    cols = _num_cols(df)
    if len(cols) < 2:
        return {"ok": False, "reason": "not_enough_numeric_columns"}

    # pick top variance columns
    var = []
    for c in cols:
        x = pd.to_numeric(df[c], errors="coerce")
        v = float(x.var(skipna=True)) if x.notna().any() else 0.0
        var.append((v, c))
    var.sort(reverse=True)
    top = [c for _, c in var[:max_cols]]

    cands: List[Dict[str, Any]] = []

    def score_series(s: pd.Series) -> Tuple[float, float, int]:
        s2 = pd.to_numeric(s, errors="coerce").dropna()
        if s2.empty:
            return (np.inf, np.nan, 0)
        mu = float(s2.mean())
        sd = float(s2.std())
        cv = float(sd / (abs(mu) + 1e-12))
        return (cv, mu, int(len(s2)))

    for i in range(len(top)):
        for j in range(i + 1, len(top)):
            a, b = top[i], top[j]
            xa = pd.to_numeric(df[a], errors="coerce")
            xb = pd.to_numeric(df[b], errors="coerce")

            # sum
            cv, mu, n = score_series(xa + xb)
            cands.append({"type": "sum", "expr": f"{a}+{b}", "cv": cv, "mean": mu, "n": n})

            # diff
            cv, mu, n = score_series(xa - xb)
            cands.append({"type": "diff", "expr": f"{a}-{b}", "cv": cv, "mean": mu, "n": n})

            # prod
            cv, mu, n = score_series(xa * xb)
            cands.append({"type": "prod", "expr": f"{a}*{b}", "cv": cv, "mean": mu, "n": n})

            # ratio (avoid division by 0)
            denom = xb.replace(0, np.nan)
            cv, mu, n = score_series(xa / denom)
            cands.append({"type": "ratio", "expr": f"{a}/{b}", "cv": cv, "mean": mu, "n": n})

    cands.sort(key=lambda d: d["cv"])
    topk = cands[:25]

    # simple plausibility score: if we find very low CV invariants, score higher
    best_cv = topk[0]["cv"] if topk else np.inf
    plausibility = 0
    if np.isfinite(best_cv):
        if best_cv < 0.05:
            plausibility = 95
        elif best_cv < 0.10:
            plausibility = 85
        elif best_cv < 0.20:
            plausibility = 70
        elif best_cv < 0.35:
            plausibility = 55
        else:
            plausibility = 40

    return {
        "ok": True,
        "columns_considered": top,
        "top_invariants": topk,
        "best_cv": float(best_cv) if np.isfinite(best_cv) else None,
        "invariants_plausibility_hint": int(plausibility),
    }
