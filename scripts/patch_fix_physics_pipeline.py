from __future__ import annotations

import re
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).resolve().parents[1]  # Aurora_QIE/
PHYSICS = ROOT / "fantasyai" / "aurora" / "math" / "physics.py"
DISCOVERY = ROOT / "fantasyai" / "aurora" / "math" / "physics_discovery.py"

STAMP = datetime.now().strftime("%Y%m%d_%H%M%S")

def backup(path: Path) -> Path:
    b = path.with_suffix(path.suffix + f".bak_fix_{STAMP}")
    b.write_text(path.read_text(encoding="utf-8", errors="replace"), encoding="utf-8")
    return b

def replace_top_level_def(src: str, def_name: str, new_block: str) -> tuple[str, bool]:
    """
    Replace a top-level `def def_name(...):` block with `new_block`.
    If not found, append `new_block` at end and return (src+new_block, False).
    """
    pat = re.compile(rf"^(def\s+{re.escape(def_name)}\s*\(.*?\):\s*\n)", re.M | re.S)
    m = pat.search(src)
    if not m:
        return (src.rstrip() + "\n\n" + new_block.strip() + "\n", False)

    start = m.start(1)
    tail = src[m.end(1):]
    m2 = re.search(r"^(def\s+|class\s+)", tail, flags=re.M)
    end = m.end(1) + (m2.start(0) if m2 else len(tail))
    out = src[:start] + new_block.strip() + "\n\n" + src[end:]
    return (out, True)

def ensure_function(src: str, def_name: str, block: str) -> tuple[str, bool]:
    if re.search(rf"^def\s+{re.escape(def_name)}\s*\(", src, flags=re.M):
        return src, False
    return src.rstrip() + "\n\n" + block.strip() + "\n", True

BUILD_TIME_AXIS_SECONDS = r'''
def _build_time_axis_seconds(df, time_col):
    """
    Backwards-compat helper expected by physics_discovery + older engine code.
    Returns: (t_seconds: np.ndarray, time_axis: list, dt_seconds: float)
    """
    t_seconds, time_axis, dt_seconds = build_time_axis(df, time_col=time_col)
    return t_seconds, time_axis, float(dt_seconds)
'''.strip()

