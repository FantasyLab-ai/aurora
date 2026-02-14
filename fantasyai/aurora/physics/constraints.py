from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple, Any
import numpy as np
import pandas as pd


@dataclass
class Constraint:
    name: str
    columns: List[str]
    kind: str  # "nonneg", "bounds", "monotone_increasing", "monotone_decreasing", "max_rate"
    params: Dict[str, Any]


def _safe_numeric(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s, errors="coerce")


def infer_basic_constraints(df: pd.DataFrame) -> List[Constraint]:
    """
    Heuristics: infer conservative constraints from column names + distributions.
    Safe defaults: only apply non-negativity when it is extremely likely.
    """
    cols = [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c])]
    constraints: List[Constraint] = []

    # Keyword-based hints (extend over time)
    nonneg_keywords = ["count", "qty", "volume", "vol", "mass", "temp", "pressure", "ppm", "ppb", "concentration", "conc", "rate", "speed", "power", "energy"]
    bounded_0_1_keywords = ["prob", "ratio", "fraction", "pct", "percent", "share"]

    for c in cols:
        lc = c.lower()

        x = _safe_numeric(df[c]).dropna()
        if x.empty:
            continue

        # If column is almost always >= 0, infer nonneg
        frac_neg = float((x < 0).mean())
        if frac_neg < 0.001 and (any(k in lc for k in nonneg_keywords) or x.min() >= -1e-9):
            constraints.append(Constraint(
                name=f"nonneg:{c}",
                columns=[c],
                kind="nonneg",
                params={}
            ))

        # If looks like 0..1, infer bounds
        if x.min() >= -1e-6 and x.max() <= 1.0 + 1e-6 and any(k in lc for k in bounded_0_1_keywords):
            constraints.append(Constraint(
                name=f"bounds01:{c}",
                columns=[c],
                kind="bounds",
                params={"lo": 0.0, "hi": 1.0}
            ))

        # If looks like 0..100 and contains pct/percent, infer bounds
        if "pct" in lc or "percent" in lc:
            if x.min() >= -1e-6 and x.max() <= 100.0 + 1e-6:
                constraints.append(Constraint(
                    name=f"bounds0100:{c}",
                    columns=[c],
                    kind="bounds",
                    params={"lo": 0.0, "hi": 100.0}
                ))

    return constraints


def check_constraints(df: pd.DataFrame, constraints: List[Constraint]) -> Dict[str, Any]:
    """
    Returns a structured report describing violations.
    """
    violations: List[Dict[str, Any]] = []
    for con in constraints:
        for col in con.columns:
            if col not in df.columns:
                continue
            x = _safe_numeric(df[col])
            x_valid = x.dropna()
            if x_valid.empty:
                continue

            if con.kind == "nonneg":
                bad = x_valid[x_valid < 0]
                if len(bad) > 0:
                    violations.append({
                        "constraint": con.name,
                        "kind": con.kind,
                        "column": col,
                        "count": int(len(bad)),
                        "fraction": float(len(bad) / len(x_valid)),
                        "min_value": float(bad.min()),
                    })

            elif con.kind == "bounds":
                lo = float(con.params.get("lo", -np.inf))
                hi = float(con.params.get("hi", np.inf))
                bad = x_valid[(x_valid < lo) | (x_valid > hi)]
                if len(bad) > 0:
                    violations.append({
                        "constraint": con.name,
                        "kind": con.kind,
                        "column": col,
                        "count": int(len(bad)),
                        "fraction": float(len(bad) / len(x_valid)),
                        "min_value": float(bad.min()),
                        "max_value": float(bad.max()),
                        "bounds": [lo, hi],
                    })

            # monotonic + max_rate are future steps (Phase 3/4)
            # We'll add them later without breaking interface.

    severity = "ok"
    if violations:
        frac = float(np.mean([v["fraction"] for v in violations]))
        severity = "warn" if frac < 0.01 else "fail"

    return {
        "severity": severity,
        "violations_count": int(len(violations)),
        "violations": violations,
    }


def project_to_constraints(df: pd.DataFrame, constraints: List[Constraint]) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    """
    Apply simple "repair" projections (clamp) to make data feasible.
    This is intentionally conservative and auditable.
    """
    out = df.copy()
    repairs: List[Dict[str, Any]] = []

    for con in constraints:
        for col in con.columns:
            if col not in out.columns:
                continue
            x = _safe_numeric(out[col])
            if x.dropna().empty:
                continue

            if con.kind == "nonneg":
                n_bad = int((x < 0).sum())
                if n_bad > 0:
                    out[col] = x.clip(lower=0.0)
                    repairs.append({"constraint": con.name, "column": col, "repaired_values": n_bad, "method": "clip(lower=0)"})

            elif con.kind == "bounds":
                lo = float(con.params.get("lo", -np.inf))
                hi = float(con.params.get("hi", np.inf))
                n_bad = int(((x < lo) | (x > hi)).sum())
                if n_bad > 0:
                    out[col] = x.clip(lower=lo, upper=hi)
                    repairs.append({"constraint": con.name, "column": col, "repaired_values": n_bad, "method": f"clip([{lo},{hi}])"})

    return out, {"repairs": repairs, "repairs_count": int(len(repairs))}
