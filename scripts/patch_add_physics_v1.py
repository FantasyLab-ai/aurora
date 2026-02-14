from __future__ import annotations
from pathlib import Path
import re
import shutil
import datetime as dt

ROOT = Path.cwd()
DEEP = ROOT / "fantasyai" / "aurora" / "math" / "deep_math.py"
PHYS = ROOT / "fantasyai" / "aurora" / "math" / "physics.py"

def backup(p: Path) -> Path:
    ts = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    b = p.with_suffix(p.suffix + f".bak_phys_{ts}")
    shutil.copy2(p, b)
    return b

PHYS_CONTENT = r'''from __future__ import annotations

from typing import Any, Dict, Optional, List, Tuple
import numpy as np
import pandas as pd


def _safe_datetime(s: pd.Series) -> Optional[pd.Series]:
    try:
        if pd.api.types.is_datetime64_any_dtype(s):
            return s
        dt = pd.to_datetime(s, errors="coerce")
        if dt.notna().mean() > 0.6:
            return dt
    except Exception:
        return None
    return None


def _infer_time_col(df: pd.DataFrame) -> Optional[str]:
    # prefer common names
    for cand in ["datetime", "timestamp", "time", "date", "ds"]:
        if cand in df.columns:
            dt = _safe_datetime(df[cand])
            if dt is not None:
                return cand

    # AirQualityUCI-style pair
    if ("Date" in df.columns) and ("Time" in df.columns):
        try:
            comb = (df["Date"].astype(str).str.strip() + " " + df["Time"].astype(str).str.strip())
            dt2 = pd.to_datetime(comb, errors="coerce", dayfirst=True)
            if dt2.notna().mean() > 0.6:
                return "Date+Time"
        except Exception:
            pass

    # fallback: first parseable col
    for c in df.columns[:50]:
        dt = _safe_datetime(df[c])
        if dt is not None:
            return c
    return None


def _get_time_series(df: pd.DataFrame, time_col: Optional[str]) -> Tuple[Optional[np.ndarray], Optional[str]]:
    if time_col is None:
        return None, None

    if time_col == "Date+Time" and ("Date" in df.columns) and ("Time" in df.columns):
        comb = (df["Date"].astype(str).str.strip() + " " + df["Time"].astype(str).str.strip())
        t = pd.to_datetime(comb, errors="coerce", dayfirst=True)
        return t.values, "Date+Time"

    t = pd.to_datetime(df[time_col], errors="coerce")
    return t.values, time_col


def _robust_dt_seconds(t: np.ndarray) -> Optional[float]:
    try:
        # numpy datetime64 -> ns
        tt = t.astype("datetime64[ns]")
        d = np.diff(tt).astype("timedelta64[ns]").astype(np.int64) / 1e9
        d = d[np.isfinite(d)]
        if d.size < 5:
            return None
        med = float(np.median(d))
        if not np.isfinite(med) or med <= 0:
            return None
        return med
    except Exception:
        return None


def _fit_linear_ode(y: np.ndarray, dt_s: float) -> Dict[str, Any]:
    """
    Fit dy/dt = a*y + b using least squares (simple physics-inspired dynamics).
    Returns a, b, fit metrics, and qualitative stability classification.
    """
    y = y.astype(float)
    m = np.isfinite(y)
    y = y[m]
    if y.size < 300:
        return {"note": "skipped", "error": "physics:not_enough_finite_samples"}

    # numerical derivative
    dy = np.gradient(y, dt_s)

    # solve dy = a*y + b
    X = np.column_stack([y, np.ones_like(y)])
    # least squares
    beta, *_ = np.linalg.lstsq(X, dy, rcond=None)
    a = float(beta[0])
    b = float(beta[1])

    pred = a * y + b
    resid = dy - pred
    ss_res = float(np.sum(resid ** 2))
    ss_tot = float(np.sum((dy - float(np.mean(dy))) ** 2)) + 1e-12
    r2 = 1.0 - ss_res / ss_tot
    rmse = float(np.sqrt(np.mean(resid ** 2)))

    # stability / time constant (if stable)
    stable = (a < 0)
    tau = float(-1.0 / a) if stable and abs(a) > 1e-12 else None

    return {
        "note": "ok",
        "error": None,
        "model": "dy_dt = a*y + b",
        "a": a,
        "b": b,
        "stable": bool(stable),
        "time_constant_seconds": tau,
        "r2": float(r2),
        "rmse": rmse,
        "n": int(y.size),
    }


def physics_diagnostics(
    df: pd.DataFrame,
    target_col: str,
    time_col: Optional[str] = None,
    seed: int = 0,
) -> Dict[str, Any]:
    """
    Physics-inspired diagnostics layer:
      - infer time axis if available
      - estimate sampling cadence
      - fit simple linear ODE dy/dt = a*y + b
      - report stability + time constant + fit metrics
    Always returns a JSON-serializable dict and never raises.
    """
    try:
        tc = time_col or _infer_time_col(df)
        t_arr, used_time = _get_time_series(df, tc) if tc is not None else (None, None)

        y = pd.to_numeric(df[target_col], errors="coerce").replace([np.inf, -np.inf], np.nan).values.astype(float)

        if t_arr is None:
            # no time axis: we can still do dt=1 “index-time” but mark it
            dt_s = 1.0
            fit = _fit_linear_ode(y, dt_s=dt_s)
            fit["time_axis"] = "index"
            fit["dt_seconds"] = dt_s
            fit["time_col"] = None
            return fit

        dt_s = _robust_dt_seconds(t_arr)
        if dt_s is None:
            dt_s = 1.0  # fallback
            axis_note = "datetime_unusable_fallback_index_dt1"
        else:
            axis_note = "datetime"

        fit = _fit_linear_ode(y, dt_s=dt_s)
        fit["time_axis"] = axis_note
        fit["dt_seconds"] = float(dt_s)
        fit["time_col"] = used_time
        return fit

    except Exception as e:
        return {"note": "failed", "error": f"physics_failed:{type(e).__name__}:{e}"}
'''

