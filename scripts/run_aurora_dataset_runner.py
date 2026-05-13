from __future__ import annotations

"""
Aurora Dataset Runner (stable)

Accepts:
- dataset key: fantasyai/data/external/<key>/raw/*.csv
- folder path containing raw/*.csv (or *.csv directly)
- direct CSV path

Includes guard rails for large/messy public datasets:
- robust delimiter + encoding sniffing
- tolerant parsing (python engine + skip bad lines)
- deep_math sampling + ID-like column dropping + numeric sanitation
"""

from fantasyai.aurora.mathstack_v2 import run_mathstack_v2  # stable wrapper

import argparse
import csv
import json
import os
import re
import traceback
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import pandas as pd


from fantasyai.aurora.contracts.schema_contract import build_schema_contract

# =========
# Utilities
# =========

# ============================
# Schema / Structure Contract
# ============================

_NA_TOKENS = {
    "", "na", "n/a", "nan", "null", "none", "nil", "unknown", "unk", "?", "-", "--", "n\\a"
}

def _normalize_colname(c: str) -> str:
    c2 = str(c).strip()
    c2 = re.sub(r"\\s+", " ", c2)
    return c2

def _coerce_na_tokens(s: pd.Series) -> pd.Series:
    # normalize common string NA tokens to actual NA
    if not pd.api.types.is_object_dtype(s) and not pd.api.types.is_string_dtype(s):
        return s
    ss = s.astype("string")
    lower = ss.str.strip().str.lower()
    mask = lower.isin(_NA_TOKENS)
    out = ss.mask(mask, pd.NA)
    return out

def _sample_series(s: pd.Series, n: int = 5000, seed: int = 0) -> pd.Series:
    s2 = s.dropna()
    if len(s2) <= n:
        return s2
    return s2.sample(n=n, random_state=seed)

def _infer_datetime_quality(s: pd.Series) -> dict:
    ss = _sample_series(s, n=5000, seed=0)
    if len(ss) == 0:
        return {"parse_rate": 0.0, "example": None}
    try:
        dt = pd.to_datetime(ss, errors="coerce")
        rate = float(dt.notna().mean())
        ex = None
        good = dt.dropna()
        if len(good) > 0:
            ex = str(good.iloc[0])
        return {"parse_rate": rate, "example": ex}
    except Exception:
        return {"parse_rate": 0.0, "example": None}

def _infer_col_role(df: pd.DataFrame, c: str) -> dict:
    s0 = df[c]
    s = _coerce_na_tokens(s0)

    role = "unknown"
    hints = []
    name = str(c).lower()

    # Geo heuristic
    if name in ("lat", "latitude") or "latitude" in name:
        role = "geo_lat"
        hints.append("name_hint")
    if name in ("lon", "lng", "longitude") or "longitude" in name:
        role = "geo_lon"
        hints.append("name_hint")

    # Datetime heuristic
    dtq = {"parse_rate": 0.0, "example": None}
    if any(k in name for k in ("time", "date", "timestamp", "datetime", "dt")):
        dtq = _infer_datetime_quality(s)
        if dtq["parse_rate"] >= 0.80:
            role = "datetime"
            hints.append("datetime_parse>=0.80")

    # Numeric
    if role == "unknown":
        if pd.api.types.is_numeric_dtype(s0):
            role = "numeric"
            hints.append("dtype_numeric")
        else:
            # try coerce numeric if object
            try:
                ss = _sample_series(s, n=5000, seed=0)
                num = pd.to_numeric(ss, errors="coerce")
                num_rate = float(num.notna().mean()) if len(num) else 0.0
                if num_rate >= 0.90:
                    role = "numeric"
                    hints.append("numeric_coerce>=0.90")
            except Exception:
                pass

    # ID-like (override numeric/categorical if it looks like an ID)
    # - very high uniqueness ratio
    # - name patterns
    # - monotonic near-unique numeric
    try:
        ss = _sample_series(s, n=20000, seed=0)
        uniq_ratio = float(ss.nunique(dropna=True) / max(1, len(ss))) if len(ss) else 0.0
    except Exception:
        uniq_ratio = 0.0

    name_id_hint = (
        name == "id"
        or name.endswith("_id")
        or "uuid" in name
        or "guid" in name
        or name.endswith("nbr")
        or name.endswith("number")
        or "incident_id" in name
        or "case_id" in name
    )

    mono_hint = False
    try:
        if role == "numeric":
            sn = pd.to_numeric(_sample_series(s, n=20000, seed=0), errors="coerce").dropna()
            if len(sn) > 20:
                mono_hint = bool(sn.is_monotonic_increasing and (sn.nunique() / len(sn) > 0.98))
    except Exception:
        mono_hint = False

    if name_id_hint or uniq_ratio > 0.98 or mono_hint:
        role = "id_like"
        hints.append("id_like")

    # Categorical vs free_text for remaining object-ish
    if role == "unknown":
        # treat low-cardinality as categorical
        try:
            ss = _sample_series(s, n=20000, seed=0)
            nunq = int(ss.nunique(dropna=True)) if len(ss) else 0
            # free-text tends to have very high cardinality and long strings
            avg_len = float(ss.astype("string").str.len().dropna().mean()) if len(ss) else 0.0
            if nunq <= 200 and (nunq / max(1, len(ss))) < 0.20:
                role = "categorical"
                hints.append("low_cardinality")
            elif avg_len >= 40 and (nunq / max(1, len(ss))) > 0.50:
                role = "free_text"
                hints.append("long_strings_high_cardinality")
            else:
                role = "categorical"
                hints.append("default_categorical")
        except Exception:
            role = "categorical"
            hints.append("fallback_categorical")

    # Datetime quality if not already computed
    if role != "datetime":
        if any(k in name for k in ("time", "date", "timestamp", "datetime", "dt")):
            if dtq["parse_rate"] == 0.0:
                dtq = _infer_datetime_quality(s)

    return {
        "name": _normalize_colname(c),
        "role": role,
        "hints": hints,
        "dtype": str(s0.dtype),
        "missing_rate": float(s.isna().mean()) if len(s) else 0.0,
        "unique_est": int(_sample_series(s, n=20000, seed=0).nunique(dropna=True)) if len(s) else 0,
        "unique_ratio_est": uniq_ratio,
        "datetime_parse_rate": float(dtq.get("parse_rate", 0.0)),
        "datetime_example": dtq.get("example", None),
    }

