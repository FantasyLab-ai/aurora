"""Manual runner for v3 tests (whatif engine)."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fantasyai.aurora.whatif import (
    InterventionSpec, parse_intervention_text,
    simulate_intervention, check_identifiability,
)

passed = 0
failed = 0
def run(name, fn):
    global passed, failed
    try:
        fn()
        print(f"  PASS  {name}")
        passed += 1
    except AssertionError as e:
        print(f"  FAIL  {name}: {e}")
        failed += 1
    except Exception as e:
        print(f"  ERR   {name}: {type(e).__name__}: {e}")
        failed += 1

print("=== parser ===")

def p1():
    spec, used = parse_intervention_text("precip_mm +5", use_llm_fallback=False)
    assert used == "regex"; assert spec.variable == "precip_mm"
    assert spec.kind == "delta"; assert spec.value == 5.0
run("signed delta", p1)

def p2():
    spec, used = parse_intervention_text("wind_speed -3.5", use_llm_fallback=False)
    assert used == "regex"; assert spec.value == -3.5
run("negative signed delta", p2)

def p3():
    spec, used = parse_intervention_text("set wind_speed to 25", use_llm_fallback=False)
    assert used == "regex"; assert spec.kind == "set"; assert spec.value == 25.0
run("set intervention", p3)

def p4():
    spec, used = parse_intervention_text("temperature_c = 18.5", use_llm_fallback=False)
    assert spec.kind == "set"; assert spec.value == 18.5
run("equals form", p4)

def p5():
    spec, used = parse_intervention_text("if precip_mm increases by 5", use_llm_fallback=False)
    assert spec.value == 5.0; assert spec.kind == "delta"
run("increases by", p5)

def p6():
    spec, used = parse_intervention_text("pressure drops by 6", use_llm_fallback=False)
    assert spec.value == -6.0
run("drops by", p6)

def p7():
    spec, used = parse_intervention_text(
        "precip_mm +5 for 12 hours", use_llm_fallback=False, dt_seconds=3600,
    )
    assert spec.duration_steps == 12
run("duration in hours w/ dt=3600", p7)

def p8():
    spec, _ = parse_intervention_text("wind_speed +2 for 24 steps", use_llm_fallback=False)
    assert spec.duration_steps == 24
run("duration in steps", p8)

def p9():
    cols = ["timestamp", "wind_speed_mps", "wave_height_m"]
    spec, used = parse_intervention_text("wind +5", available_columns=cols, use_llm_fallback=False)
    assert spec.variable == "wind_speed_mps"
run("substring variable resolution", p9)

def p10():
    spec, used = parse_intervention_text("complete nonsense", use_llm_fallback=False)
    assert spec is None; assert used == "none"
run("unparseable returns none", p10)

print("\n=== identifiability ===")

def _deep(treatment="precip_mm", target="temperature_c", covariates=None):
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

def _struct(cols):
    return {"columns": [{"name": c, "role": "numeric", "dtype": "float64"} for c in cols]}

def i1():
    spec = InterventionSpec(variable="precip_mm", kind="delta", value=5.0)
    ident = check_identifiability(spec, deep_math=_deep(covariates=["solar_rad"]),
                                   structure=_struct(["precip_mm", "temperature_c", "solar_rad"]))
    assert ident["identifiable"] is True
    assert "back-door" in ident["approach"]
run("identifies when treatment matches", i1)

def i2():
    spec = InterventionSpec(variable="precip_mm", kind="delta", value=5.0)
    ident = check_identifiability(spec, deep_math=_deep(covariates=[]),
                                   structure=_struct(["precip_mm", "temperature_c"]))
    assert ident["identifiable"] is True
    assert "naive" in ident["approach"].lower()
    assert any("confound" in c.lower() for c in ident["caveats"])
run("flags missing covariates", i2)

def i3():
    spec = InterventionSpec(variable="ghost_var", kind="delta", value=1.0)
    ident = check_identifiability(spec, deep_math=_deep(),
                                   structure=_struct(["precip_mm", "temperature_c"]))
    assert ident["identifiable"] is False
    assert any("not present" in v for v in ident["violated_if"])
run("refuses unknown variable", i3)

def i4():
    spec = InterventionSpec(variable="humidity_pct", kind="delta", value=5.0)
    ident = check_identifiability(spec, deep_math=_deep(),
                                   structure=_struct(["precip_mm", "temperature_c", "humidity_pct"]))
    assert ident["identifiable"] is False
    assert any("treatment" in s.lower() for s in ident["satisfied_if"])
run("refuses var not in any model", i4)

print("\n=== simulator ===")

def s1():
    spec = InterventionSpec(variable="precip_mm", kind="delta", value=5.0)
    result = simulate_intervention(spec, deep_math=_deep(covariates=["solar_rad"]),
                                    structure=_struct(["precip_mm", "temperature_c", "solar_rad"]))
    assert result["ok"] is True
    assert result["method"] == "linear_ols_effect_lookup"
    assert abs(result["delta_target"] - 1.35*5.0) < 1e-6
run("OLS path on treatment match", s1)

def s2():
    spec = InterventionSpec(variable="precip_mm", kind="delta", value=5.0)
    deep = _deep(covariates=["solar_rad"])
    deep["forecasting"] = {"sigma": 2.0}
    result = simulate_intervention(spec, deep_math=deep, structure=None)
    assert result["ci_low"] is not None
    assert result["ci_low"] < result["delta_target"] < result["ci_high"]
run("OLS path produces CI from sigma", s2)

def s3():
    spec = InterventionSpec(variable="temperature_c", kind="set", value=30.0)
    deep = {
        "target_col": "temperature_c",
        "physics_discovery": {
            "best": {"name": "linear_ode",
                     "params": {"a": -0.05, "b": 1.0, "dt": 1.0}, "k": 2},
            "target_col": "temperature_c",
        },
    }
    struct = {"columns": [{"name": "temperature_c"}]}
    result = simulate_intervention(spec, deep_math=deep, structure=struct, horizon_steps=10)
    assert result["ok"] is True
    assert result["method"] == "physics_forward_sim_linear_ode"
    assert len(result["trajectory"]) == 10
    assert result["trajectory"][-1]["value"] < 30.0  # cooling
run("physics linear_ode forward sim", s3)

def s4():
    spec = InterventionSpec(variable="population", kind="set", value=10.0)
    deep = {
        "target_col": "population",
        "physics_discovery": {
            "best": {"name": "logistic",
                     "params": {"r": 0.1, "K": 1000.0, "dt": 1.0}, "k": 2},
            "target_col": "population",
        },
    }
    struct = {"columns": [{"name": "population"}]}
    result = simulate_intervention(spec, deep_math=deep, structure=struct, horizon_steps=20)
    assert result["ok"] is True
    assert result["trajectory"][-1]["value"] > 10.0  # growth
run("physics logistic forward sim", s4)

def s5():
    spec = InterventionSpec(variable="totally_unknown", kind="delta", value=1.0)
    result = simulate_intervention(spec, deep_math=_deep(),
                                    structure=_struct(["precip_mm", "temperature_c"]))
    assert result["ok"] is True
    assert result["identifiable"] is False
    assert result["method"] == "refused"
    assert result["delta_target"] is None
    assert result["trajectory"] == []
run("refuses cleanly on unknown variable", s5)

def s6():
    spec = InterventionSpec(variable="x", value=1.0)
    result = simulate_intervention(spec, deep_math=None, structure=None)
    assert result["ok"] is False
run("handles missing deep_math", s6)

def s7():
    spec = InterventionSpec(variable="precip_mm", kind="delta", value=5.0)
    deep = _deep(covariates=[])  # no covariates
    result = simulate_intervention(spec, deep_math=deep, structure=_struct(["precip_mm", "temperature_c"]))
    assert "confounding" in result["headline"].lower() or "no covariate" in result["headline"].lower()
run("headline flags missing covariates", s7)

print(f"\n=== {passed} passed, {failed} failed ===")
sys.exit(0 if failed == 0 else 1)
