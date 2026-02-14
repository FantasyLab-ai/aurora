from __future__ import annotations

from pathlib import Path
import hashlib
import re

ROOT = Path(__file__).resolve().parents[1]
CAUSAL = ROOT / "fantasyai" / "aurora" / "math" / "causal.py"

def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:12]

def main() -> int:
    if not CAUSAL.exists():
        print(f"[FAIL] missing: {CAUSAL}")
        return 2

    txt = CAUSAL.read_text(encoding="utf-8")

    # If already present, do nothing.
    if re.search(r"^\s*def\s+causal_what_if_linear\s*\(", txt, flags=re.M):
        print(f"[OK] causal_what_if_linear already present -> {CAUSAL} (sha={sha(CAUSAL)})")
        return 0

    add_lines = [
        "",
        "",
        "# ----------------------------",
        "# Compatibility shim (worldclass stack)",
        "# ----------------------------",
        "def causal_what_if_linear(",
        "    df: 'pd.DataFrame',",
        "    treatment_col: str,",
        "    outcome_col: str,",
        "    covariate_cols: 'Optional[List[str]]' = None,",
        "    delta: float = 1.0,",
        "    seed: int = 0,",
        ") -> 'Dict[str, Any]':",
        "    '''",
        "    Simple, stable \"what-if\" estimator for continuous (or numeric) treatment:",
        "      - fits ridge linear model y ~ [treatment + covariates]",
        "      - returns predicted delta in outcome for +delta change in treatment",
        "    Deterministic, dependency-light, and designed as a compatibility API.",
        "",
        "    NOTE: This is not a formal causal guarantee without identification assumptions.",
        "    Use estimate_ate_binary_dr(...) when treatment is truly binary.",
        "    '''",
        "    # Local imports to avoid import-order failures during module load",
        "    import numpy as np",
        "    import pandas as pd",
        "    from typing import Any, Dict, List, Optional",
        "",
        "    if treatment_col not in df.columns or outcome_col not in df.columns:",
        "        return {",
        "            'error': 'missing_required_columns',",
        "            'treatment_col': treatment_col,",
        "            'outcome_col': outcome_col,",
        "        }",
        "",
        "    t = pd.to_numeric(df[treatment_col], errors='coerce')",
        "    y = pd.to_numeric(df[outcome_col], errors='coerce')",
        "",
        "    if covariate_cols is None:",
        "        num_cols = df.select_dtypes(include='number').columns.tolist()",
        "        covariate_cols = [c for c in num_cols if c not in (treatment_col, outcome_col)]",
        "",
        "    cols = [treatment_col] + (covariate_cols or [])",
        "    X = df[cols].copy()",
        "    for c in cols:",
        "        X[c] = pd.to_numeric(X[c], errors='coerce')",
        "    X = X.replace([np.inf, -np.inf], np.nan)",
        "",
        "    m = t.notna() & y.notna() & X.notna().any(axis=1)",
        "    X = X.loc[m].copy()",
        "    yv = y.loc[m].astype(float).to_numpy()",
        "",
        "    n = int(len(X))",
        "    if n < 200:",
        "        return {'error': 'too_few_rows', 'n': n}",
        "",
        "    X = X.fillna(X.median(numeric_only=True))",
        "    arr = X.to_numpy(dtype=float)",
        "    mu = np.nanmean(arr, axis=0)",
        "    sd = np.nanstd(arr, axis=0)",
        "    sd = np.where((sd == 0) | ~np.isfinite(sd), 1.0, sd)",
        "    Z = (arr - mu) / sd",
        "    Z = np.where(np.isfinite(Z), Z, 0.0)",
        "",
        "    l2 = 1.0",
        "    A = (Z.T @ Z) / n + l2 * np.eye(Z.shape[1])",
        "    b = (Z.T @ yv) / n",
        "    w = np.linalg.solve(A, b)",
        "",
        "    sd_t = float(sd[0]) if len(sd) else 1.0",
        "    d_std = float(delta) / sd_t",
        "    delta_y = float(w[0] * d_std)",
        "",
        "    return {",
        "        'method': 'WHAT_IF_LINEAR_RIDGE',",
        "        'n': n,",
        "        'treatment_col': treatment_col,",
        "        'outcome_col': outcome_col,",
        "        'covariates_n': int(len(cols) - 1),",
        "        'delta_treatment': float(delta),",
        "        'estimated_delta_outcome': delta_y,",
        "        'model_meta': {'mu': mu.tolist(), 'sd': sd.tolist(), 'l2': float(l2)},",
        "        'notes': [",
        "            'Linear ridge what-if; fast, stable sensitivity probe.',",
        "            'Not a causality guarantee without identification assumptions.',",
        "        ],",
        "    }",
        "",
    ]

    out = txt
    if not out.endswith('\n'):
        out += '\n'
    out += '\n'.join(add_lines)

    CAUSAL.write_text(out, encoding="utf-8")
    print(f"[OK] added causal_what_if_linear -> {CAUSAL} (sha={sha(CAUSAL)})")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
