from __future__ import annotations

import re, json, shutil
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).resolve().parents[1]
MATH = ROOT / "fantasyai" / "aurora" / "math"

PHYSICS = MATH / "physics.py"
PHYS_DISC = MATH / "physics_discovery.py"
CAUSAL = MATH / "causal.py"
DEEP = MATH / "deep_math.py"

def ts():
    return datetime.now().strftime("%Y%m%d_%H%M%S")

def backup(p: Path, tag: str):
    b = p.with_suffix(p.suffix + f".bak_{tag}_{ts()}")
    shutil.copy2(p, b)
    return b

def write_text(p: Path, s: str):
    p.write_text(s, encoding="utf-8", newline="\n")

def patch_physics_py():
    if not PHYSICS.exists():
        raise SystemExit(f"ERROR: missing {PHYSICS}")

    backup(PHYSICS, "rebuild")

    # Clean, robust physics layer used by physics_v2 + invariants
    content = r'''from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple, List

import numpy as np
import pandas as pd


def _is_mostly_numeric(s: pd.Series, thresh: float = 0.85) -> bool:
    """Return True if a column is mostly numeric-like."""
    try:
        x = pd.to_numeric(s.astype(str).str.replace(",", "", regex=False), errors="coerce")
        ok = np.isfinite(x.to_numpy(dtype=float, copy=False))
        return float(ok.mean()) >= thresh
    except Exception:
        return False


def _infer_time_col(df: pd.DataFrame) -> Optional[str]:
    """
    Prefer a real datetime-like column.
    - Reject columns that are mostly numeric (IDs / measures).
    - Choose the column with the highest datetime parse success rate.
    """
    best = None
    best_rate = 0.0

    for c in df.columns:
        s = df[c]
        if s is None:
            continue
        # numeric columns are not time columns
        if _is_mostly_numeric(s, thresh=0.90):
            continue

        dt = pd.to_datetime(s, errors="coerce", infer_datetime_format=True)
        rate = float(dt.notna().mean())
        if rate > best_rate and rate >= 0.70:
            best = c
            best_rate = rate

    return best


def _infer_target_col(df: pd.DataFrame) -> Optional[str]:
    """
    Pick a stable numeric target column:
    - numeric coercible
    - low missingness
    - non-trivial variance
    """
    best = None
    best_score = -1.0

    for c in df.columns:
        x = pd.to_numeric(df[c], errors="coerce")
        ok = x.notna()
        n = int(ok.sum())
        if n < 50:
            continue
        miss = float(1.0 - ok.mean())
        if miss > 0.60:
            continue
        v = float(np.nanvar(x.to_numpy(dtype=float, copy=False)))
        if not np.isfinite(v) or v <= 1e-12:
            continue

        # prefer high variance, low missingness
        score = v * (1.0 - miss)
        if score > best_score:
            best_score = score
            best = c

    return best


def _build_time_axis(df: pd.DataFrame, time_col: Optional[str]) -> Tuple[np.ndarray, str, float]:
    """
    Return (t_seconds, axis_name, dt_seconds_est).
    If no valid datetime column, fall back to an index-based axis with dt=1.
    """
    if time_col and time_col in df.columns:
        dt = pd.to_datetime(df[time_col], errors="coerce", infer_datetime_format=True)
        ok = dt.notna().to_numpy()
        if ok.mean() >= 0.70:
            dtn = dt.to_numpy()
            # convert to seconds from start
            t0 = pd.Timestamp(dt[dt.notna()].iloc[0]).to_datetime64()
            tsec = (dtn.astype("datetime64[ns]") - t0).astype("timedelta64[s]").astype(float)
            # estimate median step
            diffs = np.diff(np.sort(tsec[~np.isnan(tsec)]))
            dt_est = float(np.nanmedian(diffs)) if diffs.size else 1.0
            dt_est = 1.0 if not np.isfinite(dt_est) or dt_est <= 0 else dt_est
            return tsec, f"datetime:{time_col}", dt_est

    n = len(df)
    return np.arange(float(n), dtype=float), "index_fallback", 1.0


def physics_diagnostics(
    df: pd.DataFrame,
    time_col: Optional[str] = None,
    target_col: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Simple physically-interpretable first-order dynamics fit:
        dy/dt = a*y + b
    Robust defaults:
    - infers time_col and target_col if not provided
    - uses index fallback if datetime is unusable
    """
    out: Dict[str, Any] = {"note": "ok", "error": None}

    try:
        if time_col is None:
            time_col = _infer_time_col(df)
        if target_col is None:
            target_col = _infer_target_col(df)

        if target_col is None:
            out.update({"note": "skipped", "error": "no_numeric_target"})
            return out

        y = pd.to_numeric(df[target_col], errors="coerce").to_numpy(dtype=float, copy=False)
        tsec, axis_name, dt_est = _build_time_axis(df, time_col)

        # align finite points
        ok = np.isfinite(y) & np.isfinite(tsec)
        y = y[ok]
        tsec = tsec[ok]
        n = int(y.size)
        if n < 100:
            out.update({"note": "skipped", "error": "insufficient_points", "n": n})
            return out

        # sort by time
        idx = np.argsort(tsec)
        y = y[idx]
        tsec = tsec[idx]

        # finite differences
        dt = np.diff(tsec)
        dy = np.diff(y)
        good = np.isfinite(dt) & (dt > 0) & np.isfinite(dy)
        if int(good.sum()) < 50:
            out.update({"note": "skipped", "error": "bad_time_steps"})
            return out

        dt = dt[good]
        dy = dy[good]
        y_mid = y[:-1][good]

        dydt = dy / dt

        # least squares: dydt = a*y + b
        A = np.column_stack([y_mid, np.ones_like(y_mid)])
        coef, *_ = np.linalg.lstsq(A, dydt, rcond=None)
        a = float(coef[0])
        b = float(coef[1])

        pred = A @ coef
        resid = dydt - pred
        rmse = float(np.sqrt(np.nanmean(resid**2)))
        # R2
        ssr = float(np.nansum(resid**2))
        sst = float(np.nansum((dydt - np.nanmean(dydt))**2))
        r2 = float(1.0 - ssr / sst) if sst > 0 else 0.0

        stable = (a < 0.0) if np.isfinite(a) else False
        tau = (1.0 / abs(a)) if (np.isfinite(a) and abs(a) > 0) else None

        out.update(
            {
                "model": "dy_dt = a*y + b",
                "a": a,
                "b": b,
                "stable": bool(stable),
                "time_constant_seconds": float(tau) if tau is not None else None,
                "r2": r2,
                "rmse": rmse,
                "n": n,
                "time_axis": axis_name,
                "dt_seconds": float(dt_est),
                "time_col": time_col,
                "target_col": target_col,
            }
        )
        return out

    except Exception as e:
        out.update({"note": "failed", "error": f"{type(e).__name__}: {e}"})
        return out


def physics_invariants(
    df: pd.DataFrame,
    target_col: Optional[str] = None,
    time_col: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Phase 4 invariants / constraints layer.
    Produces:
      - boundedness
      - positivity rate
      - monotonic segments (rough)
      - a simple physics_consistency_score in [0,1]
    """
    out: Dict[str, Any] = {"note": "ok", "error": None}

    try:
        if target_col is None:
            target_col = _infer_target_col(df)
        if target_col is None:
            out.update({"note": "skipped", "error": "no_numeric_target"})
            return out

        y = pd.to_numeric(df[target_col], errors="coerce").to_numpy(dtype=float, copy=False)
        ok = np.isfinite(y)
        y = y[ok]
        n = int(y.size)
        if n < 100:
            out.update({"note": "skipped", "error": "insufficient_points", "n": n})
            return out

        ymin = float(np.nanmin(y))
        ymax = float(np.nanmax(y))
        pos_rate = float((y > 0).mean())

        # crude monotonicity: fraction of positive vs negative diffs
        dy = np.diff(y)
        inc = float((dy > 0).mean()) if dy.size else 0.0
        dec = float((dy < 0).mean()) if dy.size else 0.0

        bounded = bool(np.isfinite(ymin) and np.isfinite(ymax))

        # score: boundedness + "reasonable" positivity + not wildly oscillatory
        score = 0.0
        score += 0.40 if bounded else 0.0
        score += 0.30 * min(1.0, max(0.0, pos_rate))  # 0..0.30
        osc_penalty = abs(inc - dec)  # if near 0, oscillatory; if near 1, directional
        score += 0.30 * min(1.0, max(0.0, osc_penalty))

        out.update(
            {
                "target_col": target_col,
                "n": n,
                "y_min": ymin,
                "y_max": ymax,
                "positivity_rate": pos_rate,
                "inc_rate": inc,
                "dec_rate": dec,
                "bounded": bounded,
                "physics_consistency_score": float(max(0.0, min(1.0, score))),
            }
        )
        return out

    except Exception as e:
        out.update({"note": "failed", "error": f"{type(e).__name__}: {e}"})
        return out
'''
    write_text(PHYSICS, content)

