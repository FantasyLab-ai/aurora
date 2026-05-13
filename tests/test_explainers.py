"""
Explainer module tests.

Covers:
* anomaly explainer renders structured explanation with attribution percentages
* anomaly explainer handles missing drivers gracefully
* regime explainer computes correct deltas vs previous segment
* regime explainer handles initial segment (no previous to compare)
* both explainers return {available: false} on bad input rather than crashing
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fantasyai.aurora.explainers import explain_anomaly, explain_regime


# ---------------------------------------------------------------------------
# Anomaly explainer
# ---------------------------------------------------------------------------

def test_anomaly_explanation_basic():
    point = {
        "rank": 1, "row_id": "47", "score": 5.67,
        "top_feature_drivers": [
            {"feature": "precip_mm", "robust_z": 10.86, "direction": "high"},
            {"feature": "temp_c", "robust_z": 0.48, "direction": "high"},
        ],
    }
    ex = explain_anomaly(point)
    assert ex["available"] is True
    assert ex["claim_type"] == "anomaly"
    assert "row 47" in ex["headline"]
    assert "precip_mm" in ex["headline"]
    assert ex["score"]["raw"] == pytest.approx(5.67)
    # The dominant driver should win attribution; should sum near 100%.
    pcts = [d["contribution_pct"] for d in ex["drivers"]]
    assert sum(pcts) == pytest.approx(100.0, abs=0.5)
    # First driver should have the largest contribution.
    assert ex["drivers"][0]["feature"] == "precip_mm"
    assert ex["drivers"][0]["contribution_pct"] > ex["drivers"][1]["contribution_pct"]
    # Falsification + alternative views are present.
    assert "if row 47" in ex["falsification"]
    assert isinstance(ex["alternative_views"], list)


def test_anomaly_explanation_no_drivers():
    point = {"rank": 1, "row_id": "5", "score": 3.1, "top_feature_drivers": []}
    ex = explain_anomaly(point)
    assert ex["available"] is False
    assert "no driver" in ex["reason"].lower()


def test_anomaly_explanation_invalid_input():
    """Non-dict input shouldn't crash."""
    ex = explain_anomaly(None)
    assert ex["available"] is False
    ex = explain_anomaly("garbage")
    assert ex["available"] is False


def test_anomaly_explanation_co_occurrence_detection():
    """When 2+ points are within ±24 rows of each other, alt views note it."""
    points = [
        {"rank": 1, "row_id": "47", "score": 5.0,
         "top_feature_drivers": [{"feature": "x", "robust_z": 5.0, "direction": "high"}]},
        {"rank": 2, "row_id": "50", "score": 4.0,
         "top_feature_drivers": [{"feature": "x", "robust_z": 4.0, "direction": "high"}]},
        {"rank": 3, "row_id": "200", "score": 3.5,
         "top_feature_drivers": [{"feature": "x", "robust_z": 3.5, "direction": "high"}]},
    ]
    ex = explain_anomaly(points[0], all_points=points)
    co = [v for v in ex["alternative_views"] if "co-occurrence" in v]
    assert len(co) == 1, "Should detect that row 50 is within ±24 of row 47"


def test_anomaly_explanation_isolation_when_far_apart():
    points = [
        {"rank": 1, "row_id": "47", "score": 5.0,
         "top_feature_drivers": [{"feature": "x", "robust_z": 5.0, "direction": "high"}]},
        {"rank": 2, "row_id": "5000", "score": 4.0,
         "top_feature_drivers": [{"feature": "x", "robust_z": 4.0, "direction": "high"}]},
    ]
    ex = explain_anomaly(points[0], all_points=points)
    iso = [v for v in ex["alternative_views"] if "isolation" in v]
    assert len(iso) == 1


# ---------------------------------------------------------------------------
# Regime explainer
# ---------------------------------------------------------------------------

REGIMES_BLOCK = {
    "method": "ruptures_pelt_rbf",
    "target_col": "temperature_c",
    "n": 365,
    "penalty": 17.7,
    "change_points": [175],
    "regime_count": 2,
    "stats": [
        {"start": 0, "end": 175, "n": 175, "mean": 21.57, "std": 3.05, "min": 13.08, "max": 27.47},
        {"start": 175, "end": 365, "n": 190, "mean": 8.81, "std": 3.62, "min": 2.28, "max": 18.66},
    ],
    "note": "ok",
}


def test_regime_explanation_initial_segment():
    """First segment has no previous to compare; deltas list is empty but no crash."""
    ex = explain_regime(REGIMES_BLOCK, segment_index=0)
    assert ex["available"] is True
    assert ex["segment"]["start"] == 0
    assert ex["segment"]["end"] == 175
    assert ex["deltas"] == []
    # Should still have method + falsification + why-this-one-won.
    assert "ruptures" in ex["method"]
    assert ex["falsification"]
    assert ex["why_this_one_won"]


def test_regime_explanation_second_segment_computes_deltas():
    """Segment 1 vs segment 0: mean dropped 21.57 → 8.81 = -12.76."""
    ex = explain_regime(REGIMES_BLOCK, segment_index=1)
    assert ex["available"] is True
    deltas = ex["deltas"]
    assert len(deltas) >= 1
    mean_delta = next(d for d in deltas if d["stat"] == "mean")
    assert mean_delta["before"] == pytest.approx(21.57)
    assert mean_delta["after"] == pytest.approx(8.81)
    assert mean_delta["delta"] == pytest.approx(-12.76, abs=0.01)
    # Headline should call out the direction.
    assert "dropped" in ex["headline"]
    # delta_normalized should be substantial (large σ shift).
    assert abs(mean_delta["delta_normalized"]) > 2.0


def test_regime_explanation_out_of_range():
    ex = explain_regime(REGIMES_BLOCK, segment_index=99)
    assert ex["available"] is False
    assert "out of range" in ex["reason"]


def test_regime_explanation_invalid_input():
    ex = explain_regime(None, segment_index=0)
    assert ex["available"] is False
    ex = explain_regime({"stats": "not a list"}, segment_index=0)
    assert ex["available"] is False


def test_regime_explanation_std_ratio_interpretation():
    """When std doubles, the note should say 'expanded by 100%'."""
    block = {
        "method": "ruptures_pelt_rbf",
        "target_col": "x",
        "n": 200, "penalty": 10.0, "change_points": [100], "regime_count": 2,
        "stats": [
            {"start": 0, "end": 100, "n": 100, "mean": 0.0, "std": 1.0, "min": -3, "max": 3},
            {"start": 100, "end": 200, "n": 100, "mean": 0.0, "std": 2.0, "min": -6, "max": 6},
        ],
        "note": "ok",
    }
    ex = explain_regime(block, segment_index=1)
    std_delta = next((d for d in ex["deltas"] if d["stat"] == "std"), None)
    assert std_delta is not None
    assert std_delta["ratio"] == pytest.approx(2.0)
    assert "expanded" in std_delta["note"]