def _pick_best_time_column(col_profiles: list[dict]) -> str | None:
    # choose the best datetime candidate
    cands = [p for p in col_profiles if p["role"] == "datetime"]
    if not cands:
        # sometimes a date column parses at 0.79 etc; allow "datetime_parse_rate" rescue
        cands = [p for p in col_profiles if p.get("datetime_parse_rate", 0.0) >= 0.80]
    if not cands:
        return None
    cands.sort(key=lambda p: (p.get("datetime_parse_rate", 0.0), -p.get("missing_rate", 0.0)), reverse=True)
    return cands[0]["name"]

def _detect_geo(col_profiles: list[dict]) -> dict:
    lat = next((p["name"] for p in col_profiles if p["role"] == "geo_lat"), None)
    lon = next((p["name"] for p in col_profiles if p["role"] == "geo_lon"), None)
    return {"has_geo": bool(lat and lon), "lat_col": lat, "lon_col": lon}

def _recommend_targets(df: pd.DataFrame, col_profiles: list[dict], time_col: str | None) -> list[dict]:
    # Rank numeric columns that are not id_like and not geo
    bad_roles = {"id_like", "geo_lat", "geo_lon"}
    nums = [p for p in col_profiles if p["role"] == "numeric" and p["role"] not in bad_roles]
    # If the dataset includes common “count” fields, boost them
    boosts = []
    for p in nums:
        name = p["name"].lower()
        score = 0.0
        score += (1.0 - p["missing_rate"]) * 0.5
        score += max(0.0, 0.5 - abs(p["unique_ratio_est"] - 0.2))  # prefer neither constant nor near-unique
        if any(k in name for k in ("count", "num", "number", "total", "injur", "fatal", "killed", "amount", "value")):
            score += 0.5
            boosts.append(p["name"])
        if time_col and any(k in name for k in ("per_day", "rate", "rolling", "avg", "mean")):
            score += 0.2
        p2 = dict(p)
        p2["target_score"] = float(score)
        nums[nums.index(p)] = p2

    nums.sort(key=lambda x: x.get("target_score", 0.0), reverse=True)
    out = []
    for p in nums[:15]:
        out.append({
            "col": p["name"],
            "score": p["target_score"],
            "why": p["hints"],
        })
    return out

