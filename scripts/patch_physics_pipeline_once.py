from __future__ import annotations

from pathlib import Path
import re
from datetime import datetime

ROOT = Path(__file__).resolve().parents[1]  # ...\Aurora_QIE\scripts -> ...\Aurora_QIE
MATH = ROOT / "fantasyai" / "aurora" / "math"
PHYSICS = MATH / "physics.py"
DISCOVERY = MATH / "physics_discovery.py"

def backup(p: Path) -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    b = p.with_suffix(p.suffix + f".bak_physics_shim_{ts}")
    b.write_text(p.read_text(encoding="utf-8", errors="replace"), encoding="utf-8")
    return b

def patch_physics_py():
    if not PHYSICS.exists():
        raise FileNotFoundError(f"missing: {PHYSICS}")

    txt = PHYSICS.read_text(encoding="utf-8", errors="replace")

    changed = False

    # 1) Add _build_time_axis_seconds shim if missing
    if "def _build_time_axis_seconds" not in txt:
        shim = r'''
# ----------------------------------------------------------------------
# Back-compat shims (older modules expect these names)
# ----------------------------------------------------------------------
def _build_time_axis_seconds(df, time_col=None):
    """
    Older code expected _build_time_axis_seconds().
    Our canonical helper is build_time_axis(df, time_col).
    """
    try:
        tc = time_col or _infer_time_col(df)
    except Exception:
        tc = time_col
    return build_time_axis(df, tc)
'''
        txt = txt.rstrip() + "\n" + shim + "\n"
        changed = True

    # 2) Add physics_diagnostics shim if missing
    if "def physics_diagnostics" not in txt:
        shim2 = r'''
def physics_diagnostics(
    df,
    target_col=None,
    time_col=None,
    seed: int = 0,
):
    """
    Lightweight physics diagnostics (v1 shim).

    Purpose:
      - Provide a stable callable for deep_math + physics_v2 import.
      - Keep it robust + domain-agnostic.
      - Return a dict with 'note'/'error' keys like the rest of Aurora.

    This does NOT try to be a full symbolic physics engine — it just
    extracts a clean time axis + series and reports basic kinematics
    and constraints.
    """
    try:
        import numpy as np
        import pandas as pd

        # Infer cols if not provided
        if target_col is None:
            try:
                target_col = select_numeric_target(df)
            except Exception:
                # fall back to first numeric-ish column
                num = [c for c in df.columns if _is_mostly_numeric(df[c].values)]
                target_col = num[0] if num else df.columns[0]

        if time_col is None:
            try:
                time_col = _infer_time_col(df)
            except Exception:
                time_col = None

        # Build time axis in seconds
        t = build_time_axis(df, time_col)
        y = pd.to_numeric(df[target_col], errors="coerce").astype(float).values

        m = np.isfinite(t) & np.isfinite(y)
        t2 = t[m]
        y2 = y[m]

        if y2.size < 25:
            return {
                "note": "skipped",
                "error": "insufficient_numeric_points",
                "target_col": target_col,
                "time_col": time_col,
                "n_finite": int(y2.size),
            }

        # Ensure monotonic time
        order = np.argsort(t2)
        t2 = t2[order]
        y2 = y2[order]

        dt = np.diff(t2)
        dt_med = float(np.median(dt)) if dt.size else 1.0
        dt_med = dt_med if dt_med > 0 else 1.0

        # Simple finite-diff velocity/acceleration
        v = np.diff(y2) / np.maximum(np.diff(t2), 1e-9)
        a = np.diff(v) / np.maximum(np.diff(t2[:-1]), 1e-9) if v.size > 1 else np.array([])

        stats = {
            "y_mean": float(np.mean(y2)),
            "y_std": float(np.std(y2)),
            "y_min": float(np.min(y2)),
            "y_max": float(np.max(y2)),
            "dt_seconds_median": float(dt_med),
            "velocity_mean": float(np.mean(v)) if v.size else None,
            "velocity_std": float(np.std(v)) if v.size else None,
            "accel_mean": float(np.mean(a)) if a.size else None,
            "accel_std": float(np.std(a)) if a.size else None,
        }

        # Reuse constraints engine (Phase 4)
        try:
            cons = physics_constraints(df=df, target_col=target_col, time_col=time_col)
        except Exception as e:
            cons = {"note": "failed", "error": f"{type(e).__name__}: {e}"}

        return {
            "note": "ok",
            "error": None,
            "target_col": target_col,
            "time_col": time_col,
            "n_finite": int(y2.size),
            "stats": stats,
            "constraints": cons,
        }
    except Exception as e:
        return {"note": "failed", "error": f"{type(e).__name__}: {e}"}
'''
        txt = txt.rstrip() + "\n" + shim2 + "\n"
        changed = True

    if changed:
        b = backup(PHYSICS)
        PHYSICS.write_text(txt, encoding="utf-8")
        print(f"[OK] patched physics.py  (backup: {b})")
    else:
        print("[OK] physics.py already has shims; no changes")

def patch_physics_discovery():
    if not DISCOVERY.exists():
        raise FileNotFoundError(f"missing: {DISCOVERY}")

    txt = DISCOVERY.read_text(encoding="utf-8", errors="replace")
    if "constraints = physics_constraints(y)" not in txt:
        print("[OK] physics_discovery.py already patched (no signature bug found)")
        return

    b = backup(DISCOVERY)
    txt2 = txt.replace(
        "constraints = physics_constraints(y)",
        "constraints = physics_constraints(df=df, target_col=target_col, time_col=time_col)",
    )
    DISCOVERY.write_text(txt2, encoding="utf-8")
    print(f"[OK] patched physics_discovery.py (backup: {b})")

def main():
    patch_physics_py()
    patch_physics_discovery()

if __name__ == "__main__":
    main()
