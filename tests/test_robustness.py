"""
Robustness suite — verify Aurora handles the demo fixture datasets gracefully.

For each fixture we assert:
  1. The multi-format loader reads the file without crashing
  2. Shape & basic metadata match expectations (rows, cols, encoding)
  3. The format is detected correctly
  4. The structure-contract path resolves to a non-empty column list
  5. state_builder handles a synthetic minimal artifact set built from the
     fixture's shape — no panel renders empty without an explanation

This is intentionally a *smoke* suite, not a full pipeline run. Running the
real subprocess on every fixture in CI is expensive; we test the critical
read + shape paths here and reserve full-pipeline tests for a separate
``-m slow`` mark (future).

When a new fixture is added to ``data/fixtures/``, register it in
FIXTURE_EXPECTATIONS below.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fantasyai.aurora.io.loader import load_dataset, supported_formats
from fantasyai.aurora.state_builder import build_state


FIXTURES_DIR = ROOT / "data" / "fixtures"


# ---------------------------------------------------------------------------
# Fixture expectations — what each demo dataset MUST satisfy
# ---------------------------------------------------------------------------

FIXTURE_EXPECTATIONS = {
    "climate_buoy_demo.csv": {
        "format": "csv",
        "min_rows": 360,
        "expected_cols": [
            "date", "temperature_c", "precip_mm",
            "humidity_pct", "barometric_pressure_hpa", "station_status",
        ],
        "has_time_axis": True,    # date column should be parseable
        "has_categorical": True,  # station_status
        "min_size_bytes": 5_000,
    },
    "factory_bearing_demo.csv": {
        "format": "csv",
        "min_rows": 990,
        "expected_cols": [
            "timestamp_s", "vibration_g", "motor_temp_c", "rpm", "bearing_load_kn",
        ],
        "has_time_axis": True,
        "has_categorical": False,
        "min_size_bytes": 20_000,
    },
    "patient_cohort_demo.csv": {
        "format": "csv",
        "min_rows": 195,
        "expected_cols": [
            "patient_id", "age", "sex", "bmi", "cholesterol_mg_dl",
            "blood_pressure_sys", "biomarker_x", "treatment_group", "adverse_event",
        ],
        "has_time_axis": False,   # cross-sectional
        "has_categorical": True,  # sex, treatment_group, adverse_event
        "min_size_bytes": 5_000,
    },
}


# ---------------------------------------------------------------------------
# Fixture availability
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def fixtures_dir() -> Path:
    if not FIXTURES_DIR.exists():
        pytest.skip(
            f"data/fixtures/ not present — run "
            f"'python tools/gen_demo_data.py' from the repo root"
        )
    return FIXTURES_DIR


# ---------------------------------------------------------------------------
# Per-fixture loader tests — parameterized
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("fixture_name", list(FIXTURE_EXPECTATIONS.keys()))
def test_fixture_exists_and_meets_size(fixtures_dir: Path, fixture_name: str):
    p = fixtures_dir / fixture_name
    assert p.exists(), f"missing fixture: {fixture_name}"
    expected = FIXTURE_EXPECTATIONS[fixture_name]
    assert p.stat().st_size >= expected["min_size_bytes"], \
        f"{fixture_name} is too small ({p.stat().st_size} bytes)"


@pytest.mark.parametrize("fixture_name", list(FIXTURE_EXPECTATIONS.keys()))
def test_fixture_loads_via_loader(fixtures_dir: Path, fixture_name: str):
    p = fixtures_dir / fixture_name
    expected = FIXTURE_EXPECTATIONS[fixture_name]
    res = load_dataset(p)
    # Format detection
    assert res.format == expected["format"], \
        f"{fixture_name}: detected {res.format!r}, expected {expected['format']!r}"
    # Row count
    assert res.rows >= expected["min_rows"], \
        f"{fixture_name}: only {res.rows} rows, expected ≥ {expected['min_rows']}"
    # Column set
    assert set(res.df.columns) == set(expected["expected_cols"]), \
        f"{fixture_name}: cols mismatch — got {set(res.df.columns)}"
    # Encoding should be detected
    assert res.encoding, f"{fixture_name}: no encoding detected"


@pytest.mark.parametrize("fixture_name", list(FIXTURE_EXPECTATIONS.keys()))
def test_fixture_categorical_or_numeric_signal(fixtures_dir: Path, fixture_name: str):
    """If we expect categorical columns, at least one should have non-numeric dtype."""
    p = fixtures_dir / fixture_name
    expected = FIXTURE_EXPECTATIONS[fixture_name]
    res = load_dataset(p)
    object_cols = [c for c in res.df.columns if res.df[c].dtype == object]
    if expected["has_categorical"]:
        assert object_cols, f"{fixture_name}: expected at least one categorical-ish column"


# ---------------------------------------------------------------------------
# state_builder smoke — synthetic minimal artifacts per fixture
# ---------------------------------------------------------------------------

def _write_minimal_run_dir(tmp_path: Path, fixture_name: str) -> Path:
    """Build a minimal but valid set of artifacts that the state_builder can read.

    We don't run the actual subprocess here — that's too expensive for CI. Instead,
    we synthesize the artifact JSONs that the runner *would* have produced for
    a successful run, and verify state_builder handles every shape gracefully.
    """
    expected = FIXTURE_EXPECTATIONS[fixture_name]
    rd = tmp_path / f"smoke_{fixture_name.replace('.csv', '')}"
    rd.mkdir()

    # structure_contract.json
    columns = []
    for c in expected["expected_cols"]:
        kind_role = "time" if c.lower() in ("date", "timestamp", "timestamp_s", "time") else "numeric"
        columns.append({
            "name": c,
            "role": kind_role,
            "dtype": "object" if c in ("station_status", "sex", "treatment_group",
                                       "adverse_event", "patient_id") else "float64",
            "missing_rate": 0.01,
            "datetime_parse_rate": 0.99 if kind_role == "time" else 0.0,
        })
    structure = {
        "version": "structure_contract_v1",
        "shape": {"rows": expected["min_rows"], "cols": len(expected["expected_cols"])},
        "eligible": {
            "math_eligible": True,
            "deep_math_eligible": True,
            "time_series_eligible": expected["has_time_axis"],
            "geo_eligible": False,
        },
        "columns": columns,
        "time": {"best_time_col": "date" if "date" in expected["expected_cols"]
                                  else ("timestamp_s" if "timestamp_s" in expected["expected_cols"]
                                        else None)},
        "time_col": "date" if "date" in expected["expected_cols"]
                    else ("timestamp_s" if "timestamp_s" in expected["expected_cols"] else None),
        "target_col": "temperature_c" if "temperature_c" in expected["expected_cols"]
                      else ("vibration_g" if "vibration_g" in expected["expected_cols"]
                            else "biomarker_x"),
        "n_rows": expected["min_rows"],
        "n_cols": len(expected["expected_cols"]),
        "has_time_axis": expected["has_time_axis"],
    }
    (rd / "structure_contract.json").write_text(json.dumps(structure))

    # _RUN_META.json
    meta = {
        "time_col": structure["time_col"],
        "target_col": structure["target_col"],
        "dt_seconds": 86400 if expected["has_time_axis"] else None,
        "n_rows": structure["n_rows"], "n_cols": structure["n_cols"],
        "numeric_cols": [c for c in structure["columns"] if c["dtype"] != "object"],
    }
    (rd / "_RUN_META.json").write_text(json.dumps(meta))

    # _RESOLVED_CONTRACT.json
    resolved = {**meta, "version": "resolved_contract_v1",
                "run_dir": str(rd),
                "dataset_key": fixture_name,
                "has_time_axis": expected["has_time_axis"],
                "numeric_col_count": len(meta["numeric_cols"])}
    (rd / "_RESOLVED_CONTRACT.json").write_text(json.dumps(resolved))

    # Minimal deep_math.json — empty extensions, but valid skeleton.
    deep_math = {
        "version": "deep_math_v3",
        "rows": meta["n_rows"], "cols": meta["n_cols"],
        "target_col": meta["target_col"],
        "time_col": meta["time_col"],
        "numeric_cols_used": [c["name"] for c in meta["numeric_cols"]],
        "extensions": {"anomaly_attribution": {"top_k": 0, "points": []}},
    }
    (rd / "deep_math.json").write_text(json.dumps(deep_math))

    # report.json with score
    report = {
        "ok": True, "plan_id": "smoke", "score": {"run_quality": 0.5, "confidence": 0.5},
        "events": [], "results": [], "artifacts_present": [],
        "run_dir": str(rd), "inputs": {},
    }
    (rd / "report.json").write_text(json.dumps(report))

    # Empty motif.json + _PLOTS_INDEX.json
    (rd / "motif.json").write_text(json.dumps({"version": "motif_v1", "motifs": [], "summary": {}}))
    (rd / "_PLOTS_INDEX.json").write_text(json.dumps({"version": "plots_v2", "plots": []}))
    return rd


@pytest.mark.parametrize("fixture_name", list(FIXTURE_EXPECTATIONS.keys()))
def test_state_builder_smoke_for_fixture(tmp_path: Path, fixture_name: str):
    """state_builder must handle each fixture's expected shape without crashing,
    and every section must populate or honestly say 'available: false' with a reason."""
    rd = _write_minimal_run_dir(tmp_path, fixture_name)
    state = build_state(rd)
    # Top-level shape
    assert isinstance(state, dict)
    assert "dataset" in state
    assert "structure" in state
    assert "overview" in state
    assert "anomalies" in state
    assert "forecast" in state
    assert "causal" in state
    assert "physics" in state
    assert "regimes" in state
    assert "motifs" in state
    assert "findings" in state
    assert "copilot" in state

    # Honest-mode assertion: any section reporting available=False must include a reason.
    for section_name in ("dataset", "structure", "overview", "forecast",
                         "causal", "physics", "copilot"):
        section = state[section_name]
        if isinstance(section, dict) and section.get("available") is False:
            assert section.get("reason"), (
                f"{fixture_name}: {section_name} reports unavailable but no reason given"
            )

    # Structure must reflect the fixture's basic shape.
    assert state["structure"]["available"] is True
    assert len(state["structure"]["columns"]) == len(FIXTURE_EXPECTATIONS[fixture_name]["expected_cols"])

    # Provenance ledger should have been built lazily.
    assert state.get("provenance_available") is True or state.get("provenance_available") is False
    # ↑ Either is acceptable for an empty deep_math; we just verify the key exists.


# ---------------------------------------------------------------------------
# Loader format coverage
# ---------------------------------------------------------------------------

def test_supported_formats_includes_all_main_extensions():
    formats = supported_formats()
    for ext in (".csv", ".tsv", ".json", ".jsonl", ".parquet", ".xlsx", ".gz"):
        assert ext in formats, f"loader missing support for {ext}"
