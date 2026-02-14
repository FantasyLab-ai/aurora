from __future__ import annotations

import re
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).resolve().parents[1]
PHYSICS = ROOT / "fantasyai" / "aurora" / "math" / "physics.py"
STAMP = datetime.now().strftime("%Y%m%d_%H%M%S")

def backup(path: Path) -> Path:
    b = path.with_suffix(path.suffix + f".bak_dtfix_{STAMP}")
    b.write_text(path.read_text(encoding="utf-8", errors="replace"), encoding="utf-8")
    return b

def replace_func_block(src: str, name: str, new_block: str) -> tuple[str, bool]:
    pat = re.compile(rf"^def\s+{re.escape(name)}\s*\(.*?\):\s*\n", re.M | re.S)
    m = pat.search(src)
    if not m:
        return (src.rstrip() + "\n\n" + new_block.strip() + "\n", False)
    start = m.start()
    tail = src[m.end():]
    m2 = re.search(r"^(def\s+|class\s+)", tail, flags=re.M)
    end = m.end() + (m2.start(0) if m2 else len(tail))
    out = src[:start] + new_block.strip() + "\n\n" + src[end:]
    return out, True

NEW_DIAGNOSTICS = r'''
def physics_diagnostics(df, target_col=None, time_col=None):
    """
    Lightweight physics diagnostics for a single target series.

    Guarantees:
      - never throws (returns {"note":"failed","error":...})
      - JSON-serializable output only
      - robust to different Aurora signatures for physics_constraints + time axis helpers
      - avoids derivative blowups by preferring raw numeric time_col when available
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

        # Target series
        y_raw = pd.to_numeric(df[target_col], errors="coerce").to_numpy(dtype=float)

        # Prefer direct numeric time_col if possible (prevents dt=1e-09 scaling issues)
        t_direct = pd.to_numeric(df[time_col], errors="coerce").to_numpy(dtype=float) if time_col in df.columns else None
        use_direct = False
        if t_direct is not None:
            m = np.isfinite(t_direct) & np.isfinite(y_raw)
            td = t_direct[m]
            if td.size >= 3:
                # sort for dt check
                td_sorted = np.sort(td)
                d = np.diff(td_sorted)
                d = d[np.isfinite(d) & (d > 0)]
                if d.size:
                    med = float(np.median(d))
                    # if median dt is reasonable (not nanos), accept direct time
                    if med >= 1e-6:
                        use_direct = True

        if use_direct:
            # normalize to start at 0
            t = t_direct.astype(float)
            t = t - np.nanmin(t)
            time_axis = list(range(len(t)))
            # compute dt robustly
            d = np.diff(np.sort(t[np.isfinite(t)]))
            d = d[np.isfinite(d) & (d > 0)]
            dt_seconds = float(np.median(d)) if d.size else 1.0
        else:
            # Fallback to canonical helper
            t_sec, time_axis, dt_seconds = _build_time_axis_seconds(df, time_col=time_col)
            t = np.asarray(t_sec, dtype=float)
            dt_seconds = float(dt_seconds) if dt_seconds is not None else None
            # if dt still suspicious, recompute from t
            if (dt_seconds is None) or (not np.isfinite(dt_seconds)) or (dt_seconds <= 0) or (dt_seconds < 1e-6):
                d = np.diff(np.sort(t[np.isfinite(t)]))
                d = d[np.isfinite(d) & (d > 0)]
                dt_seconds = float(np.median(d)) if d.size else 1.0

        # Now align masks using the chosen t
        mask = np.isfinite(t) & np.isfinite(y_raw)
        t = t[mask]
        y = y_raw[mask]

        n = int(y.size)
        if n < 3:
            return {"note": "failed", "error": "too_few_points", "n": n, "target_col": target_col, "time_col": time_col}

        order = np.argsort(t)
        t = t[order]
        y = y[order]

        # Derivatives: use t directly (supports irregular spacing)
        dy_dt = np.gradient(y, t)
        d2y_dt2 = np.gradient(dy_dt, t)

        dy_sign_changes = int(np.sum(np.diff(np.sign(dy_dt)) != 0))
        is_monotonic = bool(np.all(dy_dt >= 0) or np.all(dy_dt <= 0))

        # Growth heuristic: linear vs exponential (if y>0)
        A = np.vstack([t, np.ones_like(t)]).T

        def r2_fit(y_true, y_hat):
            ss_res = float(np.sum((y_true - y_hat) ** 2))
            ss_tot = float(np.sum((y_true - float(np.mean(y_true))) ** 2)) or 1.0
            return 1.0 - ss_res / ss_tot

        coef_lin, *_ = np.linalg.lstsq(A, y, rcond=None)
        y_hat = A @ coef_lin
        r2_lin = r2_fit(y, y_hat)
        growth = "linear" if r2_lin >= 0.98 else "nonlinear"

        if np.all(y > 0) and n >= 8:
            logy = np.log(y)
            coef_log, *_ = np.linalg.lstsq(A, logy, rcond=None)
            logy_hat = A @ coef_log
            r2_log = r2_fit(logy, logy_hat)
            if r2_log >= 0.98 and r2_log >= r2_lin + 0.02:
                growth = "exponential"

        # Constraints: try multiple signatures without breaking existing code
        constraints = None
        try:
            constraints = physics_constraints(df=df, target_col=target_col, time_col=time_col)
        except TypeError:
            try:
                constraints = physics_constraints(df=df, ycol=target_col, tcol=time_col)
            except TypeError:
                try:
                    constraints = physics_constraints(df=df, y_col=target_col, t_col=time_col)
                except TypeError:
                    try:
                        constraints = physics_constraints(df, target_col, time_col)
                    except Exception as e:
                        constraints = {"note": "failed", "error": f"{type(e).__name__}: {e}"}
        except Exception as e:
            constraints = {"note": "failed", "error": f"{type(e).__name__}: {e}"}

        # Normalize score field name if constraints uses a different key
        score = 0.0
        if isinstance(constraints, dict):
            if "physics_consistency_score" in constraints and constraints["physics_consistency_score"] is not None:
                score = float(constraints["physics_consistency_score"])
            elif "consistency_score" in constraints and constraints["consistency_score"] is not None:
                score = float(constraints["consistency_score"])

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
            "physics_consistency_score": score,
        }
    except Exception as e:
        return {"note": "failed", "error": f"{type(e).__name__}: {e}"}
'''.strip()

def main():
    if not PHYSICS.exists():
        raise FileNotFoundError(PHYSICS)

    src = PHYSICS.read_text(encoding="utf-8", errors="replace")
    b = backup(PHYSICS)

    src2, replaced = replace_func_block(src, "physics_diagnostics", NEW_DIAGNOSTICS)
    PHYSICS.write_text(src2, encoding="utf-8")

    import py_compile
    py_compile.compile(str(PHYSICS), doraise=True)

    print("[OK] physics_diagnostics dt fix patched + compiled clean")
    print(" - file:", PHYSICS)
    print(" - backup:", b)
    print(" - replaced_existing:", replaced)

if __name__ == "__main__":
    main()
