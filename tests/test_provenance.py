"""
Provenance ledger tests.

Covers:
* ProvenanceEntry serialization round-trips correctly
* hash_payload is deterministic across calls (same input → same hash)
* write_ledger + read_ledger preserve order and content
* load_entry returns the right entry by claim_id
* build_provenance_for_run synthesizes entries from real artifact shapes
* Empty / missing artifacts produce empty ledgers (no crash)
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

import pytest

# Make the package importable when tests run from the worktree.
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fantasyai.aurora.provenance import (
    ProvenanceEntry, write_ledger, read_ledger, load_entry, iter_ledger,
    build_provenance_for_run, LEDGER_FILENAME,
)
from fantasyai.aurora.provenance.ledger import hash_payload, hash_file


# ---------------------------------------------------------------------------
# ProvenanceEntry shape
# ---------------------------------------------------------------------------

def test_entry_to_dict_roundtrip():
    e = ProvenanceEntry(
        claim_id="anom-0001",
        claim_type="anomaly",
        claim_summary="row 47 flagged",
        method="iso_forest_v1",
        artifact_ref="deep_math.json:extensions.anomaly_attribution.points[0]",
    )
    d = e.to_dict()
    assert d["claim_id"] == "anom-0001"
    assert d["claim_type"] == "anomaly"
    assert d["claim_summary"] == "row 47 flagged"
    assert d["method"] == "iso_forest_v1"
    assert d["artifact_ref"].startswith("deep_math.json")
    assert d["timestamp"], "timestamp must be auto-populated"
    assert "Z" in d["timestamp"], "timestamp should be ISO UTC"


# ---------------------------------------------------------------------------
# Hashing
# ---------------------------------------------------------------------------

def test_hash_payload_is_deterministic():
    payload = {"b": 1, "a": [1, 2, {"x": 3}]}
    h1 = hash_payload(payload)
    h2 = hash_payload(payload)
    assert h1 == h2
    assert h1.startswith("sha256:")
    # Different content → different hash.
    assert hash_payload({"b": 1, "a": [1, 2, {"x": 4}]}) != h1
    # Key order shouldn't matter.
    assert hash_payload({"a": [1, 2, {"x": 3}], "b": 1}) == h1


def test_hash_file(tmp_path: Path):
    p = tmp_path / "data.csv"
    p.write_text("col_a,col_b\n1,2\n3,4\n", encoding="utf-8")
    h = hash_file(p)
    assert h.startswith("sha256:")
    assert len(h) == len("sha256:") + 64


# ---------------------------------------------------------------------------
# Writer / reader round-trips
# ---------------------------------------------------------------------------

def test_write_then_read(tmp_path: Path):
    entries = [
        ProvenanceEntry(claim_id="a-1", claim_type="anomaly",
                        claim_summary="x", method="m", artifact_ref="a"),
        ProvenanceEntry(claim_id="r-1", claim_type="regime",
                        claim_summary="y", method="n", artifact_ref="b"),
    ]
    write_ledger(tmp_path, entries)
    p = tmp_path / LEDGER_FILENAME
    assert p.exists()
    rows = read_ledger(tmp_path)
    assert len(rows) == 2
    assert rows[0]["claim_id"] == "a-1"
    assert rows[1]["claim_type"] == "regime"


def test_load_entry_finds_by_id(tmp_path: Path):
    entries = [ProvenanceEntry(claim_id=f"x-{i}", claim_type="t",
                               claim_summary=str(i), method="m",
                               artifact_ref="a")
               for i in range(5)]
    write_ledger(tmp_path, entries)
    found = load_entry(tmp_path, "x-3")
    assert found is not None
    assert found["claim_id"] == "x-3"
    missing = load_entry(tmp_path, "does-not-exist")
    assert missing is None


def test_iter_ledger_handles_empty_and_missing(tmp_path: Path):
    # Missing file → empty iterator, no crash.
    assert list(iter_ledger(tmp_path)) == []
    # Empty file → empty iterator.
    (tmp_path / LEDGER_FILENAME).write_text("", encoding="utf-8")
    assert list(iter_ledger(tmp_path)) == []


def test_iter_ledger_skips_malformed_lines(tmp_path: Path):
    # Mix valid and invalid; iterator should skip the bad one.
    p = tmp_path / LEDGER_FILENAME
    p.write_text(
        '{"claim_id":"good-1","claim_type":"anomaly","claim_summary":"ok","method":"m","artifact_ref":"a"}\n'
        'NOT JSON AT ALL\n'
        '{"claim_id":"good-2","claim_type":"regime","claim_summary":"ok","method":"m","artifact_ref":"b"}\n',
        encoding="utf-8",
    )
    items = list(iter_ledger(tmp_path))
    assert len(items) == 2
    assert {i["claim_id"] for i in items} == {"good-1", "good-2"}


# ---------------------------------------------------------------------------
# Synthesizer against realistic artifact shapes
# ---------------------------------------------------------------------------

def _write_run_dir(tmp_path: Path, deep_math: dict, motif: dict | None = None,
                   structure: dict | None = None, resolved: dict | None = None) -> Path:
    rd = tmp_path / "run_test"
    rd.mkdir()
    (rd / "deep_math.json").write_text(json.dumps(deep_math), encoding="utf-8")
    if motif is not None:
        (rd / "motif.json").write_text(json.dumps(motif), encoding="utf-8")
    if structure is not None:
        (rd / "structure_contract.json").write_text(json.dumps(structure), encoding="utf-8")
    if resolved is not None:
        (rd / "_RESOLVED_CONTRACT.json").write_text(json.dumps(resolved), encoding="utf-8")
    return rd


def test_build_provenance_for_run_anomalies(tmp_path: Path):
    """Anomaly attribution points → one anomaly entry each."""
    deep = {
        "extensions": {
            "anomaly_attribution": {
                "top_k": 10,
                "points": [
                    {"rank": 1, "row_id": "47", "score": 5.67,
                     "top_feature_drivers": [
                         {"feature": "precip_mm", "robust_z": 10.86, "direction": "high"},
                         {"feature": "temp_c", "robust_z": 0.48, "direction": "high"},
                     ]},
                    {"rank": 2, "row_id": "12", "score": 5.18,
                     "top_feature_drivers": [
                         {"feature": "precip_mm", "robust_z": 10.12, "direction": "high"},
                     ]},
                ],
            },
        },
    }
    rd = _write_run_dir(tmp_path, deep)
    entries = build_provenance_for_run(rd, write=True)
    anom = [e for e in entries if e["claim_type"] == "anomaly"]
    assert len(anom) == 2
    assert anom[0]["claim_id"] == "anom-0000"
    assert anom[1]["claim_id"] == "anom-0001"
    assert "row 47" in anom[0]["claim_summary"]
    assert "precip_mm" in anom[0]["claim_summary"]
    assert anom[0]["artifact_ref"].endswith("points[0]")
    # Ledger file written
    assert (rd / LEDGER_FILENAME).exists()
    # Reading via load_entry round-trips
    found = load_entry(rd, "anom-0001")
    assert found is not None and found["claim_type"] == "anomaly"


def test_build_provenance_handles_forecast_and_physics(tmp_path: Path):
    deep = {
        "target_col": "temperature_c",
        "forecasting": {
            "method": "ar1", "horizon": 24, "alpha": 0.05,
            "phi": 0.96, "sigma": 1.99,
            "forecast": [12.1, 11.7, 11.2], "lower": [8.2, 6.2, 4.7],
            "upper": [16.0, 17.1, 17.7], "note": "ok",
        },
        "physics_discovery": {
            "best": {"name": "linear_ode", "model": "dy/dt = a*y + b",
                     "params": {"a": -0.001}, "rmse_test": 5.83, "aic": 15168, "k": 2},
            "runner_ups": [
                {"name": "logistic", "rmse_test": 6.79, "k": 2},
                {"name": "damped_oscillator", "rmse_test": 9.95, "k": 2},
            ],
            "target_col": "temperature_c",
            "time_col": None,
        },
    }
    rd = _write_run_dir(tmp_path, deep)
    entries = build_provenance_for_run(rd, write=True)
    types = {e["claim_type"] for e in entries}
    assert "forecast" in types
    assert "physics" in types
    physics_entries = [e for e in entries if e["claim_type"] == "physics"]
    assert any(e["claim_id"] == "physics-best" for e in physics_entries)
    assert sum(1 for e in physics_entries if e["claim_id"].startswith("physics-ru-")) == 2


def test_build_provenance_handles_empty_artifacts(tmp_path: Path):
    """No artifacts → empty ledger, no crashes."""
    rd = tmp_path / "empty_run"
    rd.mkdir()
    entries = build_provenance_for_run(rd, write=True)
    assert entries == []
    # Ledger should NOT be written when there are no entries (per builder logic).
    assert not (rd / LEDGER_FILENAME).exists()


def test_build_provenance_skips_failed_blocks(tmp_path: Path):
    """Blocks with note='failed' or 'skipped' shouldn't produce entries."""
    deep = {
        "forecasting": {"note": "failed", "error": "no time axis"},
        "causal_what_if": {"note": "skipped"},
        "physics_discovery": {},  # empty
        "regimes": {"note": "failed", "error": "not enough rows"},
    }
    rd = _write_run_dir(tmp_path, deep)
    entries = build_provenance_for_run(rd, write=True)
    # No forecast/causal/physics/regime entries should appear.
    assert all(e["claim_type"] not in ("forecast", "causal", "regime") for e in entries)
