from __future__ import annotations
from pathlib import Path
from datetime import datetime
import re
import inspect
import json

ROOT = Path(__file__).resolve().parents[1]
DEEP_MATH = ROOT / "fantasyai" / "aurora" / "math" / "deep_math.py"

def backup(p: Path) -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    bak = p.with_suffix(p.suffix + f".bak_{ts}")
    bak.write_bytes(p.read_bytes())
    return bak

HELPERS = r'''
# ---------------------------
# Robust call helpers (auto-patched)
# ---------------------------
def _safe_numeric_series(df, col: str):
    import pandas as pd
    import numpy as np
    if col not in df.columns:
        return None
    s = pd.to_numeric(df[col], errors="coerce")
    s = s.replace([np.inf, -np.inf], np.nan).dropna()
    return s

def _forecast_with_bands_robust(*, df, target_col: str, time_col: str, horizon: int = 24, alpha: float = 0.05, seed=None):
    """
    Calls forecast_with_bands safely across versions:
    - removes non-numeric issues by coercing to numeric
    - only passes kwargs supported by the installed function
    """
    try:
        from fantasyai.aurora.math.forecasting import forecast_with_bands  # type: ignore
    except Exception as e:
        return {"error": f"forecast_with_bands_unavailable:{type(e).__name__}:{e}"}

    try:
        sig = inspect.signature(forecast_with_bands)
        kw = {}
        # Prefer giving the function what it expects.
        # If it expects df/target_col/time_col, pass them.
        if "df" in sig.parameters:
            kw["df"] = df
        if "target_col" in sig.parameters:
            kw["target_col"] = target_col
        if "time_col" in sig.parameters:
            kw["time_col"] = time_col

        # If it instead expects a series, pass numeric series of target_col.
        if ("series" in sig.parameters) and ("df" not in sig.parameters):
            s = _safe_numeric_series(df, target_col)
            if s is None or len(s) < 50:
                return {"error": "forecasting_failed:not_enough_numeric_rows"}
            kw["series"] = s

        # Common optional args
        if "horizon" in sig.parameters:
            kw["horizon"] = int(horizon)
        if "alpha" in sig.parameters:
            kw["alpha"] = float(alpha)
        if "seed" in sig.parameters and seed is not None:
            kw["seed"] = int(seed)

        # Some versions support seasonal_periods; ONLY pass if supported.
        if "seasonal_periods" in sig.parameters:
            kw["seasonal_periods"] = None

        res = forecast_with_bands(**kw)  # type: ignore

        # Normalize output into dict-like jsonable structure
        if isinstance(res, dict):
            res.setdefault("note", "ok")
            return res

        # If function returns tuple or custom object, wrap it
        return {"note": "ok", "result": res}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}

def _causal_what_if_robust(*, df, target_col: str, treatment_col: str, controls=None, delta: float = 1.0):
    """
    Calls causal_what_if_linear safely across versions:
    - supports either (target_col, treatment_col, ...) OR (target_col, feature_col, ...)
    - always returns dict with note or error
    """
    controls = controls or []
    try:
        from fantasyai.aurora.math.causal import causal_what_if_linear  # type: ignore
    except Exception as e:
        return {"error": f"causal_unavailable:{type(e).__name__}:{e}"}

    try:
        sig = inspect.signature(causal_what_if_linear)
        kw = {"df": df}

        if "target_col" in sig.parameters:
            kw["target_col"] = target_col

        # Newer wrapper naming
        if "treatment_col" in sig.parameters:
            kw["treatment_col"] = treatment_col
        # Older naming
        elif "feature_col" in sig.parameters:
            kw["feature_col"] = treatment_col

        if "controls" in sig.parameters:
            kw["controls"] = controls
        if "feature_cols" in sig.parameters:
            kw["feature_cols"] = controls  # best effort; many wrappers accept list
        if "delta" in sig.parameters:
            kw["delta"] = float(delta)
        if "delta_x" in sig.parameters:
            kw["delta_x"] = float(delta)
        if "delta_x" not in sig.parameters and "delta" not in sig.parameters and "delta_x_grid" in sig.parameters:
            # ignore; wrapper will use default

        res = causal_what_if_linear(**kw)  # type: ignore
        if isinstance(res, dict):
            res.setdefault("note", "ok")
            return res
        return {"note": "ok", "result": res}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}
'''

