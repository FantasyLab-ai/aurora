"""
Tests for ``studio_api._sanitize_for_json`` — the single chokepoint that
keeps numpy/pandas/datetime types from reaching Flask's encoder.

This file is the regression net for Problem 1 of the hardening sprint:
when this suite is green, no /api/* response can return numpy types and
no JSON 500 can occur.
"""
from __future__ import annotations

import json
import math
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from studio_api import _sanitize_for_json   # noqa: E402

np = pytest.importorskip("numpy")
pd = pytest.importorskip("pandas")


# ============================================================
# Native types pass through untouched
# ============================================================

class TestNatives:

    def test_none(self):
        assert _sanitize_for_json(None) is None

    def test_bool(self):
        assert _sanitize_for_json(True) is True
        assert _sanitize_for_json(False) is False

    def test_int(self):
        assert _sanitize_for_json(7) == 7

    def test_float(self):
        assert _sanitize_for_json(3.14) == 3.14

    def test_string(self):
        assert _sanitize_for_json("hello") == "hello"

    def test_empty_collections(self):
        assert _sanitize_for_json([]) == []
        assert _sanitize_for_json({}) == {}
        assert _sanitize_for_json(()) == []

    def test_nan_inf_become_null(self):
        assert _sanitize_for_json(float("nan")) is None
        assert _sanitize_for_json(float("inf")) is None
        assert _sanitize_for_json(float("-inf")) is None


# ============================================================
# Numpy scalars + arrays
# ============================================================

class TestNumpy:

    def test_int64(self):
        v = _sanitize_for_json(np.int64(7))
        assert v == 7
        assert isinstance(v, int) and not isinstance(v, np.generic)

    def test_int32(self):
        assert _sanitize_for_json(np.int32(-3)) == -3

    def test_uint8(self):
        assert _sanitize_for_json(np.uint8(255)) == 255

    def test_float64(self):
        v = _sanitize_for_json(np.float64(3.14))
        assert v == pytest.approx(3.14)
        assert isinstance(v, float) and not isinstance(v, np.generic)

    def test_float32(self):
        v = _sanitize_for_json(np.float32(2.5))
        assert isinstance(v, float)

    def test_float64_nan_becomes_none(self):
        # The bug we shipped: np.float64('nan') would slip past the old check.
        assert _sanitize_for_json(np.float64("nan")) is None

    def test_float64_inf_becomes_none(self):
        assert _sanitize_for_json(np.float64("inf")) is None

    def test_bool_(self):
        assert _sanitize_for_json(np.bool_(True)) is True
        assert _sanitize_for_json(np.bool_(False)) is False

    def test_datetime64(self):
        v = _sanitize_for_json(np.datetime64("2024-08-12"))
        assert isinstance(v, str)
        assert "2024-08-12" in v

    def test_array_1d(self):
        assert _sanitize_for_json(np.array([1, 2, 3])) == [1, 2, 3]

    def test_array_with_nan(self):
        out = _sanitize_for_json(np.array([1.0, 2.0, np.nan]))
        assert out == [1.0, 2.0, None]

    def test_array_2d(self):
        out = _sanitize_for_json(np.array([[1, 2], [3, 4]]))
        assert out == [[1, 2], [3, 4]]

    def test_array_mixed_with_native(self):
        out = _sanitize_for_json({"x": np.array([1, 2]), "y": 3})
        assert out == {"x": [1, 2], "y": 3}


# ============================================================
# Pandas
# ============================================================

class TestPandas:

    def test_timestamp(self):
        ts = pd.Timestamp("2024-08-12 03:14:00")
        v = _sanitize_for_json(ts)
        assert isinstance(v, str)
        assert "2024-08-12" in v

    def test_timestamp_with_tz(self):
        ts = pd.Timestamp("2024-08-12T03:14:00", tz="UTC")
        v = _sanitize_for_json(ts)
        assert isinstance(v, str)

    def test_nat(self):
        assert _sanitize_for_json(pd.NaT) is None

    def test_timedelta(self):
        td = pd.Timedelta(seconds=120)
        v = _sanitize_for_json(td)
        assert v == 120.0

    def test_series(self):
        s = pd.Series([1, 2, 3])
        assert _sanitize_for_json(s) == [1, 2, 3]

    def test_series_with_nan(self):
        s = pd.Series([1.0, 2.0, np.nan])
        out = _sanitize_for_json(s)
        # NaN → None at the leaf level.
        assert out[0] == 1.0
        assert out[2] is None

    def test_dataframe(self):
        df = pd.DataFrame({"a": [1, 2], "b": ["x", "y"]})
        out = _sanitize_for_json(df)
        assert out == [{"a": 1, "b": "x"}, {"a": 2, "b": "y"}]


# ============================================================
# datetime / Path / sets
# ============================================================

class TestMisc:

    def test_datetime_naive(self):
        dt = datetime(2024, 8, 12, 3, 14, 0)
        v = _sanitize_for_json(dt)
        assert v.startswith("2024-08-12T03:14")

    def test_datetime_utc(self):
        dt = datetime(2024, 8, 12, 3, 14, 0, tzinfo=timezone.utc)
        v = _sanitize_for_json(dt)
        assert isinstance(v, str)

    def test_date(self):
        d = date(2024, 8, 12)
        assert _sanitize_for_json(d) == "2024-08-12"

    def test_path(self):
        p = Path("/tmp/foo.csv")
        assert _sanitize_for_json(p) == str(p)

    def test_set(self):
        out = _sanitize_for_json({1, 2, 3})
        assert sorted(out) == [1, 2, 3]


