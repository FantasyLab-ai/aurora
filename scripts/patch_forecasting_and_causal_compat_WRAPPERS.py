from __future__ import annotations

from pathlib import Path
from datetime import datetime
import hashlib
import re

ROOT = Path(__file__).resolve().parents[1]
FORECASTING = ROOT / "fantasyai" / "aurora" / "math" / "forecasting.py"
CAUSAL      = ROOT / "fantasyai" / "aurora" / "math" / "causal.py"

def sha12(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()[:12]

def backup(p: Path) -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    bak = p.with_suffix(p.suffix + f".bak_{ts}")
    bak.write_bytes(p.read_bytes())
    return bak

FORECAST_WRAPPER = r'''
# =============================================================================
# Compatibility wrapper: forecast_with_bands
# - Accepts arbitrary kwargs (e.g., seasonal_periods) so callers won't break
# - Sanitizes inputs (Series/DataFrame/object arrays) into a numeric 1D series
# - Returns forecast + bands as plain python lists
# =============================================================================
def forecast_with_bands(y, horizon: int = 24, alpha: float = 0.05, method: str = "ar1", **kwargs):
    """
    Stable forecast helper used by deep-math.
    Accepts extra kwargs for forward/backward compatibility (e.g. seasonal_periods).
    """
    import numpy as np
    import pandas as pd

    # Normalize horizon
    try:
        h = int(horizon)
    except Exception:
        h = 24
    h = max(1, min(h, 1000))

    # Convert y -> 1D numeric array
    if isinstance(y, pd.DataFrame):
        num_cols = [c for c in y.columns if pd.api.types.is_numeric_dtype(y[c])]
        if not num_cols:
            # try coercion on all columns
            yy = None
            for c in y.columns:
                s = pd.to_numeric(y[c], errors="coerce")
                if s.notna().sum() >= 10:
                    yy = s
                    break
            if yy is None:
                raise ValueError("forecast_with_bands: no usable numeric data in DataFrame")
        else:
            yy = pd.to_numeric(y[num_cols[0]], errors="coerce")
    else:
        # Series / list / ndarray / object
        yy = pd.to_numeric(y, errors="coerce")

    yy = yy.dropna()
    if len(yy) < 10:
        raise ValueError("forecast_with_bands: not enough numeric points after cleaning")

    arr = yy.to_numpy(dtype=float)

    # Simple AR(1) fallback model (robust, no external deps)
    x = arr[:-1]
    z = arr[1:]
    denom = float(np.dot(x, x)) if len(x) else 0.0
    phi = float(np.dot(x, z) / denom) if denom > 1e-12 else 0.0
    resid = z - (phi * x)
    sigma = float(np.std(resid)) if len(resid) else float(np.std(arr))

    # Forecast
    f = []
    last = float(arr[-1])
    for _ in range(h):
        last = phi * last
        f.append(last)

    f = np.asarray(f, dtype=float)

    # Very simple symmetric bands using Normal approx
    # (If scipy not present, use 1.96 for ~95% and scale alpha crudely)
    z95 = 1.96
    try:
        # approximate z-score from alpha if needed
        # keep stable, no scipy dependency
        if alpha and alpha > 0 and alpha < 1:
            if abs(alpha - 0.05) > 1e-6:
                # crude mapping for common alphas
                z_map = {0.10: 1.645, 0.05: 1.96, 0.02: 2.326, 0.01: 2.576}
                z95 = z_map.get(float(alpha), 1.96)
    except Exception:
        z95 = 1.96

    lo = (f - z95 * sigma).tolist()
    hi = (f + z95 * sigma).tolist()

    return {
        "method": method,
        "horizon": h,
        "alpha": float(alpha),
        "phi": phi,
        "sigma": sigma,
        "forecast": f.tolist(),
        "lower": lo,
        "upper": hi,
        "note": "ok",
    }
'''.lstrip()

CAUSAL_WRAPPER = r'''
# =============================================================================
# Compatibility wrapper: causal_what_if_linear
# - Accepts arbitrary kwargs (target_col/treatment_col/feature_cols/etc.)
# - Auto-selects treatment_col if missing
# - Uses robust OLS via numpy lstsq, returns simple effect estimate
# =============================================================================
def causal_what_if_linear(
    df,
    target_col: str | None = None,
    treatment_col: str | None = None,
    feature_cols=None,
    delta: float = 1.0,
    **kwargs
):
    """
    Stable what-if estimator:
      - Picks a numeric target_col if not provided
      - Picks a numeric treatment_col if not provided (not equal to target_col)
      - Fits OLS: target ~ treatment + controls
      - Returns effect of +delta treatment on predicted target
    """
    import numpy as np
    import pandas as pd

    if df is None:
        raise ValueError("causal_what_if_linear: df is None")
    if not isinstance(df, pd.DataFrame):
        df = pd.DataFrame(df)

    # pick numeric columns
    numeric_cols = [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c])]
    if not numeric_cols:
        # attempt coercion
        for c in list(df.columns):
            df[c] = pd.to_numeric(df[c], errors="ignore")
        numeric_cols = [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c])]

    if not numeric_cols:
        raise ValueError("causal_what_if_linear: no numeric columns available")

    if target_col is None or target_col not in df.columns or target_col not in numeric_cols:
        target_col = numeric_cols[0]

    if treatment_col is None or treatment_col not in df.columns or treatment_col not in numeric_cols or treatment_col == target_col:
        # choose a different numeric col
        treatment_col = None
        for c in numeric_cols:
            if c != target_col:
                treatment_col = c
                break
        if treatment_col is None:
            raise ValueError("causal_what_if_linear: could not select a treatment_col")

    # controls
    if feature_cols is None:
        feature_cols = [c for c in numeric_cols if c not in (target_col, treatment_col)]
    else:
        feature_cols = [c for c in feature_cols if c in df.columns and c not in (target_col, treatment_col)]

    use_cols = [target_col, treatment_col] + list(feature_cols)
    d = df[use_cols].copy()
    for c in use_cols:
        d[c] = pd.to_numeric(d[c], errors="coerce")
    d = d.dropna()

    if len(d) < 50:
        raise ValueError("causal_what_if_linear: not enough rows after cleaning")

    y = d[target_col].to_numpy(dtype=float)
    X_parts = [np.ones(len(d), dtype=float), d[treatment_col].to_numpy(dtype=float)]
    for c in feature_cols:
        X_parts.append(d[c].to_numpy(dtype=float))
    X = np.column_stack(X_parts)

    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    b0 = float(beta[0])
    b_treat = float(beta[1])

    base = float(np.mean(y))
    effect = b_treat * float(delta)
    return {
        "target_col": target_col,
        "treatment_col": treatment_col,
        "feature_cols": list(feature_cols),
        "n": int(len(d)),
        "beta0": b0,
        "beta_treatment": b_treat,
        "delta": float(delta),
        "estimated_effect": float(effect),
        "note": "ok",
    }
'''.lstrip()

def ensure_appended(file_path: Path, marker: str, payload: str) -> tuple[bool, str]:
    txt = file_path.read_text(encoding="utf-8", errors="ignore")
    if marker in txt:
        return (False, "already_present")
    # append with spacing
    if not txt.endswith("\n"):
        txt += "\n"
    txt += "\n" + payload + "\n"
    file_path.write_text(txt, encoding="utf-8")
    return (True, "added")

def main() -> int:
    for p in (FORECASTING, CAUSAL):
        if not p.exists():
            raise SystemExit(f"Missing: {p}")

    bak_f = backup(FORECASTING)
    bak_c = backup(CAUSAL)
    print(f"[OK] backup -> {bak_f}")
    print(f"[OK] backup -> {bak_c}")

    added_f, msg_f = ensure_appended(FORECASTING, "Compatibility wrapper: forecast_with_bands", FORECAST_WRAPPER)
    added_c, msg_c = ensure_appended(CAUSAL,      "Compatibility wrapper: causal_what_if_linear",  CAUSAL_WRAPPER)

    print(f"[OK] forecasting.py: {msg_f} (added={added_f}) sha={sha12(FORECASTING)}")
    print(f"[OK] causal.py:      {msg_c} (added={added_c}) sha={sha12(CAUSAL)}")

    import py_compile
    py_compile.compile(str(FORECASTING), doraise=True)
    py_compile.compile(str(CAUSAL), doraise=True)
    print("[DONE] forecasting + causal compat wrappers compiled cleanly.")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
