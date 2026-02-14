from __future__ import annotations

from pathlib import Path
from datetime import datetime
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
FORECASTING = ROOT / "fantasyai" / "aurora" / "math" / "forecasting.py"
CAUSAL = ROOT / "fantasyai" / "aurora" / "math" / "causal.py"

def backup(p: Path) -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    bak = p.with_suffix(p.suffix + f".bak_{ts}")
    bak.write_bytes(p.read_bytes())
    return bak

FORECASTING_SHIM = r"""
# =========================
# COMPAT SHIM: forecast_with_bands (stable)
# =========================
def forecast_with_bands(
    data,
    target_col=None,
    time_col=None,
    horizon=24,
    seasonal_periods=None,
    seed=None,
    **kwargs
):
    \"\"\"Stable forecasting shim.

    Accepts either:
      - data: pd.Series/np.array-like
      - data: pd.DataFrame with target_col (preferred)
      - data: pd.DataFrame without target_col -> auto-pick first numeric col

    Returns a dict with forecast bands. Never raises (wraps errors).
    \"\"\"
    try:
        import numpy as np
        import pandas as pd
    except Exception as e:
        return {"error": f"ImportError:{type(e).__name__}:{e}"}

    try:
        # Resolve y series
        if hasattr(data, "select_dtypes"):  # DataFrame-like
            df = data
            if isinstance(target_col, str) and target_col in df.columns:
                y = df[target_col]
            else:
                num_df = df.select_dtypes(include=["number"])
                if num_df.shape[1] == 0:
                    # Try coercing all cols to numeric (drop fully NaN)
                    coerced = df.apply(lambda s: pd.to_numeric(s, errors="coerce"))
                    coerced = coerced.dropna(axis=1, how="all")
                    if coerced.shape[1] == 0:
                        return {"error": "ValueError:no_numeric_columns_for_forecast"}
                    y = coerced.iloc[:, 0]
                else:
                    y = num_df.iloc[:, 0]
        else:
            y = data

        # Coerce to numeric vector
        y = pd.Series(y)
        y = pd.to_numeric(y, errors="coerce")
        y = y.dropna()

        if len(y) < 10:
            return {"error": f"ValueError:not_enough_numeric_points:{len(y)}"}

        # Very stable baseline: AR(1)-ish with bootstrap-ish bands via residual std
        # (Simple, fast, no extra deps.)
        yv = y.to_numpy(dtype=float)
        last = float(yv[-1])
        # crude drift estimate
        diffs = np.diff(yv)
        drift = float(np.nanmedian(diffs)) if len(diffs) else 0.0
        resid = diffs - drift if len(diffs) else np.array([0.0])
        sigma = float(np.nanstd(resid)) if len(resid) else 0.0

        h = int(horizon) if horizon is not None else 24
        h = max(1, min(h, 10000))

        median = []
        p05 = []
        p95 = []
        cur = last
        for i in range(h):
            cur = cur + drift
            median.append(cur)
            # widening interval
            w = 1.645 * sigma * (i + 1) ** 0.5
            p05.append(cur - w)
            p95.append(cur + w)

        return {
            "method": "drift_baseline",
            "horizon": h,
            "target_col": target_col,
            "time_col": time_col,
            "seasonal_periods": seasonal_periods,
            "seed": seed,
            "bands": {"p05": p05, "p50": median, "p95": p95},
            "note": "ok",
        }
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}
"""

