"""
Aurora preflight — data quality checks that run before main analysis.

Currently covers two axes:

  * **Schema validation** — for each column, infer the most-likely
    type and surface any rows that violate it. Catches sensor faults,
    mixed-type imports, and stringified numbers.

  * **Missingness patterns** — classify each column's missing-value
    pattern (random / structured / time-correlated / bulk) so the
    user knows whether they have a sampling problem or just sparse
    observations.

Output: a list of Aurora-shaped finding dicts. Same schema as every
other Aurora finding (severity, confidence, title, description,
method, claim_id, fabricated). Drops into the existing findings
pipeline cleanly.

Design principles:

  * **Zero new runtime deps.** Uses only numpy + pandas, both already
    in requirements.txt. We deliberately do *not* add pandera. Aurora's
    contract is local-first + minimal deps; a 20MB schema library to
    do "is this column numeric?" doesn't fit.

  * **Honest about uncertainty.** Every preflight finding carries a
    confidence value reflecting how strong the signal is. A column
    that's 99% numeric with 1% strings is a different signal than a
    column that's 50% / 50%.

  * **Read-only.** Preflight never modifies the DataFrame. It only
    *observes* and reports.

Public API:

    from fantasyai.aurora.preflight import run_preflight
    findings = run_preflight(df)
    # findings is List[Dict[str, Any]] — Aurora finding shape
"""
from __future__ import annotations

from .schema_validation import (
    ColumnSchema,
    SchemaViolation,
    infer_column_schema,
    detect_schema_violations,
)
from .missingness import (
    MissingnessPattern,
    classify_missingness,
)
from .findings import (
    run_preflight,
    PreflightResult,
)

__all__ = [
    # Schema
    "ColumnSchema",
    "SchemaViolation",
    "infer_column_schema",
    "detect_schema_violations",
    # Missingness
    "MissingnessPattern",
    "classify_missingness",
    # Top-level
    "run_preflight",
    "PreflightResult",
]