def _recommend_views(col_profiles: list[dict], time_col: str | None, geo: dict) -> list[dict]:
    # Views tell downstream modules *how* to interpret the dataset
    roles = {}
    for p in col_profiles:
        roles.setdefault(p["role"], []).append(p["name"])

    views = []
    views.append({"view": "raw_table", "purpose": "Full dataset with type-normalized columns."})
    views.append({"view": "numeric_matrix", "purpose": "Numeric-only columns for correlations/anomalies/optimization."})

    if time_col:
        views.append({"view": "time_series_counts", "purpose": "Aggregate row counts by time (events over time).", "time_col": time_col})
        views.append({"view": "time_series_numeric_sums", "purpose": "Aggregate numeric sums by time for forecasting/regimes.", "time_col": time_col})

    if geo.get("has_geo"):
        views.append({"view": "geo_view", "purpose": "Geospatial sanity + clustering/heatmap features.", "lat_col": geo["lat_col"], "lon_col": geo["lon_col"]})

    # if there are categoricals, recommend grouped time series
    if time_col and roles.get("categorical"):
        views.append({
            "view": "grouped_time_series_topk",
            "purpose": "Top-K categories grouped time series for regime/anomaly segmentation.",
            "time_col": time_col,
            "group_cols": roles["categorical"][:5],
            "topk": 10,
        })

    return views

def build_structure_contract(df: pd.DataFrame, dataset_key: str | None = None) -> dict:
    # profile columns (use sample for speed)
    col_profiles = []
    for c in df.columns:
        try:
            col_profiles.append(_infer_col_role(df, c))
        except Exception as e:
            col_profiles.append({
                "name": _normalize_colname(c),
                "role": "unknown",
                "hints": ["profile_error"],
                "dtype": str(df[c].dtype),
                "missing_rate": float(df[c].isna().mean()) if len(df) else 0.0,
                "unique_est": None,
                "unique_ratio_est": None,
                "error": f"{type(e).__name__}:{e}",
            })

    time_col = _pick_best_time_column(col_profiles)
    geo = _detect_geo(col_profiles)

    # high-level dataset flags
    rows, cols = int(df.shape[0]), int(df.shape[1])
    numeric_cols = [p["name"] for p in col_profiles if p["role"] == "numeric"]
    id_cols = [p["name"] for p in col_profiles if p["role"] == "id_like"]

    eligible = {
        "math_eligible": bool(rows >= 5 and cols >= 2),
        "deep_math_eligible": bool(rows >= 20 and len(numeric_cols) >= 1),
        "time_series_eligible": bool(time_col is not None),
        "geo_eligible": bool(geo.get("has_geo")),
    }

    reason = None
    if cols < 2:
        reason = "Too few columns (likely not a tabular dataset)."
    elif rows < 5:
        reason = "Too few rows for stable statistics."
    elif len(numeric_cols) < 1:
        reason = "No numeric columns detected."

    targets = _recommend_targets(df, col_profiles, time_col)
    views = _recommend_views(col_profiles, time_col, geo)

    return {
        "version": "structure_contract_v1",
        "dataset_key": dataset_key,
        "shape": {"rows": rows, "cols": cols},
        "eligible": eligible,
        "reason_if_ineligible": reason,
        "columns": col_profiles,
        "time": {"best_time_col": time_col},
        "geo": geo,
        "id_like_cols": id_cols[:200],
        "numeric_cols": numeric_cols[:200],
        "recommended_targets": targets,
        "recommended_views": views,
    }


