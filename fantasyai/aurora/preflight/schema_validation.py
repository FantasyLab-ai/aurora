"""
Column-level schema inference + violation detection.

Aurora doesn't ask the user to declare schemas. We infer them. For
each column we:

  1. Look at every non-null value
  2. Try to classify it: numeric, datetime, boolean, string
  3. Pick the dominant type (>= 70% of values)
  4. Report any values that don't match as *schema violations* with
     row indices so the analyst can investigate

Why 70%: a clean numeric column is usually 100% numeric. A column
with sensor faults is usually 95%+ numeric with a few "ERR" or
"---" strings. A truly mixed-type column (rare in real data) is
50/50 and gets reported as "mixed" with no dominant type.

We never *coerce* values silently. The DataFrame the user passes in
is the DataFrame Aurora analyses. We just *report* what's off.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


# Thresholds. Each captured as a module-level constant so future tuning
# is centralised + reviewable.
DOMINANT_THRESHOLD = 0.70  # ≥70% of non-null values must agree to declare a type
NEGLIGIBLE_VIOLATION_RATE = 0.001  # <0.1% — don't bother reporting (probably noise)


# Type tokens we infer to. Strings (not enums) so the JSON output stays
# JSON-safe without needing custom encoders.
TYPE_NUMERIC = "numeric"
TYPE_INTEGER = "integer"
TYPE_FLOAT = "float"
TYPE_BOOLEAN = "boolean"
TYPE_DATETIME = "datetime"
TYPE_STRING = "string"
TYPE_MIXED = "mixed"
TYPE_EMPTY = "empty"  # column is 100% null


# Common "missing-value sentinel" strings that show up in CSVs from
# the wild. Treated as null for schema purposes but NOT silently
# dropped — they still count toward missingness.
_NULL_SENTINELS = frozenset({
    "", "null", "NULL", "Null", "none", "None", "NONE",
    "na", "NA", "n/a", "N/A", "nan", "NaN", "NAN",
    "-", "--", "---", "?", "??", "missing", "MISSING",
})


# Regex used for "looks numeric" detection. Catches signed ints,
# floats, scientific notation, percentages (without the %), and
# values with thousands separators.
_NUMERIC_RE = re.compile(
    r"^\s*[-+]?(\d{1,3}(,\d{3})+|\d+)(\.\d+)?([eE][-+]?\d+)?\s*$"
)

# Boolean-like string tokens.
_BOOL_TRUE_TOKENS = frozenset({"true", "True", "TRUE", "yes", "Yes", "YES", "y", "Y", "1"})
_BOOL_FALSE_TOKENS = frozenset({"false", "False", "FALSE", "no", "No", "NO", "n", "N", "0"})

# Crude datetime detection — matches common ISO + slash/dash formats.
# We delegate the final parse to pandas if available; this regex just
# decides whether to *try*.
_DATETIME_RE = re.compile(
    r"^\d{4}[-/]\d{1,2}([-/]\d{1,2})?([ T]\d{1,2}:\d{2}(:\d{2})?)?\s*$"
)


# ---------------------------------------------------------------------------
# Dataclasses (return shapes)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ColumnSchema:
    """The inferred schema for one column.

    Attributes:
        column           Column name.
        inferred_type    One of the TYPE_* constants. ``TYPE_MIXED`` if
                         no dominant type. ``TYPE_EMPTY`` if all null.
        confidence       Fraction of non-null values that matched the
                         dominant type (1.0 = perfectly clean).
        non_null_count   Number of non-null values examined.
        type_distribution Per-type vote counts (e.g.,
                         ``{"numeric": 950, "string": 50}``).
        sample_values    Up to 5 example non-null values, for UX.
    """
    column: str
    inferred_type: str
    confidence: float
    non_null_count: int
    type_distribution: Dict[str, int]
    sample_values: Tuple[Any, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class SchemaViolation:
    """A column where one or more values disagree with the inferred type.

    Attributes:
        column            Column name.
        inferred_type     The dominant type Aurora picked.
        violating_count   Number of rows that don't match.
        violation_rate    violating_count / non_null_count.
        sample_rows       Up to 5 (row_idx, value, value_type) tuples
                          showing concrete violations the user can
                          investigate.
        severity          ``info|warn|crit`` based on violation rate.
    """
    column: str
    inferred_type: str
    violating_count: int
    violation_rate: float
    sample_rows: Tuple[Tuple[int, Any, str], ...]
    severity: str


# ---------------------------------------------------------------------------
# Type classification of a single value
# ---------------------------------------------------------------------------

def _classify_value(v: Any) -> Optional[str]:
    """Return the type token for one value, or None if it's null.

    Booleans win over numerics (since True/False are also valid as 1/0
    in Python). Datetimes are detected from strings only — pandas
    Timestamp objects are caught earlier."""
    if v is None:
        return None
    # numpy float NaN slips through ``v is None`` — catch it explicitly.
    try:
        if isinstance(v, float) and math.isnan(v):
            return None
    except (TypeError, ValueError):
        pass

    # Strings: peel off whitespace + null sentinels first.
    if isinstance(v, str):
        s = v.strip()
        if s in _NULL_SENTINELS or not s:
            return None
        if s in _BOOL_TRUE_TOKENS or s in _BOOL_FALSE_TOKENS:
            return TYPE_BOOLEAN
        if _NUMERIC_RE.match(s):
            return TYPE_NUMERIC
        if _DATETIME_RE.match(s):
            return TYPE_DATETIME
        return TYPE_STRING

    # Python booleans BEFORE numerics (bool is a subclass of int).
    if isinstance(v, bool):
        return TYPE_BOOLEAN
    if isinstance(v, int):
        return TYPE_INTEGER
    if isinstance(v, float):
        return TYPE_FLOAT

    # pandas / numpy datetime types — duck-typed check so we don't have
    # to import pandas at module top-level.
    try:
        import numpy as np
        if isinstance(v, np.datetime64):
            return TYPE_DATETIME
        if isinstance(v, np.integer):
            return TYPE_INTEGER
        if isinstance(v, np.floating):
            return TYPE_FLOAT
        if isinstance(v, np.bool_):
            return TYPE_BOOLEAN
    except ImportError:
        pass
    try:
        import pandas as pd
        if isinstance(v, pd.Timestamp):
            return TYPE_DATETIME
    except ImportError:
        pass

    # Catch-all: stringify and let the string path classify it.
    return _classify_value(str(v))


def _normalise_numeric(type_token: str) -> str:
    """Collapse integer/float/numeric into a single 'numeric' bucket
    when comparing. The fine-grained tokens are kept for reporting."""
    return TYPE_NUMERIC if type_token in (TYPE_NUMERIC, TYPE_INTEGER, TYPE_FLOAT) else type_token


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def infer_column_schema(values: List[Any], *, column_name: str = "") -> ColumnSchema:
    """Classify a list of values and return the inferred ColumnSchema.

    Designed to be called by ``detect_schema_violations`` (which iterates
    over a DataFrame), but also useable standalone for unit tests.
    """
    type_counts: Dict[str, int] = {}
    samples: List[Any] = []
    non_null = 0
    for v in values:
        tok = _classify_value(v)
        if tok is None:
            continue
        non_null += 1
        type_counts[tok] = type_counts.get(tok, 0) + 1
        if len(samples) < 5:
            samples.append(v)

    if non_null == 0:
        return ColumnSchema(
            column=column_name,
            inferred_type=TYPE_EMPTY,
            confidence=0.0,
            non_null_count=0,
            type_distribution={},
            sample_values=tuple(),
        )

    # Pick the dominant type. We collapse int/float/numeric into one
    # bucket for dominance voting, but report the finest available
    # token in inferred_type when one type wins outright.
    bucket_counts: Dict[str, int] = {}
    for t, c in type_counts.items():
        b = _normalise_numeric(t)
        bucket_counts[b] = bucket_counts.get(b, 0) + c

    dominant_bucket, dominant_count = max(bucket_counts.items(), key=lambda kv: kv[1])
    confidence = dominant_count / non_null

    if confidence < DOMINANT_THRESHOLD:
        inferred = TYPE_MIXED
    elif dominant_bucket == TYPE_NUMERIC:
        # Prefer the finer-grained token if it exists.
        if type_counts.get(TYPE_INTEGER, 0) > type_counts.get(TYPE_FLOAT, 0) and \
           type_counts.get(TYPE_INTEGER, 0) > type_counts.get(TYPE_NUMERIC, 0):
            inferred = TYPE_INTEGER
        elif type_counts.get(TYPE_FLOAT, 0) > type_counts.get(TYPE_INTEGER, 0):
            inferred = TYPE_FLOAT
        else:
            inferred = TYPE_NUMERIC
    else:
        inferred = dominant_bucket

    return ColumnSchema(
        column=column_name,
        inferred_type=inferred,
        confidence=float(confidence),
        non_null_count=non_null,
        type_distribution=dict(type_counts),
        sample_values=tuple(samples),
    )


def detect_schema_violations(
    df: Any,
    *,
    skip_columns: Optional[List[str]] = None,
) -> Tuple[Dict[str, ColumnSchema], List[SchemaViolation]]:
    """Run schema inference + violation detection on every column.

    Returns:
        (column_schemas, violations) — a dict mapping column name to its
        inferred ColumnSchema, plus a list of SchemaViolation for each
        column whose minority types exceed ``NEGLIGIBLE_VIOLATION_RATE``.

    The DataFrame is not modified.
    """
    skip = set(skip_columns or [])
    schemas: Dict[str, ColumnSchema] = {}
    violations: List[SchemaViolation] = []

    for col in df.columns:
        if col in skip:
            continue
        # Pandas Series → Python list for our classifier. We avoid
        # tolist() on heterogeneous columns because numpy can lose
        # the original string dtype.
        values = list(df[col])
        schema = infer_column_schema(values, column_name=str(col))
        schemas[str(col)] = schema

        # If type is MIXED or there are minority-type values, build a
        # SchemaViolation. Skip when the column is EMPTY (no signal).
        if schema.inferred_type in (TYPE_EMPTY, TYPE_MIXED):
            if schema.inferred_type == TYPE_MIXED and schema.non_null_count > 0:
                # Report the mixed column itself as a violation.
                bad_samples = _bad_value_samples(values, schema.inferred_type, limit=5)
                violations.append(SchemaViolation(
                    column=schema.column,
                    inferred_type=TYPE_MIXED,
                    violating_count=schema.non_null_count,
                    violation_rate=1.0,
                    sample_rows=tuple(bad_samples),
                    severity=_severity_for_rate(1.0),
                ))
            continue

        # Dominant type known — count violating values.
        violating_count = 0
        bad_samples: List[Tuple[int, Any, str]] = []
        for row_idx, v in enumerate(values):
            tok = _classify_value(v)
            if tok is None:
                continue  # null doesn't count as violation
            if _normalise_numeric(tok) != _normalise_numeric(schema.inferred_type):
                violating_count += 1
                if len(bad_samples) < 5:
                    bad_samples.append((row_idx, v, tok))

        if violating_count == 0:
            continue
        rate = violating_count / schema.non_null_count
        if rate < NEGLIGIBLE_VIOLATION_RATE:
            continue  # ignore vanishingly small noise

        violations.append(SchemaViolation(
            column=schema.column,
            inferred_type=schema.inferred_type,
            violating_count=violating_count,
            violation_rate=float(rate),
            sample_rows=tuple(bad_samples),
            severity=_severity_for_rate(rate),
        ))

    return schemas, violations


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

def _severity_for_rate(rate: float) -> str:
    """Map a violation rate to Aurora's severity scale."""
    if rate >= 0.10:
        return "crit"   # 10%+ violations — column is meaningfully broken
    if rate >= 0.01:
        return "warn"   # 1-10% — sensor faults, occasional bad rows
    return "info"        # <1% — worth noting, not blocking


def _bad_value_samples(
    values: List[Any],
    inferred_type: str,
    *,
    limit: int = 5,
) -> List[Tuple[int, Any, str]]:
    """Pull up to ``limit`` (row_idx, value, classified_type) samples
    for a mixed column. Useful for surfacing concrete violations to
    the user."""
    out: List[Tuple[int, Any, str]] = []
    type_seen: Dict[str, int] = {}
    for row_idx, v in enumerate(values):
        tok = _classify_value(v)
        if tok is None:
            continue
        # Pick samples that span different types so the UI shows variety.
        if type_seen.get(tok, 0) < 2 and len(out) < limit:
            out.append((row_idx, v, tok))
            type_seen[tok] = type_seen.get(tok, 0) + 1
    return out