def patch_physics_discovery_alias():
    if not PHYS_DISC.exists():
        raise SystemExit(f"ERROR: missing {PHYS_DISC}")

    s = PHYS_DISC.read_text(encoding="utf-8", errors="replace")
    if re.search(r"def\s+physics_discovery\s*\(", s):
        return  # already present

    backup(PHYS_DISC, "alias")

    # Add alias wrapper at end of file
    s2 = s.rstrip() + "\n\n" + r'''
# --- Compatibility alias (engine expects physics_discovery.physics_discovery) ---
def physics_discovery(*args, **kwargs):
    """
    Backwards/engine compatibility entrypoint.
    Preferred entrypoint in this module may be discover_physics_models(...).
    """
    if "discover_physics_models" in globals():
        return discover_physics_models(*args, **kwargs)
    if "discover" in globals():
        return discover(*args, **kwargs)
    raise AttributeError("No discover_physics_models/discover function found in physics_discovery.py")
'''
    write_text(PHYS_DISC, s2)

def patch_causal_alias():
    if not CAUSAL.exists():
        raise SystemExit(f"ERROR: missing {CAUSAL}")

    s = CAUSAL.read_text(encoding="utf-8", errors="replace")
    if re.search(r"def\s+causal_what_if_linear_robust_v2\s*\(", s):
        return

    backup(CAUSAL, "alias")

    # Insert alias after causal_what_if_linear definition (or append if not found)
    if "def causal_what_if_linear" in s:
        # append at end to avoid fragile indentation issues
        s2 = s.rstrip() + "\n\n" + r'''
# --- Compatibility alias (engine expects causal_what_if_linear_robust_v2) ---
def causal_what_if_linear_robust_v2(*args, **kwargs):
    """
    Alias to causal_what_if_linear for compatibility with deep_math expectations.
    """
    return causal_what_if_linear(*args, **kwargs)
'''
        write_text(CAUSAL, s2)
    else:
        # just append
        s2 = s.rstrip() + "\n\n" + r'''
def causal_what_if_linear_robust_v2(*args, **kwargs):
    raise AttributeError("causal_what_if_linear not found; cannot alias")
'''
        write_text(CAUSAL, s2)

