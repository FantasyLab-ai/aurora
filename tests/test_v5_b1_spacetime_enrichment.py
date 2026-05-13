"""
V5.B.1 — Spacetime backend enrichment for the system graph.

Pins:
  - nodes[i].history is sampled at ~5min intervals over the last 4h
    when a time axis exists. Falls back to row-index-based offsets when
    no time axis. Returns null for nodes whose data column doesn't exist.
  - causal_lags is built from topology relationships + state.causal,
    filtered to lag>0 and confidence>=0.5. Deduplicated.
  - future_events surfaces forecast peak threshold crossings + regime
    entry predictions, sorted by time_offset_hours.
  - Every enrichment is fail-safe: missing upstream data → null/empty
    field, never raises, never blocks state assembly.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

np = pytest.importorskip("numpy")
pd = pytest.importorskip("pandas")

from fantasyai.aurora.system_model.spacetime import (   # noqa: E402
    enrich_state_for_v5, enrich_with_history,
    enrich_with_causal_lags, enrich_with_future_events,
    WORLDLINE_HOURS,
)


# ============================================================
# Fixtures
# ============================================================

def _make_state_with_time_axis(tmp_path: Path,
                                hours: int = 8) -> dict:
    """A realistic state dict with a real CSV on disk + a system_model."""
    n = hours * 12     # 5-min intervals
    rng = np.random.RandomState(0)
    df = pd.DataFrame({
        "timestamp": pd.date_range("2024-01-01", periods=n, freq="5min"),
        "wave_height_m": 1.0 + 0.3 * np.sin(np.arange(n) * 0.2)
                          + rng.normal(0, 0.1, n),
        "wind_speed_ms": 5.0 + rng.normal(0, 1, n),
        "pressure_hpa":  1013.0 + rng.normal(0, 1.5, n),
    })
    csv = tmp_path / "buoy.csv"
    df.to_csv(csv, index=False)
    return {
        "dataset": {"path": str(csv), "name": "buoy.csv", "rows": n,
                     "cols": len(df.columns)},
        "structure": {"available": True, "time_col": "timestamp",
                       "time_axis": True, "cadence": "5m"},
        "system_model": {
            "template_id": "enviro",
            "topology": {
                "entities": [
                    {"id": "wave_height_m",  "label": "Wave height",
                     "role": "response", "data_column": "wave_height_m"},
                    {"id": "wind_speed_ms",  "label": "Wind",
                     "role": "forcing",  "data_column": "wind_speed_ms"},
                    {"id": "pressure_hpa",   "label": "Pressure",
                     "role": "indicator","data_column": "pressure_hpa"},
                    {"id": "missing_col",    "label": "Missing",
                     "role": "indicator","data_column": "not_in_csv"},
                ],
                "relationships": [
                    {"id": "rel_p_w", "source": "pressure_hpa",
                     "target": "wind_speed_ms", "kind": "lagged",
                     "lag": 1080.0, "confidence": 0.78,
                     "rationale": "frontal passage"},
                    {"id": "rel_w_v", "source": "wind_speed_ms",
                     "target": "wave_height_m", "kind": "lagged",
                     "lag": 1320.0, "confidence": 0.85,
                     "rationale": "fetch"},
                    # Should be FILTERED (lag=0)
                    {"id": "rel_zero", "source": "wind_speed_ms",
                     "target": "pressure_hpa", "kind": "correlational",
                     "lag": 0, "confidence": 0.9},
                    # Should be FILTERED (low confidence)
                    {"id": "rel_lowconf", "source": "wave_height_m",
                     "target": "pressure_hpa", "kind": "lagged",
                     "lag": 600.0, "confidence": 0.30},
                ],
            },
            "state": {"active_regime": "STABLE",
                       "predicted_next_regime": "STORM",
                       "predicted_transition_hours": 3.5,
                       "transition_confidence": 0.71},
        },
        "forecast": {"available": True, "target": "wave_height_m",
                      "method": "AR(1)", "horizon": 12,
                      "headline_value": 2.4, "headline_ci_low": 2.0,
                      "headline_ci_high": 2.8, "score": 0.78,
                      "phi": 0.82, "sigma": 0.3,
                      "points": [
                          {"step": i+1, "value": 1.0 + 0.12*i,
                           "ci_low": 0.9 + 0.10*i, "ci_high": 1.1 + 0.14*i}
                          for i in range(12)
                      ]},
        "causal": {"available": True, "items": [
            {"if_clause": "wind_speed_ms +1",
             "then_clause": "wave_height_m +0.3 with 22 min lag",
             "lag_minutes": 22.0, "confidence": 0.81,
             "source": "wind_speed_ms", "target": "wave_height_m"},
        ]},
        "regimes": [], "anomalies": [], "motifs": [], "findings": [],
        "physics": {"available": True}, "overview": {"available": True},
    }


# ============================================================
# B.1: history (worldlines)
# ============================================================

class TestHistory:

    def test_history_attached_per_node(self, tmp_path):
        state = _make_state_with_time_axis(tmp_path)
        enrich_with_history(state)
        nodes = state["system_model"]["topology"]["entities"]
        # Three nodes with valid columns get history; one (missing_col) gets None.
        history_lengths = {n["id"]: (len(n["history"]) if n["history"] else 0)
                            for n in nodes}
        assert history_lengths["wave_height_m"] > 0
        assert history_lengths["wind_speed_ms"] > 0
        assert history_lengths["pressure_hpa"] > 0
        assert nodes[3]["history"] is None    # missing_col falls back gracefully

    def test_history_returns_iso_timestamps(self, tmp_path):
        state = _make_state_with_time_axis(tmp_path)
        enrich_with_history(state)
        first = state["system_model"]["topology"]["entities"][0]["history"]
        assert first
        sample = first[0]
        assert "v" in sample
        # Either iso "t" (time-axis) or numeric "t_offset_hours" (no time-axis).
        assert "t" in sample or "t_offset_hours" in sample

    def test_history_capped_to_worldline_hours(self, tmp_path):
        # 8 hours of data; we should only get back ~4 hours of samples.
        state = _make_state_with_time_axis(tmp_path, hours=8)
        enrich_with_history(state)
        first = state["system_model"]["topology"]["entities"][0]["history"]
        ts = pd.to_datetime([s["t"] for s in first])
        span_hours = (ts.max() - ts.min()).total_seconds() / 3600.0
        # ≤ WORLDLINE_HOURS plus a little tolerance for boundary inclusion.
        assert span_hours <= WORLDLINE_HOURS + 0.5

    def test_no_dataset_path_yields_null_histories(self):
        state = {
            "dataset": {"path": None},
            "system_model": {"topology": {"entities": [
                {"id": "x", "data_column": "x"}]}},
        }
        enrich_with_history(state)
        assert state["system_model"]["topology"]["entities"][0]["history"] is None

    def test_no_time_axis_falls_back_to_offset_hours(self, tmp_path):
        # No time column → row-index sampling with synthetic offsets.
        df = pd.DataFrame({"x": np.random.RandomState(0).normal(0, 1, 200)})
        csv = tmp_path / "no_time.csv"
        df.to_csv(csv, index=False)
        state = {
            "dataset": {"path": str(csv)},
            "structure": {"available": True, "time_col": None},
            "system_model": {"topology": {"entities": [
                {"id": "x", "data_column": "x"}]}},
        }
        enrich_with_history(state)
        h = state["system_model"]["topology"]["entities"][0]["history"]
        assert h
        assert "t_offset_hours" in h[0]
        # First sample is oldest (most negative offset).
        assert h[0]["t_offset_hours"] <= h[-1]["t_offset_hours"]


# ============================================================
# B.3: causal_lags
# ============================================================

class TestCausalLags:

    def test_lags_extracted_from_relationships(self, tmp_path):
        state = _make_state_with_time_axis(tmp_path)
        enrich_with_causal_lags(state)
        lags = state["system_model"]["causal_lags"]
        # rel_zero (lag=0) and rel_lowconf (conf=0.3) are filtered.
        assert all(l["lag_minutes"] > 0 for l in lags)
        assert all(l.get("confidence") is None or l["confidence"] >= 0.5
                    for l in lags)
        # The two valid topology relationships + 1 from state.causal.
        # After dedup the count is between 2 and 3 depending on overlap.
        assert 2 <= len(lags) <= 3

    def test_lag_minutes_is_seconds_div_60(self, tmp_path):
        state = _make_state_with_time_axis(tmp_path)
        enrich_with_causal_lags(state)
        lags = state["system_model"]["causal_lags"]
        # rel_p_w: lag=1080s → 18.0min
        match = [l for l in lags if l["source_node_id"] == "pressure_hpa"
                  and l["target_node_id"] == "wind_speed_ms"]
        assert match, "pressure→wind lag missing"
        assert match[0]["lag_minutes"] == 18.0

    def test_dedup_prefers_higher_confidence(self):
        # State.causal duplicates a topology lag; the higher-confidence
        # entry should win after dedup.
        state = {
            "system_model": {"topology": {"relationships": [
                {"id": "rel_a", "source": "x", "target": "y",
                 "lag": 600.0, "confidence": 0.6,
                 "rationale": "topology"},
            ]}},
            "causal": {"items": [
                {"source": "x", "target": "y", "lag_minutes": 10.0,
                 "confidence": 0.9, "rationale": "data-discovered"},
            ]},
        }
        enrich_with_causal_lags(state)
        lags = state["system_model"]["causal_lags"]
        assert len(lags) == 1
        assert lags[0]["confidence"] == 0.9
        # Higher-confidence entry's rationale wins.
        assert "discovered" in lags[0]["rationale"]

    def test_no_lags_yields_empty_array(self):
        state = {"system_model": {"topology": {"relationships": []}},
                  "causal": {"items": []}}
        enrich_with_causal_lags(state)
        assert state["system_model"]["causal_lags"] == []


# ============================================================
# B.6: future_events
# ============================================================

class TestFutureEvents:

    def test_threshold_crossing_emitted(self, tmp_path):
        state = _make_state_with_time_axis(tmp_path)
        enrich_with_future_events(state)
        events = state["system_model"]["future_events"]
        threshold_evs = [e for e in events if e["kind"] == "threshold_cross"]
        assert len(threshold_evs) == 1
        ev = threshold_evs[0]
        assert ev["node_id"] == "wave_height_m"
        assert ev["value"] == 2.4
        assert ev["ci_low"] == 2.0 and ev["ci_high"] == 2.8
        assert ev["probability"] is not None

    def test_regime_entry_emitted(self, tmp_path):
        state = _make_state_with_time_axis(tmp_path)
        enrich_with_future_events(state)
        events = state["system_model"]["future_events"]
        regime_evs = [e for e in events if e["kind"] == "regime_entry"]
        assert len(regime_evs) == 1
        ev = regime_evs[0]
        assert ev["label"] == "STORM"
        assert ev["time_offset_hours"] == 3.5
        assert ev["probability"] == 0.71

    def test_events_sorted_by_time_offset(self, tmp_path):
        state = _make_state_with_time_axis(tmp_path)
        enrich_with_future_events(state)
        events = state["system_model"]["future_events"]
        offsets = [e.get("time_offset_hours") or 9999.0 for e in events]
        assert offsets == sorted(offsets)

    def test_no_forecast_no_threshold_event(self):
        state = {"system_model": {"state": {}},
                  "forecast": {"available": False}}
        enrich_with_future_events(state)
        events = state["system_model"]["future_events"]
        assert all(e["kind"] != "threshold_cross" for e in events)


# ============================================================
# Integration — enrich_state_for_v5 walks all 3 enrichments
# ============================================================

class TestIntegration:

    def test_enrich_state_for_v5_runs_all_three(self, tmp_path):
        state = _make_state_with_time_axis(tmp_path)
        enrich_state_for_v5(state)
        sm = state["system_model"]
        # All three enrichment fields are present.
        assert "causal_lags" in sm
        assert "future_events" in sm
        for n in sm["topology"]["entities"]:
            # history key exists on every node, even when null.
            assert "history" in n

    def test_no_system_model_is_a_noop(self):
        state = {"system_model": None}
        enrich_state_for_v5(state)        # must not raise
        assert state["system_model"] is None

    def test_failure_in_one_enrichment_does_not_block_others(self, tmp_path):
        state = _make_state_with_time_axis(tmp_path)
        # Corrupt the dataset path so history fails — others must still run.
        state["dataset"]["path"] = "/not/a/real/file.csv"
        enrich_state_for_v5(state)
        sm = state["system_model"]
        # All histories are None because dataset can't load — that's fine.
        # But causal_lags + future_events must still be populated.
        assert sm.get("causal_lags") is not None
        assert sm.get("future_events") is not None