def main():
    if not DEEP.exists():
        raise SystemExit(f"ERROR: deep_math.py not found at {DEEP}")

    # 1) Write physics module (idempotent)
    PHYS.parent.mkdir(parents=True, exist_ok=True)
    if PHYS.exists():
        b = backup(PHYS)
        print(f"[OK] backup -> {b}")
    PHYS.write_text(PHYS_CONTENT, encoding="utf-8")
    print(f"[OK] wrote -> {PHYS}")

    # 2) Patch deep_math.py to import and call physics_diagnostics safely
    deep_txt = DEEP.read_text(encoding="utf-8", errors="ignore")
    b2 = backup(DEEP)
    print(f"[OK] backup -> {b2}")

    # add optional import line near other _try_imports
    if 'fantasyai.aurora.math.physics' not in deep_txt:
        # insert after simulate_counterfactuals_from_regression import line if present
        anchor = "simulate_counterfactuals_from_regression"
        idx = deep_txt.find(anchor)
        if idx != -1:
            # insert after that line
            lines = deep_txt.splitlines(True)
            out_lines = []
            inserted = False
            for line in lines:
                out_lines.append(line)
                if (not inserted) and (anchor in line):
                    out_lines.append('physics_diagnostics = _try_import("fantasyai.aurora.math.physics", "physics_diagnostics")\n')
                    inserted = True
            deep_txt = "".join(out_lines)
        else:
            # fallback: append near top after other imports
            deep_txt = deep_txt.replace(
                "simulate_counterfactuals_from_regression = _try_import",
                'physics_diagnostics = _try_import("fantasyai.aurora.math.physics", "physics_diagnostics")\n'
                "simulate_counterfactuals_from_regression = _try_import",
            )
        print("[OK] injected optional import: physics_diagnostics")
    else:
        print("[OK] physics_diagnostics already referenced in deep_math.py")

    # ensure out["physics"] always exists + compute after causal (safe)
    if '"physics"' not in deep_txt:
        # ensure default key near forecasting/causal defaults
        deep_txt = deep_txt.replace(
            'out["causal_what_if"] = {"note": "skipped", "error": None}\n',
            'out["causal_what_if"] = {"note": "skipped", "error": None}\n'
            '    out["physics"] = {"note": "skipped", "error": None}\n'
        )
        print("[OK] added always-present out['physics'] default")

        # insert compute block after causal_what_if assignment
        pattern = r'(out\["causal_what_if"\]\s*=\s*_safe_causal\(.*?\)\n)'
        m = re.search(pattern, deep_txt, flags=re.DOTALL)
        if m:
            insert = (
                m.group(1)
                + '\n    # 8) Physics-inspired diagnostics (safe, optional)\n'
                  '    try:\n'
                  '        if callable(physics_diagnostics):\n'
                  '            out["physics"] = physics_diagnostics(df=df, target_col=ycol, time_col=tcol, seed=seed)\n'
                  '            if isinstance(out["physics"], dict):\n'
                  '                out["physics"].setdefault("note", "ok")\n'
                  '        else:\n'
                  '            out["physics"] = {"note": "skipped", "error": "physics_diagnostics_unavailable"}\n'
                  '    except Exception as e:\n'
                  '        out["physics"] = {"note": "failed", "error": f"{type(e).__name__}: {e}"}\n'
            )
            deep_txt = deep_txt[:m.start()] + insert + deep_txt[m.end():]
            print("[OK] injected physics execution block after causal what-if")
        else:
            print("[WARN] could not find causal assignment block; physics not wired (manual patch needed)")
    else:
        print("[OK] out['physics'] already present in deep_math.py")

    DEEP.write_text(deep_txt, encoding="utf-8")
    print(f"[DONE] patched -> {DEEP}")

    # quick compile check
    import py_compile
    py_compile.compile(str(DEEP), doraise=True)
    py_compile.compile(str(PHYS), doraise=True)
    print("[DONE] deep_math.py + physics.py compiled clean")

if __name__ == "__main__":
    main()
