"""
Task-1 regression tests — Top Anomalies tile / state_builder fallback path.

Pins the bug where the Top Anomalies tile rendered "UNAVAILABLE · No
anomalies above the significance threshold (z ≥ 3.0)" on
factory_bearing_demo.csv while:

  * ``math_results.json["top_anomalies"]`` on disk had 20 entries with
    the canonical rows (540, 220, 999, 993, 987, ...) and z-scores in
    the +13 to +17 range.
  * The synthesis narrative correctly referenced multivariate outliers.

The cause was a contract mismatch between writer and reader:

  * The CURRENT production runner (``run_aurora_dataset_runner`` →
    ``mathstack_v2.run_mathstack_v2``) writes top anomalies to
    ``math_results.json["top_anomalies"]`` with schema:
      {row_index, score, top_contributors: [{col, abs_robust_z}, ...]}

  * The state_builder (``_build_anomalies``) only read from
    ``deep_math.json["extensions"]["anomaly_attribution"]["points"]``
    with schema:
      {rank, row_id, score, top_feature_drivers: [{feature, robust_z,
       direction}, ...]}

  * The current runner does NOT populate ``deep_math.extensions``, so
    ``_build_anomalies`` returned ``[]`` on every fresh run.

These tests guard against regression by:

  1. Asserting ``_build_anomalies`` reads from ``math_results.json``
     when ``deep_math.extensions`` is missing.
  2. Asserting the schema translation preserves row identity, score,
     column attribution, and severity (Hampel z=3/5 threshold).
  3. Asserting the primary path (``deep_math.extensions``) still wins
     when both are present — i.e. richer driver attribution beats the
     simpler ``top_contributors`` block.
  4. Asserting ``_build_anomaly_counts`` produces accurate totals from
     the translated anomalies (so the overview / footer / synthesis
     narrative all agree on a number).

Threshold (z≥3 warn, z≥5 crit) per Hampel 1974 is preserved — these
tests would catch a regression that loosened it.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fantasyai.aurora.state_builder import (  # noqa: E402
    _build_anomalies,
    _build_anomaly_counts,
)


# ============================================================
# 1. Fallback path — math_results.top_anomalies populates the tile
# ============================================================

class TestMathResultsFallback:
    def _math_results_sample(self) -> dict:
        """Canonical shape produced by ``mathstack_v2.run_mathstack_v2``
        on factory_bearing_demo.csv."""
        return {
            "rows": 1000,
            "cols": 5,
            "top_anomalies": [
                {"row_index": 540, "score": 16.638,
                 "top_contributors": [
                     {"col": "vibration_g", "abs_robust_z": 16.638},
                     {"col": "bearing_load_kn", "abs_robust_z": 0.713},
                     {"col": "rpm", "abs_robust_z": 0.546},
                 ]},
                {"row_index": 220, "score": 15.919,
                 "top_contributors": [
                     {"col": "vibration_g", "abs_robust_z": 15.919},
                 ]},
                {"row_index": 999, "score": 15.605,
                 "top_contributors": [
                     {"col": "motor_temp_c", "abs_robust_z": 15.605},
                 ]},
                # A z≥3 row that should be 'warn' not 'crit'.
                {"row_index": 100, "score": 3.5,
                 "top_contributors": [{"col": "rpm", "abs_robust_z": 3.5}]},
                # A z<3 row — would not pass the detector's cap, but if
                # it appears, the severity classification must call it 'info'.
                {"row_index": 50, "score": 2.0,
                 "top_contributors": [{"col": "rpm", "abs_robust_z": 2.0}]},
            ],
        }

    def test_falls_back_when_deep_extensions_missing(self):
        deep = {"version": "deep_math_v3"}   # no extensions
        out = _build_anomalies(deep, self._math_results_sample())
        assert len(out) == 5, "fallback should emit all top_anomalies entries"

    def test_falls_back_when_deep_is_none(self):
        out = _build_anomalies(None, self._math_results_sample())
        assert len(out) == 5

    def test_row_id_preserved_in_when_field(self):
        out = _build_anomalies(None, self._math_results_sample())
        rows = [int(a["when"].split()[-1]) for a in out]
        assert rows == [540, 220, 999, 100, 50]

    def test_score_preserved(self):
        out = _build_anomalies(None, self._math_results_sample())
        assert out[0]["score"] == pytest.approx(16.638)
        assert out[1]["score"] == pytest.approx(15.919)

    def test_column_pulled_from_top_contributor(self):
        out = _build_anomalies(None, self._math_results_sample())
        assert out[0]["column"] == "vibration_g"
        assert out[2]["column"] == "motor_temp_c"

    def test_drivers_translated_from_contributors(self):
        out = _build_anomalies(None, self._math_results_sample())
        drivers = out[0]["drivers"]
        assert len(drivers) >= 1
        # Schema: col → feature, abs_robust_z → robust_z
        assert drivers[0]["feature"] == "vibration_g"
        assert drivers[0]["robust_z"] == pytest.approx(16.638)

    def test_severity_classification_per_hampel(self):
        """z>=5 → crit; 3<=z<5 → warn; z<3 → info. Hampel 1974 threshold."""
        out = _build_anomalies(None, self._math_results_sample())
        sevs = [(int(a["when"].split()[-1]), a["severity"]) for a in out]
        sev_map = dict(sevs)
        assert sev_map[540] == "crit"   # z=16.6
        assert sev_map[220] == "crit"   # z=15.9
        assert sev_map[999] == "crit"   # z=15.6
        assert sev_map[100] == "warn"   # z=3.5
        assert sev_map[50]  == "info"   # z=2.0

    def test_rank_starts_at_1_and_is_monotonic(self):
        out = _build_anomalies(None, self._math_results_sample())
        ranks = [a["rank"] for a in out]
        assert ranks == [1, 2, 3, 4, 5]


# ============================================================
# 2. Primary path still wins when present
# ============================================================

class TestPrimaryPathStillWorks:
    """When ``deep_math.extensions.anomaly_attribution.points`` IS
    populated (legacy / demo runs), the state_builder must prefer it
    over the fallback — it carries direction info that the fallback
    cannot reconstruct from unsigned top_contributors."""

    def test_primary_wins_when_both_present(self):
        deep = {
            "extensions": {
                "anomaly_attribution": {
                    "points": [
                        {"rank": 1, "row_id": 7, "score": 9.0,
                         "top_feature_drivers": [
                             {"feature": "x", "robust_z": -9.0,
                              "direction": "low"}]}
                    ],
                },
            },
        }
        mathr = {"top_anomalies": [
            {"row_index": 99, "score": 4.0,
             "top_contributors": [{"col": "y", "abs_robust_z": 4.0}]},
        ]}
        out = _build_anomalies(deep, mathr)
        # Primary path's row 7 must surface, NOT mathr's row 99.
        assert len(out) == 1
        assert "row 7" in out[0]["when"]
        # And signed z survives (the fallback would have stripped it).
        assert out[0]["z"] == pytest.approx(-9.0)

    def test_empty_extensions_falls_through_to_mathr(self):
        """An empty ``points`` list MUST trigger the fallback, not
        return [] — that's the exact regression the user reported."""
        deep = {"extensions": {"anomaly_attribution": {"points": []}}}
        mathr = {"top_anomalies": [
            {"row_index": 540, "score": 16.6,
             "top_contributors": [{"col": "vibration_g",
                                    "abs_robust_z": 16.6}]},
        ]}
        out = _build_anomalies(deep, mathr)
        assert len(out) == 1
        assert "row 540" in out[0]["when"]


