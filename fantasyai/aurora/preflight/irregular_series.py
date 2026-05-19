"""
Irregular-sampling detection (Stream 1.1c in the 12-month plan).

The roadmap entry mentions tsfresh / sktime for irregular time series.
We can deliver 90% of the value (detect + report + suggest resampling)
with a small pandas-only handler. The actual feature-extraction
heaviness of tsfresh is something users opt into; Aurora's preflight
shouldn't depend on a 200MB+ optional library to do the diagnostic
work.

This module:

  1. Identifies a column as a "time column" (datetime-typed or
     parseable to datetime).
  2. Computes the inter-arrival gaps between consecutive timestamps.
  3. Classifies the sampling pattern as one of:
     - ``regular``        — gaps are near-constant; mean ≈ median, low cv
     - ``mildly_irregular`` — gaps vary but no big outliers (cv < 0.5)
     - ``irregular``      — gaps span multiple orders of magnitude
     - ``gaps``           — at least one gap is > 10× the median (sensor downtime)
  4. Emits an Aurora-shaped finding with the diagnosis + a suggested
     resample frequency (if applicable).

If pandas is installed and the user explicitly wants tsfresh-style
extraction, ``recommend_resample_freq`` returns a string like ``'5min'``
or ``'1D'`` they can hand to ``df.resample()``.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

LOG = logging.getLogger(__name__)


# Tunables (module-level so future fine-tuning is centralised).
REGULAR_CV_THRESHOLD = 0.05  # coefficient of variation; below = "regular"
MILDLY_IRREGULAR_CV = 0.5    # cv up to this = "mildly_irregular"
GAP_OUTLIER_RATIO = 10.0     # gap > 10× median = "gap" / sensor downtime


# Pattern tokens (string, JSON-safe).
PATTERN_REGULAR = "regular"
PATTERN_MILDLY_IRREGULAR = "mildly_irregular"
PATTERN_IRREGULAR = "irregular"
PATTERN_GAPS = "gaps"
PATTERN_NONE = "no_time_column"


@dataclass(frozen=True)
class IrregularityReport:
    """Diagnostic record for one time series."""
    time_column: str
    n_observations: int
    pattern: str
    median_gap_seconds: float
    mean_gap_seconds: float
    cv: float                # coefficient of variation of gaps
    max_gap_seconds: float
    n_outlier_gaps: int      # gaps > GAP_OUTLIER_RATIO × median
    recommended_resample: Optional[str] = None
    notes: str = ""


def detect_time_column(df: Any) -> Optional[str]:
    """Return the most likely time column name in ``df``, or None.

    Strategy:
      1. If any column is already datetime-typed → use it.
      2. Otherwise scan string-ish columns for ones whose values
         parse via ``pd.to_datetime`` for ≥80% of non-null rows.

    Picks the column with the most successful parses on ties.
    """
    try:
        import pandas as pd
    except ImportError:
        return None

    # First pass: explicit datetime types.
    for c in df.columns:
        if pd.api.types.is_datetime64_any_dtype(df[c]):
            return str(c)

    # Second pass: string columns that look like dates.
    best_col: Optional[str] = None
    best_rate = 0.0
    for c in df.columns:
        s = df[c]
        if not (s.dtype == object or pd.api.types.is_string_dtype(s.dtype)):
            continue
        non_null = s.dropna()
        if len(non_null) < 5:
            continue
        try:
            parsed = pd.to_datetime(non_null, errors="coerce", utc=False)
        except Exception:
            continue
        rate = parsed.notna().mean()
        if rate >= 0.8 and rate > best_rate:
            best_rate = float(rate)
            best_col = str(c)
    return best_col


def analyse_sampling(df: Any, time_column: str) -> IrregularityReport:
    """Compute the irregularity report for ``df[time_column]``.

    Returns an ``IrregularityReport`` whose ``pattern`` is one of
    PATTERN_REGULAR / MILDLY_IRREGULAR / IRREGULAR / GAPS / NONE.
    """
    try:
        import numpy as np
        import pandas as pd
    except ImportError:
        return IrregularityReport(
            time_column=time_column, n_observations=0,
            pattern=PATTERN_NONE, median_gap_seconds=0.0, mean_gap_seconds=0.0,
            cv=0.0, max_gap_seconds=0.0, n_outlier_gaps=0,
            notes="pandas/numpy unavailable",
        )

    if time_column not in df.columns:
        return IrregularityReport(
            time_column=time_column, n_observations=0,
            pattern=PATTERN_NONE, median_gap_seconds=0.0, mean_gap_seconds=0.0,
            cv=0.0, max_gap_seconds=0.0, n_outlier_gaps=0,
            notes=f"column {time_column!r} not present",
        )

    s = df[time_column]
    # Coerce to datetime.
    if not pd.api.types.is_datetime64_any_dtype(s):
        try:
            s = pd.to_datetime(s, errors="coerce")
        except Exception as e:
            return IrregularityReport(
                time_column=time_column, n_observations=0,
                pattern=PATTERN_NONE, median_gap_seconds=0.0, mean_gap_seconds=0.0,
                cv=0.0, max_gap_seconds=0.0, n_outlier_gaps=0,
                notes=f"could not parse to datetime: {e}",
            )

    s = s.dropna().sort_values()
    n = len(s)
    if n < 2:
        return IrregularityReport(
            time_column=time_column, n_observations=n,
            pattern=PATTERN_NONE, median_gap_seconds=0.0, mean_gap_seconds=0.0,
            cv=0.0, max_gap_seconds=0.0, n_outlier_gaps=0,
            notes="need ≥2 non-null timestamps to analyse",
        )

    gaps = s.diff().dropna()
    secs = gaps.dt.total_seconds().to_numpy()
    if len(secs) == 0 or float(np.median(secs)) <= 0.0:
        return IrregularityReport(
            time_column=time_column, n_observations=n,
            pattern=PATTERN_NONE, median_gap_seconds=0.0, mean_gap_seconds=0.0,
            cv=0.0, max_gap_seconds=0.0, n_outlier_gaps=0,
            notes="zero or duplicate timestamps",
        )

    median_g = float(np.median(secs))
    mean_g = float(np.mean(secs))
    cv = float(np.std(secs) / mean_g) if mean_g > 0 else 0.0
    max_g = float(np.max(secs))
    outliers = int(np.sum(secs > GAP_OUTLIER_RATIO * median_g))

    if outliers >= 1 and max_g > GAP_OUTLIER_RATIO * median_g:
        pattern = PATTERN_GAPS
    elif cv < REGULAR_CV_THRESHOLD:
        pattern = PATTERN_REGULAR
    elif cv < MILDLY_IRREGULAR_CV:
        pattern = PATTERN_MILDLY_IRREGULAR
    else:
        pattern = PATTERN_IRREGULAR

    recommended = recommend_resample_freq(median_g)
    return IrregularityReport(
        time_column=time_column,
        n_observations=n,
        pattern=pattern,
        median_gap_seconds=median_g,
        mean_gap_seconds=mean_g,
        cv=cv,
        max_gap_seconds=max_g,
        n_outlier_gaps=outliers,
        recommended_resample=recommended,
        notes=_pattern_note(pattern, median_g, max_g, outliers),
    )


def recommend_resample_freq(median_seconds: float) -> Optional[str]:
    """Translate a median inter-arrival gap (in seconds) into a pandas
    resample-frequency string. Aim for a value the user can hand
    directly to ``df.resample(...)``."""
    if median_seconds <= 0:
        return None
    table = [
        (1.0,     "1s"),
        (60.0,    "1min"),
        (300.0,   "5min"),
        (900.0,   "15min"),
        (3600.0,  "1h"),
        (21600.0, "6h"),
        (86400.0, "1D"),
        (604800.0, "7D"),
    ]
    for limit, freq in table:
        if median_seconds <= limit:
            return freq
    # Months / years — return a daily-aligned ISO duration.
    return f"{int(median_seconds // 86400)}D"


def to_finding(report: IrregularityReport, *, claim_seq: int = 0) -> Dict[str, Any]:
    """Convert an ``IrregularityReport`` to Aurora's finding shape."""
    if report.pattern == PATTERN_NONE:
        return {
            "severity": "info",
            "confidence": 1.0,
            "title": f"Sampling: no usable time column",
            "description": report.notes,
            "method": "preflight_irregular_series",
            "actions": [],
            "kind_hint": "no_time_axis",
            "claim_id": f"preflight_irregular_series-{claim_seq:04d}",
            "fabricated": False,
            "evidence": {"status": "skipped", "notes": report.notes},
        }

    severity = "info"
    if report.pattern == PATTERN_GAPS:
        severity = "warn"
    elif report.pattern == PATTERN_IRREGULAR:
        severity = "warn"

    return {
        "severity": severity,
        "confidence": 0.9,
        "title": (
            f"Sampling: {report.pattern.replace('_', ' ')} "
            f"(median gap {_fmt_seconds(report.median_gap_seconds)}, "
            f"CV={report.cv:.2f})"
        ),
        "description": report.notes,
        "method": "preflight_irregular_series",
        "actions": ["VIEW EVIDENCE"],
        "kind_hint": "sampling_pattern",
        "claim_id": f"preflight_irregular_series-{claim_seq:04d}",
        "fabricated": False,
        "evidence": {
            "time_column": report.time_column,
            "n_observations": report.n_observations,
            "pattern": report.pattern,
            "median_gap_seconds": round(report.median_gap_seconds, 3),
            "mean_gap_seconds": round(report.mean_gap_seconds, 3),
            "cv": round(report.cv, 4),
            "max_gap_seconds": round(report.max_gap_seconds, 3),
            "n_outlier_gaps": report.n_outlier_gaps,
            "recommended_resample": report.recommended_resample,
        },
    }


