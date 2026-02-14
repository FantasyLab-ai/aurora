from __future__ import annotations
import re, time
from pathlib import Path

ROOT = Path.cwd()

PHYS      = ROOT / "fantasyai" / "aurora" / "math" / "physics.py"
DISC      = ROOT / "fantasyai" / "aurora" / "math" / "physics_discovery.py"
DEEP      = ROOT / "fantasyai" / "aurora" / "math" / "deep_math.py"

def backup(p: Path) -> Path:
    ts = time.strftime("%Y%m%d_%H%M%S")
    b = p.with_suffix(p.suffix + f".bak_physfix_{ts}")
    b.write_text(p.read_text(encoding="utf-8", errors="replace"), encoding="utf-8")
    return b

def write_physics_py():
    if not PHYS.exists():
        raise SystemExit(f"ERROR: missing {PHYS}")

    backup(PHYS)

    code = r'''from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple, Dict, Any, List
import math
import numpy as np
import pandas as pd


def _is_mostly_numeric(s: pd.Series, thresh: float = 0.85) -> bool:
    try:
        x = pd.to_numeric(s, errors="coerce")
        ok = float(x.notna().mean())
        return ok >= thresh
    except Exception:
        return False


def _try_parse_datetime(col: pd.Series) -> Tuple[float, pd.Series]:
    """Return (parse_rate, parsed_series)."""
    try:
        dt = pd.to_datetime(col, errors="coerce")
        rate = float(dt.notna().mean())
        return rate, dt
    except Exception:
        return 0.0, pd.to_datetime(pd.Series([pd.NaT] * len(col)), errors="coerce")


def _infer_time_col(df: pd.DataFrame) -> Optional[str]:
    """
    Choose the best time-like column. We pick the column with:
      - high datetime parse rate
      - and reasonably high uniqueness (to avoid constantSelector-like issues)
    """
    best = None
    best_score = 0.0

    for c in df.columns:
        s = df[c]
        # Ignore pure numeric columns as time unless they look like unix timestamps (we keep it simple here).
        rate, dt = _try_parse_datetime(s)
        if rate < 0.85:
            continue

        uniq = float(dt.dropna().nunique()) / max(1.0, float(len(dt.dropna())))
        score = rate * (0.5 + 0.5 * min(1.0, uniq * 10.0))  # boost for some uniqueness

        if score > best_score:
            best_score = score
            best = c

    return best


def select_numeric_target(df: pd.DataFrame, exclude: Optional[List[str]] = None) -> Optional[str]:
    exclude = set(exclude or [])
    candidates = []
    for c in df.columns:
        if c in exclude:
            continue
        if pd.api.types.is_numeric_dtype(df[c]):
            x = pd.to_numeric(df[c], errors="coerce")
            if x.notna().mean() < 0.8:
                continue
            var = float(np.nanvar(x.to_numpy(dtype=float)))
            candidates.append((var, c))

    if not candidates:
        return None
    candidates.sort(reverse=True)
    return candidates[0][1]


def build_time_axis(df: pd.DataFrame, time_col: Optional[str]) -> Tuple[np.ndarray, Dict[str, Any]]:
    """
    Returns:
      t: seconds from start (float array)
      meta: {"time_axis": str, "dt_seconds": float, "time_col": Optional[str]}
    """
    n = len(df)
    if n <= 1:
        t = np.arange(n, dtype=float)
        return t, {"time_axis": "fallback_index", "dt_seconds": 1.0, "time_col": time_col}

    if time_col is None or time_col not in df.columns:
        t = np.arange(n, dtype=float)
        return t, {"time_axis": "fallback_index", "dt_seconds": 1.0, "time_col": None}

    rate, dt = _try_parse_datetime(df[time_col])
    if rate < 0.85:
        t = np.arange(n, dtype=float)
        return t, {"time_axis": "datetime_unusable_fallback_index", "dt_seconds": 1.0, "time_col": time_col}

    dt = dt.sort_values().reset_index(drop=True)
    # If the user time column is not sorted vs df, we still build a monotone axis.
    secs = (dt - dt.iloc[0]).dt.total_seconds().to_numpy(dtype=float)
    # estimate dt_seconds as median positive diff
    diffs = np.diff(secs)
    diffs = diffs[np.isfinite(diffs) & (diffs > 0)]
    dt_seconds = float(np.median(diffs)) if len(diffs) else 1.0

    # For alignment with y, we want axis length n; use fallback index if dt weird.
    if not np.isfinite(dt_seconds) or dt_seconds <= 0:
        dt_seconds = 1.0

    t = np.arange(n, dtype=float) * dt_seconds
    return t, {"time_axis": "datetime_indexed", "dt_seconds": dt_seconds, "time_col": time_col}


def physics_constraints(df: pd.DataFrame, target_col: Optional[str], time_col: Optional[str]) -> Dict[str, Any]:
    """
    Phase 4: invariants / constraints checks (domain-agnostic)
    Returns a dict with a consistency_score in [0,1] plus violations.
    """
    out: Dict[str, Any] = {"note": "ok", "error": None}
    try:
        if target_col is None or target_col not in df.columns:
            out["note"] = "skipped"
            out["error"] = "no_target_col"
            out["consistency_score"] = None
            return out

        y = pd.to_numeric(df[target_col], errors="coerce").to_numpy(dtype=float)
        y = y[np.isfinite(y)]
        if len(y) < 50:
            out["note"] = "skipped"
            out["error"] = "insufficient_numeric_target"
            out["consistency_score"] = None
            return out

        violations = []
        score = 1.0

        # boundedness (soft): huge outliers penalize
        p01, p99 = np.nanpercentile(y, [1, 99])
        if not np.isfinite(p01) or not np.isfinite(p99) or p99 <= p01:
            violations.append("boundedness_unknown")
            score *= 0.85
        else:
            span = p99 - p01
            extreme = np.nanmax(np.abs(y - np.nanmedian(y)))
            if span > 0 and extreme > 10.0 * span:
                violations.append("extreme_outliers")
                score *= 0.70

        # positivity check (soft) – many scientific signals must be >=0; we don't assume, we just report.
        neg_rate = float((y < 0).mean())
        out["negative_rate"] = neg_rate
        if neg_rate > 0.30:
            violations.append("mostly_negative_values")
            score *= 0.90

        # monotonicity check (optional): report if nearly monotone
        dy = np.diff(y)
        if len(dy) > 0:
            frac_pos = float((dy > 0).mean())
            frac_neg = float((dy < 0).mean())
            out["monotone_increasing_fraction"] = frac_pos
            out["monotone_decreasing_fraction"] = frac_neg
            if max(frac_pos, frac_neg) > 0.95:
                out["monotonicity"] = "near_monotone"
            else:
                out["monotonicity"] = "non_monotone"

        out["violations"] = violations
        out["consistency_score"] = float(max(0.0, min(1.0, score)))
        out["target_col"] = target_col
        out["time_col"] = time_col
        return out

    except Exception as e:
        out["note"] = "failed"
        out["error"] = f"{type(e).__name__}: {e}"
        out["consistency_score"] = None
        return out
'''
    PHYS.write_text(code, encoding="utf-8")
    print(f"[OK] rewrote physics.py -> {PHYS}")

