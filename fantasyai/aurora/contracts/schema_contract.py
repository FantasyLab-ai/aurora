from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple
import re

import numpy as np
import pandas as pd


@dataclass
class SchemaContract:
    # High-level stats
    rows: int
    cols: int

    # Key detected roles
    time_col: Optional[str]
    lat_col: Optional[str]
    lon_col: Optional[str]

    # Columns
    numeric_cols: List[str]
    categorical_cols: List[str]
    id_like_cols: List[str]

    # Diagnostics
    parse_warnings: List[str]
    missingness_top: List[Tuple[str, float]]

    # Canonical mapping
    original_to_canonical: Dict[str, str]


def _canonicalize_name(name: str) -> str:
    s = str(name).strip().lower()
    s = re.sub(r"[^\w]+", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    return s[:120] if s else "col"


def _dedupe_columns(cols: List[str]) -> List[str]:
    seen = {}
    out = []
    for c in cols:
        if c not in seen:
            seen[c] = 0
            out.append(c)
        else:
            seen[c] += 1
            out.append(f"{c}__{seen[c]}")
    return out


def _infer_lat_lon(cols: List[str]) -> Tuple[Optional[str], Optional[str]]:
    lat = None
    lon = None
    for c in cols:
        lc = c.lower()
        if lat is None and lc in ("lat", "latitude", "y", "y_lat", "geo_latitude"):
            lat = c
        if lon is None and lc in ("lon", "lng", "longitude", "x", "x_lon", "geo_longitude"):
            lon = c
    # fallback: contains patterns
    if lat is None:
        for c in cols:
            if "lat" in c.lower() and "plate" not in c.lower():
                lat = c; break
    if lon is None:
        for c in cols:
            if any(k in c.lower() for k in ("lon", "lng", "long")):
                lon = c; break
    return lat, lon


def _infer_time_col(df: pd.DataFrame) -> Optional[str]:
    # Prefer columns with time-ish names and parseable values
    candidates = []
    for c in df.columns:
        name = str(c).lower()
        if any(k in name for k in ("time", "date", "timestamp", "datetime", "dt")):
            candidates.append(c)

    def score(col: str) -> float:
        s = df[col]
        # sample for speed
        ss = s.dropna()
        if len(ss) > 5000:
            ss = ss.sample(5000, random_state=0)
        try:
            dt = pd.to_datetime(ss, errors="coerce", utc=False)
            ok = float(dt.notna().mean())
        except Exception:
            ok = 0.0
        # encourage monotonic-ish
        mono = 0.0
        try:
            if ok > 0.6:
                dt2 = pd.to_datetime(s, errors="coerce")
                dt2 = dt2.dropna()
                if len(dt2) > 10:
                    mono = 0.25 if dt2.is_monotonic_increasing else 0.0
        except Exception:
            mono = 0.0
        return ok + mono

    best = None
    best_score = 0.0
    for c in candidates:
        sc = score(c)
        if sc > best_score:
            best_score = sc
            best = c

    return best


def _id_like_cols(df: pd.DataFrame) -> List[str]:
    out: List[str] = []
    n = len(df)
    if n <= 0:
        return out

    for c in df.columns:
        s = df[c]
        uniq = s.nunique(dropna=True)
        uniq_ratio = float(uniq) / float(max(n, 1))

        # Name hints
        name = str(c).lower()
        name_hint = any(k in name for k in ("id", "guid", "uuid", "encounter", "record", "row", "index"))

        # Monotonic numeric hint
        mono = False
        try:
            if pd.api.types.is_numeric_dtype(s):
                sn = s.dropna()
                if len(sn) > 20000:
                    sn = sn.sample(20000, random_state=0)
                if len(sn) > 10:
                    mono = bool(sn.is_monotonic_increasing and (sn.nunique() / len(sn) > 0.98))
        except Exception:
            mono = False

        if name_hint or uniq_ratio > 0.98 or mono:
            out.append(str(c))
    return out


def build_schema_contract(
    df: pd.DataFrame,
    seed: int = 42,
    max_numeric_rows: int = 250_000,
    max_deep_math_rows: int = 5_000,
) -> Dict[str, Any]:
    """
    Returns a dict:
      - contract: SchemaContract as a JSON-able dict
      - raw_df_stats
      - df_canonical
      - df_numeric_view
      - df_deep_math_view (sampled numeric view)
      - df_time_view (if time col detected)
    """
    parse_warnings: List[str] = []

    # Canonicalize column names
    original_cols = list(df.columns)
    canon = [_canonicalize_name(c) for c in original_cols]
    canon = _dedupe_columns(canon)
    mapping = {str(o): str(n) for o, n in zip(original_cols, canon)}

    dfc = df.copy()
    dfc.columns = canon

    # Basic cleanup: strip strings
    for c in dfc.columns:
        if dfc[c].dtype == object:
            try:
                dfc[c] = dfc[c].astype(str).str.strip()
            except Exception:
                pass

    # Infer roles
    time_col = _infer_time_col(dfc)
    lat_col, lon_col = _infer_lat_lon(list(dfc.columns))

    # Build numeric view
    dfn = dfc.copy()

    # Coerce obvious numeric strings
    for c in dfn.columns:
        if dfn[c].dtype == object:
            # try numeric conversion on a sample
            s = dfn[c]
            ss = s.dropna()
            if len(ss) > 2000:
                ss = ss.sample(2000, random_state=seed)
            try:
                conv = pd.to_numeric(ss, errors="coerce")
                if float(conv.notna().mean()) > 0.7:
                    dfn[c] = pd.to_numeric(dfn[c], errors="coerce")
            except Exception:
                pass

    # Drop ID-like cols (but never drop time col)
    id_like = _id_like_cols(dfn)
    if time_col and time_col in id_like:
        id_like = [c for c in id_like if c != time_col]
    if id_like:
        dfn = dfn.drop(columns=id_like, errors="ignore")

    # Keep only numeric
    numeric_cols = [c for c in dfn.columns if pd.api.types.is_numeric_dtype(dfn[c])]
    dfn = dfn[numeric_cols].copy()

    # Replace inf
    for c in dfn.columns:
        try:
            dfn[c] = dfn[c].replace([np.inf, -np.inf], np.nan)
        except Exception:
            pass

    # Optional cap rows for performance
    if len(dfn) > max_numeric_rows:
        dfn = dfn.sample(max_numeric_rows, random_state=seed)

    # Deep math view (smaller)
    dfdm = dfn
    if len(dfdm) > max_deep_math_rows:
        dfdm = dfdm.sample(max_deep_math_rows, random_state=seed)

    # Time view (best-effort)
    dft = None
    if time_col:
        try:
            ts = pd.to_datetime(dfc[time_col], errors="coerce")
            ok = float(ts.notna().mean())
            if ok > 0.5:
                dft = dfc.copy()
                dft[time_col] = ts
                dft = dft.dropna(subset=[time_col]).sort_values(time_col)
        except Exception as e:
            parse_warnings.append(f"time_parse_failed:{type(e).__name__}:{e}")

    # Missingness diagnostics
    miss = []
    try:
        m = dfc.isna().mean().sort_values(ascending=False)
        miss = [(str(k), float(v)) for k, v in m.head(15).items()]
    except Exception:
        miss = []

    cat_cols = [c for c in dfc.columns if dfc[c].dtype == object]
    numeric_cols_final = list(dfn.columns)

    contract = SchemaContract(
        rows=int(df.shape[0]),
        cols=int(df.shape[1]),
        time_col=time_col,
        lat_col=lat_col,
        lon_col=lon_col,
        numeric_cols=numeric_cols_final[:500],
        categorical_cols=cat_cols[:500],
        id_like_cols=id_like[:500],
        parse_warnings=parse_warnings,
        missingness_top=miss,
        original_to_canonical=mapping,
    )

    return {
        "contract": {
            "rows": contract.rows,
            "cols": contract.cols,
            "time_col": contract.time_col,
            "lat_col": contract.lat_col,
            "lon_col": contract.lon_col,
            "numeric_col_count": len(contract.numeric_cols),
            "categorical_col_count": len(contract.categorical_cols),
            "id_like_col_count": len(contract.id_like_cols),
            "numeric_cols": contract.numeric_cols,
            "categorical_cols": contract.categorical_cols,
            "id_like_cols": contract.id_like_cols,
            "parse_warnings": contract.parse_warnings,
            "missingness_top": contract.missingness_top,
            "original_to_canonical": contract.original_to_canonical,
        },
        "df_canonical": dfc,
        "df_numeric_view": dfn,
        "df_deep_math_view": dfdm,
        "df_time_view": dft,
    }

