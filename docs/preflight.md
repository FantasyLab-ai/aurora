# Aurora Preflight — Data Quality Checks

Aurora's preflight stage runs *before* the main analysis pipeline and
surfaces data-quality issues as **findings** — the same shape every
other Aurora output uses. It catches the kind of mess that breaks
downstream methods silently: sensor-fault rows, mixed-type columns,
chunks of missing data that should be treated as instrument downtime
rather than imputed away.

## What it checks

| Check | What it catches | Example finding |
|---|---|---|
| **Schema validation** | Columns where the dominant type disagrees with some of the values — sensor faults, stringified-numeric, mixed CSV imports | *"Column `temp_c` has 5 value(s) that don't match inferred type `numeric` (5.0%) — sample violations: row 23: 'ERR' (looks like string); row 47: '---' (looks like string)"* |
| **Missingness patterns** | Whether missing values are random / structured / time-correlated / bulk-leading / bulk-trailing / near-total | *"Missingness: `humidity` — structured (12% missing). 1 block(s) of consecutive missing rows. Longest block: 240 rows. Likely instrument downtime — treat as a regime, not as noise."* |

## Quickstart

```python
import pandas as pd
from fantasyai.aurora.preflight import run_preflight

df = pd.read_csv("messy_data.csv")
result = run_preflight(df)

# Every Aurora finding shape — drop straight into the findings list
for f in result.findings:
    print(f"{f['severity']:5s}  {f['title']}")
    print(f"        {f['description']}")
    print()

# Rolled-up stats
print(result.summary())
# → {"schema_columns_checked": 8, "schema_violations": 2,
#    "missingness_findings": 3, "total_findings": 5,
#    "by_severity": {"warn": 3, "info": 2}}
```

## API

### `run_preflight(df, skip_columns=None, time_column=None) -> PreflightResult`

Top-level entry point. Runs schema validation + missingness
classification on every column in `df`.

- `skip_columns`: optional list of column names to ignore (e.g.,
  internal IDs or metadata you don't want flagged).
- `time_column`: optional name of a datetime column. When set,
  missingness gets *time-correlation* analysis using its values as
  the index — Aurora can detect whether missingness clusters at a
  specific time-of-day or day-of-week.

Returns a `PreflightResult` with four fields:

| Field | Type | Use |
|---|---|---|
| `findings` | `List[Dict]` | The Aurora-shaped findings, ready to merge into any pipeline |
| `column_schemas` | `Dict[str, ColumnSchema]` | Per-column type inference (useful for the Studio's structure panel) |
| `schema_violations` | `List[SchemaViolation]` | The raw violation objects |
| `missingness` | `List[MissingnessPattern]` | The raw missingness objects |

Plus `result.summary()` for a compact rollup.

### Finding shape

Every preflight finding matches Aurora's standard shape:

```json
{
  "severity": "warn",
  "confidence": 0.85,
  "title": "Schema: column 'sensor_a' has 5 value(s) that don't match...",
  "description": "...",
  "method": "preflight_schema_validation",
  "actions": ["VIEW EVIDENCE"],
  "kind_hint": "schema_violation",
  "claim_id": "preflight_schema_validation-0000",
  "fabricated": false,
  "evidence": {
    "column": "sensor_a",
    "inferred_type": "numeric",
    "violating_count": 5,
    "violation_rate": 0.05,
    "sample_rows": [
      {"row_idx": 23, "value": "'ERR'", "value_type": "string"}
    ]
  }
}
```

## Detection rules (transparent, tunable)

### Schema type inference

For each column:

1. Each non-null value is classified as one of: `numeric` (with sub-types
   `integer` / `float`), `boolean`, `datetime`, `string`.
2. The **dominant bucket** wins iff it covers ≥ **70%** of non-null
   values. Below 70%, the column is reported as `mixed`.
3. Common null-sentinel strings (`""`, `"NA"`, `"n/a"`, `"---"`, etc.)
   are treated as null, NOT as string violations.
4. Severity scales with violation rate:
   - `< 1%` → info (negligible noise)
   - `1-10%` → warn (sensor faults)
   - `≥ 10%` → crit (column is meaningfully broken)
5. Violations below `0.1%` are not reported at all (signal-to-noise).

Thresholds are exposed at the top of `schema_validation.py` for
maintainer tuning.

### Missingness patterns

For each column:

1. Compute the per-row missing mask (NaN, None, sentinel strings).
2. If `< 1%` missing → `negligible` (info).
3. If `> 80%` missing → `near_total` (crit — column probably unusable).
4. Otherwise look at structure:
   - **Bulk-leading**: first 20% of rows have 3× higher missing rate
     than the middle → likely sensor started late.
   - **Bulk-trailing**: same shape, at the end → likely sensor stopped.
   - **Structured**: ≥ 50% of missing values fall inside contiguous
     blocks of ≥ 5 rows → likely instrument downtime.
   - **Time-correlated**: if a `time_column` is given, look for
     hour-of-day or day-of-week buckets where missing rate exceeds 2×
     the overall rate.
   - **Random**: scattered with no significant block structure.

## What preflight intentionally doesn't do

- **It doesn't modify the DataFrame.** Aurora gets the data the user
  provided. Preflight only *observes*.
- **It doesn't impute.** That's a downstream decision — preflight
  surfaces the pattern, the analyst decides what to do.
- **It doesn't reject data.** Even a column that's 90% missing still
  gets passed through; preflight just flags it loudly so the user
  isn't surprised when synthesis hedges its language.
- **It doesn't try to fit a probabilistic MCAR/MAR/MNAR model.**
  That requires side-information Aurora doesn't always have. Pattern
  classification is what you actually need to act on.

## Why no pandera

The roadmap originally mentioned pandera for schema validation. We
deliberately chose to build this in-tree with zero new deps because:

1. Pandera adds ~20MB plus a pydantic dependency chain — meaningful
   for a local-first product.
2. Aurora doesn't need pandera's full DSL (declarative schemas, type
   coercion, hypothesis testing). We just need "is this column the
   type it looks like, and where are the violations?"
3. Keeping the implementation in-tree means we can tune the heuristics
   for Aurora's specific failure modes (sensor faults, stringified
   sentinels) without fighting an external API.

Users who *want* pandera can layer it on top — preflight findings
flow alongside any other validation results without conflict.

## See also

- [docs/concepts.md](concepts.md) — the glass-box principle that
  preflight follows (every finding is fabricated=false, every claim is
  traceable)
- [docs/api-reference.md](api-reference.md) — the Aurora finding
  schema preflight emits
- `fantasyai/aurora/preflight/` — the source if you want to read the
  rules + thresholds directly (they're at the top of each module)
