from pathlib import Path

ROOT = Path(r"C:\Users\bgrut\Desktop\Aurora_QIE")
target = ROOT / "fantasyai" / "aurora" / "math" / "physics.py"
backup = target.with_suffix(".py.bak_phase2_physics")

txt = r'''from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

import numpy as np
import pandas as pd


def _series_to_numeric(s: pd.Series) -> pd.Series:
    """
    Best-effort numeric coercion. Handles commas and stray whitespace.
    Returns float Series with NaN where not coercible.
    """
    if s is None:
        return pd.Series(dtype=float)

    # If already numeric-like, keep it
    if pd.api.types.is_numeric_dtype(s):
        return pd.to_numeric(s, errors="coerce")

    # Strings -> numeric (strip, remove commas)
    ss = s.astype(str).str.strip()
    ss = ss.str.replace(",", "", regex=False)
    return pd.to_numeric(ss, errors="coerce")


def _is_mostly_numeric(s: pd.Series, min_frac: float = 0.85) -> bool:
    n = len(s)
    if n == 0:
        return False
    c = _series_to_numeric(s)
    frac = float(np.isfinite(c.to_numpy()).mean())
    return frac >= float(min_frac)


def _looks_time_like_name(name: str) -> bool:
    n = (name or "").strip().lower()
    keys = ["date", "time", "datetime", "timestamp", "ts", "tstamp"]
    return any(k in n for k in keys)


def _infer_time_col(df: pd.DataFrame, *, target_col: Optional[str] = None) -> Tuple[Optional[str], str]:
    """
    Choose a reasonable time column.
    Priority:
      1) existing datetime dtype columns
      2) obvious time-like names (datetime/timestamp/date/time)
      3) Date+Time pair combined
      4) None (use fallback index time)
    Never returns target_col.
    """
    cols = list(df.columns)

    # 1) datetime dtype columns
    for c in cols:
        if target_col is not None and c == target_col:
            continue
        if pd.api.types.is_datetime64_any_dtype(df[c]):
            return c, "datetime_dtype"

    # 2) single columns with time-like names that parse well
    candidates = [c for c in cols if (target_col is None or c != target_col) and _looks_time_like_name(str(c))]
    for c in candidates:
        s = df[c]
        # Try parse
        dt = pd.to_datetime(s, errors="coerce", infer_datetime_format=True)
        if float(dt.notna().mean()) >= 0.85:
            return c, "parsed_time_like_name"

    # 3) Date + Time combo (very common in UCI AirQuality)
    date_col = None
    time_col = None
    for c in cols:
        cl = str(c).strip().lower()
        if target_col is not None and c == target_col:
            continue
        if cl == "date":
            date_col = c
        if cl == "time":
            time_col = c

    if date_col is not None and time_col is not None:
        # We''ll treat Date as the returned "time col" anchor; _make_time_axis will combine them.
        return f"{date_col} + {time_col}", "date_time_pair"

    # 4) no good time column found
    return None, "fallback_index"


def _make_time_axis(df: pd.DataFrame, *, time_col: Optional[str], target_col: Optional[str] = None) -> Dict[str, Any]:
    """
    Returns:
      {
        "time_axis": <string>,
        "dt_seconds": <float>,
        "t_seconds": np.ndarray (monotonic-ish),
        "time_col": <string or None>
      }
    """
    n = len(df)

    def _fallback():
        # Use simple 0..n-1 as "seconds" with dt=1.0
        t = np.arange(n, dtype=float)
        return {
            "time_axis": "fallback_index_seconds",
            "dt_seconds": 1.0,
            "t_seconds": t,
            "time_col": None,
        }

    if n == 0:
        return _fallback()

    # Date+Time combined
    if time_col and " + " in time_col:
        parts = [p.strip() for p in time_col.split("+")]
        if len(parts) == 2:
            dcol, tcol = parts[0], parts[1]
            if dcol in df.columns and tcol in df.columns:
                comb = df[dcol].astype(str).str.strip() + " " + df[tcol].astype(str).str.strip()
                dt = pd.to_datetime(comb, errors="coerce", dayfirst=True, infer_datetime_format=True)
                if float(dt.notna().mean()) >= 0.85:
                    tns = dt.view("int64") / 1e9  # seconds since epoch
                    # compute dt
                    diffs = np.diff(tns[np.isfinite(tns)])
                    diffs = diffs[np.isfinite(diffs)]
                    dt_sec = float(np.median(diffs)) if diffs.size else 1.0
                    return {
                        "time_axis": "datetime_from_date_time_pair",
                        "dt_seconds": dt_sec if dt_sec > 0 else 1.0,
                        "t_seconds": np.asarray(tns, dtype=float),
                        "time_col": f"{dcol}+{tcol}",
                    }

    # Single time column
    if time_col and time_col in df.columns and (target_col is None or time_col != target_col):
        s = df[time_col]

        # Datetime dtype
        if pd.api.types.is_datetime64_any_dtype(s):
            dt = s
        else:
            dt = pd.to_datetime(s, errors="coerce", infer_datetime_format=True)

        if float(dt.notna().mean()) >= 0.85:
            tns = dt.view("int64") / 1e9
            diffs = np.diff(tns[np.isfinite(tns)])
            diffs = diffs[np.isfinite(diffs)]
            dt_sec = float(np.median(diffs)) if diffs.size else 1.0
            return {
                "time_axis": "datetime_parsed",
                "dt_seconds": dt_sec if dt_sec > 0 else 1.0,
                "t_seconds": np.asarray(tns, dtype=float),
                "time_col": str(time_col),
            }

    return _fallback()


def _safe_r2(y: np.ndarray, yhat: np.ndarray) -> float:
    y = np.asarray(y, dtype=float)
    yhat = np.asarray(yhat, dtype=float)
    m = np.isfinite(y) & np.isfinite(yhat)
    if m.sum() < 3:
        return float("nan")
    y2 = y[m]
    yh2 = yhat[m]
    ss_res = float(np.sum((y2 - yh2) ** 2))
    ss_tot = float(np.sum((y2 - np.mean(y2)) ** 2))
    if ss_tot <= 1e-12:
        return float("nan")
    return 1.0 - ss_res / ss_tot


def physics_diagnostics(
    *,
    df: pd.DataFrame,
    target_col: str,
    time_col: Optional[str] = None,
    seed: int = 0
) -> Dict[str, Any]:
    """
    Phase-2 "real physics" diagnostic:
      Fit linear ODE: dy/dt = a*y + b
    Uses robust time-axis inference. Returns stable/time_constant when meaningful.
    """
    out: Dict[str, Any] = {"note": "ok", "error": None}

    if target_col not in df.columns:
        return {"note": "failed", "error": f"target_col_not_found:{target_col}"}

    # infer time col if not provided
    if time_col is None:
        inferred, method = _infer_time_col(df, target_col=target_col)
        time_col = inferred
        out["time_infer_method"] = method
    else:
        out["time_infer_method"] = "provided"

    ta = _make_time_axis(df, time_col=time_col, target_col=target_col)
    out["time_axis"] = ta["time_axis"]
    out["dt_seconds"] = float(ta["dt_seconds"])
    out["time_col"] = ta["time_col"]

    y = _series_to_numeric(df[target_col]).to_numpy(dtype=float)
    t = np.asarray(ta["t_seconds"], dtype=float)

    # Need enough clean points
    m = np.isfinite(y) & np.isfinite(t)
    if m.sum() < 20:
        return {**out, "note": "failed", "error": "insufficient_finite_points", "n": int(m.sum())}

    y = y[m]
    t = t[m]

    # Build dy/dt using central-ish diff (simple forward diff)
    dt = np.diff(t)
    dy = np.diff(y)

    # avoid zero/negative dt
    good = np.isfinite(dt) & (dt > 1e-9) & np.isfinite(dy)
    if good.sum() < 10:
        return {**out, "note": "failed", "error": "insufficient_good_dt", "n": int(good.sum())}

    dt = dt[good]
    dy = dy[good]
    y_mid = y[:-1][good]

    dydt = dy / dt

    # Fit dydt = a*y + b (least squares)
    A = np.column_stack([y_mid, np.ones_like(y_mid)])
    try:
        coef, *_ = np.linalg.lstsq(A, dydt, rcond=None)
        a = float(coef[0])
        b = float(coef[1])
    except Exception as e:
        return {**out, "note": "failed", "error": f"lstsq_failed:{type(e).__name__}:{e}"}

    # Predictions for dydt, plus quality metrics
    dydt_hat = a * y_mid + b
    rmse = float(np.sqrt(np.nanmean((dydt - dydt_hat) ** 2)))
    r2 = _safe_r2(dydt, dydt_hat)

    # Stability + time constant (for linear ODE)
    stable = bool(a < 0)
    tau = float("inf")
    if abs(a) > 1e-12:
        tau = float(abs(1.0 / a))

    out.update(
        {
            "model": "dy_dt = a*y + b",
            "a": a,
            "b": b,
            "stable": stable,
            "time_constant_seconds": tau,
            "r2": r2,
            "rmse": rmse,
            "n": int(len(y)),
        }
    )
    return out


# Provide the helpers used by physics_discovery.py if it imports them
def _make_time_axis(df: pd.DataFrame, time_col: Optional[str] = None, target_col: Optional[str] = None) -> Dict[str, Any]:
    return _make_time_axis.__wrapped__(df, time_col=time_col, target_col=target_col)  # type: ignore


# --- trick to preserve name while still using the implementation above ---
_make_time_axis.__wrapped__ = _make_time_axis  # type: ignore'''

backup.write_text(target.read_text(encoding="utf-8", errors="replace"), encoding="utf-8")
target.write_text(txt, encoding="utf-8")

print(f"[OK] backup -> {backup}")
print(f"[OK] wrote  -> {target}")
