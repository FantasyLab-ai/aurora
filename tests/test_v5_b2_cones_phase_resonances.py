"""
V5.B.2 + V5.B.3 — forecast cones, phase space, resonances.

Pins:
  - nodes[i].forecast cone arrays present on the forecast-target node,
    null on others, capped to FORECAST_CONE_HOURS.
  - phase_space picks two informative axes, projects regime centroids
    as attractors, emits the historical trajectory + future projection.
  - resonances detect coupled oscillators above coherence floor 0.7,
    capped at top 5; period_minutes lives in the configured band.
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
    enrich_with_forecast_cones, enrich_with_phase_space,
    enrich_with_resonances, FORECAST_CONE_HOURS,
    RESONANCE_COHERENCE_FLOOR, RESONANCE_TOP_K,
)


# ============================================================
# Fixture builders
# ============================================================

def _make_state(tmp_path: Path, *, n: int = 600,
                 with_resonance: bool = False,
                 multi_regime: bool = False) -> dict:
    rng = np.random.RandomState(0)
    # Two strongly-coupled oscillators + one independent one.
    t = np.arange(n)
    if with_resonance:
        # Oscillators with the SAME period (10 min) and tight coupling.
        base = np.sin(t * 2 * np.pi / 10)
        wind = base + rng.normal(0, 0.05, n)
        waves = base + rng.normal(0, 0.05, n)
    else:
        wind = rng.normal(0, 1, n)
        waves = rng.normal(0, 1, n)
    pressure = 1013 + rng.normal(0, 1.5, n)
    df = pd.DataFrame({
        "timestamp":   pd.date_range("2024-01-01", periods=n, freq="1min"),
        "wave_height_m": waves * 0.4 + 1.0,
        "wind_speed_ms": wind * 1.5 + 5.0,
        "pressure_hpa":  pressure,
    })
    csv = tmp_path / "buoy.csv"
    df.to_csv(csv, index=False)

    regimes: list = []
    if multi_regime:
        regimes = [
            {"label": "STABLE", "start": "2024-01-01T00:00:00",
             "end":   "2024-01-01T05:00:00", "mean": 1.0, "std": 0.3,
             "n": 300},
            {"label": "STORM",  "start": "2024-01-01T05:00:00",
             "end":   "2024-01-01T10:00:00", "mean": 2.5, "std": 0.6,
             "n": 300},
        ]

    return {
        "dataset": {"path": str(csv), "name": "buoy.csv", "rows": n},
        "structure": {"available": True, "time_col": "timestamp",
                       "time_axis": True, "cadence": "1m"},
        "system_model": {
            "template_id": "enviro",
            "topology": {
                "entities": [
                    {"id": "wave_height_m", "data_column": "wave_height_m",
                     "label": "Waves", "role": "response"},
                    {"id": "wind_speed_ms", "data_column": "wind_speed_ms",
                     "label": "Wind", "role": "forcing"},
                    {"id": "pressure_hpa",  "data_column": "pressure_hpa",
                     "label": "Pressure","role": "indicator"},
                ],
                "relationships": [],
            },
            "state": {"active_regime": "STABLE"},
        },
        "forecast": {"available": True, "target": "wave_height_m",
                      "method": "AR(1)", "horizon": 6,
                      "headline_value": 2.4, "headline_ci_low": 2.0,
                      "headline_ci_high": 2.8, "score": 0.78,
                      "phi": 0.82, "sigma": 0.3,
                      "points": [
                          {"step": i+1,
                           "value": 1.2 + 0.2*i,
                           "ci_low":  1.0 + 0.18*i,
                           "ci_high": 1.4 + 0.22*i}
                          for i in range(6)
                      ]},
        "regimes": regimes, "anomalies": [], "motifs": [], "findings": [],
        "physics": {"available": True}, "overview": {"available": True},
        "causal": {"items": []},
    }


# ============================================================
# B.2: Forecast cones
# ============================================================

class TestForecastCones:

    def test_target_node_gets_cone(self, tmp_path):
        state = _make_state(tmp_path)
        enrich_with_forecast_cones(state)
        nodes = state["system_model"]["topology"]["entities"]
        target_node = next(n for n in nodes if n["id"] == "wave_height_m")
        assert target_node.get("forecast") is not None
        assert len(target_node["forecast"]) > 0
        # Each entry has the documented schema.
        first = target_node["forecast"][0]
        for k in ("t", "mean", "ci_lo", "ci_hi"):
            assert k in first

    def test_non_target_nodes_get_null_cone(self, tmp_path):
        state = _make_state(tmp_path)
        enrich_with_forecast_cones(state)
        nodes = state["system_model"]["topology"]["entities"]
        non_target = [n for n in nodes if n["id"] != "wave_height_m"]
        for n in non_target:
            assert n.get("forecast") is None

    def test_no_forecast_yields_null_cones(self, tmp_path):
        state = _make_state(tmp_path)
        state["forecast"] = {"available": False}
        enrich_with_forecast_cones(state)
        for n in state["system_model"]["topology"]["entities"]:
            assert n.get("forecast") is None

    def test_cone_capped_to_horizon_hours(self, tmp_path):
        state = _make_state(tmp_path)
        # Inject a forecast that exceeds the horizon — extra points filtered.
        state["forecast"]["points"] = [
            {"step": i+1, "value": 1.0, "ci_low": 0.8, "ci_high": 1.2}
            for i in range(int(FORECAST_CONE_HOURS) + 5)
        ]
        enrich_with_forecast_cones(state)
        target = state["system_model"]["topology"]["entities"][0]
        offsets = [p["t"] for p in target["forecast"] if p.get("t") is not None]
        assert all(o <= FORECAST_CONE_HOURS for o in offsets)


# ============================================================
# B.5: Phase space
# ============================================================

class TestPhaseSpace:

    def test_phase_space_picks_two_axes(self, tmp_path):
        state = _make_state(tmp_path)
        enrich_with_phase_space(state)
        ps = state["system_model"]["phase_space"]
        assert ps is not None
        assert ps["x_axis"] in ("wave_height_m", "wind_speed_ms",
                                  "pressure_hpa")
        assert ps["y_axis"] in ("wave_height_m", "wind_speed_ms",
                                  "pressure_hpa")
        assert ps["x_axis"] != ps["y_axis"]

    def test_phase_space_history_populated(self, tmp_path):
        state = _make_state(tmp_path)
        enrich_with_phase_space(state)
        ps = state["system_model"]["phase_space"]
        assert isinstance(ps["history"], list)
        assert len(ps["history"]) > 0
        first = ps["history"][0]
        assert "x" in first and "y" in first

    def test_phase_space_attractors_for_each_regime(self, tmp_path):
        state = _make_state(tmp_path, multi_regime=True)
        enrich_with_phase_space(state)
        ps = state["system_model"]["phase_space"]
        assert isinstance(ps["attractors"], list)
        labels = {a["label"] for a in ps["attractors"]}
        assert "STABLE" in labels and "STORM" in labels

    def test_phase_space_manifold_when_multiple_regimes(self, tmp_path):
        state = _make_state(tmp_path, multi_regime=True)
        enrich_with_phase_space(state)
        ps = state["system_model"]["phase_space"]
        # With 2 attractors, manifold must exist with 4 endpoints.
        assert ps["manifold"] is not None
        for k in ("x0", "y0", "x1", "y1"):
            assert k in ps["manifold"]

    def test_phase_space_future_when_target_on_axis(self, tmp_path):
        state = _make_state(tmp_path)
        enrich_with_phase_space(state)
        ps = state["system_model"]["phase_space"]
        # Forecast target is wave_height_m — when one of the axes is that,
        # we should have a future trajectory.
        if "wave_height_m" in (ps["x_axis"], ps["y_axis"]):
            assert isinstance(ps["future"], list)

    def test_no_dataset_yields_null_phase_space(self):
        state = {"system_model": {"topology": {"entities": [
            {"id": "x", "data_column": "x"},
        ]}}, "dataset": {"path": None}}
        enrich_with_phase_space(state)
        assert state["system_model"]["phase_space"] is None


# ============================================================
# B.4: Resonances
# ============================================================

class TestResonances:

    def test_detects_coupled_oscillators(self, tmp_path):
        state = _make_state(tmp_path, with_resonance=True)
        enrich_with_resonances(state)
        rs = state["system_model"]["resonances"]
        assert isinstance(rs, list)
        # We injected wind ↔ waves at 10-min period; this pair must be
        # detected with coherence above the floor.
        pairs = {tuple(sorted([r["node_a"], r["node_b"]])) for r in rs}
        assert ("wave_height_m", "wind_speed_ms") in {
            tuple(sorted(p)) for p in pairs
        }
        for r in rs:
            assert r["coherence"] >= RESONANCE_COHERENCE_FLOOR
            assert r["period_minutes"] > 0

    def test_no_resonances_when_independent(self, tmp_path):
        state = _make_state(tmp_path, with_resonance=False)
        enrich_with_resonances(state)
        rs = state["system_model"]["resonances"]
        # Pure-noise pairs should not pass the 0.7 coherence threshold.
        # Allow zero or tiny accidental matches — but if any pair survives,
        # its coherence must be ≥ floor (we never emit below threshold).
        for r in rs:
            assert r["coherence"] >= RESONANCE_COHERENCE_FLOOR

    def test_top_k_cap(self, tmp_path):
        state = _make_state(tmp_path, with_resonance=True)
        enrich_with_resonances(state)
        assert len(state["system_model"]["resonances"]) <= RESONANCE_TOP_K

    def test_no_dataset_yields_empty(self):
        state = {"system_model": {"topology": {"entities": [
            {"id": "x", "data_column": "x"},
            {"id": "y", "data_column": "y"},
        ]}}, "dataset": {"path": None}}
        enrich_with_resonances(state)
        assert state["system_model"]["resonances"] == []

    def test_too_few_nodes_yields_empty(self, tmp_path):
        state = _make_state(tmp_path)
        # Only one entity.
        state["system_model"]["topology"]["entities"] = [
            state["system_model"]["topology"]["entities"][0]
        ]
        enrich_with_resonances(state)
        assert state["system_model"]["resonances"] == []