def patch_deep_math_add_physics_blocks():
    if not DEEP.exists():
        raise SystemExit(f"ERROR: missing {DEEP}")

    s = DEEP.read_text(encoding="utf-8", errors="replace")

    # We will:
    # 1) ensure imports are present
    # 2) add physics calls near the end of compute_deep_math_v3, before "return out"
    backup(DEEP, "phase34")

    # Ensure imports for physics functions
    if "from fantasyai.aurora.math.physics import physics_diagnostics" not in s:
        s = s.replace(
            "from fantasyai.aurora.math.physics_discovery import physics_discovery",
            "from fantasyai.aurora.math.physics_discovery import physics_discovery\n"
            "from fantasyai.aurora.math.physics import physics_diagnostics, physics_invariants",
        )

    # Find "return out" inside compute_deep_math_v3 and inject block before it.
    marker = "\n    return out\n"
    if marker not in s:
        raise SystemExit("ERROR: could not find 'return out' anchor in deep_math.py (compute_deep_math_v3).")

    inject = r'''
    # -----------------------------
    # Phase 3: Physics discovery (multi-model selection)
    # Phase 4: Invariants + consistency score
    # -----------------------------
    try:
        # Use engine-selected target/time if present; otherwise infer safely inside physics.py
        # Prefer out["table_selection"] fields if they exist.
        _time_col = None
        _target_col = None

        try:
            _target_col = (out.get("table_selection") or {}).get("target_col")
        except Exception:
            _target_col = None

        # If deep_math already chose a time column elsewhere, respect it:
        for _k in ("time_col", "timestamp_col", "datetime_col"):
            if _k in out and out.get(_k):
                _time_col = out.get(_k)
                break

        # Discovery returns best + runner-ups if your physics_discovery.py supports it
        pd_out = physics_discovery(df, time_col=_time_col, target_col=_target_col)
        out["physics_discovery"] = pd_out
    except Exception as e:
        out["physics_discovery"] = {"note": "failed", "error": f"{type(e).__name__}: {e}"}

    try:
        inv = physics_invariants(df, target_col=_target_col, time_col=_time_col)
        out["physics_invariants"] = inv
        out["physics_consistency_score"] = inv.get("physics_consistency_score")
    except Exception as e:
        out["physics_invariants"] = {"note": "failed", "error": f"{type(e).__name__}: {e}"}
        out["physics_consistency_score"] = None

    # Always keep a lightweight physics_diagnostics (simple ODE fit) too
    try:
        out["physics"] = physics_diagnostics(df, time_col=_time_col, target_col=_target_col)
    except Exception as e:
        out["physics"] = {"note": "failed", "error": f"{type(e).__name__}: {e}"}
'''
    s = s.replace(marker, inject + marker)

    write_text(DEEP, s)

def main():
    print(f"[PATCH] repo root: {ROOT}")
    patch_physics_py()
    print(f"[OK] rebuilt physics.py -> {PHYSICS}")
    patch_physics_discovery_alias()
    print(f"[OK] physics_discovery alias ensured -> {PHYS_DISC}")
    patch_causal_alias()
    print(f"[OK] causal robust_v2 alias ensured -> {CAUSAL}")
    patch_deep_math_add_physics_blocks()
    print(f"[OK] deep_math phase 3-4 block injected -> {DEEP}")

    # Compile sanity check
    import py_compile
    for p in (PHYSICS, PHYS_DISC, CAUSAL, DEEP):
        py_compile.compile(str(p), doraise=True)
    print("[DONE] all patched files compiled clean")

if __name__ == "__main__":
    main()
