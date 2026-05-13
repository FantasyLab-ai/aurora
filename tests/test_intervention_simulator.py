"""
Tests for system_model intervention + simulator.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


# ============================================================
# INTERVENTION
# ============================================================

class TestPropagateIntervention:

    def _model(self):
        return {
            "topology": {
                "entities": [
                    {"id": "A", "label": "A", "role": "forcing"},
                    {"id": "B", "label": "B", "role": "state"},
                    {"id": "C", "label": "C", "role": "response"},
                ],
                "relationships": [
                    {"id": "rAB", "source": "A", "target": "B",
                     "kind": "causal", "confidence": 0.9, "strength": 0.5,
                     "rationale": "A → B"},
                    {"id": "rBC", "source": "B", "target": "C",
                     "kind": "physical", "confidence": 0.7, "strength": 0.4,
                     "rationale": "B → C"},
                ],
            },
            "provenance": {"template_id": "test", "template_version": "v1"},
        }

    def test_unknown_source_returns_warning(self):
        from fantasyai.aurora.system_model import propagate_intervention
        r = propagate_intervention(self._model(), "ghost", 1.0)
        assert r.deltas == []
        assert any("unknown source" in w for w in r.warnings)

    def test_propagation_two_hops(self):
        from fantasyai.aurora.system_model import propagate_intervention
        r = propagate_intervention(self._model(), "A", 10.0, max_depth=3)
        deltas_by_id = {d.entity_id: d for d in r.deltas}
        # B should receive 10 × 0.5 = 5.
        assert "B" in deltas_by_id
        assert abs(deltas_by_id["B"].delta - 5.0) < 0.01
        # C should receive 5 × 0.4 = 2.
        assert "C" in deltas_by_id
        assert abs(deltas_by_id["C"].delta - 2.0) < 0.01

    def test_each_delta_has_contributors(self):
        from fantasyai.aurora.system_model import propagate_intervention
        r = propagate_intervention(self._model(), "A", 5.0)
        for d in r.deltas:
            assert d.contributors
            assert all("relationship_id" in c for c in d.contributors)
            assert all("rationale" in c for c in d.contributors)

    def test_max_depth_truncation(self):
        from fantasyai.aurora.system_model import propagate_intervention
        r = propagate_intervention(self._model(), "A", 1.0, max_depth=1)
        # max_depth=1 → only A→B propagates; C should NOT have a delta.
        ids = {d.entity_id for d in r.deltas}
        assert "B" in ids
        assert "C" not in ids

    def test_confidence_decreases_along_path(self):
        from fantasyai.aurora.system_model import propagate_intervention
        r = propagate_intervention(self._model(), "A", 1.0)
        bc = next(d for d in r.deltas if d.entity_id == "B")
        cc = next(d for d in r.deltas if d.entity_id == "C")
        # Confidence on a longer path should be lower (≤) than the short one.
        assert cc.confidence <= bc.confidence


# ============================================================
# SIMULATOR
# ============================================================

class TestSimulator:

    def _state_with_linear_ode(self):
        return {
            "physics": {
                "discovered_law": {
                    "name": "linear_ode",
                    "params": {"a": -0.1, "b": 1.0, "dt": 1.0},
                },
                "y_max": 10.0,
            },
            "forecast": {
                "available": True,
                "target": "y",
                "points": [{"value": 10.0, "ci_low": 9.5, "ci_high": 10.5}],
            },
            "system_model": {
                "topology": {"entities": [{"id": "y", "data_column": "y"}]},
            },
        }

    def test_linear_ode_runs_forward(self):
        from fantasyai.aurora.system_model import simulate_forward
        r = simulate_forward(self._state_with_linear_ode(), n_steps=20)
        assert r.ok
        assert r.method == "linear_ode"
        assert len(r.trajectory) >= 1
        # CI should be widening monotonically.
        traj = r.trajectory
        for i in range(1, len(traj)):
            ci_prev = traj[i-1].ci_high - traj[i-1].ci_low
            ci_curr = traj[i].ci_high - traj[i].ci_low
            assert ci_curr >= ci_prev - 1e-9

    def test_no_dynamic_returns_error(self):
        from fantasyai.aurora.system_model import simulate_forward
        r = simulate_forward({"physics": {"discovered_law": {}}})
        assert r.ok is False
        assert "no validated forward-integrable" in (r.error or "")

    def test_each_step_carries_equation_string(self):
        from fantasyai.aurora.system_model import simulate_forward
        r = simulate_forward(self._state_with_linear_ode(), n_steps=10)
        for s in r.trajectory:
            assert s.source_equation
            assert "a=" in s.source_equation or "y" in s.source_equation

    def test_logistic_runs_forward(self):
        from fantasyai.aurora.system_model import simulate_forward
        s = {
            "physics": {
                "discovered_law": {
                    "name": "logistic",
                    "params": {"r": 0.2, "K": 100.0, "dt": 0.5},
                },
                "y_max": 50.0,
            },
            "forecast": {"available": True, "points": [{"value": 50.0,
                                                          "ci_low": 48.0,
                                                          "ci_high": 52.0}]},
            "system_model": {"topology": {"entities": []}},
        }
        from fantasyai.aurora.system_model import simulate_forward
        r = simulate_forward(s, n_steps=20)
        assert r.ok
        assert r.method == "logistic"

    def test_pause_when_ci_explodes(self):
        from fantasyai.aurora.system_model import simulate_forward
        # Unstable: positive a, big initial sigma → CI grows fast.
        s = {
            "physics": {
                "discovered_law": {
                    "name": "linear_ode",
                    "params": {"a": 0.5, "b": 0.0, "dt": 1.0},
                },
                "y_max": 1.0,
            },
            "forecast": {"available": True, "points": [{"value": 0.01,
                                                          "ci_low": -2.0,
                                                          "ci_high": 2.0}]},
            "system_model": {"topology": {"entities": []}},
        }
        r = simulate_forward(s, n_steps=200, ci_pause_threshold=2.0)
        assert r.ok
        # Should have paused before the full 200 steps.
        assert r.paused
        assert "CI ratio" in r.paused_reason


# ============================================================
# Aurora tick scaffold smoke
# ============================================================

class TestAuroraTickScript:

    def test_script_exists(self):
        path = ROOT / "bin" / "aurora_tick.py"
        assert path.exists()
        # Just verify it's importable (parses without syntax errors).
        import ast
        ast.parse(path.read_text(encoding="utf-8"))
