"""
Tests for the live what-if engine: NL parser, simulator, identifiability check.

Covers:
* regex parser handles the 5 documented input shapes
* parser correctly resolves variable names against available columns
* parser extracts duration with unit conversion to step count
* simulator picks the right path (OLS vs physics) based on what's in deep_math
* simulator refuses cleanly on unknown variables (identifiable=False)
* simulator flags missing covariates as a confounding caveat
* physics forward-sim produces a non-trivial trajectory for each ODE form
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fantasyai.aurora.whatif import (
    InterventionSpec, parse_intervention_text,
    simulate_intervention, check_identifiability,
)


# ============================================================
# Parser
# ============================================================

class TestParser:

    def test_signed_delta(self):
        spec, used = parse_intervention_text("precip_mm +5", use_llm_fallback=False)
        assert used == "regex"
        assert spec.variable == "precip_mm"
        assert spec.kind == "delta"
        assert spec.value == 5.0
        assert spec.duration_steps is None

    def test_negative_signed_delta(self):
        spec, used = parse_intervention_text("wind_speed -3.5", use_llm_fallback=False)
        assert used == "regex"
        assert spec.variable == "wind_speed"
        assert spec.kind == "delta"
        assert spec.value == -3.5

    def test_set_intervention(self):
        spec, used = parse_intervention_text("set wind_speed to 25", use_llm_fallback=False)
        assert used == "regex"
        assert spec.variable == "wind_speed"
        assert spec.kind == "set"
        assert spec.value == 25.0

    def test_equals_form(self):
        spec, used = parse_intervention_text("temperature_c = 18.5", use_llm_fallback=False)
        assert used == "regex"
        assert spec.kind == "set"
        assert spec.value == 18.5

    def test_increases_by(self):
        spec, used = parse_intervention_text("if precip_mm increases by 5", use_llm_fallback=False)
        assert used == "regex"
        assert spec.variable == "precip_mm"
        assert spec.kind == "delta"
        assert spec.value == 5.0

    def test_drops_by(self):
        spec, used = parse_intervention_text("pressure drops by 6", use_llm_fallback=False)
        assert used == "regex"
        assert spec.kind == "delta"
        assert spec.value == -6.0  # drops → negative

    def test_with_duration_hours(self):
        # dt_seconds=3600 means 1 step per hour. "for 12 hours" → 12 steps.
        spec, used = parse_intervention_text(
            "precip_mm +5 for 12 hours",
            use_llm_fallback=False,
            dt_seconds=3600,
        )
        assert spec.duration_steps == 12

    def test_with_duration_steps(self):
        spec, _ = parse_intervention_text(
            "wind_speed +2 for 24 steps", use_llm_fallback=False
        )
        assert spec.duration_steps == 24

    def test_resolves_against_available_columns(self):
        """Case-insensitive + substring match: 'wind' → 'wind_speed_mps'."""
        cols = ["timestamp", "wind_speed_mps", "wave_height_m", "pressure_hpa"]
        spec, used = parse_intervention_text(
            "wind +5", available_columns=cols, use_llm_fallback=False
        )
        assert used == "regex"
        assert spec.variable == "wind_speed_mps"

    def test_unparseable_returns_none_when_llm_disabled(self):
        spec, used = parse_intervention_text(
            "completely random sentence with no intervention",
            use_llm_fallback=False,
        )
        assert spec is None
        assert used == "none"

    def test_empty_input(self):
        spec, used = parse_intervention_text("", use_llm_fallback=False)
        assert spec is None
        assert used == "none"


# ============================================================
# Identifiability
# ============================================================

class TestIdentifiability:

    @staticmethod
    def _deep_math_with_causal(treatment="precip_mm", target="temperature_c", covariates=None):
        return {
            "target_col": target,
            "causal_what_if": {
                "method": "WHAT_IF_LINEAR_OLS", "n": 365,
                "target_col": target, "treatment_col": treatment,
                "covariates_used": covariates or [],
                "delta": 1.0, "beta_treatment": 1.35, "estimated_effect": 1.35,
                "note": "ok",
            },
        }

    @staticmethod
    def _structure(cols):
        return {"columns": [{"name": c, "role": "numeric", "dtype": "float64"} for c in cols]}

    def test_identifies_when_treatment_matches(self):
        spec = InterventionSpec(variable="precip_mm", kind="delta", value=5.0)
        deep = self._deep_math_with_causal(covariates=["solar_rad"])
        struct = self._structure(["precip_mm", "temperature_c", "solar_rad"])
        ident = check_identifiability(spec, deep_math=deep, structure=struct)
        assert ident["identifiable"] is True
        assert "back-door" in ident["approach"]

    def test_flags_missing_covariates(self):
        spec = InterventionSpec(variable="precip_mm", kind="delta", value=5.0)
        deep = self._deep_math_with_causal(covariates=[])
        struct = self._structure(["precip_mm", "temperature_c"])
        ident = check_identifiability(spec, deep_math=deep, structure=struct)
        assert ident["identifiable"] is True
        assert "naive" in ident["approach"].lower()
        assert any("confound" in c.lower() for c in ident["caveats"])

    def test_refuses_unknown_variable(self):
        spec = InterventionSpec(variable="nonexistent_var", kind="delta", value=1.0)
        deep = self._deep_math_with_causal()
        struct = self._structure(["precip_mm", "temperature_c"])
        ident = check_identifiability(spec, deep_math=deep, structure=struct)
        assert ident["identifiable"] is False
        assert any("not present" in v for v in ident["violated_if"])

    def test_refuses_var_not_in_any_model(self):
        """Variable is in the data but neither the OLS treatment nor the physics target."""
        spec = InterventionSpec(variable="humidity_pct", kind="delta", value=5.0)
        deep = self._deep_math_with_causal()
        struct = self._structure(["precip_mm", "temperature_c", "humidity_pct"])
        ident = check_identifiability(spec, deep_math=deep, structure=struct)
        assert ident["identifiable"] is False
        # Should suggest a path forward.
        assert any("treatment" in s.lower() for s in ident["satisfied_if"])


# ============================================================
# Simulator
# ============================================================

class TestSimulator:

    def test_ols_path_used_when_treatment_matches(self):
        spec = InterventionSpec(variable="precip_mm", kind="delta", value=5.0)
        deep = TestIdentifiability._deep_math_with_causal(covariates=["solar_rad"])
        struct = TestIdentifiability._structure(["precip_mm", "temperature_c", "solar_rad"])
        result = simulate_intervention(spec, deep_math=deep, structure=struct)
        assert result["ok"] is True
        assert result["method"] == "linear_ols_effect_lookup"
        assert result["delta_target"] == pytest.approx(1.35 * 5.0, rel=1e-6)
        assert result["target_variable"] == "temperature_c"

    def test_ols_with_residual_sigma_produces_ci(self):
        spec = InterventionSpec(variable="precip_mm", kind="delta", value=5.0)
        deep = TestIdentifiability._deep_math_with_causal(covariates=["solar_rad"])
        deep["forecasting"] = {"sigma": 2.0}
        result = simulate_intervention(spec, deep_math=deep, structure=None)
        assert result["ci_low"] is not None
        assert result["ci_high"] is not None
        assert result["ci_low"] < result["delta_target"] < result["ci_high"]

    def test_physics_path_used_for_target_set(self):
        """When user 'sets' the target variable, simulator runs the fitted ODE forward."""
        spec = InterventionSpec(variable="temperature_c", kind="set", value=30.0)
        deep = {
            "target_col": "temperature_c",
            "physics_discovery": {
                "best": {"name": "linear_ode", "model": "dy/dt = a*y + b",
                         "params": {"a": -0.05, "b": 1.0, "dt": 1.0},
                         "rmse_test": 0.5, "k": 2},
                "target_col": "temperature_c",
            },
        }
        struct = {"columns": [{"name": "temperature_c", "role": "numeric"}]}
        result = simulate_intervention(spec, deep_math=deep, structure=struct, horizon_steps=10)
        assert result["ok"] is True
        assert result["method"] == "physics_forward_sim_linear_ode"
        assert len(result["trajectory"]) == 10
        # Cooling toward T_inf=20 (b/-a). Starting at 30, it should drop.
        assert result["trajectory"][-1]["value"] < 30.0
        # Target should drift toward equilibrium.
        assert result["delta_target"] < 0  # decreased from 30

    def test_physics_logistic_simulation(self):
        spec = InterventionSpec(variable="population", kind="set", value=10.0)
        deep = {
            "target_col": "population",
            "physics_discovery": {
                "best": {"name": "logistic", "params": {"r": 0.1, "K": 1000.0, "dt": 1.0},
                         "rmse_test": 1.0, "k": 2},
                "target_col": "population",
            },
        }
        struct = {"columns": [{"name": "population"}]}
        result = simulate_intervention(spec, deep_math=deep, structure=struct, horizon_steps=20)
        assert result["ok"] is True
        # Logistic from 10 with K=1000 should grow.
        assert result["trajectory"][-1]["value"] > 10.0
        assert result["delta_target"] > 0

    def test_refuses_unknown_variable(self):
        spec = InterventionSpec(variable="totally_unknown", kind="delta", value=1.0)
        deep = TestIdentifiability._deep_math_with_causal()
        struct = TestIdentifiability._structure(["precip_mm", "temperature_c"])
        result = simulate_intervention(spec, deep_math=deep, structure=struct)
        assert result["ok"] is True   # the request succeeded
        assert result["identifiable"] is False
        assert result["method"] == "refused"
        assert result["delta_target"] is None
        assert result["trajectory"] == []
        # Headline should explain the refusal.
        assert "can't answer" in result["headline"].lower() or "not present" in result["headline"]

    def test_handles_missing_deep_math(self):
        spec = InterventionSpec(variable="x", value=1.0)
        result = simulate_intervention(spec, deep_math=None, structure=None)
        assert result["ok"] is False

    def test_headline_flags_missing_covariates(self):
        spec = InterventionSpec(variable="precip_mm", kind="delta", value=5.0)
        deep = TestIdentifiability._deep_math_with_causal(covariates=[])  # no covariates
        struct = TestIdentifiability._structure(["precip_mm", "temperature_c"])
        result = simulate_intervention(spec, deep_math=deep, structure=struct)
        assert "confounding" in result["headline"].lower() or \
               "no covariate" in result["headline"].lower()


# ============================================================
# Spec
# ============================================================

class TestSpec:

    def test_display_text_delta_positive(self):
        s = InterventionSpec(variable="x", kind="delta", value=5.0)
        assert "Δ" in s.display_text() or "+5" in s.display_text()

    def test_display_text_with_duration(self):
        s = InterventionSpec(variable="x", kind="delta", value=5.0, duration_steps=12)
        assert "12" in s.display_text()

    def test_display_text_set(self):
        s = InterventionSpec(variable="x", kind="set", value=25.0)
        assert "=" in s.display_text()

    def test_to_dict_roundtrip(self):
        s = InterventionSpec(variable="x", kind="set", value=10.0, duration_steps=5)
        d = s.to_dict()
        assert d["variable"] == "x"
        assert d["kind"] == "set"
        assert d["value"] == 10.0
        assert d["duration_steps"] == 5