# ============================================================
# 3. _build_anomaly_counts consistency — body and footer must agree
# ============================================================

class TestCountsAgreeWithBody:
    """The user's screenshot inconsistency (tile body empty, footer
    '14 total · 3 crit · 11 mild') came from anomalies-list and
    overview-counts disagreeing. After the fix the counts MUST be
    derived from the same translated anomalies, so they cannot
    disagree on a healthy run."""

    def test_counts_match_translated_anomalies(self):
        mathr = {"top_anomalies": [
            {"row_index": 1, "score": 8.0,
             "top_contributors": [{"col": "a", "abs_robust_z": 8.0}]},
            {"row_index": 2, "score": 6.0,
             "top_contributors": [{"col": "a", "abs_robust_z": 6.0}]},
            {"row_index": 3, "score": 4.5,
             "top_contributors": [{"col": "b", "abs_robust_z": 4.5}]},
            {"row_index": 4, "score": 3.2,
             "top_contributors": [{"col": "b", "abs_robust_z": 3.2}]},
        ]}
        anomalies = _build_anomalies(None, mathr)
        counts = _build_anomaly_counts(None, anomalies)
        # 2 crit (z>=5), 2 warn (3<=z<5), 0 info
        assert counts["n_total"]    == 4
        assert counts["n_critical"] == 2
        assert counts["n_warn"]     == 2
        assert counts["n_info"]     == 0


# ============================================================
# 4. Negative case — empty data stays empty (no spurious findings)
# ============================================================

class TestNegativeCases:
    def test_empty_math_results_yields_empty(self):
        """noaa_buoy without z>=3 outliers must still surface zero
        anomalies — the fix must not invent findings."""
        out = _build_anomalies(None, {"top_anomalies": []})
        assert out == []

    def test_no_inputs_yields_empty(self):
        assert _build_anomalies(None, None) == []
        assert _build_anomalies({}, {}) == []

    def test_malformed_entries_skipped_not_crashed(self):
        """Hostile/malformed entries shouldn't kill the builder."""
        mathr = {"top_anomalies": [
            "not a dict",           # bad
            None,                   # bad
            {"row_index": 1, "score": 7.0, "top_contributors": [
                "bad", None, {"col": "a", "abs_robust_z": 7.0}]},
        ]}
        out = _build_anomalies(None, mathr)
        # Only the well-formed entry surfaces.
        assert len(out) == 1
        assert out[0]["column"] == "a"
