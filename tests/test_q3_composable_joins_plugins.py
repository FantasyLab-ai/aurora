"""
Q3 backend tests:
    * Stream A — composable findings: extractor + applier + tagging
    * Stream B — multi-dataset joins: joiner report shape + behaviour
    * Stream C — Plugin SDK: discovery, validation, safe invocation
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import patch

import pytest


# ----------------------------------------------------------------------
# Helpers — build a fake run directory
# ----------------------------------------------------------------------

def _make_run(tmp_path: Path, *, run_id: str,
              dataset: str = "demo.csv",
              physics_law: str = "logistic",
              hmm_k: int = 3,
              y_range: tuple = (10.0, 30.0),
              target_col: str = "temperature_c",
              extra_cols: List[Dict[str, Any]] = None) -> Path:
    rd = tmp_path / run_id
    rd.mkdir(parents=True, exist_ok=True)
    (rd / "_RUN_META.json").write_text(json.dumps({
        "run_id": run_id, "target_col": target_col,
        "dataset_name": dataset, "dt_seconds": 60,
    }))
    (rd / "_RESOLVED_CONTRACT.json").write_text(json.dumps({
        "dataset_key": dataset,
    }))
    cols = [
        {"name": "timestamp", "role": "time",
          "dtype": "datetime64[ns]", "datetime_parse_rate": 1.0,
          "missing_rate": 0.0},
        {"name": target_col, "role": "numeric",
          "dtype": "float64", "missing_rate": 0.01},
    ] + (extra_cols or [])
    (rd / "structure_contract.json").write_text(json.dumps({
        "shape": {"rows": 1000, "cols": len(cols)},
        "columns": cols,
        "time_col": "timestamp",
        "target_col": target_col,
        "dt_seconds": 60,
    }))
    deep = {
        "physics_discovery": {
            "best": {"name": physics_law,
                      "rmse_test": 1.5,
                      "params": {"K": 42}},
        },
        "physics": {"y_min": y_range[0], "y_max": y_range[1],
                     "is_monotonic": False},
        "hmm": {
            "available": True, "best_K": hmm_k,
            "means_sorted": [15.0, 22.0, 28.0][:hmm_k],
            "expected_dwell_time": [40, 60, 30][:hmm_k],
        },
        "extensions": {"anomaly_attribution": {"points": [
            {"row_id": 100, "score": 4.5,
              "top_feature_drivers": [
                  {"feature": target_col, "score": 0.8},
                  {"feature": "humidity_pct", "score": 0.3},
              ]},
        ]}},
    }
    (rd / "deep_math.json").write_text(json.dumps(deep))
    (rd / "math_results.json").write_text(json.dumps({
        "top_anomalies": [
            {"row": 100, "z": 4.5, "severity": "crit"},
            {"row": 200, "z": 3.2, "severity": "warn"},
        ],
    }))
    return rd


# ======================================================================
# Stream A — composable findings
# ======================================================================

class TestPriorPackExtractor:

    def test_extracts_physics_regimes_baseline_floor(self, tmp_path):
        from fantasyai.aurora.composable import extract_prior_pack
        rd = _make_run(tmp_path, run_id="r1")
        pack = extract_prior_pack(rd)
        assert pack.source_run_id == "r1"
        assert pack.physics is not None
        assert pack.physics["law"] == "logistic"
        assert pack.regimes is not None
        assert pack.regimes["k"] == 3
        assert pack.baseline is not None
        assert pack.baseline["col"] == "temperature_c"
        assert pack.anomaly_floor is not None
        # All 4 priors populated.
        assert pack.n_priors == 4

    def test_missing_run_dir_returns_empty_pack(self, tmp_path):
        from fantasyai.aurora.composable import extract_prior_pack
        rd = tmp_path / "does_not_exist"
        pack = extract_prior_pack(rd)
        # Doesn't raise; returns a sparse pack.
        assert pack.n_priors == 0
        assert pack.physics is None

    def test_write_and_reload_roundtrip(self, tmp_path):
        from fantasyai.aurora.composable import (
            extract_prior_pack, write_prior_pack, load_prior_pack,
        )
        rd = _make_run(tmp_path, run_id="r1")
        pack = extract_prior_pack(rd)
        out = rd / "priors_pack.json"
        write_prior_pack(pack, out)
        loaded = load_prior_pack(rd)
        assert loaded is not None
        assert loaded["source_run_id"] == "r1"
        assert loaded["physics"]["law"] == "logistic"


class TestPriorPackApplier:

    def test_tags_physics_finding_when_law_matches(self, tmp_path):
        from fantasyai.aurora.composable import (
            extract_prior_pack, apply_prior_pack_to_findings,
        )
        rd = _make_run(tmp_path, run_id="r1", physics_law="logistic")
        pack = extract_prior_pack(rd).to_dict()
        findings = [{
            "method": "physics_discovery",
            "title": "Best fit: logistic (RMSE 1.6)",
            "severity": "info",
            "evidence": {"law": "logistic", "rmse": 1.6},
        }]
        report = apply_prior_pack_to_findings(findings, pack)
        assert report["n_tagged"] == 1
        assert findings[0]["prior_source"]["alignment"] == "matches"
        assert findings[0]["prior_source"]["kind"] == "physics"

    def test_tags_regime_finding_at_same_k(self, tmp_path):
        from fantasyai.aurora.composable import (
            extract_prior_pack, apply_prior_pack_to_findings,
        )
        rd = _make_run(tmp_path, run_id="r1", hmm_k=4)
        pack = extract_prior_pack(rd).to_dict()
        findings = [{
            "method": "hmm",
            "title": "Latent regimes: 4 hidden states",
            "severity": "info",
            "evidence": {"best_K": 4},
        }]
        report = apply_prior_pack_to_findings(findings, pack)
        assert report["n_tagged"] == 1
        ps = findings[0]["prior_source"]
        assert ps["alignment"] == "matches"
        assert "K=4" in ps["note"]

    def test_tags_drift_when_k_differs_by_one(self, tmp_path):
        from fantasyai.aurora.composable import (
            extract_prior_pack, apply_prior_pack_to_findings,
        )
        rd = _make_run(tmp_path, run_id="r1", hmm_k=3)
        pack = extract_prior_pack(rd).to_dict()
        findings = [{
            "method": "hmm",
            "title": "Latent regimes: 4 hidden states",
            "severity": "info",
            "evidence": {"best_K": 4},
        }]
        apply_prior_pack_to_findings(findings, pack)
        assert findings[0]["prior_source"]["alignment"] == "drifts"

    def test_no_pack_no_tagging(self):
        from fantasyai.aurora.composable import tag_findings_with_prior
        findings = [{"method": "var", "title": "x", "severity": "info"}]
        n = tag_findings_with_prior(findings, None)
        assert n == 0
        assert "prior_source" not in findings[0]


# ======================================================================
# Stream B — multi-dataset joins
# ======================================================================

class TestJoinReport:

    def test_two_compatible_runs_yield_shared_keys(self, tmp_path):
        from fantasyai.aurora.joins import compute_join_report
        rd_a = _make_run(tmp_path, run_id="r_a", dataset="a.csv")
        rd_b = _make_run(tmp_path, run_id="r_b", dataset="b.csv",
                          extra_cols=[
                              {"name": "humidity_pct", "role": "numeric",
                                "dtype": "float64", "missing_rate": 0.05}
                          ])
        rep = compute_join_report(rd_a, rd_b)
        assert rep.ok is True
        assert rep.run_a_id == "r_a"
        assert rep.run_b_id == "r_b"
        # Both have ``timestamp`` and ``temperature_c`` — at least one shared.
        names = [k.name for k in rep.shared_keys]
        assert "timestamp" in names
        assert "temperature_c" in names

    def test_join_self_refused(self, tmp_path):
        from fantasyai.aurora.joins import compute_join_report
        rd = _make_run(tmp_path, run_id="solo")
        rep = compute_join_report(rd, rd)
        assert rep.ok is False
        assert any("itself" in w for w in rep.warnings)

    def test_missing_run_dirs_returns_not_ok(self, tmp_path):
        from fantasyai.aurora.joins import compute_join_report
        rep = compute_join_report(tmp_path / "no_a", tmp_path / "no_b")
        assert rep.ok is False
        assert rep.warnings

    def test_inheritance_candidates_include_physics_and_regimes(self, tmp_path):
        from fantasyai.aurora.joins import compute_join_report
        rd_a = _make_run(tmp_path, run_id="r_a", physics_law="logistic", hmm_k=3)
        rd_b = _make_run(tmp_path, run_id="r_b", physics_law="exponential", hmm_k=2)
        rep = compute_join_report(rd_a, rd_b)
        kinds = sorted({c["kind"] for c in rep.inheritance_candidates})
        assert "physics_law" in kinds
        assert "regime_k" in kinds
        # Should have candidates in both directions.
        dirs = sorted({(c["from"], c["to"]) for c in rep.inheritance_candidates})
        assert ("A", "B") in dirs
        assert ("B", "A") in dirs

    def test_schema_compat_flags_cadence_mismatch(self, tmp_path):
        from fantasyai.aurora.joins import compute_join_report
        rd_a = _make_run(tmp_path, run_id="r_a")
        rd_b = _make_run(tmp_path, run_id="r_b")
        # Hand-edit dt_seconds on B's meta.
        meta_b = json.loads((rd_b / "_RUN_META.json").read_text())
        meta_b["dt_seconds"] = 86400  # daily
        (rd_b / "_RUN_META.json").write_text(json.dumps(meta_b))
        rep = compute_join_report(rd_a, rd_b)
        assert rep.schema_compatibility["cadence_match"] == "mismatch"
        assert any("cadence" in w.lower() for w in rep.warnings)


# ======================================================================
# Stream C — Plugin SDK
# ======================================================================

class TestPluginSDK:

    def test_validate_finding_rejects_missing_fields(self):
        from fantasyai.aurora.plugins import (
            validate_finding, PluginValidationError,
        )
        with pytest.raises(PluginValidationError):
            validate_finding({}, method_name="x")
        with pytest.raises(PluginValidationError):
            validate_finding(
                {"method": "x", "title": "t"},  # missing severity, evidence
                method_name="x",
            )
        with pytest.raises(PluginValidationError):
            validate_finding(
                {"method": "x", "title": "t", "severity": "info",
                  "evidence": {"status": "INVALID"}},
                method_name="x",
            )

    def test_validate_finding_rejects_fabricated_true(self):
        from fantasyai.aurora.plugins import (
            validate_finding, PluginValidationError,
        )
        with pytest.raises(PluginValidationError):
            validate_finding({
                "method": "x", "title": "t", "severity": "info",
                "evidence": {"status": "fit"},
                "fabricated": True,
            }, method_name="x")

    def test_validate_finding_accepts_clean_shape(self):
        from fantasyai.aurora.plugins import validate_finding
        # Doesn't raise.
        validate_finding({
            "method": "x", "title": "t", "severity": "info",
            "evidence": {"status": "fit", "n_obs": 100},
        }, method_name="x")

    def test_discover_plugins_with_no_entry_points(self, monkeypatch):
        """When no plugins are installed, discovery returns []."""
        from fantasyai.aurora.plugins import registry
        # Force the entry-points lookup to return nothing.
        monkeypatch.setattr(registry, "_iter_entry_points", lambda: [])
        from fantasyai.aurora.plugins import discover_plugins
        assert discover_plugins() == []

    def test_run_plugin_methods_with_stub_plugin(self, monkeypatch):
        """Inject a synthetic entry-point and confirm its output flows."""
        from fantasyai.aurora.plugins import registry

        class _FakeEP:
            def __init__(self, name, fn):
                self.name = name
                self.value = "tests.fake:fake_fn"
                self._fn = fn
                self.dist = None
            def load(self):
                return self._fn

        def good_plugin(df, **kw):
            return {
                "method": "good_plugin",
                "title": "ran on " + str(len(df)) + " rows",
                "severity": "info",
                "confidence": 0.6,
                "fabricated": False,
                "evidence": {"status": "fit", "n_obs": len(df)},
            }

        def crashing_plugin(df, **kw):
            raise RuntimeError("boom")

        def bad_shape_plugin(df, **kw):
            return {"method": "bad_shape_plugin", "wrong": "shape"}

        monkeypatch.setattr(registry, "_iter_entry_points", lambda: [
            _FakeEP("good_plugin", good_plugin),
            _FakeEP("crashing_plugin", crashing_plugin),
            _FakeEP("bad_shape_plugin", bad_shape_plugin),
        ])
        # _load_callable resolves via importlib; bypass to return our stubs.
        target_to_fn = {
            "tests.fake:fake_fn": good_plugin,
        }
        # Hack: since all three EPs use the same target, _load_callable
        # would return the same fn for all. Instead, replace it with a
        # closure that looks at info.name.
        plugins_seen: List[str] = []
        original_load = registry._load_callable
        def fake_load(target):
            # Map by call order — three plugins, three calls.
            idx = len(plugins_seen)
            plugins_seen.append(target)
            return [good_plugin, crashing_plugin, bad_shape_plugin][idx % 3]
        monkeypatch.setattr(registry, "_load_callable", fake_load)

        import pandas as pd
        from fantasyai.aurora.plugins import run_plugin_methods
        df = pd.DataFrame({"x": [1, 2, 3, 4, 5]})
        out = run_plugin_methods(df)
        # good_plugin's finding made it through.
        methods = sorted(f.get("method") for f in out["findings"])
        assert "good_plugin" in methods
        # crashing_plugin produced a synthetic "crashed" finding.
        assert "crashing_plugin" in methods
        crashed = [f for f in out["findings"] if f["method"] == "crashing_plugin"][0]
        assert crashed["evidence"]["status"] == "failed"
        # bad_shape_plugin was dropped — no finding flowed.
        bad = [f for f in out["findings"] if f["method"] == "bad_shape_plugin"]
        assert bad == []
        # Per-plugin telemetry surfaces all three statuses.
        assert out["per_plugin"]["good_plugin"]["status"] == "fit"
        assert out["per_plugin"]["crashing_plugin"]["status"] == "failed"
        # bad_shape_plugin got recorded as having errors but skipped status.
        bp = out["per_plugin"]["bad_shape_plugin"]
        assert bp["errors"]  # at least one validation error captured