CAUSAL_SHIM = r"""
# =========================
# COMPAT SHIM: causal_what_if_linear (stable)
# =========================
def causal_what_if_linear(
    df,
    target_col=None,
    treatment_col=None,
    delta=1.0,
    seed=None,
    **kwargs
):
    \"\"\"Stable 'what-if' estimator.

    - If treatment_col is None, auto-picks a numeric column most correlated with target_col.
    - Ignores unexpected kwargs for backward/forward compatibility.
    - Returns dict; never raises.
    \"\"\"
    try:
        import numpy as np
        import pandas as pd
    except Exception as e:
        return {"error": f"ImportError:{type(e).__name__}:{e}"}

    try:
        if not hasattr(df, "columns"):
            return {"error": "TypeError:df_must_be_dataframe_like"}

        # pick target if missing
        if not isinstance(target_col, str) or target_col not in df.columns:
            num_cols = list(df.select_dtypes(include=["number"]).columns)
            if not num_cols:
                return {"error": "ValueError:no_numeric_columns_for_causal"}
            target_col = num_cols[0]

        num = df.select_dtypes(include=["number"]).copy()
        if target_col not in num.columns:
            num[target_col] = pd.to_numeric(df[target_col], errors="coerce")

        # pick treatment if missing
        if not isinstance(treatment_col, str) or treatment_col not in df.columns:
            cand = [c for c in num.columns if c != target_col]
            if not cand:
                return {"error": "ValueError:no_treatment_candidates"}
            # choose max abs correlation (robust to NaNs)
            corrs = []
            y = num[target_col]
            for c in cand:
                x = num[c]
                cc = x.corr(y)
                corrs.append((c, float(abs(cc)) if cc == cc else -1.0))
            corrs.sort(key=lambda t: t[1], reverse=True)
            treatment_col = corrs[0][0]

        # build regression on rows with both present
        d = num[[treatment_col, target_col]].copy()
        d = d.replace([np.inf, -np.inf], np.nan).dropna()
        if len(d) < 30:
            return {"error": f"ValueError:not_enough_rows:{len(d)}"}

        x = d[treatment_col].to_numpy(dtype=float)
        y = d[target_col].to_numpy(dtype=float)

        # OLS slope/intercept
        x_mean = float(np.mean(x))
        y_mean = float(np.mean(y))
        denom = float(np.sum((x - x_mean) ** 2))
        if denom == 0.0:
            return {"error": "ValueError:zero_variance_treatment"}

        beta = float(np.sum((x - x_mean) * (y - y_mean)) / denom)
        alpha = y_mean - beta * x_mean

        delta = float(delta) if delta is not None else 1.0
        avg_effect = beta * delta

        return {
            "method": "ols_linear",
            "target_col": target_col,
            "treatment_col": treatment_col,
            "delta": delta,
            "beta": beta,
            "alpha": alpha,
            "avg_effect": avg_effect,
            "n": int(len(d)),
            "note": "ok",
        }
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}
"""

def append_shim(path: Path, shim: str, marker: str) -> None:
    txt = path.read_text(encoding="utf-8", errors="ignore")
    if marker in txt:
        # replace existing block (idempotent)
        txt = re.sub(
            rf"(?s)\n# =========================\n# COMPAT SHIM: {re.escape(marker)}.*?\n(?=\n# =========================|\Z)",
            "\n" + shim.strip() + "\n",
            txt,
        )
        # if replace didn't match due to drift, just append
        if marker not in txt:
            txt = txt.rstrip() + "\n\n" + shim.strip() + "\n"
    else:
        txt = txt.rstrip() + "\n\n" + shim.strip() + "\n"
    path.write_text(txt, encoding="utf-8")

def main() -> int:
    if not FORECASTING.exists():
        print(f"[FAIL] Missing: {FORECASTING}", file=sys.stderr)
        return 2
    if not CAUSAL.exists():
        print(f"[FAIL] Missing: {CAUSAL}", file=sys.stderr)
        return 2

    bak1 = backup(FORECASTING)
    bak2 = backup(CAUSAL)
    print(f"[OK] backup -> {bak1}")
    print(f"[OK] backup -> {bak2}")

    append_shim(FORECASTING, FORECASTING_SHIM, "forecast_with_bands")
    print(f"[OK] forecasting shim ensured -> {FORECASTING}")

    append_shim(CAUSAL, CAUSAL_SHIM, "causal_what_if_linear")
    print(f"[OK] causal shim ensured -> {CAUSAL}")

    import py_compile
    py_compile.compile(str(FORECASTING), doraise=True)
    py_compile.compile(str(CAUSAL), doraise=True)
    print("[DONE] forecasting+causal shims applied + py_compile clean")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
