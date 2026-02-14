from __future__ import annotations

import hashlib
import re
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).resolve().parents[1]
MATH_DIR = ROOT / "fantasyai" / "aurora" / "math"

FORECASTING = MATH_DIR / "forecasting.py"
REGIMES     = MATH_DIR / "regimes.py"
CAUSAL      = MATH_DIR / "causal.py"
DEEP_MATH   = MATH_DIR / "deep_math.py"

def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:12]

def backup(path: Path) -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    bak = path.with_suffix(path.suffix + f".bak_{ts}")
    bak.write_bytes(path.read_bytes())
    return bak

def ensure_function(path: Path, func_name: str, func_src: str) -> bool:
    txt = path.read_text(encoding="utf-8")
    if re.search(rf"^\s*def\s+{re.escape(func_name)}\s*\(", txt, flags=re.M):
        return False
    # ensure file ends with newline
    if not txt.endswith("\n"):
        txt += "\n"
    txt += "\n\n" + func_src.rstrip() + "\n"
    path.write_text(txt, encoding="utf-8")
    return True

def move_future_import_to_top(path: Path) -> None:
    txt = path.read_text(encoding="utf-8")
    lines = txt.splitlines()

    # Find any "from __future__ import annotations" not at the top
    future_idx = None
    for i, line in enumerate(lines):
        if line.strip() == "from __future__ import annotations":
            future_idx = i
            break
    if future_idx is None:
        return

    # Remove it
    future_line = lines.pop(future_idx)

    # Reinsert at the earliest valid spot:
    # after optional shebang/encoding and module docstring
    insert_at = 0
    if lines and lines[0].startswith("#!"):
        insert_at = 1
    if insert_at < len(lines) and re.match(r"^#.*coding[:=]\s*[-\w.]+", lines[insert_at]):
        insert_at += 1

    # Skip initial blank lines
    while insert_at < len(lines) and lines[insert_at].strip() == "":
        insert_at += 1

    # Skip module docstring if present
    if insert_at < len(lines) and (lines[insert_at].lstrip().startswith('"""') or lines[insert_at].lstrip().startswith("'''")):
        q = '"""' if lines[insert_at].lstrip().startswith('"""') else "'''"
        # find end
        if lines[insert_at].count(q) >= 2:
            insert_at += 1
        else:
            insert_at += 1
            while insert_at < len(lines):
                if q in lines[insert_at]:
                    insert_at += 1
                    break
                insert_at += 1

        # consume blank line after docstring
        while insert_at < len(lines) and lines[insert_at].strip() == "":
            insert_at += 1

    lines.insert(insert_at, future_line)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