def main():
    if not DEEP_MATH.exists():
        raise SystemExit(f"Missing: {DEEP_MATH}")

    bak = backup(DEEP_MATH)
    print(f"[OK] backup -> {bak}")

    txt = DEEP_MATH.read_text(encoding="utf-8")

    # 1) Ensure helpers exist exactly once
    if "_forecast_with_bands_robust" not in txt or "_causal_what_if_robust" not in txt:
        # Insert helpers near top after imports block.
        lines = txt.splitlines()
        insert_at = 0
        for i, line in enumerate(lines):
            if line.startswith("import ") or line.startswith("from "):
                insert_at = i + 1
            else:
                if i > 0:
                    break
        lines.insert(insert_at, HELPERS.strip("\n"))
        txt = "\n".join(lines) + "\n"
        print("[OK] injected robust helpers")
    else:
        print("[OK] robust helpers already present")

    # 2) Replace forecasting call inside compute_deep_math_v3 with robust wrapper
    txt2, n_fc = re.subn(
        r"fc\s*=\s*forecast_with_bands\([^\)]*\)\s*",
        "fc = _forecast_with_bands_robust(df=df, target_col=ycol, time_col=tcol, horizon=24, alpha=0.05, seed=seed)\n",
        txt,
        flags=re.S
    )

    # If the file uses a different pattern (multi-line), also catch a more specific signature
    if n_fc == 0:
        txt2, n_fc = re.subn(
            r"fc\s*=\s*forecast_with_bands\([^\)]*target_col\s*=\s*ycol[^\)]*\)\s*",
            "fc = _forecast_with_bands_robust(df=df, target_col=ycol, time_col=tcol, horizon=24, alpha=0.05, seed=seed)\n",
            txt,
            flags=re.S
        )
    if n_fc:
        print(f"[OK] forecasting call patched ({n_fc} replacements)")
    else:
        print("[WARN] forecasting call pattern not found; no change made")

    # 3) Fix causal output assignment bug if present (out["causal_what_if"] = c -> cw)
    txt3, n_bug = re.subn(
        r'out\["causal_what_if"\]\s*=\s*c\s*$',
        'out["causal_what_if"] = cw',
        txt2,
        flags=re.M
    )
    if n_bug:
        print(f"[OK] fixed causal assignment bug ({n_bug})")

    # 4) Replace causal call with robust wrapper
    txt4, n_cw = re.subn(
        r"cw\s*=\s*causal_what_if_linear\([^\)]*target_col\s*=\s*ycol[^\)]*\)\s*",
        "cw = _causal_what_if_robust(df=df, target_col=ycol, treatment_col=best_feat, controls=[], delta=1.0)\n",
        txt3,
        flags=re.S
    )
    if n_cw:
        print(f"[OK] causal call patched ({n_cw} replacements)")
    else:
        print("[WARN] causal call pattern not found; no change made")

    # 5) Guarantee deep_math outputs always include forecasting/causal keys
    # Add defaults just before return out in compute_deep_math_v3 (best-effort).
    if 'out.setdefault("forecasting"' not in txt4:
        txt4, n_ret = re.subn(
            r"(\n\s*return\s+out\s*\n)",
            '\n    out.setdefault("forecasting", {"note": "skipped"})\n    out.setdefault("causal_what_if", {"note": "skipped"})\n\\1',
            txt4,
            count=1
        )
        if n_ret:
            print("[OK] ensured forecasting/causal keys always exist")

    DEEP_MATH.write_text(txt4, encoding="utf-8")
    print(f"[DONE] wrote -> {DEEP_MATH}")

    import py_compile
    py_compile.compile(str(DEEP_MATH), doraise=True)
    print("[DONE] deep_math.py py_compile clean")

if __name__ == "__main__":
    main()
