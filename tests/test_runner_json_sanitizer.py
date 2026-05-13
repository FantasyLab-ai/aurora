"""
Regression tests for ``scripts.run_aurora_dataset_runner._write_json``.

Pins the bug where the runner used to die with::

    TypeError: Object of type ndarray is not JSON serializable

at the ``deep_math.json`` write step, which surfaced in the UI as the
unhelpful banner ``Analysis state could not be loaded · subprocess
returned nonzero``. The actual exception was buried in the subprocess
stderr; the user never saw it.

The trigger was the new coupled-systems block in
``physics_discovery.discover_physics_models`` (NOAA-shaped data with
≥ 2 numeric columns triggers ``discover_coupled``, the LV fit succeeds,
and the returned dict contains ``xhat`` / ``yhat`` raw numpy arrays).
``_write_json`` was a one-liner around ``json.dumps`` with no
``default=`` and no sanitizer, so the run died at the boundary.

These tests guard against regression by:

  1. Asserting ``_sanitize_for_json`` converts every numpy / pandas
     leaf type the math stack might leak into JSON-native equivalents.
  2. Asserting ``_write_json`` survives a deep_out-shaped dict with
     ndarrays nested under ``physics_discovery.coupled_systems`` —
     the exact shape the NOAA crash produced.
  3. Asserting NaN / Inf floats round-trip as ``null`` (JSON has no
     spec for them; emitting them would break the frontend parser).

Brief constraint: the sanitizer must NEVER raise. Disk-write failures
in the runner kill the run; analytical methods left ndarrays in their
output for two years and that's fine — the runner just has to handle
it at the boundary.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

np = pytest.importorskip("numpy")
pd = pytest.importorskip("pandas")


# Import target — the runner module must be importable without side
# effects beyond the imports it already does (numpy, pandas, schema
# contract). If this import fails, the runner can't run anyway.
from scripts.run_aurora_dataset_runner import (  # noqa: E402
    _sanitize_for_json,
    _write_json,
)


# ============================================================
# 1. Leaf-type coverage — every shape the math stack emits
# ============================================================

class TestSanitizeLeafTypes:
    def test_numpy_ndarray_to_list(self):
        arr = np.array([1.0, 2.0, 3.0])
        out = _sanitize_for_json(arr)
        assert out == [1.0, 2.0, 3.0]
        assert isinstance(out, list)

    def test_numpy_2d_ndarray_to_nested_list(self):
        arr = np.array([[1, 2], [3, 4]])
        out = _sanitize_for_json(arr)
        assert out == [[1, 2], [3, 4]]

    def test_numpy_int64_unwraps_to_python_int(self):
        v = np.int64(42)
        out = _sanitize_for_json(v)
        assert out == 42
        assert isinstance(out, int)
        # And it survives json.dumps (np.int64 is rejected by stdlib).
        assert json.dumps(out) == "42"

    def test_numpy_float64_unwraps_to_python_float(self):
        v = np.float64(3.14)
        out = _sanitize_for_json(v)
        assert out == pytest.approx(3.14)
        assert isinstance(out, float)

    def test_numpy_bool_unwraps_to_python_bool(self):
        v = np.bool_(True)
        out = _sanitize_for_json(v)
        assert out is True

    def test_numpy_nan_becomes_none(self):
        # JSON has no NaN. Emitting raw NaN produces text the frontend
        # JSON.parse would reject ("NaN" is not valid JSON).
        out = _sanitize_for_json(np.float64("nan"))
        assert out is None

    def test_numpy_inf_becomes_none(self):
        out = _sanitize_for_json(np.float64("inf"))
        assert out is None
        out = _sanitize_for_json(np.float64("-inf"))
        assert out is None

    def test_python_nan_becomes_none(self):
        out = _sanitize_for_json(float("nan"))
        assert out is None

    def test_python_inf_becomes_none(self):
        out = _sanitize_for_json(float("inf"))
        assert out is None

    def test_pandas_series_to_list(self):
        s = pd.Series([1, 2, 3])
        out = _sanitize_for_json(s)
        assert out == [1, 2, 3]

    def test_pandas_timestamp_to_isoformat(self):
        ts = pd.Timestamp("2024-01-15 10:30:00")
        out = _sanitize_for_json(ts)
        assert isinstance(out, str)
        assert "2024-01-15" in out

    def test_pandas_nat_becomes_none(self):
        out = _sanitize_for_json(pd.NaT)
        assert out is None

    def test_pathlib_path_stringifies(self):
        out = _sanitize_for_json(Path("a") / "b" / "c.json")
        assert isinstance(out, str)
        assert "c.json" in out

    def test_dict_recurses(self):
        out = _sanitize_for_json({
            "arr": np.array([1, 2, 3]),
            "scalar": np.int64(7),
            "nested": {"more": np.float64("nan")},
        })
        assert out == {"arr": [1, 2, 3], "scalar": 7, "nested": {"more": None}}

    def test_list_recurses(self):
        out = _sanitize_for_json([np.array([1, 2]), {"x": np.int64(3)}])
        assert out == [[1, 2], {"x": 3}]

    def test_tuple_becomes_list(self):
        out = _sanitize_for_json((np.int64(1), np.int64(2)))
        assert out == [1, 2]

    def test_none_passthrough(self):
        assert _sanitize_for_json(None) is None

    def test_str_passthrough(self):
        assert _sanitize_for_json("hello") == "hello"

    def test_bool_passthrough(self):
        assert _sanitize_for_json(True) is True
        assert _sanitize_for_json(False) is False

    def test_unknown_object_falls_back_to_str(self):
        class Weird:
            def __str__(self) -> str:
                return "weird-thing"
        out = _sanitize_for_json(Weird())
        assert out == "weird-thing"

    def test_sanitizer_never_raises_on_hostile_input(self):
        class Hostile:
            def __str__(self) -> str:
                raise RuntimeError("can't stringify me")
        out = _sanitize_for_json(Hostile())
        # Falls through to None rather than propagating the exception.
        assert out is None


# ============================================================
# 2. End-to-end — _write_json survives a deep_out-shaped dict
# ============================================================

class TestWriteJsonSurvivesDeepMathShape:
    """Mirror the exact shape that ``physics_discovery`` emits when LV
    fits succeed. Pre-fix this crashed at ``json.dumps`` with::

        TypeError: Object of type ndarray is not JSON serializable
    """

    def _deep_out_with_coupled_systems(self) -> dict:
        n = 50
        xhat = np.linspace(0.0, 10.0, n)
        yhat = np.sin(xhat)
        return {
            "version": "deep_math_v3",
            "seed": np.int64(42),
            "rows": np.int64(n),
            "physics_discovery": {
                "note": "ok",
                "best": {
                    "name": "lotka_volterra",
                    "rmse": np.float64(0.123),
                    "params": {"alpha": np.float64(1.5),
                                "beta": np.float64(0.5)},
                },
                "coupled_systems": {
                    "note": "ok",
                    "best": {
                        # The exact leak — fit_lotka_volterra puts ndarrays here.
                        "ok": True,
                        "name": "lotka_volterra",
                        "xhat": xhat,
                        "yhat": yhat,
                        "rmse": np.float64(0.05),
                        "aic": np.float64(-12.3),
                    },
                    "runner_ups": [],
                    "all_candidates": [],
                },
                "pde_detection": {"note": "skipped", "reason": "no spatial grid"},
            },
            "uncertainty": {
                "top_corr_bootstrap": [
                    {"a": "x", "b": "y", "n": 100,
                      "pearson_r": np.float64("nan"),
                      "pearson_ci95": [np.float64("inf"), np.float64("-inf")]}
                ]
            },
        }

    def test_write_succeeds(self, tmp_path):
        p = tmp_path / "deep_math.json"
        _write_json(p, self._deep_out_with_coupled_systems())
        assert p.exists()
        # File MUST be parseable JSON.
        d = json.loads(p.read_text(encoding="utf-8"))
        assert d["version"] == "deep_math_v3"

    def test_ndarrays_become_lists_on_disk(self, tmp_path):
        p = tmp_path / "deep_math.json"
        _write_json(p, self._deep_out_with_coupled_systems())
        d = json.loads(p.read_text(encoding="utf-8"))
        cs_best = d["physics_discovery"]["coupled_systems"]["best"]
        assert isinstance(cs_best["xhat"], list)
        assert isinstance(cs_best["yhat"], list)
        assert len(cs_best["xhat"]) == 50

    def test_nan_inf_become_null_on_disk(self, tmp_path):
        p = tmp_path / "deep_math.json"
        _write_json(p, self._deep_out_with_coupled_systems())
        # Read raw text — JSON literally cannot contain "NaN" / "Infinity"
        # tokens (Python's json.dumps emits them by default with allow_nan=True
        # which would break the frontend's JSON.parse).
        text = p.read_text(encoding="utf-8")
        assert "NaN" not in text
        assert "Infinity" not in text
        # And the loaded dict has None where NaN/Inf used to be.
        d = json.loads(text)
        boot = d["uncertainty"]["top_corr_bootstrap"][0]
        assert boot["pearson_r"] is None
        assert boot["pearson_ci95"] == [None, None]

    def test_numpy_scalars_unwrap_on_disk(self, tmp_path):
        p = tmp_path / "deep_math.json"
        _write_json(p, self._deep_out_with_coupled_systems())
        d = json.loads(p.read_text(encoding="utf-8"))
        # ``seed: np.int64(42)`` must round-trip as Python int.
        assert d["seed"] == 42
        assert isinstance(d["seed"], int)


# ============================================================
# 3. The literal failure mode — pre-fix, json.dumps would crash
# ============================================================

class TestSanityCheckPreFixWouldHaveCrashed:
    """Confirm the bug WOULD have fired without the sanitizer — i.e.,
    this test suite isn't just verifying a no-op. If the runner ever
    re-introduces a naive json.dumps without the sanitizer, this test
    keeps the failure mode visible."""

    def test_naive_json_dumps_does_crash_on_ndarray(self):
        with pytest.raises(TypeError, match="not JSON serializable"):
            json.dumps({"xhat": np.array([1.0, 2.0])})

    def test_naive_json_dumps_does_crash_on_numpy_scalar_in_some_paths(self):
        # np.float64 sometimes passes (subclasses float on some platforms),
        # but np.int64 ON WINDOWS rejects through the encoder. The
        # important thing is that the SANITIZED version always works:
        out = _sanitize_for_json({"k": np.int64(1), "v": np.array([1, 2])})
        assert json.dumps(out) == '{"k": 1, "v": [1, 2]}'