def _pattern_note(pattern: str, median: float, max_g: float,
                   outliers: int) -> str:
    if pattern == PATTERN_REGULAR:
        return (
            f"Timestamps are evenly spaced (median gap "
            f"{_fmt_seconds(median)}). Downstream forecasting + spectral "
            f"methods can run without resampling."
        )
    if pattern == PATTERN_MILDLY_IRREGULAR:
        return (
            f"Sampling is slightly uneven but no large gaps. Most methods "
            f"still work; consider resampling to the median for clean "
            f"spectral analysis."
        )
    if pattern == PATTERN_IRREGULAR:
        return (
            f"Sampling intervals vary widely (CV high). Forecasting and "
            f"spectral methods (FFT) will be biased. Resample to a "
            f"uniform grid before deeper analysis."
        )
    if pattern == PATTERN_GAPS:
        return (
            f"Detected {outliers} gap(s) larger than {GAP_OUTLIER_RATIO}× "
            f"the median. Largest gap: {_fmt_seconds(max_g)}. Likely "
            f"sensor downtime — treat as regime boundaries rather than "
            f"imputing across."
        )
    return ""


def _fmt_seconds(s: float) -> str:
    """Render seconds as a human-readable duration."""
    if s < 1:
        return f"{s*1000:.0f}ms"
    if s < 60:
        return f"{s:.1f}s"
    if s < 3600:
        return f"{s/60:.1f}min"
    if s < 86400:
        return f"{s/3600:.1f}h"
    return f"{s/86400:.1f}d"
