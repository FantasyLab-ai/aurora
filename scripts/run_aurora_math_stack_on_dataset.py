from __future__ import annotations

import json, os, time
from pathlib import Path

import numpy as np
import pandas as pd

def _safe_mkdir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)

def _read_table(path: str) -> pd.DataFrame:
    p = Path(path)
    suf = p.suffix.lower()

    if suf == ".parquet":
        # PhysioNet demo meds is parquet
        return pd.read_parquet(p)
    if suf == ".gz" and p.name.lower().endswith(".csv.gz"):
        return pd.read_csv(p, compression="gzip", engine="python", sep=None, on_bad_lines="skip")
    if suf == ".csv":
        return pd.read_csv(p, engine="python", sep=None, on_bad_lines="skip")
    # fallback: try pandas best effort
    return pd.read_csv(p, engine="python", sep=None, on_bad_lines="skip")

def _coerce_numeric(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for c in out.columns:
        if out[c].dtype == "object":
            out[c] = pd.to_numeric(out[c], errors="ignore")
    return out

def _basic_profile(df: pd.DataFrame) -> dict:
    return {
        "shape": [int(df.shape[0]), int(df.shape[1])],
        "columns": [str(c) for c in df.columns[:200]],
        "missing_frac_top10": (
            df.isna().mean().sort_values(ascending=False).head(10).to_dict()
        ),
        "dtypes_top30": {str(k): str(v) for k, v in df.dtypes.head(30).items()},
    }

def _numeric_summary(df: pd.DataFrame) -> dict:
    num = df.select_dtypes(include=[np.number]).copy()
    if num.empty:
        return {"note": "No numeric columns detected."}
    desc = num.describe(include="all").replace([np.inf, -np.inf], np.nan)
    return {
        "numeric_cols": [str(c) for c in num.columns[:200]],
        "describe": json.loads(desc.to_json()),
        "corr_top_pairs": _top_corr_pairs(num)
    }

def _top_corr_pairs(num: pd.DataFrame, k: int = 10) -> list:
    if num.shape[1] < 2:
        return []
    corr = num.corr(numeric_only=True).abs()
    np.fill_diagonal(corr.values, 0)
    pairs = []
    for _ in range(min(k, corr.size)):
        i, j = np.unravel_index(np.argmax(corr.values), corr.shape)
        v = float(corr.iat[i, j])
        if v <= 0:
            break
        pairs.append({"a": str(corr.index[i]), "b": str(corr.columns[j]), "abs_corr": v})
        corr.iat[i, j] = 0
    return pairs

def _simple_regression(df: pd.DataFrame) -> dict:
    """
    Heuristic:
    - pick a "target" numeric column (last numeric col)
    - regress it on up to 8 other numeric cols using ridge-ish closed form
    """
    num = df.select_dtypes(include=[np.number]).dropna()
    if num.shape[1] < 2 or num.shape[0] < 30:
        return {"note": "Not enough clean numeric data for regression."}

    y_col = num.columns[-1]
    X_cols = [c for c in num.columns[:-1]][:8]
    X = num[X_cols].to_numpy(dtype=float)
    y = num[y_col].to_numpy(dtype=float)

    # add intercept
    X1 = np.concatenate([np.ones((X.shape[0], 1)), X], axis=1)
    # ridge
    lam = 1e-3
    beta = np.linalg.pinv(X1.T @ X1 + lam * np.eye(X1.shape[1])) @ (X1.T @ y)

    yhat = X1 @ beta
    ss_res = float(((y - yhat) ** 2).sum())
    ss_tot = float(((y - y.mean()) ** 2).sum()) or 1.0
    r2 = 1.0 - ss_res / ss_tot

    return {
        "target": str(y_col),
        "features": [str(c) for c in X_cols],
        "coef": {
            "intercept": float(beta[0]),
            **{str(c): float(b) for c, b in zip(X_cols, beta[1:])}
        },
        "diagnostics": {
            "n": int(X.shape[0]),
            "r2": float(r2),
            "mse": float(ss_res / max(1, X.shape[0])),
        }
    }

def main() -> None:
    csv = os.environ.get("AURORA_DATASET_CSV")
    run_dir = Path(os.environ.get("AURORA_RUN_DIR", "")) if os.environ.get("AURORA_RUN_DIR") else None
    if not csv:
        raise SystemExit("Missing AURORA_DATASET_CSV env var (dataset runner should set this).")

    df = _read_table(csv)
    df = _coerce_numeric(df)

    # write artifacts
    out = (run_dir / "math_stack") if run_dir else (Path("outputs") / "aurora_dataset_runs" / "math_stack_" + time.strftime("%Y%m%d_%H%M%S"))
    _safe_mkdir(out)

    profile = _basic_profile(df)
    numeric = _numeric_summary(df)
    reg = _simple_regression(df)

    (out / "profile.json").write_text(json.dumps(profile, indent=2), encoding="utf-8")
    (out / "numeric.json").write_text(json.dumps(numeric, indent=2), encoding="utf-8")
    (out / "regression.json").write_text(json.dumps(reg, indent=2), encoding="utf-8")

    # Storycard payload (one file your UI can render)
    storycard = {
        "title": "Aurora Math Stack — Dataset Run",
        "dataset_path": csv,
        "profile": profile,
        "numeric": numeric,
        "regression": reg,
        "next_steps": [
            "If you want causal what-if: define target + treatment columns in registry metadata (next patch).",
            "If you want clustering/regimes: add time column hint or entity ID column hint.",
            "If you want anomaly: choose numeric measures and optional grouping keys."
        ]
    }
    (out / "storycard.json").write_text(json.dumps(storycard, indent=2), encoding="utf-8")

    print(json.dumps({"ok": True, "storycard": str((out / "storycard.json").resolve())}, indent=2))

if __name__ == "__main__":
    main()