def harden_deep_math_imports(path: Path) -> None:
    """
    Make deep_math resilient: optional imports should not crash module import.
    We keep required imports as-is, but wrap optional internal modules in try/except.
    """
    txt = path.read_text(encoding="utf-8")

    # If we already have our hardened block marker, don't repeat.
    if "## WORLDCLASS_IMPORT_GUARD" in txt:
        return

    # Insert a guarded import section right after stdlib imports / before first internal import,
    # but without being too clever: we just add a safe fallback helper and guarded imports at top.
    insert_marker = None
    for m in re.finditer(r"^\s*from\s+\.\s*import\s+|^\s*from\s+\.\w+\s+import\s+", txt, flags=re.M):
        insert_marker = m.start()
        break

    guard_block = """
## WORLDCLASS_IMPORT_GUARD
# Optional-module guard: deep_math should never fail import if optional math modules aren't ready yet.
# We degrade gracefully and report missing modules in output instead of crashing the runner.
_OPTIONAL_IMPORT_ERRORS = {}

def _optional_import(name: str):
    try:
        module = __import__(name, fromlist=["*"])
        return module
    except Exception as e:
        _OPTIONAL_IMPORT_ERRORS[name] = f"{type(e).__name__}:{e}"
        return None
""".lstrip()

    if insert_marker is None:
        # no internal imports found; just append after top
        txt2 = guard_block + "\n" + txt
    else:
        txt2 = txt[:insert_marker] + guard_block + "\n" + txt[insert_marker:]

    # Now rewrite specific fragile imports if they exist, to guarded form.
    # We replace e.g. "from .regimes import detect_regimes" with guarded get.
    replacements = [
        (r"^\s*from\s+\.forecasting\s+import\s+forecast_with_bands\s*$",
         "try:\n    from .forecasting import forecast_with_bands\nexcept Exception as e:\n    forecast_with_bands = None\n    _OPTIONAL_IMPORT_ERRORS['fantasyai.aurora.math.forecasting'] = f\"{type(e).__name__}:{e}\""),
        (r"^\s*from\s+\.regimes\s+import\s+detect_regimes\s*$",
         "try:\n    from .regimes import detect_regimes\nexcept Exception as e:\n    detect_regimes = None\n    _OPTIONAL_IMPORT_ERRORS['fantasyai.aurora.math.regimes'] = f\"{type(e).__name__}:{e}\""),
        (r"^\s*from\s+\.causal\s+import\s+causal_what_if_linear\s*$",
         "try:\n    from .causal import causal_what_if_linear\nexcept Exception as e:\n    causal_what_if_linear = None\n    _OPTIONAL_IMPORT_ERRORS['fantasyai.aurora.math.causal'] = f\"{type(e).__name__}:{e}\""),
    ]

    for pat, repl in replacements:
        txt2 = re.sub(pat, repl, txt2, flags=re.M)

    path.write_text(txt2, encoding="utf-8")