def patch_physics_discovery_py():
    if not DISC.exists():
        raise SystemExit(f"ERROR: missing {DISC}")
    backup(DISC)

    s = DISC.read_text(encoding="utf-8", errors="replace")

    # If user has discover_physics_models but no physics_discovery, add wrapper.
    has_discover = re.search(r"def\s+discover_physics_models\s*\(", s) is not None
    has_export   = re.search(r"def\s+physics_discovery\s*\(", s) is not None

    if has_discover and not has_export:
        s += "\n\n# --- Backward-compatible export expected by deep_math.py ---\n"
        s += "def physics_discovery(df, target_col=None, time_col=None, max_models=6, seed=42):\n"
        s += "    \"\"\"Compatibility wrapper: deep_math.py expects physics_discovery().\"\"\"\n"
        s += "    return discover_physics_models(df, target_col=target_col, time_col=time_col, max_models=max_models, seed=seed)\n"
        DISC.write_text(s, encoding="utf-8")
        print("[OK] added physics_discovery() wrapper -> physics_discovery.py")
    else:
        print("[OK] physics_discovery.py already exports physics_discovery() (no change)")

def patch_deep_math_py():
    if not DEEP.exists():
        raise SystemExit(f"ERROR: missing {DEEP}")
    backup(DEEP)

    s = DEEP.read_text(encoding="utf-8", errors="replace")

    anchor = "# --- Physics model v2 ---"
    if anchor not in s:
        raise SystemExit("ERROR: could not find physics anchor '# --- Physics model v2 ---' in deep_math.py")

    insertion = r'''
    # --- Physics column selection (shared) ---
    # We need stable defaults for ycol/tcol so physics_v2 / invariants / discovery never crash.
    try:
        ycol = deep.get("target_col", None)
    except Exception:
        ycol = None
    try:
        tcol = deep.get("time_col", None)
    except Exception:
        tcol = None

    # Target: pick a numeric column with strong signal if none specified
    if ycol is None:
        try:
            from fantasyai.aurora.math.physics import select_numeric_target as _sel_num_target
            ycol = _sel_num_target(df, exclude=[])
        except Exception:
            ycol = None
        deep["target_col"] = ycol

    # Time: pick a datetime-like column if present, otherwise allow physics to use fallback index
    if tcol is None:
        try:
            from fantasyai.aurora.math.physics import _infer_time_col as _infer_t
            tcol = _infer_t(df)
        except Exception:
            tcol = None
        deep["time_col"] = tcol
'''

    # Insert once just before the physics v2 block
    parts = s.split(anchor)
    if len(parts) != 2:
        raise SystemExit("ERROR: physics anchor split ambiguous in deep_math.py")

    before, after = parts[0], parts[1]
    if "Physics column selection (shared)" not in before:
        s2 = before + insertion + "\n" + anchor + after
    else:
        s2 = s

    # Ensure physics_consistency_score is computed from invariants if present
    # If code already sets it, don't duplicate.
    if "physics_consistency_score" not in s2:
        # Add it near the end, right before deep_results return (best-effort).
        # We'll place it right before the final "return deep" / "return deep_results" if present.
        m = re.search(r"\n\s*return\s+deep_results\s*\n", s2)
        if m:
            idx = m.start()
            s2 = s2[:idx] + "\n    # --- Physics consistency score (Phase 4 output) ---\n" \
                 "    try:\n" \
                 "        inv = deep.get('physics_invariants', {}) if isinstance(deep, dict) else {}\n" \
                 "        score = None\n" \
                 "        if isinstance(inv, dict):\n" \
                 "            score = inv.get('consistency_score', None)\n" \
                 "        deep['physics_consistency_score'] = score\n" \
                 "    except Exception:\n" \
                 "        pass\n" + s2[idx:]
        else:
            # If return deep_results not found, do nothing (safe)
            pass

    DEEP.write_text(s2, encoding="utf-8")
    print(f"[OK] patched deep_math.py -> {DEEP}")

def main():
    write_physics_py()
    patch_physics_discovery_py()
    patch_deep_math_py()
    print("[DONE] physics layer patched (exports + ycol/tcol + invariants).")

if __name__ == "__main__":
    main()
