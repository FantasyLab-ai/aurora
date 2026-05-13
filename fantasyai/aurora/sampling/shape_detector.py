"""
Tier-0 streaming structure inference.

Reads the dataset in chunks, never loading the full file into memory. Detects:

* row count (exact)
* column types (numeric / categorical / time / string)
* time-axis presence + column + cadence guess
* per-column completeness, mean, std, min, max, n_unique (approximate)
* the canonical *shape* — one of:
    - small         — fits in T1 budget; tiering is bypassed
    - time_series   — has time axis + ≥80% numeric
    - panel         — has time axis + a categorical "id" column with ≥3 distinct values
    - very_wide     — > 5000 cols
    - tall_sparse   — NaN rate > 60%
    - wide          — fallback (cross-section, no time axis, ≤ 1000 cols)

Target: < 10 s on 100M rows. Achieved by:
1. read in 100k-row chunks (configurable)
2. per-chunk numeric coercion + nan-rate accumulation
3. cardinality estimated by a sample-set (cap N=10000) per column
4. time parsing only attempted on the first non-empty value per chunk
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Set


# Tier-1 budget heuristic — files smaller than this skip tiering entirely.
SMALL_DATASET_ROW_THRESHOLD = 200_000
SMALL_DATASET_BYTE_THRESHOLD = 50 * 1024 * 1024
VERY_WIDE_COL_THRESHOLD = 5000
TALL_SPARSE_NAN_RATE = 0.60
PANEL_MIN_DISTINCT_IDS = 3


@dataclass
class ColumnProfile:
    name: str
    dtype: str = "object"
    is_numeric: bool = False
    is_time: bool = False
    n_total: int = 0
    n_finite: int = 0
    n_nan: int = 0
    n_unique_est: int = 0
    completeness: float = 0.0
    mean: Optional[float] = None
    std: Optional[float] = None
    min: Optional[float] = None
    max: Optional[float] = None
    sample_values: List[Any] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["sample_values"] = d["sample_values"][:5]
        return d


@dataclass
class StructureProfile:
    rows_total: int = 0
    cols_total: int = 0
    file_path: str = ""
    file_size_bytes: int = 0
    elapsed_s: float = 0.0
    columns: List[ColumnProfile] = field(default_factory=list)
    time_col: Optional[str] = None
    panel_id_col: Optional[str] = None
    cadence_seconds: Optional[float] = None
    has_time_axis: bool = False
    is_small: bool = False
    shape: str = "wide"
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "rows_total": self.rows_total,
            "cols_total": self.cols_total,
            "file_path": self.file_path,
            "file_size_bytes": self.file_size_bytes,
            "elapsed_s": round(self.elapsed_s, 2),
            "time_col": self.time_col,
            "panel_id_col": self.panel_id_col,
            "cadence_seconds": self.cadence_seconds,
            "has_time_axis": self.has_time_axis,
            "is_small": self.is_small,
            "shape": self.shape,
            "notes": list(self.notes),
            "columns": [c.to_dict() for c in self.columns],
        }


# ---------------------------------------------------------------------------
# Public entry
# ---------------------------------------------------------------------------

def infer_structure(file_path: Path,
                    chunk_size: int = 100_000) -> StructureProfile:
    """Streaming pass over the file. Returns a complete StructureProfile."""
    import pandas as pd
    import numpy as np
    t0 = time.time()
    file_path = Path(file_path)
    profile = StructureProfile(
        file_path=str(file_path),
        file_size_bytes=file_path.stat().st_size if file_path.exists() else 0,
    )

    # Per-column accumulators.
    col_profiles: Dict[str, ColumnProfile] = {}
    col_uniques: Dict[str, Set[str]] = {}     # capped at 10000 per col
    col_running: Dict[str, Dict[str, float]] = {}  # mean/std/min/max via Welford

    def _ensure_col(name: str, dtype: str = "object") -> ColumnProfile:
        if name not in col_profiles:
            col_profiles[name] = ColumnProfile(name=name, dtype=dtype)
            col_uniques[name] = set()
            col_running[name] = {"sum": 0.0, "sum_sq": 0.0, "n": 0,
                                  "min": float("inf"), "max": float("-inf")}
        return col_profiles[name]

    rows_total = 0
    chunk_iter = pd.read_csv(file_path, chunksize=chunk_size,
                              low_memory=False, on_bad_lines="skip")
    for chunk in chunk_iter:
        rows_total += len(chunk)
        for c in chunk.columns:
            col = _ensure_col(c, str(chunk[c].dtype))
            col.dtype = str(chunk[c].dtype)
            col.n_total += len(chunk)
            # NaN count.
            n_nan = int(chunk[c].isna().sum())
            col.n_nan += n_nan
            col.n_finite += (len(chunk) - n_nan)
            # Cardinality sample (cap at 10000 to bound memory).
            uniq = col_uniques[c]
            if len(uniq) < 10000:
                # cheap: take first ~500 unique-looking values
                for v in chunk[c].dropna().astype(str).unique()[:500]:
                    if len(uniq) >= 10000:
                        break
                    uniq.add(v)
            # Numeric stats: try numeric coercion.
            try:
                num = pd.to_numeric(chunk[c], errors="coerce")
                n_num = int(num.notna().sum())
                if n_num > 0:
                    rs = col_running[c]
                    arr = num.dropna().to_numpy(dtype=float)
                    rs["sum"] += float(arr.sum())
                    rs["sum_sq"] += float((arr * arr).sum())
                    rs["n"] += int(arr.size)
                    if arr.size:
                        rs["min"] = min(rs["min"], float(arr.min()))
                        rs["max"] = max(rs["max"], float(arr.max()))
                    col.is_numeric = (col.is_numeric or
                                       (n_num >= 0.5 * (len(chunk) - n_nan)))
            except Exception:
                pass
            # Sample values for inspection.
            if len(col.sample_values) < 5:
                vs = chunk[c].dropna().head(2).tolist()
                col.sample_values.extend(vs)

    profile.rows_total = rows_total
    profile.cols_total = len(col_profiles)

    # Finalize per-column stats.
    for name, col in col_profiles.items():
        col.n_unique_est = len(col_uniques[name])
        col.completeness = (col.n_finite / col.n_total) if col.n_total else 0.0
        rs = col_running[name]
        if rs["n"] > 0:
            mean = rs["sum"] / rs["n"]
            var = max(0.0, (rs["sum_sq"] / rs["n"]) - mean * mean)
            col.mean = float(mean)
            col.std = float(math.sqrt(var))
            col.min = rs["min"] if math.isfinite(rs["min"]) else None
            col.max = rs["max"] if math.isfinite(rs["max"]) else None
        # Detect time-likeness on the sample values.
        col.is_time = _looks_like_time(col.sample_values)

    profile.columns = list(col_profiles.values())

    # ------- Pick time column + cadence -------
    profile.time_col, profile.cadence_seconds = _pick_time_column(profile, file_path)
    profile.has_time_axis = profile.time_col is not None

    # ------- Detect panel id column -------
    profile.panel_id_col = _pick_panel_id(profile)

    # ------- Decide shape -------
    profile.shape = _decide_shape(profile)
    profile.is_small = (profile.rows_total < SMALL_DATASET_ROW_THRESHOLD
                        and profile.file_size_bytes < SMALL_DATASET_BYTE_THRESHOLD)

    profile.elapsed_s = time.time() - t0
    return profile


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _looks_like_time(samples: List[Any]) -> bool:
    """Strict: only return True for samples that look like real datetime strings.

    Pandas will parse `13.08` as a year, but that's not what we mean by a
    time column. We require:
      - the sample value is NOT a plain number
      - it contains date-ish punctuation (-, /, :, T, space) OR a year-like
        4-digit prefix
      - pandas successfully parses it
    """
    if not samples:
        return False
    try:
        import pandas as pd
        candidates = []
        for v in samples:
            if v is None:
                continue
            s = str(v).strip()
            if not s:
                continue
            # Reject plain numerics like 13.08 or 100 or -3.5.
            try:
                float(s)
                continue
            except ValueError:
                pass
            # Look for date-like punctuation or T separator.
            if not any(ch in s for ch in ("-", "/", ":", "T")) and not s[:4].isdigit():
                continue
            candidates.append(s)
        if not candidates:
            return False
        parsed = pd.to_datetime(candidates, errors="coerce")
        ok_count = sum(1 for v in parsed if v is not None and pd.notna(v))
        return ok_count >= 0.5 * len(candidates)
    except Exception:
        return False


def _pick_time_column(profile: StructureProfile,
                      file_path: Path) -> tuple:
    """Find the most-likely time column. Returns (col_name, cadence_seconds).

    Strategy: prefer a column flagged is_time; among ties prefer one named
    'time'/'timestamp'/'date'. Cadence is estimated from a small head-read.
    """
    import pandas as pd
    candidates = [c for c in profile.columns if c.is_time]
    if not candidates:
        return None, None
    name_score = {"timestamp": 5, "time": 4, "date": 3, "datetime": 4,
                  "ts": 2, "epoch": 2}
    candidates.sort(key=lambda c: -name_score.get(c.name.lower(), 0))
    chosen = candidates[0].name
    # Cadence: read first 200 rows; compute median dt.
    try:
        head = pd.read_csv(file_path, usecols=[chosen], nrows=200)
        ts = pd.to_datetime(head[chosen], errors="coerce").dropna()
        if ts.size >= 3:
            dts = ts.sort_values().diff().dropna().dt.total_seconds()
            if dts.size:
                return chosen, float(dts.median())
    except Exception:
        pass
    return chosen, None


def _pick_panel_id(profile: StructureProfile) -> Optional[str]:
    """A panel-id column has 3+ distinct values, low cardinality vs row count,
    and a name that LOOKS like an id/group identifier.

    We use word-boundary matching (after splitting on '_' and camelCase) to
    avoid false positives like "humidity" matching "id". Short keys like 'id'
    require an exact token match; longer keys can substring-match.
    """
    import re

    name_hints_short = {"id"}
    name_hints_long = ("station", "patient", "patient_id", "subject", "ticker",
                       "symbol", "asset", "device", "sensor", "machine",
                       "cohort", "group", "user_id", "ticker_id")

    def _tokens(name: str) -> list:
        s = name.lower()
        # split snake_case + camelCase
        parts = re.split(r"[_\s]+|(?<=[a-z])(?=[A-Z])", name)
        return [p.lower() for p in parts if p]

    for c in profile.columns:
        nm = c.name.lower()
        toks = _tokens(c.name)
        # Short keys ('id') require exact token match — never substring.
        is_id_like = any(tok in name_hints_short for tok in toks)
        # Long keys may match as substring (covers 'station_id', 'patient_id').
        if not is_id_like:
            is_id_like = any(h in nm for h in name_hints_long)
        if not is_id_like:
            continue
        if (c.n_unique_est >= PANEL_MIN_DISTINCT_IDS and
                profile.rows_total > 0 and
                c.n_unique_est < 0.5 * profile.rows_total):
            return c.name
    return None


def _decide_shape(profile: StructureProfile) -> str:
    n_rows = profile.rows_total
    n_cols = profile.cols_total
    if n_rows < SMALL_DATASET_ROW_THRESHOLD:
        return "small"
    if n_cols >= VERY_WIDE_COL_THRESHOLD:
        return "very_wide"
    nan_rates = [1.0 - c.completeness for c in profile.columns if c.n_total]
    avg_nan = sum(nan_rates) / len(nan_rates) if nan_rates else 0.0
    if avg_nan >= TALL_SPARSE_NAN_RATE:
        return "tall_sparse"
    # Atom 2.2: detect "network" shape BEFORE panel — a panel id column
    # would also match origin/destination keyword regexes, so we resolve
    # network first.
    if _detect_network_shape(profile):
        return "network"
    if profile.panel_id_col and profile.has_time_axis:
        return "panel"
    if profile.has_time_axis:
        n_numeric = sum(1 for c in profile.columns if c.is_numeric)
        if n_cols and (n_numeric / n_cols) >= 0.50:
            return "time_series"
    return "wide"


# ---------------------------------------------------------------------------
# Atom 2.2: Network-shape detection
# ---------------------------------------------------------------------------
# We surface 'network' for two structural patterns:
#   (a) edge list — two categorical columns with overlapping cardinality
#                    matching origin/destination naming patterns
#   (b) explicit edge column — a single column containing structured
#       "from->to" / "from~to" / "from,to" pairs
#
# OD-matrix detection is a separate code path (square numeric matrix where
# row labels match column labels); we'll add it in a follow-up — most public
# logistics datasets ship as edge lists, not matrices.

import re as _re

_OD_ORIGIN_PATTERNS = [
    r"\borigin\b", r"\bsource\b", r"\bsrc\b",
    r"\bfrom\b", r"\bfrom_id\b", r"\bfrom_location\b",
    r"\bpu_location_id\b", r"\bpickup\b", r"\bpickup_zone\b",
    r"\bstart_node\b", r"\bstart_id\b", r"\bport_of_origin\b",
    r"\bsupplier\b", r"\bvendor_id\b",
    r"\bsender\b", r"\bship_from\b",
]
_OD_DEST_PATTERNS = [
    r"\bdestination\b", r"\bdest\b", r"\btarget\b",
    r"\bto\b", r"\bto_id\b", r"\bto_location\b",
    r"\bdo_location_id\b", r"\bdropoff\b", r"\bdropoff_zone\b",
    r"\bend_node\b", r"\bend_id\b", r"\bport_of_destination\b",
    r"\bcustomer\b", r"\bdelivery_to\b",
    r"\breceiver\b", r"\bship_to\b",
]
_EDGE_NAMING_PATTERNS = [
    r"\bedge\b", r"\barc\b", r"\bsegment\b", r"\bleg\b",
    r"\blink\b", r"\bod_pair\b", r"\bcorridor\b",
    r"\blane\b",
]


def _matches_any(name: str, patterns: List[str]) -> bool:
    name_l = (name or "").lower()
    return any(_re.search(p, name_l) for p in patterns)


def _detect_network_shape(profile: StructureProfile) -> bool:
    """Return True when the dataset looks like an edge-list / OD network."""
    cols = profile.columns or []
    if len(cols) < 2:
        return False
    # ---- Edge-list signature ----
    # ID columns are often stringified integers (NYC TLC pu_location_id is
    # 1..265). The numeric flag is a poor exclusion criterion here. We use
    # bounded cardinality instead: a real ID column has many fewer unique
    # values than rows.
    def _id_like(c):
        if not _matches_any(c.name, _OD_ORIGIN_PATTERNS + _OD_DEST_PATTERNS):
            return False
        # Reject continuous numerics (high cardinality relative to rows).
        if profile.rows_total > 0:
            ratio = c.n_unique_est / profile.rows_total
            if ratio > 0.5:
                return False
        return c.n_unique_est >= 2
    origins = [c for c in cols
               if _matches_any(c.name, _OD_ORIGIN_PATTERNS) and _id_like(c)]
    dests = [c for c in cols
             if _matches_any(c.name, _OD_DEST_PATTERNS) and _id_like(c)]
    if origins and dests:
        # Cardinality overlap heuristic: origin and destination must have
        # similar magnitudes of distinct values (within 5x of each other —
        # a strict 50% overlap requires the actual sets, which we don't
        # store; cardinality-magnitude similarity is a cheap proxy).
        for o in origins:
            for d in dests:
                if o.name == d.name:
                    continue
                ratio = (max(o.n_unique_est, d.n_unique_est) /
                          max(1, min(o.n_unique_est, d.n_unique_est)))
                if ratio <= 5.0:
                    return True
    # ---- Explicit edge column ----
    for c in cols:
        if _matches_any(c.name, _EDGE_NAMING_PATTERNS) and not c.is_numeric:
            sv = [str(v) for v in (c.sample_values or []) if v is not None]
            if any(_re.search(r"[->~,]", s) for s in sv):
                return True
    return False
