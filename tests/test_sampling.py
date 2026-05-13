"""
Tests for ``fantasyai/aurora/sampling``: state machine, shape detection,
samplers, replication.
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

np = pytest.importorskip("numpy")
pd = pytest.importorskip("pandas")


# ============================================================
# State machine
# ============================================================

class TestStateMachine:

    def test_save_and_load_round_trip(self, tmp_path):
        from fantasyai.aurora.sampling import SamplingState
        s = SamplingState(parent_run_dir=str(tmp_path),
                          dataset_path="x.csv", dataset_basename="x.csv",
                          rows_total=10_000, cols_total=4)
        s.save()
        loaded = SamplingState.load(tmp_path)
        assert loaded is not None
        assert loaded.dataset_basename == "x.csv"
        assert loaded.rows_total == 10_000
        assert loaded.tiers["0"].state == "pending"

    def test_tier_progression(self, tmp_path):
        from fantasyai.aurora.sampling import SamplingState
        s = SamplingState(parent_run_dir=str(tmp_path),
                          dataset_path="x.csv", dataset_basename="x.csv")
        s.begin_tier(1, method="random_iid", rows=1000, cols=5,
                     seed="abcd1234", rows_total=10_000)
        assert s.tiers["1"].state == "running"
        assert s.current_tier == 1
        s.complete_tier(1, child_run_dir="/fake/child")
        assert s.tiers["1"].state == "complete"
        assert s.tiers["1"].child_run_dir == "/fake/child"
        assert s.highest_complete_tier() == 1

    def test_t3_becomes_available_after_t2(self, tmp_path):
        from fantasyai.aurora.sampling import SamplingState
        s = SamplingState(parent_run_dir=str(tmp_path),
                          dataset_path="x.csv", dataset_basename="x.csv")
        s.begin_tier(2, method="random_iid", rows=10000, cols=5, seed="x")
        s.complete_tier(2, child_run_dir="/fake/c2")
        assert s.tiers["3"].state == "available"

    def test_cancel_running_tier(self, tmp_path):
        from fantasyai.aurora.sampling import SamplingState
        s = SamplingState(parent_run_dir=str(tmp_path),
                          dataset_path="x.csv", dataset_basename="x.csv")
        s.begin_tier(1, method="random_iid", rows=100, cols=2, seed="x")
        s.cancel_tier(1, note="user cancelled")
        assert s.tiers["1"].state == "cancelled"
        assert "user cancelled" in (s.tiers["1"].note or "")


# ============================================================
# Shape detection
# ============================================================

class TestShapeDetector:

    def _write_csv(self, tmp: Path, name: str, df: pd.DataFrame) -> Path:
        p = tmp / name
        df.to_csv(p, index=False)
        return p

    def test_time_series_shape(self, tmp_path):
        from fantasyai.aurora.sampling import infer_structure
        n = 5000
        df = pd.DataFrame({
            "timestamp": pd.date_range("2024-01-01", periods=n, freq="h"),
            "temp": np.random.RandomState(0).normal(20, 5, n),
            "humidity": np.random.RandomState(1).normal(60, 10, n),
        })
        p = self._write_csv(tmp_path, "ts.csv", df)
        prof = infer_structure(p)
        # n=5000 < 200K threshold, so it's "small" — but the time axis should still be detected.
        assert prof.has_time_axis is True
        assert prof.time_col == "timestamp"
        assert prof.is_small is True
        assert prof.shape == "small"

    def test_no_time_axis_wide(self, tmp_path):
        from fantasyai.aurora.sampling import infer_structure
        rng = np.random.RandomState(0)
        df = pd.DataFrame({
            "alpha": rng.normal(0, 1, 1000),
            "beta": rng.normal(0, 2, 1000),
            "category": np.random.choice(["x", "y", "z"], 1000),
        })
        p = self._write_csv(tmp_path, "wide.csv", df)
        prof = infer_structure(p)
        assert prof.has_time_axis is False
        assert prof.is_small is True
        # 1000 rows is well under the small threshold so shape == small.
        assert prof.shape == "small"

    def test_panel_shape(self, tmp_path):
        from fantasyai.aurora.sampling import infer_structure
        # 5 stations × 1000 timestamps = 5000 rows; below small threshold but
        # the panel id column should still be detected.
        rows = []
        for sid in range(5):
            for t in range(1000):
                rows.append({
                    "station_id": f"S{sid}",
                    "timestamp": pd.Timestamp("2024-01-01") + pd.Timedelta(hours=t),
                    "value": np.random.RandomState(sid * 1000 + t).normal(0, 1),
                })
        df = pd.DataFrame(rows)
        p = self._write_csv(tmp_path, "panel.csv", df)
        prof = infer_structure(p)
        assert prof.has_time_axis is True
        assert prof.panel_id_col == "station_id"


# ============================================================
# Samplers
# ============================================================

class TestSamplers:

    def _make_ts(self, tmp: Path, n: int = 5000) -> Path:
        df = pd.DataFrame({
            "timestamp": pd.date_range("2024-01-01", periods=n, freq="h"),
            "x": np.random.RandomState(0).normal(0, 1, n),
            "y": np.random.RandomState(1).normal(5, 2, n),
        })
        p = tmp / "ts.csv"
        df.to_csv(p, index=False)
        return p

    def test_make_seed_deterministic(self, tmp_path):
        from fantasyai.aurora.sampling import make_seed
        p = self._make_ts(tmp_path)
        s1 = make_seed(p, tier=1, size=1000)
        s2 = make_seed(p, tier=1, size=1000)
        assert s1 == s2
        # different size → different seed
        s3 = make_seed(p, tier=1, size=2000)
        assert s1 != s3

    def test_random_iid_sampler(self, tmp_path):
        from fantasyai.aurora.sampling import (
            infer_structure, run_sampler,
        )
        p = self._make_ts(tmp_path, n=5000)
        prof = infer_structure(p)
        out = tmp_path / "sample.csv"
        manifest = run_sampler("random_iid", p, profile=prof,
                                rows_target=1000, max_cols=10,
                                tier=1, out_path=out)
        assert out.exists()
        assert manifest.method == "random_iid"
        assert manifest.tier == 1
        # We asked for 1000 but might get slightly more or fewer depending on prob.
        assert manifest.rows_sampled <= 1000
        sampled_df = pd.read_csv(out)
        assert len(sampled_df) == manifest.rows_sampled

    def test_stratified_temporal_sampler(self, tmp_path):
        from fantasyai.aurora.sampling import infer_structure, run_sampler
        p = self._make_ts(tmp_path, n=10000)
        prof = infer_structure(p)
        out = tmp_path / "sample.csv"
        manifest = run_sampler("stratified_temporal", p, profile=prof,
                                rows_target=500, max_cols=10,
                                tier=1, out_path=out)
        assert out.exists()
        assert manifest.method == "stratified_temporal"
        assert manifest.n_strata is not None
        assert manifest.stratification_axis == "time"

    def test_select_columns_by_variance_keeps_must_include(self, tmp_path):
        from fantasyai.aurora.sampling import (
            infer_structure, select_columns_by_variance,
        )
        # Build a dataset with many columns; some have high variance.
        rng = np.random.RandomState(0)
        cols = {f"c{i}": rng.normal(0, i + 1, 1000) for i in range(20)}
        cols["target"] = rng.normal(0, 0.001, 1000)   # low-variance MUST-include
        df = pd.DataFrame(cols)
        p = tmp_path / "wide.csv"
        df.to_csv(p, index=False)
        prof = infer_structure(p)
        keep = select_columns_by_variance(prof, max_cols=5,
                                           must_include={"target"})
        assert "target" in keep
        assert len(keep) == 5


# ============================================================
# Replication status
# ============================================================

class TestReplication:

    def test_confirmed_when_effect_stable(self):
        from fantasyai.aurora.sampling import attach_replication
        lower = {"anomalies": [{"column": "x", "when": "row 1", "z": 4.0}]}
        higher = {"anomalies": [{"column": "x", "when": "row 1", "z": 4.1}]}
        attach_replication(lower, higher, tier_lower=1, tier_higher=2)
        rs = higher["anomalies"][0]["replication_status"]
        assert rs["status"] == "confirmed"

    def test_strengthened_when_effect_grows(self):
        from fantasyai.aurora.sampling import attach_replication
        lower = {"anomalies": [{"column": "x", "when": "row 1", "z": 3.0}]}
        higher = {"anomalies": [{"column": "x", "when": "row 1", "z": 5.0}]}
        attach_replication(lower, higher, tier_lower=1, tier_higher=2)
        rs = higher["anomalies"][0]["replication_status"]
        assert rs["status"] == "strengthened"

    def test_weakened_when_effect_drops(self):
        from fantasyai.aurora.sampling import attach_replication
        lower = {"anomalies": [{"column": "x", "when": "row 1", "z": 4.0}]}
        higher = {"anomalies": [{"column": "x", "when": "row 1", "z": 1.5}]}
        attach_replication(lower, higher, tier_lower=1, tier_higher=2)
        rs = higher["anomalies"][0]["replication_status"]
        assert rs["status"] == "weakened"

    def test_contradicted_when_absent_at_higher_tier(self):
        from fantasyai.aurora.sampling import attach_replication
        lower = {"anomalies": [{"column": "x", "when": "row 1", "z": 4.0}]}
        higher = {"anomalies": []}
        attach_replication(lower, higher, tier_lower=1, tier_higher=2)
        # The lower-tier item should be appended as contradicted in higher.
        assert any(a["replication_status"]["status"] == "contradicted"
                   for a in higher["anomalies"])

    def test_novel_when_only_at_higher(self):
        from fantasyai.aurora.sampling import attach_replication
        lower = {"anomalies": []}
        higher = {"anomalies": [{"column": "x", "when": "row 5", "z": 4.0}]}
        attach_replication(lower, higher, tier_lower=1, tier_higher=2)
        assert higher["anomalies"][0]["replication_status"]["status"] == "novel"

    def test_summary_aggregates(self):
        from fantasyai.aurora.sampling import attach_replication
        lower = {"anomalies": [{"column": "x", "when": "1", "z": 4.0}],
                 "regimes": [{"label": "A", "start": "0", "end": "10", "std": 2.0}]}
        higher = {"anomalies": [{"column": "x", "when": "1", "z": 4.1}],
                  "regimes":  [{"label": "A", "start": "0", "end": "10", "std": 1.9}]}
        attach_replication(lower, higher, tier_lower=1, tier_higher=2)
        s = higher["replication_summary"]
        assert s["total_items"] == 2
        assert s["n_replicated"] == 2
        assert s["fraction_replicated"] == 1.0
