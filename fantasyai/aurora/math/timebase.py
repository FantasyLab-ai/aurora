from __future__ import annotations
from dataclasses import dataclass
from typing import Optional, Dict, Any, Tuple
import numpy as np
import pandas as pd

@dataclass
class TimebaseMeta:
    time_col_used: Optional[str]
    is_datetime: bool
    cadence_seconds: Optional[float]
    is_monotonic: bool
    parse_rate: float
    note: str

def _try_build_date_time(df: pd.DataFrame) -> Optional[pd.Series]:
    if ("Date" in df.columns) and ("Time" in df.columns):
        comb = (df["Date"].astype(str).str.strip() + " " + df["Time"].astype(str).str.strip())
        dt2 = pd.to_datetime(comb, errors="coerce", dayfirst=True)
        if dt2.notna().mean() >= 0.6:
            return dt2
    return None

def _best_datetime_col(df: pd.DataFrame) -> Optional[str]:
    # Prefer common names first
    for cand in ["datetime", "timestamp", "time", "date", "ds"]:
        if cand in df.columns:
            dtc = pd.to_datetime(df[cand], errors="coerce")
            if dtc.notna().mean() >= 0.8:
                return cand
    # Otherwise scan object columns
    for c in df.columns:
        if pd.api.types.is_numeric_dtype(df[c]):
            continue
        dtc = pd.to_datetime(df[c], errors="coerce")
        if dtc.notna().mean() >= 0.85:
            return c
    return None

def _infer_cadence_seconds(dtc: pd.Series) -> Optional[float]:
    try:
        ok = dtc.dropna().sort_values()
        if ok.size < 20:
            return None
        diffs = ok.diff().dropna().dt.total_seconds().values
        diffs = diffs[np.isfinite(diffs) & (diffs > 0)]
        if diffs.size < 10:
            return None
        # robust typical cadence
        return float(np.median(diffs))
    except Exception:
        return None

def build_canonical_time(df: pd.DataFrame) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    """
    Adds __aurora_time (datetime) if possible.
    Returns (df2, meta dict).
    """
    meta = TimebaseMeta(
        time_col_used=None,
        is_datetime=False,
        cadence_seconds=None,
        is_monotonic=False,
        parse_rate=0.0,
        note="no_time_detected",
    )

    df2 = df.copy()

    dtc = _try_build_date_time(df2)
    if dtc is not None:
        meta.time_col_used = "Date+Time"
    else:
        col = _best_datetime_col(df2)
        if col:
            dtc = pd.to_datetime(df2[col], errors="coerce")
            meta.time_col_used = col

    if dtc is None:
        return df2, meta.__dict__

    parse_rate = float(dtc.notna().mean())
    ok = dtc.dropna()
    is_mono = bool(ok.is_monotonic_increasing) if ok.size > 0 else False

    df2["__aurora_time"] = dtc
    df2 = df2.sort_values("__aurora_time", kind="mergesort")

    cadence = _infer_cadence_seconds(dtc)
    meta.is_datetime = True
    meta.parse_rate = parse_rate
    meta.is_monotonic = is_mono
    meta.cadence_seconds = cadence
    meta.note = "ok" if parse_rate >= 0.6 else "low_parse_rate"

    return df2, meta.__dict__
