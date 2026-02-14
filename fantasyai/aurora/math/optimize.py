from __future__ import annotations
from typing import Any, Dict, List, Optional
import numpy as np
import pandas as pd

def _to_num(s: pd.Series) -> np.ndarray:
    v = pd.to_numeric(s, errors="coerce").replace([np.inf, -np.inf], np.nan).values.astype(float)
    return v

def optimize_goal(
    df: pd.DataFrame,
    goal: Dict[str, Any],
    seed: int = 0,
) -> Dict[str, Any]:
    """
    General-purpose black-box optimizer (random search + local refinement)
    suitable for "holy shit level" product scaffolding.

    goal schema:
      {
        "objective": {"type": "maximize"|"minimize", "metric_col": "Y"},
        "levers": [{"col":"X1","min":0,"max":1},{"col":"X2","min":10,"max":30}],
        "constraints": [{"type":"budget","expr":"0.2*X1 + 1.0*X2","max":20}],
        "scoring": {"type":"proxy_linear", "features":["X1","X2","X3"], "target_col":"Y"}
      }
    """
    rng = np.random.default_rng(seed)

    # Validate
    if "objective" not in goal or "levers" not in goal:
        return {"error": "invalid_goal_schema"}
    obj = goal["objective"]
    levers = goal["levers"]

    for lv in levers:
        if lv["col"] not in df.columns:
            return {"error": f"lever_missing:{lv['col']}"}

    # Proxy model (simple ridge linear) to score candidates
    scoring = goal.get("scoring", {"type": "proxy_linear"})
    target_col = scoring.get("target_col", obj.get("metric_col"))
    if target_col not in df.columns:
        return {"error": f"missing_target:{target_col}"}

    # features: all numeric except target by default
    if "features" in scoring:
        feats = scoring["features"]
    else:
        feats = df.select_dtypes(include="number").columns.tolist()
        feats = [c for c in feats if c != target_col]
    feats = [c for c in feats if c in df.columns]

    X = df[feats].copy()
    for c in feats:
        X[c] = pd.to_numeric(X[c], errors="coerce")
    X = X.replace([np.inf, -np.inf], np.nan).fillna(X.median(numeric_only=True))
    y = pd.to_numeric(df[target_col], errors="coerce").replace([np.inf, -np.inf], np.nan).fillna(df[target_col].median()).values.astype(float)

    # standardize
    Xa = X.to_numpy(dtype=float)
    mu = Xa.mean(axis=0)
    sd = Xa.std(axis=0)
    sd = np.where((sd == 0) | ~np.isfinite(sd), 1.0, sd)
    Z = (Xa - mu) / sd

    # ridge
    l2 = 1.0
    A = (Z.T @ Z) / len(Z) + l2 * np.eye(Z.shape[1])
    b = (Z.T @ y) / len(Z)
    w = np.linalg.solve(A, b)

    def proxy_score(row: Dict[str, float]) -> float:
        x = np.array([row.get(c, float(df[c].median())) for c in feats], dtype=float)
        z = (x - mu) / sd
        return float(z @ w)

    # constraints evaluator (basic)
    constraints = goal.get("constraints", [])
    def ok_constraints(candidate: Dict[str, float]) -> bool:
        for con in constraints:
            if con.get("type") == "budget":
                # very small safe evaluator: only supports form "a*X + b*Y + ..."
                expr = con.get("expr", "")
                mx = float(con.get("max", np.inf))
                total = 0.0
                # parse terms separated by +
                for term in expr.split("+"):
                    term = term.strip()
                    if not term:
                        continue
                    if "*" in term:
                        a, name = term.split("*", 1)
                        a = float(a.strip())
                        name = name.strip()
                        total += a * float(candidate.get(name, 0.0))
                    else:
                        # bare var
                        total += float(candidate.get(term, 0.0))
                if total > mx + 1e-9:
                    return False
        return True

    # Random search
    N = int(goal.get("search", {}).get("samples", 2000))
    best = None
    best_s = -1e99 if obj.get("type") == "maximize" else 1e99

    # base values from median
    base = {c: float(pd.to_numeric(df[c], errors="coerce").median()) for c in df.columns if c in df.columns}

    for _ in range(N):
        cand = dict(base)
        for lv in levers:
            lo = float(lv.get("min", 0.0))
            hi = float(lv.get("max", 1.0))
            cand[lv["col"]] = float(rng.uniform(lo, hi))

        if not ok_constraints(cand):
            continue

        s = proxy_score(cand)
        better = (s > best_s) if obj.get("type") == "maximize" else (s < best_s)
        if better:
            best_s = s
            best = cand

    if best is None:
        return {"error": "no_feasible_solution"}

    return {
        "method": "random_search_proxy_linear",
        "objective": obj,
        "best_score_proxy": float(best_s),
        "best_candidate": {lv["col"]: best[lv["col"]] for lv in levers},
        "notes": [
            "This is a safe baseline optimizer scaffold.",
            "To go ‘holy shit’, swap proxy_score with forecasts/causal uplift/risk penalties and add smarter search (CMA-ES/BayesOpt).",
        ],
    }