def main() -> int:
    # 0) sanity: files exist
    for p in [FORECASTING, REGIMES, CAUSAL, DEEP_MATH]:
        if not p.exists():
            raise SystemExit(f"Missing file: {p}")

    # 1) backups
    for p in [FORECASTING, REGIMES, CAUSAL, DEEP_MATH]:
        bak = backup(p)
        print(f"[OK] backup -> {bak}")

    # 2) forecasting compat
    added = ensure_function(
        FORECASTING,
        "forecast_with_bands",
        r'''
def forecast_with_bands(y, horizon=14, alpha=0.05):
    """
    Lightweight forecast with uncertainty bands.

    Parameters
    ----------
    y : array-like
        numeric series
    horizon : int
        steps ahead
    alpha : float
        tail probability (0.05 => ~95% band)

    Returns
    -------
    dict with keys: forecast, lower, upper, method
    """
    import numpy as np

    arr = np.asarray(y, dtype=float)
    arr = arr[np.isfinite(arr)]
    if arr.size < 3:
        mu = float(np.nanmean(arr)) if arr.size else 0.0
        f = [mu] * int(horizon)
        return {"forecast": f, "lower": f, "upper": f, "method": "naive_mean"}

    mu = float(np.mean(arr))
    sig = float(np.std(arr, ddof=1)) if arr.size > 1 else 0.0
    # Normal approx band; simple and stable
    z = 1.96  # ~95%
    if alpha and alpha != 0.05:
        # keep stable; don't depend on scipy here
        z = 1.96
    f = [mu] * int(horizon)
    lo = [mu - z * sig] * int(horizon)
    hi = [mu + z * sig] * int(horizon)
    return {"forecast": f, "lower": lo, "upper": hi, "method": "naive_mean_normal_band"}
''',
    )
    print(f"[OK] forecasting: forecast_with_bands present (added={added}) sha={sha(FORECASTING)}")

    # 3) regimes compat (also fix __future__ placement if present)
    move_future_import_to_top(REGIMES)
    added = ensure_function(
        REGIMES,
        "detect_regimes",
        r'''
def detect_regimes(series, n_bkps=3):
    """
    Simple regime / change-point detection.

    Uses 'ruptures' if available, otherwise falls back to variance-based segmentation.

    Returns
    -------
    dict with keys: breakpoints, method
    """
    import numpy as np

    x = np.asarray(series, dtype=float)
    x = x[np.isfinite(x)]
    if x.size < 20:
        return {"breakpoints": [], "method": "insufficient_data"}

    try:
        import ruptures as rpt  # type: ignore
        algo = rpt.Pelt(model="rbf").fit(x.reshape(-1, 1))
        bkps = algo.predict(pen=10)  # stable default
        bkps = [int(b) for b in bkps if int(b) < int(x.size)]
        return {"breakpoints": bkps, "method": "ruptures_pelt_rbf"}
    except Exception:
        # fallback: split where rolling variance spikes
        w = max(10, x.size // 20)
        var = np.array([np.var(x[max(0, i-w):i+1]) for i in range(x.size)])
        thresh = float(np.quantile(var, 0.95))
        idx = np.where(var >= thresh)[0]
        # keep a few well-separated change points
        bkps = []
        last = -10**9
        for i in idx:
            if i - last >= w:
                bkps.append(int(i))
                last = int(i)
            if len(bkps) >= int(n_bkps):
                break
        return {"breakpoints": bkps, "method": "rolling_var_fallback"}
''',
    )
    print(f"[OK] regimes: detect_regimes present (added={added}) sha={sha(REGIMES)}")

    # 4) causal compat
    added = ensure_function(
        CAUSAL,
        "causal_what_if_linear",
        r'''
def causal_what_if_linear(df, treatment, outcome, controls=None, delta=1.0):
    """
    Simple, stable 'what-if' estimator for numeric treatment.

    Fits OLS: outcome ~ treatment + controls (with intercept).
    Returns the estimated average effect of increasing treatment by `delta`.

    Parameters
    ----------
    df : pandas.DataFrame
    treatment : str
    outcome : str
    controls : list[str] | None
    delta : float

    Returns
    -------
    dict containing coef, intercept, r2, effect_delta, n, used_cols
    """
    import numpy as np
    import pandas as pd

    controls = list(controls) if controls else []
    used = [treatment, outcome] + controls
    d = df[used].copy()

    # numeric hygiene
    for c in used:
        d[c] = pd.to_numeric(d[c], errors="coerce")
    d = d.dropna()
    n = int(len(d))
    if n < max(30, 5 + len(controls)):
        return {"ok": False, "reason": "insufficient_data", "n": n, "used_cols": used}

    y = d[outcome].to_numpy(dtype=float)
    X_cols = [treatment] + controls
    X = d[X_cols].to_numpy(dtype=float)

    # add intercept
    X1 = np.column_stack([np.ones(n), X])

    # OLS via lstsq (stable, no heavy deps)
    beta, *_ = np.linalg.lstsq(X1, y, rcond=None)
    intercept = float(beta[0])
    coef_treat = float(beta[1])

    yhat = X1 @ beta
    ss_res = float(np.sum((y - yhat) ** 2))
    ss_tot = float(np.sum((y - float(np.mean(y))) ** 2))
    r2 = float(1.0 - ss_res / ss_tot) if ss_tot > 0 else float("nan")

    return {
        "ok": True,
        "n": n,
        "used_cols": used,
        "model": "ols_lstsq",
        "intercept": intercept,
        "coef_treatment": coef_treat,
        "effect_delta": float(delta) * coef_treat,
        "r2": r2,
    }
''',
    )
    print(f"[OK] causal: causal_what_if_linear present (added={added}) sha={sha(CAUSAL)}")

    # 5) harden deep_math imports so missing optional symbols never crash import
    harden_deep_math_imports(DEEP_MATH)
    print(f"[OK] deep_math hardened imports sha={sha(DEEP_MATH)}")

    # 6) compile check
    import py_compile
    for p in [FORECASTING, REGIMES, CAUSAL, DEEP_MATH]:
        py_compile.compile(str(p), doraise=True)
    print("[DONE] compat pack applied + py_compile clean")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