def _now_stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _sanitize_for_json(obj: Any) -> Any:
    """Recursively coerce ``obj`` into JSON-native Python types.

    Mirror of ``studio_api._sanitize_for_json`` (kept here too so the
    runner subprocess does not depend on Flask). Some math methods —
    notably the coupled-systems fits surfaced via
    ``physics_discovery.discover_physics_models`` — leave raw numpy
    ndarrays in their output dicts (``xhat`` / ``yhat`` / ``Rhat``).
    Plain ``json.dumps`` crashes on those with::

        TypeError: Object of type ndarray is not JSON serializable

    so the runner used to die at the ``deep_math.json`` write step,
    surfacing as ``subprocess returned nonzero`` in the UI banner with
    no per-method error context.

    This walker:
      * converts numpy ndarrays to lists (recursing on contents)
      * unwraps numpy scalars (``np.int64`` / ``np.float64`` etc.) to
        Python natives via ``.item()``
      * coerces NaN / Inf floats to ``None`` (JSON has no spec for them)
      * normalises pandas Timestamp / NaT / Timedelta / Series / DataFrame
      * stringifies datetime / date / Path
      * recurses through dict / list / tuple / set / frozenset

    Never raises. Hostile leaves become ``str(obj)`` or ``None``.
    """
    # 1. Fast path for the most common natives.
    if obj is None or isinstance(obj, (str, bool)):
        return obj

    # 2. Numpy — scalar BEFORE int/float because numpy.int64 IS-A int on
    #    most platforms but the stdlib JSON encoder still rejects it.
    try:
        import numpy as _np
    except Exception:  # pragma: no cover — numpy is a hard dep, but be safe
        _np = None
    if _np is not None:
        if isinstance(obj, _np.generic):
            try:
                v = obj.item()
            except Exception:
                return str(obj)
            # ``item()`` may itself return a NaN float; recurse to canonicalise.
            return _sanitize_for_json(v)
        if isinstance(obj, _np.ndarray):
            try:
                return _sanitize_for_json(obj.tolist())
            except Exception:
                return None

    # 3. Native int / float (after numpy.generic check above).
    if isinstance(obj, int):
        return obj
    if isinstance(obj, float):
        # JSON has no representation for NaN or +/-Inf — emit None so
        # downstream parsers (frontend, jq, anything) don't choke.
        if obj != obj or obj in (float("inf"), float("-inf")):
            return None
        return obj

    # 4. Pandas — Timestamp / NaT / Timedelta / Series / DataFrame.
    try:
        import pandas as _pd
    except Exception:  # pragma: no cover
        _pd = None
    if _pd is not None:
        if isinstance(obj, _pd.Timestamp):
            try:
                return None if _pd.isna(obj) else obj.isoformat()
            except Exception:
                return str(obj)
        # NaT comparison via identity-or-isna; pd.NaT is a singleton.
        try:
            if obj is _pd.NaT or (hasattr(obj, "__class__") and
                                  obj.__class__.__name__ == "NaTType"):
                return None
        except Exception:
            pass
        if isinstance(obj, _pd.Timedelta):
            try:
                return obj.total_seconds()
            except Exception:
                return str(obj)
        if isinstance(obj, _pd.Series):
            try:
                return _sanitize_for_json(obj.tolist())
            except Exception:
                return None
        if isinstance(obj, _pd.DataFrame):
            try:
                return _sanitize_for_json(obj.to_dict(orient="records"))
            except Exception:
                return None

    # 5. datetime / date / Path.
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, date):
        return obj.isoformat()
    if isinstance(obj, Path):
        return str(obj)

    # 6. Containers — recurse.
    if isinstance(obj, dict):
        out: Dict[str, Any] = {}
        for k, v in obj.items():
            try:
                key = k if isinstance(k, str) else str(k)
            except Exception:
                key = "<unhashable>"
            out[key] = _sanitize_for_json(v)
        return out
    if isinstance(obj, (list, tuple, set, frozenset)):
        return [_sanitize_for_json(v) for v in obj]

    # 7. Last resort — best-effort stringification.
    try:
        return str(obj)
    except Exception:
        return None


def _write_json(p: Path, obj: Any) -> None:
    """Write ``obj`` to ``p`` as pretty-printed JSON.

    Routes through ``_sanitize_for_json`` so the per-stage artifacts
    (``deep_math.json``, ``math_results.json``, …) never crash on a
    numpy ndarray that an analytical method left in its output. Without
    this the entire run dies at the write step with the unhelpful
    ``TypeError: Object of type ndarray is not JSON serializable``,
    surfacing in the UI as ``subprocess returned nonzero`` with no
    diagnosable per-method error.
    """
    safe = _sanitize_for_json(obj)
    p.write_text(json.dumps(safe, indent=2, ensure_ascii=False), encoding="utf-8")


def _safe_slug(s: str, max_len: int = 80) -> str:
    s = s.strip()
    s = re.sub(r"[^A-Za-z0-9._-]+", "_", s).strip("_")
    if not s:
        s = "dataset"
    return s[:max_len]


def _safe_str(x: Any, max_len: int = 4000) -> str:
    s = str(x)
    return s if len(s) <= max_len else (s[:max_len] + "…")


def _strip_think(s: str) -> str:
    if not s:
        return ""
    return re.sub(r"(?is)<think>.*?</think>", "", s).strip()


# ====================
# Dataset resolution
# ====================

@dataclass(frozen=True)
class ResolvedDataset:
    dataset_key: str
    folder: Path
    table: Path


