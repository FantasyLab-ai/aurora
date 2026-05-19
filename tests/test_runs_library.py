"""
Tests for the v1.2-E Runs Library:
    * Pinning — pin/unpin/list, per-workspace isolation, cap enforcement
    * Compare — RunComparison shape, delta block, new/disappeared findings
    * Share   — bundle export to a portable file
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List

import pytest


# ----------------------------------------------------------------------
# Helpers — fake run dir builder (smaller than the Q3 helper)
# ----------------------------------------------------------------------

def _make_run(tmp_path: Path,
              *,
              run_id: str,
              dataset: str = "demo.csv",
              physics_law: str = "logistic",
              hmm_k: int = 3,
              top_anomalies: List[Dict[str, Any]] = None,
              extended_methods: Dict[str, Any] = None,
              confidence: float = 0.82,
              with_bundle: bool = False) -> Path:
    rd = tmp_path / run_id
    rd.mkdir(parents=True, exist_ok=True)
    (rd / "_RUN_META.json").write_text(json.dumps({
        "run_id": run_id, "target_col": "temperature_c",
        "dataset_name": dataset, "n_rows": 1000, "dt_seconds": 60,
    }))
    (rd / "report.json").write_text(json.dumps({
        "score": {"confidence": confidence,
                   "stability_good": True, "drift_good": True},
        "events": [], "fabricated_count": 0,
    }))
    (rd / "deep_math.json").write_text(json.dumps({
        "physics_discovery": {"best": {"name": physics_law, "rmse_test": 1.5}},
        "hmm": {"available": True, "best_K": hmm_k,
                 "means_sorted": [15.0, 22.0, 28.0][:hmm_k]},
    }))
    (rd / "math_results.json").write_text(json.dumps({
        "top_anomalies": top_anomalies or [],
    }))
    if extended_methods is not None:
        (rd / "extended_methods.json").write_text(json.dumps(extended_methods))
    if with_bundle:
        (rd / "aurora.bundle.json").write_text(json.dumps({
            "run": {"run_id": run_id},
            "dataset": {"name": dataset, "sha256": "abc123"},
            "findings": [],
            "integrity": {"content_hash": "deadbeef" * 8},
        }))
    return rd


# ----------------------------------------------------------------------
# Pinning
# ----------------------------------------------------------------------

class TestPinning:

    def test_pin_then_list_roundtrip(self, monkeypatch, tmp_path):
        from fantasyai.aurora.runs_library import pin_run, list_pinned_runs
        monkeypatch.setenv("AURORA_DATA_ROOT", str(tmp_path))
        pin_run("run-1", workspace_id="alice", note="important")
        listed = list_pinned_runs("alice")
        assert len(listed) == 1
        assert listed[0]["run_id"] == "run-1"
        assert listed[0]["note"] == "important"
        assert "pinned_at" in listed[0]

    def test_pin_is_idempotent_and_refreshes(self, monkeypatch, tmp_path):
        from fantasyai.aurora.runs_library import (
            pin_run, list_pinned_runs, is_pinned,
        )
        monkeypatch.setenv("AURORA_DATA_ROOT", str(tmp_path))
        e1 = pin_run("run-1", workspace_id="alice", note="first")
        e2 = pin_run("run-1", workspace_id="alice", note="second")
        assert is_pinned("run-1", "alice")
        listed = list_pinned_runs("alice")
        assert len(listed) == 1
        assert listed[0]["note"] == "second"
        # pinned_at refreshed (>= original).
        assert float(e2.get("pinned_at") or 0) >= float(e1.get("pinned_at") or 0)

    def test_unpin_removes_entry(self, monkeypatch, tmp_path):
        from fantasyai.aurora.runs_library import (
            pin_run, unpin_run, is_pinned, list_pinned_runs,
        )
        monkeypatch.setenv("AURORA_DATA_ROOT", str(tmp_path))
        pin_run("run-1", workspace_id="alice")
        assert is_pinned("run-1", "alice")
        changed = unpin_run("run-1", workspace_id="alice")
        assert changed is True
        assert not is_pinned("run-1", "alice")
        # Idempotent — unpinning twice doesn't raise.
        assert unpin_run("run-1", workspace_id="alice") is False

    def test_pin_workspace_isolation(self, monkeypatch, tmp_path):
        from fantasyai.aurora.runs_library import (
            pin_run, list_pinned_runs, is_pinned,
        )
        monkeypatch.setenv("AURORA_DATA_ROOT", str(tmp_path))
        pin_run("run-A", workspace_id="alice")
        pin_run("run-B", workspace_id="bob")
        assert is_pinned("run-A", "alice")
        assert not is_pinned("run-A", "bob")
        assert is_pinned("run-B", "bob")
        assert not is_pinned("run-B", "alice")

    def test_pin_cap_enforced(self, monkeypatch, tmp_path):
        from fantasyai.aurora.runs_library import (
            pin_run, list_pinned_runs,
        )
        from fantasyai.aurora.runs_library.pinned import MAX_PINNED
        monkeypatch.setenv("AURORA_DATA_ROOT", str(tmp_path))
        for i in range(MAX_PINNED + 25):
            pin_run(f"run-{i:04d}", workspace_id="alice")
        listed = list_pinned_runs("alice")
        assert len(listed) == MAX_PINNED

    def test_pin_returns_entry_with_run_id(self, monkeypatch, tmp_path):
        from fantasyai.aurora.runs_library import pin_run
        monkeypatch.setenv("AURORA_DATA_ROOT", str(tmp_path))
        e = pin_run("run-xyz", workspace_id="alice", note="abc")
        assert e["run_id"] == "run-xyz"
        assert e["note"] == "abc"
        assert isinstance(e["pinned_at"], float)

    def test_pin_rejects_empty_run_id(self, monkeypatch, tmp_path):
        from fantasyai.aurora.runs_library import pin_run
        monkeypatch.setenv("AURORA_DATA_ROOT", str(tmp_path))
        with pytest.raises(ValueError):
            pin_run("", workspace_id="alice")


# ----------------------------------------------------------------------
# Compare
# ----------------------------------------------------------------------

class TestCompareRuns:

    def test_compare_self_refused(self, tmp_path):
        from fantasyai.aurora.runs_library import compare_runs
        rd = _make_run(tmp_path, run_id="solo")
        rep = compare_runs(rd, rd)
        assert rep.ok is False
        assert any("itself" in w for w in rep.warnings)

    def test_compare_same_dataset_same_run_id(self, tmp_path):
        from fantasyai.aurora.runs_library import compare_runs
        a = _make_run(tmp_path, run_id="r1", dataset="demo.csv",
                       physics_law="logistic", hmm_k=3, confidence=0.80)
        b = _make_run(tmp_path, run_id="r2", dataset="demo.csv",
                       physics_law="logistic", hmm_k=3, confidence=0.86)
        rep = compare_runs(a, b)
        assert rep.ok is True
        assert rep.same_dataset is True
        assert rep.summary_a["dataset"] == "demo.csv"
        assert rep.summary_b["dataset"] == "demo.csv"
        # confidence delta should round to +0.06
        assert abs((rep.delta.get("confidence_delta") or 0) - 0.06) < 1e-3
        # physics law unchanged
        assert rep.delta.get("physics_law_changed") is False
        # no join report when datasets match
        assert rep.join_report is None

    def test_compare_different_datasets_attaches_join_report(self, tmp_path):
        from fantasyai.aurora.runs_library import compare_runs
        a = _make_run(tmp_path, run_id="r1", dataset="a.csv")
        b = _make_run(tmp_path, run_id="r2", dataset="b.csv")
        rep = compare_runs(a, b)
        assert rep.ok is True
        assert rep.same_dataset is False
        assert rep.join_report is not None
        assert rep.join_report.get("ok") is True
        # Warning about different datasets surfaces.
        assert any("different datasets" in w.lower() for w in rep.warnings)

    def test_compare_detects_law_change(self, tmp_path):
        from fantasyai.aurora.runs_library import compare_runs
        a = _make_run(tmp_path, run_id="r1", physics_law="logistic")
        b = _make_run(tmp_path, run_id="r2", physics_law="exponential")
        rep = compare_runs(a, b)
        assert rep.delta.get("physics_law_changed") is True

    def test_compare_finds_new_and_disappeared_findings(self, tmp_path):
        from fantasyai.aurora.runs_library import compare_runs
        ext_a = {"findings": [
            {"method": "var", "title": "A", "severity": "info",
              "evidence": {"status": "fit", "n_obs": 50}},
            {"method": "dtw", "title": "B", "severity": "info",
              "evidence": {"status": "fit", "n_obs": 50}},
        ]}
        ext_b = {"findings": [
            {"method": "var", "title": "A", "severity": "info",
              "evidence": {"status": "fit", "n_obs": 50}},
            {"method": "kalman", "title": "C", "severity": "warn",
              "evidence": {"status": "fit", "n_obs": 50}},
        ]}
        a = _make_run(tmp_path, run_id="r1", extended_methods=ext_a)
        b = _make_run(tmp_path, run_id="r2", extended_methods=ext_b)
        rep = compare_runs(a, b)
        # Same method+title+severity → shared. dtw vs kalman → disappeared/new.
        assert rep.shared_findings_n >= 1
        assert len(rep.new_findings) >= 1
        assert len(rep.disappeared_findings) >= 1

    def test_compare_missing_dir_returns_not_ok(self, tmp_path):
        from fantasyai.aurora.runs_library import compare_runs
        rep = compare_runs(tmp_path / "no_a", tmp_path / "no_b")
        assert rep.ok is False
        assert rep.warnings


# ----------------------------------------------------------------------
# Share
# ----------------------------------------------------------------------

class TestShareBundle:

    def test_share_writes_existing_bundle(self, monkeypatch, tmp_path):
        from fantasyai.aurora.runs_library import share_run_bundle
        monkeypatch.setenv("AURORA_DATA_ROOT", str(tmp_path))
        rd = _make_run(tmp_path, run_id="r-share", with_bundle=True)
        info = share_run_bundle(rd, workspace_id="alice")
        assert info.ok is True
        assert info.local_path is not None
        out_path = Path(info.local_path)
        assert out_path.exists()
        assert out_path.suffix == ".json"
        # File contains the bundle doc.
        text = out_path.read_text(encoding="utf-8")
        doc = json.loads(text)
        assert doc["run"]["run_id"] == "r-share"
        assert info.bundle_hash is not None
        assert info.n_bytes > 0

    def test_share_run_dir_missing(self, tmp_path):
        from fantasyai.aurora.runs_library import share_run_bundle
        info = share_run_bundle(tmp_path / "no_such_run")
        assert info.ok is False
        assert "not found" in (info.error or "").lower()

    def test_share_falls_back_when_no_bundle(self, monkeypatch, tmp_path):
        """When the run has no bundle on disk and SDK build fails too,
        the share returns ok=False with a clear error rather than crashing."""
        from fantasyai.aurora.runs_library import share_run_bundle
        monkeypatch.setenv("AURORA_DATA_ROOT", str(tmp_path))
        rd = _make_run(tmp_path, run_id="r-no-bundle", with_bundle=False)
        info = share_run_bundle(rd, workspace_id="alice")
        # Result depends on whether the SDK can synthesise a bundle —
        # accept either ok=True or ok=False, but never an exception.
        assert info.run_id == "r-no-bundle"
        if not info.ok:
            assert info.error
