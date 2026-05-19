"""
Missing-value pattern classification.

For each column with missing values, we classify the *pattern* of the
missingness — not just the count. The pattern tells the analyst
whether they have:

  * **random** missingness — a few NaNs scattered through the column
    (typical: sensor blips, instrument noise). Usually safe to impute
    or drop.

  * **structured** missingness — a contiguous block of rows is missing
    (typical: instrument went offline for a window). Imputation is
    dangerous; the block should be treated as a separate regime.

  * **time-correlated** missingness — missing rows cluster periodically
    or correlate with a time-of-day pattern (typical: scheduled
    downtime, batch-write gaps). Forecasting is biased if not modelled.

  * **bulk-leading** / **bulk-trailing** — missing values cluster at the
    start or end (typical: dataset starts before a sensor was deployed,
    or ends after it was decommissioned). Crop, don't impute.

  * **near-total** — the column is mostly empty (>80% missing). Probably
    not usable as a feature; surface so the user knows.

  * **negligible** — fewer than ~1% missing. Reported only as info.

Classification uses simple, transparent rules. We deliberately do not
fit a probabilistic missingness model (MCAR/MAR/MNAR) — that requires
side information Aurora doesn't always have. Pattern classification is
what the user actually needs to decide what to do next.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple


# Thresholds. Tunable; kept as constants for clarity.
NEGLIGIBLE_THRESHOLD = 0.01      # <1% missing → "negligible"
NEAR_TOTAL_THRESHOLD = 0.80      # >80% missing → "near-total"
BULK_LEADING_TAIL_FRAC = 0.20    # missing cluster in first/last 20% of rows
BULK_LEADING_RATIO = 3.0          # missing rate in head/tail must be 3x rest
STRUCTURED_BLOCK_MIN = 5         # min consecutive-missing rows to count as a "block"
STRUCTURED_BLOCK_FRAC = 0.50     # of all missing values, what fraction must be in blocks


# Pattern tokens.
PATTERN_RANDOM = "random"
PATTERN_STRUCTURED = "structured"
PATTERN_TIME_CORRELATED = "time_correlated"
PATTERN_BULK_LEADING = "bulk_leading"
PATTERN_BULK_TRAILING = "bulk_trailing"
PATTERN_NEAR_TOTAL = "near_total"
PATTERN_NEGLIGIBLE = "negligible"
PATTERN_NONE = "none"


@dataclass(frozen=True)
class MissingnessPattern:
    """Classification of one column's missingness.

    Attributes:
        column            Column name.
        missing_count     Number of missing values (NaN, None, sentinel strings).
        total_count       Total rows.
        missing_rate      missing_count / total_count.
        pattern           One of the PATTERN_* tokens.
        confidence        How confident we are in this classification (0-1).
        details           Optional dict with extra diagnostics (block count,
                          longest block, etc).
        severity          ``info|warn|crit`` based on rate + pattern.
        sample_blocks     Up to 5 (start_row, end_row, length) tuples for
                          structured patterns.
    """
    column: str
    missing_count: int
    total_count: int
    missing_rate: float
    pattern: str
    confidence: float
    details: Dict[str, Any] = field(default_factory=dict)
    severity: str = "info"
    sample_blocks: Tuple[Tuple[int, int, int], ...] = field(default_factory=tuple)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def classify_missingness(
    is_missing: Sequence[bool],
    *,
    column_name: str = "",
    time_index: Optional[Sequence[Any]] = None,
) -> MissingnessPattern:
    """Classify the missingness pattern of one column.

    Args:
        is_missing:   Length-N boolean sequence where True = row is missing.
        column_name:  Column name for reporting.
        time_index:   Optional length-N sequence of timestamps. If
                      provided, we look for time-of-day / day-of-week
                      periodicity in the missing rows. Without it, only
                      structural patterns are detected.

    Returns:
        A MissingnessPattern. ``pattern == PATTERN_NONE`` if no missing
        values.
    """
    mask = [bool(x) for x in is_missing]
    n = len(mask)
    if n == 0:
        return MissingnessPattern(
            column=column_name, missing_count=0, total_count=0,
            missing_rate=0.0, pattern=PATTERN_NONE, confidence=1.0,
        )

    missing_count = sum(mask)
    rate = missing_count / n

    if missing_count == 0:
        return MissingnessPattern(
            column=column_name, missing_count=0, total_count=n,
            missing_rate=0.0, pattern=PATTERN_NONE, confidence=1.0,
        )

    if rate < NEGLIGIBLE_THRESHOLD:
        return MissingnessPattern(
            column=column_name,
            missing_count=missing_count, total_count=n,
            missing_rate=rate, pattern=PATTERN_NEGLIGIBLE,
            confidence=1.0, severity="info",
        )

    if rate > NEAR_TOTAL_THRESHOLD:
        return MissingnessPattern(
            column=column_name,
            missing_count=missing_count, total_count=n,
            missing_rate=rate, pattern=PATTERN_NEAR_TOTAL,
            confidence=1.0, severity="crit",
            details={"note": "Column is mostly empty; probably not usable as a feature."},
        )

    # --- Structural pattern detection -----------------------------------
    blocks = _find_missing_blocks(mask, min_size=STRUCTURED_BLOCK_MIN)
    block_count = len(blocks)
    longest_block = max((b[2] for b in blocks), default=0)
    in_blocks_count = sum(b[2] for b in blocks)
    in_blocks_frac = (in_blocks_count / missing_count) if missing_count else 0.0

    # --- Bulk-leading / bulk-trailing -----------------------------------
    head_n = max(int(n * BULK_LEADING_TAIL_FRAC), 1)
    tail_n = head_n
    head_missing = sum(mask[:head_n])
    tail_missing = sum(mask[-tail_n:])
    middle_missing = missing_count - head_missing - tail_missing
    middle_n = max(n - head_n - tail_n, 1)
    head_rate = head_missing / head_n
    tail_rate = tail_missing / tail_n
    middle_rate = middle_missing / middle_n if middle_n > 0 else 0.0

    is_bulk_leading = (
        head_rate > 0.5
        and middle_rate > 0
        and head_rate / max(middle_rate, 1e-9) >= BULK_LEADING_RATIO
    ) or (head_rate > 0.5 and middle_rate < 0.05)

    is_bulk_trailing = (
        tail_rate > 0.5
        and middle_rate > 0
        and tail_rate / max(middle_rate, 1e-9) >= BULK_LEADING_RATIO
    ) or (tail_rate > 0.5 and middle_rate < 0.05)

    # --- Time-correlated (only if time_index supplied) ------------------
    time_corr_score = 0.0
    time_corr_period: Optional[str] = None
    if time_index is not None and len(time_index) == n:
        time_corr_score, time_corr_period = _detect_time_correlation(mask, time_index)

    # --- Decide ----------------------------------------------------------
    if is_bulk_leading and not is_bulk_trailing:
        return MissingnessPattern(
            column=column_name,
            missing_count=missing_count, total_count=n,
            missing_rate=rate, pattern=PATTERN_BULK_LEADING,
            confidence=min(1.0, head_rate),
            severity=_severity_for_rate(rate),
            details={
                "note": "Missing values concentrated at the start. Likely the dataset "
                         "begins before the sensor was active. Consider cropping.",
                "head_rate": round(head_rate, 4),
                "middle_rate": round(middle_rate, 4),
            },
        )

    if is_bulk_trailing and not is_bulk_leading:
        return MissingnessPattern(
            column=column_name,
            missing_count=missing_count, total_count=n,
            missing_rate=rate, pattern=PATTERN_BULK_TRAILING,
            confidence=min(1.0, tail_rate),
            severity=_severity_for_rate(rate),
            details={
                "note": "Missing values concentrated at the end. Likely the sensor "
                         "stopped reporting. Consider cropping.",
                "tail_rate": round(tail_rate, 4),
                "middle_rate": round(middle_rate, 4),
            },
        )

    if time_corr_score >= 0.5 and time_corr_period:
        return MissingnessPattern(
            column=column_name,
            missing_count=missing_count, total_count=n,
            missing_rate=rate, pattern=PATTERN_TIME_CORRELATED,
            confidence=time_corr_score,
            severity=_severity_for_rate(rate),
            details={
                "note": f"Missing values cluster around {time_corr_period}. "
                         f"Likely scheduled downtime or batch-write gap.",
                "period": time_corr_period,
            },
        )

    if in_blocks_frac >= STRUCTURED_BLOCK_FRAC and block_count > 0:
        # Confidence rises with how much of the missingness lives in blocks
        # AND how long the longest block is relative to the data.
        conf = min(1.0, in_blocks_frac * (1.0 + math.log1p(longest_block / 100)))
        return MissingnessPattern(
            column=column_name,
            missing_count=missing_count, total_count=n,
            missing_rate=rate, pattern=PATTERN_STRUCTURED,
            confidence=conf,
            severity=_severity_for_rate(rate),
            details={
                "note": f"{block_count} block(s) of consecutive missing rows. "
                         f"Longest block: {longest_block} rows. "
                         "Likely instrument downtime — treat as a regime, not as noise.",
                "block_count": block_count,
                "longest_block": longest_block,
                "fraction_in_blocks": round(in_blocks_frac, 4),
            },
            sample_blocks=tuple(blocks[:5]),
        )

    return MissingnessPattern(
        column=column_name,
        missing_count=missing_count, total_count=n,
        missing_rate=rate, pattern=PATTERN_RANDOM,
        confidence=1.0 - in_blocks_frac,  # less block structure = more confident "random"
        severity=_severity_for_rate(rate),
        details={
            "note": "Missing values are scattered. Likely sensor noise or "
                     "occasional reading failures.",
        },
    )


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

def _find_missing_blocks(
    mask: List[bool],
    *,
    min_size: int = 1,
) -> List[Tuple[int, int, int]]:
    """Find contiguous runs of True in ``mask``. Returns a list of
    (start, end_inclusive, length) tuples. Only blocks of length
    ``>= min_size`` are reported."""
    out: List[Tuple[int, int, int]] = []
    n = len(mask)
    i = 0
    while i < n:
        if not mask[i]:
            i += 1
            continue
        j = i
        while j < n and mask[j]:
            j += 1
        length = j - i
        if length >= min_size:
            out.append((i, j - 1, length))
        i = j
    return out


def _detect_time_correlation(
    mask: List[bool],
    time_index: Sequence[Any],
) -> Tuple[float, Optional[str]]:
    """Detect whether missing rows correlate with a temporal pattern.

    Returns (score in [0, 1], period_label or None). Score reflects how
    concentrated the missingness is in a specific time-of-day or
    day-of-week bucket. Without pandas this falls back to (0.0, None).
    """
    try:
        import pandas as pd
    except ImportError:
        return 0.0, None
    try:
        ts = pd.to_datetime(pd.Series(list(time_index)), errors="coerce")
    except Exception:
        return 0.0, None
    if ts.isna().all():
        return 0.0, None

    miss = pd.Series(mask)
    # Hour-of-day concentration.
    hour_rates = (
        miss.groupby(ts.dt.hour).mean()
        if not ts.dt.hour.isna().all() else pd.Series(dtype=float)
    )
    # Day-of-week concentration.
    dow_rates = (
        miss.groupby(ts.dt.dayofweek).mean()
        if not ts.dt.dayofweek.isna().all() else pd.Series(dtype=float)
    )

    # Score = max deviation of a bucket's missing-rate from the overall mean.
    overall = miss.mean() or 0.0
    best_score = 0.0
    best_label: Optional[str] = None

    if not hour_rates.empty:
        spread = float(hour_rates.max() - hour_rates.min())
        if spread > 0.2 and hour_rates.max() > overall * 2 and hour_rates.max() > 0.2:
            best_score = max(best_score, min(1.0, spread))
            peak_hour = int(hour_rates.idxmax())
            best_label = f"hour {peak_hour:02d}:00"

    if not dow_rates.empty:
        spread = float(dow_rates.max() - dow_rates.min())
        if spread > 0.2 and dow_rates.max() > overall * 2 and dow_rates.max() > 0.2:
            if spread > best_score:
                best_score = min(1.0, spread)
                day_names = ["Monday", "Tuesday", "Wednesday", "Thursday",
                              "Friday", "Saturday", "Sunday"]
                peak_dow = int(dow_rates.idxmax())
                best_label = day_names[peak_dow] if 0 <= peak_dow < 7 else f"weekday {peak_dow}"

    return best_score, best_label


def _severity_for_rate(rate: float) -> str:
    """Severity for missingness, separate from schema. Slightly more
    permissive — a column can be 30% missing and still useful."""
    if rate >= 0.50:
        return "crit"
    if rate >= 0.10:
        return "warn"
    return "info"
