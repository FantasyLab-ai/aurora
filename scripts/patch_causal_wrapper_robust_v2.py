from __future__ import annotations

from pathlib import Path
from datetime import datetime
import hashlib

ROOT = Path(__file__).resolve().parents[1]
CAUSAL = ROOT / "fantasyai" / "aurora" / "math" / "causal.py"

def sha12(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()[:12]

def backup(p: Path) -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    bak = p.with_suffix(p.suffix + f".bak_{ts}")
    bak.write_bytes(p.read_bytes())
    return bak

NEW_WRAPPER = r'''
# =============================================================================
# Compatibility wrapper (robust v2): causal_what_if_linear
# - Retries if too few rows after cleaning
# - Falls back to (target,treatment)-only
# - Final fallback: median imputation (no catastrophic row drops)
# =============================================================================
def causal_what_if_linear(
    df,
    target_col: str | None = None,
    treatment_col: str | None = None,
    feature_cols=None,
    delta: float = 1.0,
    min_rows: int = 200,
    **kwargs
):
    import numpy as np
    import pandas as pd

    if df is None:
        raise ValueError("causal_what_if_linear: df is None")
    if not isinstance(df, pd.DataFrame):
        df = pd.DataFrame(df)

    # pick numeric columns (with coercion option)
    for c in list(df.columns):
        # don't blow away non-numeric columns; only coerce when safe
        if df[c].dtype == object:
            try:
                df[c] = pd.to_numeric(df[c], errors="ignore")
            except Exception:
                pass

    numeric_cols = [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c])]
    if not numeric_cols:
        raise ValueError("causal_what_if_linear: no numeric columns available")

    if target_col is None or target_col not in numeric_cols:
        target_col = numeric_cols[0]

    if treatment_col is None or treatment_col not in numeric_cols or treatment_col == target_col:
        treatment_col = None
        for c in numeric_cols:
            if c != target_col:
                treatment_col = c
                break
        if treatment_col is None:
            raise ValueError("causal_what_if_linear: could not select a treatment_col")

    # default controls
    if feature_cols is None:
        feature_cols = [c for c in numeric_cols if c not in (target_col, treatment_col)]
    else:
        feature_cols = [c for c in feature_cols if c in numeric_cols and c not in (target_col, treatment_col)]

    def fit_ols(d: pd.DataFrame, controls: list[str]) -> dict:
        y = d[target_col].to_numpy(dtype=float)
        X_parts = [np.ones(len(d), dtype=float), d[treatment_col].to_numpy(dtype=float)]
        for c in controls:
            X_parts.append(d[c].to_numpy(dtype=float))
        X = np.column_stack(X_parts)
        beta, *_ = np.linalg.lstsq(X, y, rcond=None)
        b_treat = float(beta[1])
        return {
            "target_col": target_col,
            "treatment_col": treatment_col,
            "feature_cols": controls,
            "n": int(len(d)),
            "beta_treatment": b_treat,
            "delta": float(delta),
            "estimated_effect": float(b_treat * float(delta)),
            "note": "ok",
        }

    # Attempt 1: full controls with dropna
    use_cols = [target_col, treatment_col] + list(feature_cols)
    d1 = df[use_cols].copy()
    for c in use_cols:
        d1[c] = pd.to_numeric(d1[c], errors="coerce")
    d1 = d1.dropna()
    if len(d1) >= min_rows:
        out = fit_ols(d1, list(feature_cols))
        out["strategy"] = "dropna_full_controls"
        return out

    # Attempt 2: only target+treatment with dropna
    d2 = df[[target_col, treatment_col]].copy()
    d2[target_col] = pd.to_numeric(d2[target_col], errors="coerce")
    d2[treatment_col] = pd.to_numeric(d2[treatment_col], errors="coerce")
    d2 = d2.dropna()
    if len(d2) >= max(50, min_rows // 4):
        out = fit_ols(d2, [])
        out["strategy"] = "dropna_target_treatment_only"
        return out

    # Attempt 3: median imputation on target+treatment (+ top 2 controls if available)
    controls = list(feature_cols)[:2]
    use_cols3 = [target_col, treatment_col] + controls
    d3 = df[use_cols3].copy()
    for c in use_cols3:
        d3[c] = pd.to_numeric(d3[c], errors="coerce")
        med = d3[c].median()
        if pd.isna(med):
            # if an entire column is NaN, drop it (but keep target/treatment)
            if c in controls:
                d3 = d3.drop(columns=[c])
            continue
        d3[c] = d3[c].fillna(med)

    if len(d3) < 10:
        raise ValueError("causal_what_if_linear: not enough rows even after imputation")

    # controls may have been dropped
    final_controls = [c for c in d3.columns if c not in (target_col, treatment_col)]
    out = fit_ols(d3, final_controls)
    out["strategy"] = "median_impute"
    return out
'''.lstrip()

def main() -> int:
    if not CAUSAL.exists():
        raise SystemExit(f"Missing: {CAUSAL}")

    bak = backup(CAUSAL)
    print(f"[OK] backup -> {bak}")

    txt = CAUSAL.read_text(encoding="utf-8", errors="ignore")
    if "Compatibility wrapper (robust v2): causal_what_if_linear" in txt:
        print(f"[OK] robust v2 causal wrapper already present -> {CAUSAL} (sha={sha12(CAUSAL)})")
    else:
        if not txt.endswith("\n"):
            txt += "\n"
        txt += "\n" + NEW_WRAPPER + "\n"
        CAUSAL.write_text(txt, encoding="utf-8")
        print(f"[OK] appended robust v2 causal wrapper -> {CAUSAL} (sha={sha12(CAUSAL)})")

    import py_compile
    py_compile.compile(str(CAUSAL), doraise=True)
    print("[DONE] causal.py compiled clean")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
