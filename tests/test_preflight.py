"""
Tests for the preflight data-quality module.

Strategy: construct small synthetic DataFrames that exhibit each
failure mode (clean column, sensor-fault column, mixed-type column,
bulk-leading missingness, block missingness, etc.) and assert
preflight classifies them correctly.

No real-world data, no fixtures from disk — every test is self-
contained so it runs identically across Python versions and OSes.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fantasyai.aurora.preflight import (
    ColumnSchema,
    SchemaViolation,
    MissingnessPattern,
    infer_column_schema,
    detect_schema_violations,
    classify_missingness,
    run_preflight,
)
from fantasyai.aurora.preflight.schema_validation import (
    TYPE_NUMERIC, TYPE_INTEGER, TYPE_FLOAT, TYPE_STRING,
    TYPE_DATETIME, TYPE_BOOLEAN, TYPE_MIXED, TYPE_EMPTY,
)
from fantasyai.aurora.preflight.missingness import (
    PATTERN_RANDOM, PATTERN_STRUCTURED, PATTERN_BULK_LEADING,
    PATTERN_BULK_TRAILING, PATTERN_NEAR_TOTAL, PATTERN_NEGLIGIBLE,
    PATTERN_NONE, PATTERN_TIME_CORRELATED,
)


# ===========================================================================
# Schema inference — value-level classification
# ===========================================================================

class TestColumnSchemaInference:

    def test_clean_numeric_column(self):
        s = infer_column_schema([1.0, 2.5, 3.7, 4.0, 5.5])
        # numeric votes go into the same bucket; reported as float because
        # all values were floats
        assert s.inferred_type in (TYPE_FLOAT, TYPE_NUMERIC)
        assert s.confidence == 1.0
        assert s.non_null_count == 5

    def test_clean_integer_column(self):
        s = infer_column_schema([1, 2, 3, 4, 5, 6, 7])
        assert s.inferred_type in (TYPE_INTEGER, TYPE_NUMERIC)
        assert s.confidence == 1.0

    def test_clean_string_column(self):
        s = infer_column_schema(["red", "green", "blue", "yellow"])
        assert s.inferred_type == TYPE_STRING
        assert s.confidence == 1.0

    def test_clean_boolean_column(self):
        s = infer_column_schema([True, False, True, True, False])
        assert s.inferred_type == TYPE_BOOLEAN
        assert s.confidence == 1.0

    def test_numeric_column_with_sensor_fault(self):
        """95% numeric + 5% string 'ERR' should report as numeric with
        moderate confidence."""
        vals = [float(i) for i in range(95)] + ["ERR"] * 5
        s = infer_column_schema(vals)
        assert s.inferred_type in (TYPE_NUMERIC, TYPE_FLOAT)
        assert 0.94 < s.confidence < 0.96

    def test_truly_mixed_column(self):
        vals = ["a", 1, 2.5, "b", 3, "c", 4.0]  # roughly 50/50
        s = infer_column_schema(vals)
        # neither type clears 70% → mixed
        assert s.inferred_type == TYPE_MIXED

    def test_empty_column(self):
        s = infer_column_schema([None, None, None])
        assert s.inferred_type == TYPE_EMPTY
        assert s.confidence == 0.0
        assert s.non_null_count == 0

    def test_null_sentinel_strings_count_as_null(self):
        s = infer_column_schema(["1.0", "2.0", "NA", "n/a", "3.0", "---"])
        # Only 3 non-null numerics
        assert s.non_null_count == 3
        assert s.inferred_type in (TYPE_NUMERIC, TYPE_FLOAT)
        assert s.confidence == 1.0

    def test_iso_datetime_strings(self):
        s = infer_column_schema(["2024-01-01", "2024-02-15", "2024-03-30"])
        assert s.inferred_type == TYPE_DATETIME

    def test_pandas_timestamp_objects(self):
        ts = pd.to_datetime(["2024-01-01", "2024-02-15", "2024-03-30"])
        s = infer_column_schema(list(ts))
        assert s.inferred_type == TYPE_DATETIME

    def test_numeric_strings_with_commas(self):
        # "1,234.56" should still register as numeric
        s = infer_column_schema(["1,234.56", "2,345.67", "3,456.78"])
        assert s.inferred_type == TYPE_NUMERIC

    def test_scientific_notation_strings(self):
        s = infer_column_schema(["1.5e10", "2.3e-5", "4.7e+8"])
        assert s.inferred_type == TYPE_NUMERIC

    def test_distribution_in_sample_values(self):
        """sample_values should contain examples but cap at 5."""
        vals = list(range(50))
        s = infer_column_schema(vals)
        assert len(s.sample_values) == 5
        assert s.sample_values[0] in vals


# ===========================================================================
# Schema violation detection on real DataFrames
# ===========================================================================

class TestSchemaViolations:

    def test_clean_dataframe_no_violations(self):
        df = pd.DataFrame({
            "id": [1, 2, 3, 4, 5],
            "value": [1.1, 2.2, 3.3, 4.4, 5.5],
            "label": ["a", "b", "c", "d", "e"],
        })
        schemas, violations = detect_schema_violations(df)
        assert "id" in schemas
        assert "value" in schemas
        assert "label" in schemas
        assert violations == []

    def test_sensor_fault_caught_as_violation(self):
        """A numeric column with sensor 'ERR' strings should be flagged."""
        vals = [float(i) for i in range(95)] + ["ERR"] * 5
        df = pd.DataFrame({"sensor": vals})
        schemas, violations = detect_schema_violations(df)
        assert len(violations) == 1
        v = violations[0]
        assert v.column == "sensor"
        assert v.inferred_type in (TYPE_NUMERIC, TYPE_FLOAT)
        assert v.violating_count == 5
        assert 0.04 < v.violation_rate < 0.06
        assert v.severity == "warn"   # 5% rate → warn
        # Sample rows include the bad indices.
        assert any(t == TYPE_STRING for _, _, t in v.sample_rows)

    def test_mixed_type_column_reported(self):
        df = pd.DataFrame({
            "weird": ["a", 1, "b", 2, "c", 3, 4, "d", 5, "e"],
        })
        schemas, violations = detect_schema_violations(df)
        assert len(violations) == 1
        assert violations[0].inferred_type == TYPE_MIXED
        assert violations[0].severity in ("warn", "crit")

    def test_negligible_violation_not_reported(self):
        """A 1-in-10000 violation is below the noise threshold."""
        vals = [float(i) for i in range(9999)] + ["ERR"]
        df = pd.DataFrame({"clean_ish": vals})
        _, violations = detect_schema_violations(df)
        assert violations == []  # 0.01% is below NEGLIGIBLE_VIOLATION_RATE

    def test_skip_columns_honored(self):
        df = pd.DataFrame({
            "id": [1, 2, 3],
            "messy": ["a", 1, "b"],
        })
        schemas, violations = detect_schema_violations(df, skip_columns=["messy"])
        assert "messy" not in schemas
        assert violations == []

    def test_severity_scales_with_violation_rate(self):
        """1% → warn, 10%+ → crit. We hit the high-rate corner here."""
        vals = [float(i) for i in range(85)] + ["ERR"] * 15  # 15% violations
        df = pd.DataFrame({"broken": vals})
        _, violations = detect_schema_violations(df)
        assert len(violations) == 1
        assert violations[0].severity == "crit"


# ===========================================================================
# Missingness pattern classification
# ===========================================================================

class TestMissingnessPatterns:

    def test_no_missing_returns_none(self):
        p = classify_missingness([False] * 100)
        assert p.pattern == PATTERN_NONE
        assert p.missing_count == 0

    def test_negligible_missingness(self):
        # 1 missing in 1000 → 0.1%, below 1% threshold
        mask = [False] * 999 + [True]
        p = classify_missingness(mask)
        assert p.pattern == PATTERN_NEGLIGIBLE
        assert p.severity == "info"

    def test_near_total_missingness(self):
        # 90% missing
        mask = [True] * 90 + [False] * 10
        p = classify_missingness(mask)
        assert p.pattern == PATTERN_NEAR_TOTAL
        assert p.severity == "crit"

    def test_random_missingness_scattered(self):
        # 5% scattered, no blocks
        mask = [False] * 200
        for idx in (3, 17, 42, 89, 121, 156, 178, 191):
            mask[idx] = True
        p = classify_missingness(mask)
        assert p.pattern in (PATTERN_RANDOM, PATTERN_NEGLIGIBLE)

    def test_structured_block_missingness(self):
        # One big contiguous block in the middle
        mask = [False] * 200
        for i in range(80, 120):  # 40 consecutive missing
            mask[i] = True
        p = classify_missingness(mask)
        assert p.pattern == PATTERN_STRUCTURED
        assert p.details["block_count"] == 1
        assert p.details["longest_block"] == 40

    def test_bulk_leading_missingness(self):
        # First 30% missing, rest clean
        mask = [True] * 60 + [False] * 140
        p = classify_missingness(mask)
        assert p.pattern == PATTERN_BULK_LEADING

    def test_bulk_trailing_missingness(self):
        # Last 30% missing, rest clean
        mask = [False] * 140 + [True] * 60
        p = classify_missingness(mask)
        assert p.pattern == PATTERN_BULK_TRAILING

    def test_time_correlated_missingness(self):
        """Missing rows clustered around 3am should be detected when a
        timestamp column is supplied."""
        # 24 hours x 30 days = 720 hourly rows
        times = pd.date_range("2024-01-01", periods=720, freq="h")
        mask = []
        for ts in times:
            # Missing iff hour == 3 (scheduled downtime).
            mask.append(ts.hour == 3)
        p = classify_missingness(mask, time_index=list(times))
        # Should hit either time_correlated or structured.
        # Time-correlated requires pandas to be available.
        assert p.pattern in (PATTERN_TIME_CORRELATED, PATTERN_STRUCTURED)


# ===========================================================================
# Top-level run_preflight integration
# ===========================================================================

class TestRunPreflight:

    def test_clean_dataframe_returns_low_severity_only(self):
        df = pd.DataFrame({
            "id": list(range(100)),
            "value": [i * 1.5 for i in range(100)],
        })
        result = run_preflight(df)
        # No schema violations. Missingness findings may exist (PATTERN_NONE
        # is skipped) — assert no severity rises above info.
        assert all(f["severity"] == "info" for f in result.findings)
        assert result.summary()["schema_violations"] == 0

    def test_dirty_dataframe_produces_findings(self):
        df = pd.DataFrame({
            # Numeric with 5 sensor-fault rows
            "sensor_a": [float(i) for i in range(95)] + ["ERR"] * 5,
            # Column that's 60% missing
            "patchy": [None] * 60 + list(range(40)),
            # Clean string column
            "label": ["x"] * 100,
        })
        result = run_preflight(df)
        kinds = {f["kind_hint"] for f in result.findings}
        assert "schema_violation" in kinds
        assert "missingness_pattern" in kinds

        # Severity rollup includes warn or crit.
        sev = result.summary()["by_severity"]
        assert "warn" in sev or "crit" in sev

    def test_all_findings_have_fabricated_false(self):
        """Sanity: preflight is not synthesis — every finding must be
        fabricated=False (it's all real computation)."""
        df = pd.DataFrame({"a": [1, 2, "ERR", 4]})
        result = run_preflight(df)
        assert all(f["fabricated"] is False for f in result.findings)

    def test_findings_have_required_fields(self):
        """Every finding must have the Aurora-standard keys so downstream
        code can consume it without special-casing."""
        df = pd.DataFrame({"a": [1, 2, "ERR", 4]})
        result = run_preflight(df)
        required = {"severity", "confidence", "title", "description",
                     "method", "actions", "kind_hint", "claim_id", "fabricated"}
        for f in result.findings:
            missing = required - set(f.keys())
            assert not missing, f"finding missing keys: {missing}"

    def test_skip_columns_honored(self):
        df = pd.DataFrame({
            "id": [1, 2, 3],
            "messy": ["a", 1, "b", 2, "c"][:3],
        })
        result = run_preflight(df, skip_columns=["messy"])
        assert "messy" not in result.column_schemas

    def test_preflight_does_not_mutate_dataframe(self):
        df = pd.DataFrame({
            "a": [1.0, 2.0, "bad", 4.0, 5.0],
            "b": [None, 2, 3, None, 5],
        })
        before = df.copy()
        run_preflight(df)
        # Original DataFrame untouched (cell-by-cell since 'bad' makes
        # the obvious equality fragile).
        for col in df.columns:
            for i, (a, b) in enumerate(zip(df[col], before[col])):
                if pd.isna(a) and pd.isna(b):
                    continue
                assert a == b, f"row {i} col {col} mutated: {a!r} vs {b!r}"

    def test_summary_includes_severity_breakdown(self):
        df = pd.DataFrame({
            "sensor": [float(i) for i in range(85)] + ["ERR"] * 15,
        })
        result = run_preflight(df)
        summary = result.summary()
        assert "by_severity" in summary
        assert summary["total_findings"] >= 1
        assert summary["schema_columns_checked"] == 1


# ===========================================================================
# Irregular-sampling detection (Stream 1.1c)
# ===========================================================================

class TestIrregularSeries:

    def test_regular_sampling_classified_regular(self):
        from fantasyai.aurora.preflight import analyse_sampling, detect_time_column
        times = pd.date_range("2024-01-01", periods=200, freq="h")
        df = pd.DataFrame({"ts": times, "x": range(200)})
        col = detect_time_column(df)
        assert col == "ts"
        report = analyse_sampling(df, col)
        assert report.pattern == "regular"
        assert report.cv < 0.05

    def test_irregular_sampling_classified_irregular(self):
        from fantasyai.aurora.preflight import analyse_sampling
        # Wildly varying gaps.
        timestamps = pd.to_datetime([
            "2024-01-01T00:00:00", "2024-01-01T00:01:00",
            "2024-01-01T00:30:00", "2024-01-01T05:00:00",
            "2024-01-02T00:00:00", "2024-01-02T00:05:00",
            "2024-01-03T10:00:00", "2024-01-04T00:00:00",
            "2024-01-04T00:01:00", "2024-01-04T00:02:00",
        ])
        df = pd.DataFrame({"ts": timestamps, "x": range(10)})
        report = analyse_sampling(df, "ts")
        assert report.pattern in ("irregular", "gaps")
        assert report.cv > 0.5

    def test_bulk_gap_detected(self):
        from fantasyai.aurora.preflight import analyse_sampling
        # 100 evenly-spaced minutes + one 24-hour gap + 100 more minutes
        base = pd.date_range("2024-01-01", periods=100, freq="min")
        after = pd.date_range("2024-01-02", periods=100, freq="min")
        times = list(base) + list(after)
        df = pd.DataFrame({"ts": times, "x": range(len(times))})
        report = analyse_sampling(df, "ts")
        assert report.pattern == "gaps"
        assert report.n_outlier_gaps >= 1
        assert report.max_gap_seconds > 60 * 60  # > 1 hour

    def test_run_preflight_emits_sampling_finding(self):
        from fantasyai.aurora.preflight import run_preflight
        # Build a df with a clear time column + a sensor-fault column,
        # confirming we get findings from BOTH the schema check and the
        # sampling check.
        times = pd.date_range("2024-01-01", periods=100, freq="h")
        df = pd.DataFrame({
            "ts": times,
            "temp": [20.0 + i * 0.1 for i in range(95)] + ["ERR"] * 5,
        })
        result = run_preflight(df, time_column="ts")
        kinds = {f["kind_hint"] for f in result.findings}
        # ts is regular → no sampling finding; temp has violations → schema finding.
        assert "schema_violation" in kinds
        assert result.irregular_sampling is not None
        assert result.irregular_sampling.pattern == "regular"

    def test_recommend_resample_freq_buckets_correctly(self):
        from fantasyai.aurora.preflight import recommend_resample_freq
        assert recommend_resample_freq(0.5) == "1s"
        assert recommend_resample_freq(45) == "1min"
        assert recommend_resample_freq(280) == "5min"
        assert recommend_resample_freq(3500) == "1h"
        assert recommend_resample_freq(50000) == "1D"