def _pick_latest_csv(folder: Path) -> Path:
    csvs = sorted(folder.glob("*.csv"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not csvs:
        raise FileNotFoundError(f"No .csv found in: {folder}")
    return csvs[0]


def _resolve_dataset_table(dataset_arg: str) -> Dict[str, str]:
    ds = dataset_arg.strip().strip('"').strip("'")
    p = Path(ds)

    # direct CSV path
    if p.exists() and p.is_file() and p.suffix.lower() == ".csv":
        return {"dataset_key": ds, "folder": str(p.parent), "table": str(p)}

    # folder path
    if p.exists() and p.is_dir():
        raw = p / "raw"
        if raw.exists() and raw.is_dir():
            table = _pick_latest_csv(raw)
            return {"dataset_key": ds, "folder": str(raw), "table": str(table)}
        table = _pick_latest_csv(p)
        return {"dataset_key": ds, "folder": str(p), "table": str(table)}

    # dataset key under repo
    root = Path(__file__).resolve().parents[1]
    folder = root / "fantasyai" / "data" / "external" / ds / "raw"
    if not folder.exists():
        raise FileNotFoundError(f"Missing dataset folder: {folder}")
    table = _pick_latest_csv(folder)
    return {"dataset_key": ds, "folder": str(folder), "table": str(table)}


# ======================
# Robust CSV loader
# ======================

def _sniff_encoding_and_sample(p: Path, nbytes: int = 200_000) -> Tuple[str, str]:
    raw_sample = p.read_bytes()[:nbytes]
    encodings = ["utf-8", "utf-8-sig", "cp1252", "latin1"]
    for enc in encodings:
        try:
            txt = raw_sample.decode(enc)
            return enc, txt
        except Exception:
            continue
    return "latin1", raw_sample.decode("latin1", errors="replace")


def _sniff_delimiter(sample_text: str) -> str:
    delim = ","
    try:
        sniffer = csv.Sniffer()
        dialect = sniffer.sniff(sample_text, delimiters=[",", ";", "\t", "|"])
        delim = dialect.delimiter
    except Exception:
        candidates = [",", ";", "\t", "|"]
        lines = [ln for ln in sample_text.splitlines() if ln.strip()][:50]
        scores: Dict[str, int] = {}
        for d in candidates:
            counts = [len(ln.split(d)) for ln in lines[:30]]
            counts.sort()
            scores[d] = counts[len(counts) // 2] if counts else 1
        delim = max(scores, key=scores.get)
    return delim


def _load_df(table_path: str) -> pd.DataFrame:
    p = Path(table_path)
    if not p.exists():
        raise FileNotFoundError(table_path)

    used_enc, sample_text = _sniff_encoding_and_sample(p)
    delim = _sniff_delimiter(sample_text)

    attempts = [
        dict(sep=delim, engine="c", low_memory=False),
        dict(sep=delim, engine="python", on_bad_lines="skip"),
        dict(sep=delim, engine="python", on_bad_lines="warn", quoting=csv.QUOTE_NONE, escapechar="\\"),
    ]

    last_err: Optional[Exception] = None
    for kw in attempts:
        try:
            return pd.read_csv(p, encoding=used_enc, **kw)
        except Exception as e:
            last_err = e

    raise last_err or RuntimeError("Unknown read_csv failure")


# ================================
# Guard rails (domain-agnostic)
# ================================

def _sanitize_numeric(df: pd.DataFrame) -> pd.DataFrame:
    df2 = df.copy()
    num_cols = df2.select_dtypes(include=["number"]).columns
    if len(num_cols) > 0:
        df2[num_cols] = df2[num_cols].replace([float("inf"), float("-inf")], pd.NA)
    return df2


def _id_like_cols(df: pd.DataFrame) -> list[str]:
    out: list[str] = []
    n = len(df)
    if n <= 1:
        return out

    for c in df.columns:
        name = str(c).lower()
        s = df[c]

        name_hint = (
            name == "id"
            or name.endswith("_id")
            or "uuid" in name
            or "guid" in name
            or name.endswith("nbr")
            or name.endswith("number")
        )

        uniq_ratio = 0.0
        try:
            ss = s.dropna()
            if len(ss) > 20000:
                ss = ss.sample(20000, random_state=0)
            uniq_ratio = float(ss.nunique(dropna=True) / max(1, len(ss)))
        except Exception:
            uniq_ratio = 0.0

        mono_hint = False
        try:
            if pd.api.types.is_numeric_dtype(s):
                sn = s.dropna()
                if len(sn) > 20000:
                    sn = sn.sample(20000, random_state=0)
                if len(sn) > 10:
                    mono_hint = bool(sn.is_monotonic_increasing and (sn.nunique() / len(sn) > 0.98))
        except Exception:
            mono_hint = False

        if name_hint or uniq_ratio > 0.98 or mono_hint:
            out.append(str(c))

    return out


def _sample_df(df: pd.DataFrame, max_rows: int, seed: int) -> pd.DataFrame:
    if not max_rows or max_rows <= 0:
        return df
    if len(df) <= max_rows:
        return df
    return df.sample(n=max_rows, random_state=seed)


def _infer_time_col(df: pd.DataFrame) -> Optional[str]:
    candidates = []
    for c in df.columns:
        name = str(c).lower()
        if any(k in name for k in ("time", "date", "timestamp", "datetime", "dt")):
            candidates.append(c)

    if not candidates:
        return None

    best = None
    best_rate = 0.0
    for c in candidates:
        s = df[c].dropna()
        if len(s) > 5000:
            s = s.sample(5000, random_state=0)
        try:
            dt = pd.to_datetime(s, errors="coerce")
            rate = float(dt.notna().mean())
        except Exception:
            rate = 0.0
        if rate > best_rate:
            best_rate = rate
            best = c

    return best if best_rate >= 0.80 else None


def _prepare_deep_math_df(df: pd.DataFrame, seed: int, max_rows: int = 5000) -> pd.DataFrame:
    df2 = _sanitize_numeric(df)

    time_col = _infer_time_col(df2)

    id_cols = _id_like_cols(df2)
    if id_cols:
        if time_col in id_cols:
            id_cols = [c for c in id_cols if c != time_col]
        df2 = df2.drop(columns=id_cols, errors="ignore")

    if time_col is None:
        drop_fake_time = []
        for c in df2.columns:
            name = str(c).lower()
            if any(k in name for k in ("time", "date", "timestamp", "datetime", "dt")):
                drop_fake_time.append(c)
        if drop_fake_time:
            df2 = df2.drop(columns=drop_fake_time, errors="ignore")

    df2 = _sample_df(df2, max_rows=max_rows, seed=seed)
    return df2


def _assess_dataset_structure(df: pd.DataFrame) -> Dict[str, Any]:
    rows = int(df.shape[0])
    cols = int(df.shape[1])
    numeric_cols = df.select_dtypes(include=["number"]).columns.tolist()

    time_name_candidates = []
    for c in df.columns:
        name = str(c).lower()
        if any(k in name for k in ("time", "date", "timestamp", "datetime", "dt")):
            time_name_candidates.append(str(c))

    math_eligible = (rows >= 5) and (cols >= 2)
    deep_math_eligible = (rows >= 20) and (len(numeric_cols) >= 1)

    reason = None
    if cols < 2:
        reason = "Too few columns (likely a log/text stream, not a tabular dataset)."
    elif rows < 5:
        reason = "Too few rows for meaningful statistics."
    elif len(numeric_cols) < 1:
        reason = "No numeric columns detected (physics/math requires numeric signals)."

    return {
        "rows": rows,
        "cols": cols,
        "numeric_cols": [str(c) for c in numeric_cols][:200],
        "numeric_col_count": int(len(numeric_cols)),
        "time_name_candidates": time_name_candidates[:50],
        "math_eligible": bool(math_eligible),
        "deep_math_eligible": bool(deep_math_eligible),
        "reason_if_ineligible": reason,
    }


# ==========================
# Deep Math import (canonical)
# ==========================

compute_deep_math = None
compute_deep_math_resolved_from = None
try:
    from fantasyai.aurora.math.deep_math import compute_deep_math_v3 as compute_deep_math
    compute_deep_math_resolved_from = "compute_deep_math_v3"
except Exception:
    try:
        from fantasyai.aurora.math.deep_math import compute_deep_math_v2 as compute_deep_math
        compute_deep_math_resolved_from = "compute_deep_math_v2"
    except Exception:
        try:
            from fantasyai.aurora.math.deep_math import compute_deep_math as compute_deep_math  # type: ignore
            compute_deep_math_resolved_from = "compute_deep_math"
        except Exception:
            compute_deep_math = None
            compute_deep_math_resolved_from = None


# ======================
# LLM (optional / Ollama)
# ======================

def _ollama_generate(prompt: str) -> Dict[str, Any]:
    import urllib.request

    url = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
    model = os.environ.get("OLLAMA_MODEL", "deepseek-r1:8b")

    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "format": "json",
        "options": {
            "num_predict": int(os.environ.get("AURORA_LLM_MAX_TOKENS", "1200")),
            "temperature": float(os.environ.get("AURORA_LLM_TEMP", "0.2")),
        },
    }

    req = urllib.request.Request(
        url + "/api/generate",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = resp.read().decode("utf-8", errors="replace")
    return json.loads(data)


def _main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", required=True)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--also-math", action="store_true")
    ap.add_argument("--math-v2", action="store_true")
    ap.add_argument("--deep-math", action="store_true")
    ap.add_argument("--also-llm", action="store_true")
    ap.add_argument("--deep-math-max-rows", type=int, default=int(os.environ.get("AURORA_DEEP_MATH_MAX_ROWS", "5000")))
    args = ap.parse_args()

    resolved = _resolve_dataset_table(args.dataset)

    out_root = Path(__file__).resolve().parents[1] / "outputs" / "aurora_dataset_runs"
    out_root.mkdir(parents=True, exist_ok=True)

    dataset_id = Path(args.dataset).name or str(args.dataset)
    dataset_slug = _safe_slug(dataset_id)
    run_dir = out_root / f"{_now_stamp()}__{dataset_slug}"
    run_dir.mkdir(parents=True, exist_ok=True)

    df = _load_df(resolved["table"])

    # ==========================
    # Schema / Structure Contract
    # ==========================
    bundle = build_schema_contract(df, seed=args.seed)
    schema_contract = bundle["contract"]
    df_canonical = bundle["df_canonical"]
    df_numeric = bundle["df_numeric_view"]
    df_deep_view = bundle["df_deep_math_view"]
    df_time = bundle["df_time_view"]

    _write_json(run_dir / "schema_contract.json", schema_contract)

    # Downstream modules should prefer numeric/time views (more robust than raw)
    df_for_engine = df_numeric if (df_numeric is not None and df_numeric.shape[1] >= 1) else df_canonical

    # Build rich schema/structure contract
    structure_contract = build_structure_contract(df_for_engine if "df_for_engine" in locals() else df, dataset_key=resolved.get("dataset_key"))
    _write_json(run_dir / "structure_contract.json", structure_contract)

    # Keep legacy structure.json for backward compatibility
    structure = _assess_dataset_structure(df_for_engine)
    _write_json(run_dir / "structure.json", structure)

    _write_json(run_dir / "tableselection.json", {
        "dataset_key": resolved["dataset_key"],
        "folder": resolved["folder"],
        "table": resolved["table"],
        "rows": int(df.shape[0]),
        "cols": int(df.shape[1]),
    })

    llm_used = False

    # Mathstack v2
    if args.also_math and args.math_v2:
        if not structure.get("math_eligible", False):
            math_results: Dict[str, Any] = {
                "skipped": True,
                "reason": structure.get("reason_if_ineligible") or "math gated",
                "structure": structure,
            }
        else:
            try:
                math_results = run_mathstack_v2(df=df_for_engine, seed=args.seed, out_dir=run_dir)
            except Exception as e:
                math_results = {
                    "error": f"mathstack_v2_failed:{type(e).__name__}:{e}",
                    "trace": traceback.format_exc()[:8000],
                }
    else:
        math_results = {"note": "math disabled (missing --also-math --math-v2)"}

    _write_json(run_dir / "math_results.json", math_results)

    # Deep math (sampled)
    deep_out: Optional[Dict[str, Any]] = None
    if args.deep_math:
        if not structure.get("deep_math_eligible", False):
            deep_out = {
                "skipped": True,
                "reason": structure.get("reason_if_ineligible") or "deep_math gated",
                "structure": structure,
            }
        elif compute_deep_math is None:
            deep_out = {"error": "deep_math_failed:compute_deep_math_not_available"}
        else:
            try:
                df_deep = _prepare_deep_math_df(df_for_engine, seed=args.seed, max_rows=int(args.deep_math_max_rows))
                try:
                    deep_out = compute_deep_math(df=df_deep, seed=args.seed)  # type: ignore[misc]
                except TypeError:
                    deep_out = compute_deep_math(df_deep, args.seed)  # type: ignore[misc]
                deep_out = deep_out if isinstance(deep_out, dict) else {"result": deep_out}
                deep_out["_runner_info"] = {
                    "resolved_from": compute_deep_math_resolved_from,
                    "deep_math_max_rows": int(args.deep_math_max_rows),
                    "deep_rows_used": int(getattr(df_deep, "shape", (0, 0))[0]),
                    "deep_cols_used": int(getattr(df_deep, "shape", (0, 0))[1]),
                }
            except Exception as e:
                deep_out = {
                    "error": f"deep_math_failed:{type(e).__name__}:{e}",
                    "trace": traceback.format_exc()[:8000],
                }

    _write_json(run_dir / "deep_math.json", deep_out)

    # Optional LLM narrative
    if args.also_llm:
        provider = os.environ.get("AURORA_LLM_PROVIDER", "").strip().lower()
        debug: Dict[str, Any] = {"provider": provider}
        narrative: Dict[str, Any] = {
            "version": "llm_insights_v2",
            "provider": provider,
            "model": os.environ.get("OLLAMA_MODEL", ""),
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "one_paragraph_summary": "",
            "insights": [],
            "risks_assumptions": [],
            "next_analyses": [],
            "signal_calls": [],
        }

        try:
            if provider == "ollama":
                math_json = json.dumps(math_results, indent=2)[:9000]
                deep_json = json.dumps(deep_out, indent=2)[:9000]
                prompt = (
                    "You are Aurora — a domain-agnostic quantitative analyst and scientific copilot.\n"
                    "You will be given two JSON blobs: math_results and deep_math.\n\n"
                    "GROUNDING RULES (NO HALLUCINATION):\n"
                    "- Use ONLY facts present in the provided JSON.\n"
                    "- If a detail is missing, say it's missing and suggest what to compute next.\n\n"
                    "OUTPUT RULES (STRICT):\n"
                    "- Return ONLY one valid JSON object. No markdown, no extra text.\n"
                    "- Do NOT include <think> blocks.\n\n"
                    "JSON SCHEMA (MUST MATCH EXACTLY):\n"
                    "{\n"
                    "  \"one_paragraph_summary\": \"string\",\n"
                    "  \"insights\": [\"5-10 bullets citing key/value\"],\n"
                    "  \"risks_assumptions\": [\"3-6 grounded items\"],\n"
                    "  \"next_analyses\": [\"3-8 concrete next steps\"],\n"
                    "  \"signal_calls\": [\"1-5 recommended actions\" ]\n"
                    "}\n\n"
                    "math_results:\n" + math_json + "\n\n"
                    "deep_math:\n" + deep_json
                )
                resp = _ollama_generate(prompt)
                llm_used = True

                raw_text = resp.get("response", "") or resp.get("message", {}).get("content", "")
                raw_text = _safe_str(raw_text, max_len=200000)
                clean_text = _strip_think(raw_text)

                parsed = None
                parse_error = None
                try:
                    parsed = json.loads(clean_text)
                except Exception as e:
                    mm = re.search(r"(?s)\{.*\}", clean_text)
                    if mm:
                        try:
                            parsed = json.loads(mm.group(0))
                        except Exception as e2:
                            parse_error = f"{type(e2).__name__}:{e2}"
                    else:
                        parse_error = f"{type(e).__name__}:{e}"

                if isinstance(parsed, dict):
                    narrative.update({
                        "one_paragraph_summary": str(parsed.get("one_paragraph_summary", "") or ""),
                        "insights": list(parsed.get("insights") or []),
                        "risks_assumptions": list(parsed.get("risks_assumptions") or []),
                        "next_analyses": list(parsed.get("next_analyses") or []),
                        "signal_calls": list(parsed.get("signal_calls") or []),
                    })
                    debug["ok"] = True
                else:
                    debug["ok"] = False
                    debug["parsed_error"] = parse_error or "no_json_object_detected"
                    narrative["fallback_text"] = clean_text[:2000]

                debug["model"] = os.environ.get("OLLAMA_MODEL", "")
                debug["url"] = os.environ.get("OLLAMA_URL", "")
                debug["generated_at"] = datetime.now().isoformat(timespec="seconds")
            else:
                debug["ok"] = False
                debug["reason"] = "AURORA_LLM_PROVIDER != ollama"
        except Exception as e:
            debug["ok"] = False
            debug["error"] = f"{type(e).__name__}:{e}"
            debug["trace"] = traceback.format_exc()[:8000]

        _write_json(run_dir / "llm_debug.json", debug)
        _write_json(run_dir / "llm_narrative.json", narrative)

    # Final stdout summary (parsed by steps_1_4 wrapper)
    summary = {
        "dataset_key": resolved["dataset_key"],
        "folder": resolved["folder"],
        "table": resolved["table"],
        "seed": args.seed,
        "run_dir": str(run_dir),
        "math_stack": str(run_dir),
        "llm_used": bool(llm_used),
    }
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    _main()




