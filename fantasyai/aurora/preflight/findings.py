"""
Adapt preflight results into Aurora's standard finding-dict shape.

The schema-validation and missingness modules return their own
dataclasses (ColumnSchema, SchemaViolation, MissingnessPattern). For
the rest of Aurora to consume them — state_builder, synthesis, the
frontend — they need to look like every other Aurora finding.

This module exists to keep the schema/missingness modules pure (they
return clean Python objects) while the rest of Aurora gets the
familiar shape:

    {
      "severity": "warn",
      "confidence": 0.95,
      "title": "...",
      "description": "...",
      "method": "preflight_schema_validation",
      "actions": ["VIEW EVIDENCE"],
      "kind_hint": "schema_violation",
      "claim_id": "preflight_schema_validation-0000",
      "fabricated": false
    }
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .missingness import (
    MissingnessPattern,
    PATTERN_NEGLIGIBLE,
    PATTERN_NONE,
    classify_missingness,
)
from .schema_validation import (
    ColumnSchema,
    SchemaViolation,
    detect_schema_violations,
)


# Method labels used in finding dicts. Match Aurora's existing naming
# convention (snake_case, no spaces).
METHOD_SCHEMA = "preflight_schema_validation"
METHOD_MISSINGNESS = "preflight_missingness"


# ---------------------------------------------------------------------------
# Result shape
# ---------------------------------------------------------------------------

@dataclass
class PreflightResult:
    """Bundle of preflight outputs. Most callers want ``.findings`` —
    the others are exposed for tooling that wants the raw shape (e.g.,
    the Studio's structure_contract panel)."""

    column_schemas: Dict[str, ColumnSchema] = field(default_factory=dict)
    schema_violations: List[SchemaViolation] = field(default_factory=list)
    missingness: List[MissingnessPattern] = field(default_factory=list)
    findings: List[Dict[str, Any]] = field(default_factory=list)

    def __bool__(self) -> bool:
        return bool(self.findings)

    def summary(self) -> Dict[str, Any]:
        """Compact stats roll-up. Useful for the Studio's intelligence row."""
        sev_counts: Dict[str, int] = {}
        for f in self.findings:
            sev = f.get("severity", "info")
            sev_counts[sev] = sev_counts.get(sev, 0) + 1
        return {
            "schema_columns_checked": len(self.column_schemas),
            "schema_violations": len(self.schema_violations),
            "missingness_findings": len(self.missingness),
            "total_findings": len(self.findings),
            "by_severity": sev_counts,
        }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def run_preflight(
    df: Any,
    *,
    skip_columns: Optional[List[str]] = None,
    time_column: Optional[str] = None,
) -> PreflightResult:
    """Run all preflight checks on a DataFrame and return findings.

    Args:
        df:           A pandas DataFrame. We don't type-annotate it
                      explicitly to keep this module importable without
                      pandas present at module-load time.
        skip_columns: Optional list of column names to ignore entirely
                      (useful for skipping known-junk metadata columns).
        time_column:  Optional name of a datetime column. When set,
                      missingness gets time-correlation analysis using
                      this column's values as the index.

    Returns:
        A ``PreflightResult`` whose ``.findings`` field can be merged
        straight into Aurora's findings list.
    """
    # Schema inference + violations.
    schemas, violations = detect_schema_violations(df, skip_columns=skip_columns)

    # Missingness — one pattern per column.
    time_idx = None
    if time_column and time_column in df.columns:
        time_idx = list(df[time_column])

    missingness: List[MissingnessPattern] = []
    skip = set(skip_columns or [])
    for col in df.columns:
        if col in skip:
            continue
        series = df[col]
        # is_missing: True for NaN/None plus our null-sentinel strings.
        mask = _is_missing_mask(series)
        pattern = classify_missingness(
            mask,
            column_name=str(col),
            time_index=time_idx,
        )
        # Only keep non-trivial patterns. PATTERN_NONE / PATTERN_NEGLIGIBLE
        # are still included so the UI can show "fully populated" or
        # "minor noise" — they just produce info-severity findings.
        missingness.append(pattern)

    # Convert to Aurora-shaped findings.
    findings: List[Dict[str, Any]] = []
    findings.extend(_violations_to_findings(violations))
    findings.extend(_missingness_to_findings(missingness))

    return PreflightResult(
        column_schemas=schemas,
        schema_violations=violations,
        missingness=missingness,
        findings=findings,
    )


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

def _is_missing_mask(series: Any) -> List[bool]:
    """Construct a boolean mask treating pandas-NaN, None, and the
    schema_validation null-sentinel strings as missing. Pure Python
    fallback so we don't require pandas at import time."""
    from .schema_validation import _NULL_SENTINELS, _classify_value
    out: List[bool] = []
    for v in series:
        # Aurora considers something missing iff schema_validation
        # would classify it as None.
        out.append(_classify_value(v) is None)
    return out


def _violations_to_findings(
    violations: List[SchemaViolation],
) -> List[Dict[str, Any]]:
    """Convert schema violations into Aurora findings."""
    findings: List[Dict[str, Any]] = []
    for i, v in enumerate(violations):
        if v.inferred_type == "mixed":
            title = f"Schema: column {v.column!r} has mixed types"
            description = (
                f"No dominant type detected — values span multiple types. "
                f"Sample violations: {_format_samples(v.sample_rows)}. "
                f"Aurora will analyse the column as-is, but downstream "
                f"methods may downweight or skip it."
            )
        else:
            title = (
                f"Schema: column {v.column!r} has {v.violating_count} "
                f"value(s) that don't match inferred type {v.inferred_type!r} "
                f"({v.violation_rate * 100:.1f}%)"
            )
            description = (
                f"Aurora inferred {v.column!r} is {v.inferred_type}, but "
                f"{v.violating_count} of the non-null values are a different "
                f"type. Sample violations: {_format_samples(v.sample_rows)}. "
                f"Common cause: sensor faults, mixed-import CSVs, or "
                f"stringified missing-value sentinels."
            )
        findings.append({
            "severity": v.severity,
            "confidence": max(0.0, min(1.0, v.violation_rate * 5.0)) if v.severity == "info"
                           else (1.0 if v.severity == "crit" else 0.85),
            "title": title,
            "description": description,
            "method": METHOD_SCHEMA,
            "actions": ["VIEW EVIDENCE"],
            "kind_hint": "schema_violation",
            "claim_id": f"{METHOD_SCHEMA}-{i:04d}",
            "fabricated": False,
            "evidence": {
                "column": v.column,
                "inferred_type": v.inferred_type,
                "violating_count": v.violating_count,
                "violation_rate": v.violation_rate,
                "sample_rows": [
                    {"row_idx": r, "value": str(val)[:80], "value_type": t}
                    for r, val, t in v.sample_rows
                ],
            },
        })
    return findings


def _missingness_to_findings(
    patterns: List[MissingnessPattern],
) -> List[Dict[str, Any]]:
    """Convert missingness patterns into Aurora findings.

    We skip ``PATTERN_NONE`` (column is fully populated) since "nothing
    happened" isn't a finding. We KEEP ``PATTERN_NEGLIGIBLE`` because
    it's useful information for the user to see "yes, Aurora checked
    this column, found 0.2% missing, decided not to worry."
    """
    findings: List[Dict[str, Any]] = []
    for i, p in enumerate(patterns):
        if p.pattern == PATTERN_NONE:
            continue
        if p.pattern == PATTERN_NEGLIGIBLE:
            title = f"Missingness: {p.column!r} has {p.missing_count} missing values ({p.missing_rate * 100:.2f}%) — negligible"
            description = "Below the 1% threshold; Aurora treats this as noise."
        else:
            human_pattern = p.pattern.replace("_", " ")
            title = (
                f"Missingness: {p.column!r} — {human_pattern} "
                f"({p.missing_rate * 100:.1f}% missing)"
            )
            description = (
                p.details.get("note", "")
                or f"{p.missing_count} of {p.total_count} rows missing in this column."
            )
        finding = {
            "severity": p.severity,
            "confidence": float(p.confidence),
            "title": title,
            "description": description,
            "method": METHOD_MISSINGNESS,
            "actions": ["VIEW EVIDENCE"],
            "kind_hint": "missingness_pattern",
            "claim_id": f"{METHOD_MISSINGNESS}-{i:04d}",
            "fabricated": False,
            "evidence": {
                "column": p.column,
                "pattern": p.pattern,
                "missing_count": p.missing_count,
                "total_count": p.total_count,
                "missing_rate": p.missing_rate,
                "details": p.details,
                "sample_blocks": [
                    {"start": s, "end": e, "length": L}
                    for s, e, L in p.sample_blocks
                ],
            },
        }
        findings.append(finding)
    return findings


def _format_samples(samples) -> str:
    """Render a few (row_idx, value, type) tuples as a short string for
    the description field. Truncates long values."""
    if not samples:
        return "(none)"
    bits = []
    for r, val, t in samples[:3]:
        v = str(val)
        if len(v) > 30:
            v = v[:27] + "..."
        bits.append(f"row {r}: {v!r} (looks like {t})")
    return "; ".join(bits)