PHYSICS_DIAGNOSTICS = r'''
def physics_diagnostics(df, target_col=None, time_col=None):
    """
    Lightweight, dependency-minimal physics diagnostics for a single target series.

    Goals:
      - never throw (returns {"note":"failed", "error":...} on error)
      - stable JSON-serializable output (no ragged numpy arrays)
      - provides usable signals for downstream discovery/narratives
    """
    try:
        import numpy as np
        import pandas as pd

        if not isinstance(df, pd.DataFrame):
            df = pd.DataFrame(df)

        if target_col is None or target_col not in df.columns:
            target_col = select_numeric_target(df)

        if target_col is None or target_col not in df.columns:
            return {"note": "failed", "error": "no_numeric_target"}

        if time_col is None or time_col not in df.columns:
            time_col = _infer_time_col(df)

        t_sec, time_axis, dt_seconds = _build_time_axis_seconds(df, time_col=time_col)

        y = pd.to_numeric(df[target_col], errors="coerce").to_numpy(dtype=float)
        mask = np.isfinite(t_sec) & np.isfinite(y)
        t = t_sec[mask]
        y = y[mask]

        n = int(y.size)
        if n < 3:
            return {"note": "failed", "error": "too_few_points", "n": n, "target_col": target_col, "time_col": time_col}

        # Sort by time (important if input not ordered)
        order = np.argsort(t)
        t = t[order]
        y = y[order]

        # Derivatives (supports irregular spacing)
        dy_dt = np.gradient(y, t)
        d2y_dt2 = np.gradient(dy_dt, t)

        dy_sign_changes = int(np.sum(np.diff(np.sign(dy_dt)) != 0))
        is_monotonic = bool(np.all(dy_dt >= 0) or np.all(dy_dt <= 0))

        # Growth-type heuristic
        A = np.vstack([t, np.ones_like(t)]).T

        def r2_fit(y_true, y_hat):
            ss_res = float(np.sum((y_true - y_hat) ** 2))
            ss_tot = float(np.sum((y_true - float(np.mean(y_true))) ** 2)) or 1.0
            return 1.0 - ss_res / ss_tot

        # linear
        coef_lin, *_ = np.linalg.lstsq(A, y, rcond=None)
        y_hat = A @ coef_lin
        r2_lin = r2_fit(y, y_hat)

        growth = "linear" if r2_lin >= 0.98 else "nonlinear"

        # exponential check if y>0
        if np.all(y > 0) and n >= 4:
            logy = np.log(y)
            coef_log, *_ = np.linalg.lstsq(A, logy, rcond=None)
            logy_hat = A @ coef_log
            r2_log = r2_fit(logy, logy_hat)
            if r2_log >= 0.98 and r2_log >= r2_lin + 0.02:
                growth = "exponential"

        constraints = physics_constraints(df=df, target_col=target_col, time_col=time_col)

        return {
            "note": "ok",
            "target_col": target_col,
            "time_col": time_col,
            "dt_seconds": float(dt_seconds),
            "n": n,
            "y_min": float(np.nanmin(y)),
            "y_max": float(np.nanmax(y)),
            "y_mean": float(np.nanmean(y)),
            "dy_dt_mean": float(np.nanmean(dy_dt)),
            "dy_dt_min": float(np.nanmin(dy_dt)),
            "dy_dt_max": float(np.nanmax(dy_dt)),
            "d2y_dt2_mean": float(np.nanmean(d2y_dt2)),
            "dy_sign_changes": dy_sign_changes,
            "is_monotonic": is_monotonic,
            "growth_type": growth,
            "constraints": constraints,
            "physics_consistency_score": float(constraints.get("physics_consistency_score", 0.0)),
        }
    except Exception as e:
        return {"note": "failed", "error": f"{type(e).__name__}: {e}"}
'''.strip()

def patch_physics_py():
    if not PHYSICS.exists():
        raise FileNotFoundError(f"physics.py not found at {PHYSICS}")
    src = PHYSICS.read_text(encoding="utf-8", errors="replace")
    backup(PHYSICS)

    src, _ = ensure_function(src, "_build_time_axis_seconds", BUILD_TIME_AXIS_SECONDS)
    src, _ = replace_top_level_def(src, "physics_diagnostics", PHYSICS_DIAGNOSTICS)

    PHYSICS.write_text(src, encoding="utf-8")
    return str(PHYSICS)

def patch_discovery_py():
    if not DISCOVERY.exists():
        raise FileNotFoundError(f"physics_discovery.py not found at {DISCOVERY}")
    src = DISCOVERY.read_text(encoding="utf-8", errors="replace")
    backup(DISCOVERY)

    # Make discover_physics_models accept time_col
    src = re.sub(
        r"def\s+discover_physics_models\s*\(\s*df\s*:\s*pd\.DataFrame\s*,\s*target_col\s*:\s*Optional\[str\]\s*=\s*None\s*\)\s*->\s*Dict\[str,\s*Any\]\s*:",
        "def discover_physics_models(df: pd.DataFrame, target_col: Optional[str] = None, time_col: Optional[str] = None) -> Dict[str, Any]:",
        src,
        count=1,
    )

    # Prefer provided time_col if valid, else infer
    src = re.sub(
        r"time_col\s*=\s*_infer_time_col\(df\)",
        "time_col = time_col if (time_col is not None and time_col in df.columns) else _infer_time_col(df)",
        src,
        count=1,
    )

    DISCOVERY.write_text(src, encoding="utf-8")
    return str(DISCOVERY)

def main():
    p1 = patch_physics_py()
    p2 = patch_discovery_py()

    import py_compile
    py_compile.compile(p1, doraise=True)
    py_compile.compile(p2, doraise=True)
    print("[OK] patched + compiled clean")
    print(" -", p1)
    print(" -", p2)

if __name__ == "__main__":
    main()
