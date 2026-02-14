from __future__ import annotations

import hashlib
import re
from pathlib import Path

CAUSAL = Path("fantasyai/aurora/math/causal.py")

PATCH = r'''
# ====== ULTRA-STABLE WRAPPER (v3) ======
def causal_what_if_linear_ultra(
    df,
    target_col: str,
    treatment_col: str,
    feature_cols=None,
    delta: float = 1.0,
    min_rows: int = 200,
):
    """
    Ultra-stable causal what-if:
      - numeric coercion
      - dropna only on required columns
      - remove constant columns
      - standardize predictors
      - least squares with lstsq fallback
      - ridge fallback if SVD fails
    Returns a dict with note=ok or error.
    """
    import numpy as np
    import pandas as pd

    out = {
        "target_col": target_col,
        "treatment_col": treatment_col,
        "feature_cols": feature_cols or [],
        "delta": float(delta),
        "note": None,
    }

    try:
        if df is None or len(df) == 0:
            raise ValueError("empty_df")

        cols = [target_col, treatment_col] + (list(feature_cols) if feature_cols else [])
        cols = [c for c in cols if c in df.columns]
        if target_col not in cols or treatment_col not in cols:
            raise ValueError("missing_required_columns")

        work = df[cols].copy()

        # Coerce numeric (hard)
        for c in cols:
            work[c] = pd.to_numeric(work[c], errors="coerce")

        # Drop rows only where target/treatment missing
        work = work.dropna(subset=[target_col, treatment_col])

        if len(work) < min_rows:
            raise ValueError(f"not_enough_rows_after_cleaning:{len(work)}")

        y = work[target_col].astype(float).to_numpy()
        t = work[treatment_col].astype(float).to_numpy()

        X_feats = []
        feat_names = []

        if feature_cols:
            for c in feature_cols:
                if c not in work.columns:
                    continue
                x = work[c].astype(float).to_numpy()
                # remove constant / nearly constant features
                if np.nanstd(x) < 1e-12:
                    continue
                X_feats.append(x)
                feat_names.append(c)

        # Build design matrix: [1, treatment, features...]
        ones = np.ones_like(t)
        X = np.column_stack([ones, t] + X_feats)

        # Standardize columns except intercept
        Xs = X.copy()
        for j in range(1, Xs.shape[1]):
            mu = np.nanmean(Xs[:, j])
            sd = np.nanstd(Xs[:, j])
            if not np.isfinite(sd) or sd < 1e-12:
                # drop degenerate column by setting to 0
                Xs[:, j] = 0.0
            else:
                Xs[:, j] = (Xs[:, j] - mu) / sd

        # Remove any remaining NaN/inf rows safely
        mask = np.isfinite(y)
        mask &= np.all(np.isfinite(Xs), axis=1)
        y = y[mask]
        Xs = Xs[mask]

        if len(y) < min_rows:
            raise ValueError(f"not_enough_rows_post_finite_filter:{len(y)}")

        # Solve least squares (robust)
        try:
            beta, *_ = np.linalg.lstsq(Xs, y, rcond=None)
        except Exception:
            # Ridge fallback
            lam = 1e-6
            XtX = Xs.T @ Xs
            Xty = Xs.T @ y
            beta = np.linalg.solve(XtX + lam * np.eye(XtX.shape[0]), Xty)

        # Effect of treatment: coefficient on standardized treatment
        beta_treat = float(beta[1])

        out.update(
            {
                "n": int(len(y)),
                "beta_treatment": beta_treat,
                "estimated_effect": float(beta_treat * float(delta)),
                "strategy": "coerce_numeric_dropna_target_treatment_remove_constant_standardize_lstsq_ridge_fallback",
                "feature_cols_used": feat_names,
                "note": "ok",
            }
        )
        return out

    except Exception as e:
        out["error"] = f"{type(e).__name__}: {e}"
        out["note"] = None
        return out
# ====== END ULTRA-STABLE WRAPPER (v3) ======
'''

def main() -> None:
    if not CAUSAL.exists():
        raise SystemExit(f"[FAIL] missing {CAUSAL}")

    src = CAUSAL.read_text(encoding="utf-8")

    if "causal_what_if_linear_ultra" in src:
        print("[OK] causal_what_if_linear_ultra already present; nothing to do.")
        return

    # Append patch at end
    bak = CAUSAL.with_suffix(".py.bak_ultra")
    bak.write_text(src, encoding="utf-8")
    CAUSAL.write_text(src.rstrip() + "\n\n" + PATCH + "\n", encoding="utf-8")

    sha = hashlib.sha256(CAUSAL.read_bytes()).hexdigest()[:12]
    print(f"[OK] backup -> {bak}")
    print(f"[OK] appended ultra stable wrapper -> {CAUSAL} (sha={sha})")

if __name__ == "__main__":
    main()