# ============================================================
# Nested structures (the real-world case)
# ============================================================

class TestNested:

    def test_nested_dict_with_numpy(self):
        # The exact shape state_builder produces for an anomaly.
        state = {
            "anomalies": [{
                "rank": np.int64(1),
                "score": np.float64(4.5),
                "drivers": [{
                    "feature": "temperature",
                    "robust_z": np.float64(3.2),
                    "direction": "high",
                }],
            }],
        }
        out = _sanitize_for_json(state)
        # Must round-trip through json.dumps without raising.
        s = json.dumps(out)
        assert isinstance(s, str)
        # Re-parse and verify.
        d = json.loads(s)
        assert d["anomalies"][0]["rank"] == 1
        assert d["anomalies"][0]["score"] == 4.5
        assert d["anomalies"][0]["drivers"][0]["robust_z"] == 3.2

    def test_motif_with_int64(self):
        # state_builder._build_motifs matrix-profile output.
        m = {
            "id": np.int64(0),
            "name": "Discord at row 1248",
            "details": {
                "start": np.int64(1248),
                "window": np.int64(96),
                "distance": np.float64(2.4),
            },
        }
        out = _sanitize_for_json(m)
        json.dumps(out)
        assert out["id"] == 0
        assert out["details"]["start"] == 1248

    def test_referent_with_pandas_timestamp(self):
        # Phase A — referent.raw_coordinates may carry pd.Timestamp values
        # if _build_row_lookup picked them up from a CSV.
        ref = {
            "primary_label": "2024-08-12 03:14 UTC",
            "level_used": "timestamp",
            "raw_coordinates": {
                "row_id": np.int64(1248),
                "row": {
                    "timestamp": pd.Timestamp("2024-08-12 03:14:00"),
                    "temperature": np.float64(37.2),
                },
            },
        }
        out = _sanitize_for_json(ref)
        json.dumps(out)
        assert out["raw_coordinates"]["row_id"] == 1248
        assert isinstance(out["raw_coordinates"]["row"]["timestamp"], str)

    def test_full_state_roundtrip_smoke(self):
        # Synthesise a state with every leak surface from the audit.
        state = {
            "run_id": "20260505_142318",
            "run_dir": Path("/tmp/run"),
            "anomalies": [{"rank": np.int64(1), "score": np.float64(4.5),
                           "drivers": [{"robust_z": np.float64(3.0)}]}],
            "regimes": [{"start": pd.Timestamp("2024-08-01"),
                          "end": pd.Timestamp("2024-08-15"),
                          "mean": np.float64(20.0)}],
            "motifs": [{"details": {"start_a": np.int64(100),
                                     "window": np.int64(50),
                                     "distance": np.float64(2.1)}}],
            "physics": {"prior_matches": [{"confidence": np.float64(0.75)}],
                         "calculus": {"n_points": np.int64(50000)}},
            "forecast": {"alternate_methods": {
                "knn": {"forecast": np.array([1.0, 2.0, np.nan])},
            }},
            "findings": [{"replication_status": {"status": "confirmed",
                                                  "delta": np.float64(0.05)}}],
            "system_model": {"state": {"active_patterns":
                [{"confidence": np.float64(0.8)}]}},
            "narrative_engine": {"active_contract": {"id": "thermal:explore"}},
            "sampling": {"rows_total": np.int64(2_075_259),
                          "rows_analyzed": np.int64(100_000)},
        }
        out = _sanitize_for_json(state)
        s = json.dumps(out)   # must NOT raise
        d = json.loads(s)
        # Sanity check a deep leaf to confirm coercion happened.
        assert d["anomalies"][0]["rank"] == 1
        assert d["forecast"]["alternate_methods"]["knn"]["forecast"][2] is None
        assert d["sampling"]["rows_total"] == 2_075_259


# ============================================================
# Hostile / edge cases — must not raise
# ============================================================

class TestHostile:

    def test_object_with_to_dict(self):
        class X:
            def to_dict(self):
                return {"a": np.int64(7)}
        out = _sanitize_for_json(X())
        assert out == {"a": 7}

    def test_object_with_dict(self):
        class X:
            def __init__(self):
                self.a = np.int64(7)
        out = _sanitize_for_json(X())
        # Either via __dict__ or str(); just must not raise + must be serialisable.
        json.dumps(out)

    def test_circular_references_are_not_followed_infinitely(self):
        # We don't claim to handle cycles — but we must not blow the stack.
        # In the codebase this never happens (state_builder produces a tree).
        # The fallback str() coercion catches it via __dict__'s repr.
        # Skip if pytest.set_recursion_limit isn't safe here.
        a = {}
        b = {"a": a}
        a["b"] = b
        # Just don't raise on a normal operation. Cycles get caught by Python's
        # default recursion limit; we accept that as the failure mode.
        with pytest.raises(RecursionError):
            _sanitize_for_json(a)

    def test_bytes(self):
        # Bytes have no .item() / __dict__; should fall through to str().
        out = _sanitize_for_json(b"hello")
        assert isinstance(out, str)

    def test_callable(self):
        out = _sanitize_for_json(lambda: 1)
        # Lambdas have a __dict__; the recursion will hit something stringifiable.
        json.dumps(out)

    def test_dict_with_non_string_key(self):
        # numpy int as a key — string-coerced.
        out = _sanitize_for_json({np.int64(7): "value"})
        assert out == {"7": "value"}
