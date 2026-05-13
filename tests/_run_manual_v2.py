"""Manual runner for v2 tests — works without pytest."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fantasyai.aurora.priors import (
    list_priors, get_prior, match_physics_fit,
)
from fantasyai.aurora.priors.registry import list_priors_for_runner_form
from fantasyai.aurora.explainers import explain_causal, explain_physics

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

print("=== priors registry ===")

def t1():
    priors = list_priors()
    assert len(priors) == 27, f"expected 27 priors, got {len(priors)}"
    ids = {p.id for p in priors}
    expected = {
        # linear_ode form (7)
        "newton_cooling", "exponential", "radioactive_decay", "beer_lambert",
        "pharmacokinetics_1st_order", "rc_circuit", "compound_interest",
        # logistic form (3)
        "verhulst_logistic", "gompertz", "bass_diffusion",
        # damped_oscillator form (4)
        "damped_oscillator", "rlc_circuit",
        "pendulum_small_angle", "lc_circuit",
        # v2.0 algebraic forms (6)
        "stefan_boltzmann", "kepler_third", "kleibers_law",
        "michaelis_menten", "sigmoid_adoption", "ballistic_motion",
        # v2.0 coupled multi-target families (4)
        "lotka_volterra", "sir_epidemic", "competitive_lv", "linear_coupled_2d",
        # v2.0 PDE families (3)
        "heat_equation", "wave_equation", "advection_equation",
    }
    assert ids == expected, f"got {ids - expected} extra, missing {expected - ids}"
run("27 priors loaded", t1)

def t2():
    p = get_prior("newton_cooling")
    assert p is not None
    assert p.matches_runner_form == "linear_ode"
    assert p.sign_constraints.get("a") == "negative"
    assert get_prior("nonexistent") is None
run("get_prior by id", t2)

def t3():
    linear = list_priors_for_runner_form("linear_ode")
    assert {p.id for p in linear} == {
        "newton_cooling", "exponential", "radioactive_decay", "beer_lambert",
        "pharmacokinetics_1st_order", "rc_circuit", "compound_interest",
    }
    log = list_priors_for_runner_form("logistic")
    assert {p.id for p in log} == {"verhulst_logistic", "gompertz", "bass_diffusion"}
    osc = list_priors_for_runner_form("damped_oscillator")
    assert {p.id for p in osc} == {
        "damped_oscillator", "rlc_circuit",
        "pendulum_small_angle", "lc_circuit",
    }
    # v2.0 algebraic forms
    pl = list_priors_for_runner_form("power_law")
    assert {p.id for p in pl} == {"stefan_boltzmann", "kepler_third", "kleibers_law"}
    sat = list_priors_for_runner_form("saturation")
    assert {p.id for p in sat} == {"michaelis_menten"}
    sig = list_priors_for_runner_form("sigmoid")
    assert {p.id for p in sig} == {"sigmoid_adoption"}
    poly = list_priors_for_runner_form("polynomial_2")
    assert {p.id for p in poly} == {"ballistic_motion"}
run("priors_for_runner_form (7/3/4 + 3/1/1/1)", t3)

print("\n=== prior matcher ===")

def m1():
    cand = {"name": "linear_ode", "params": {"a": -0.05, "b": 1.0}, "rmse_test": 1.5}
    matches = match_physics_fit(cand)
    by_id = {m.prior_id: m for m in matches}
    assert "newton_cooling" in by_id
    nc = by_id["newton_cooling"]
    assert nc.confidence >= 0.9
    assert nc.sign_constraints_satisfied is True
    assert nc.matched_params["k"]["fitted"] > 0
run("matches Newton cooling (decay)", m1)

def m2():
    cand = {"name": "linear_ode", "params": {"a": +0.05, "b": -1.0}, "rmse_test": 1.5}
    matches = match_physics_fit(cand)
    by_id = {m.prior_id: m for m in matches}
    assert "newton_cooling" not in by_id
    assert "exponential" in by_id
run("rejects Newton cooling on growth (sign constraint)", m2)

def m3():
    cand = {"name": "logistic", "params": {"r": 0.2, "K": 1000.0}, "rmse_test": 5.0}
    matches = match_physics_fit(cand)
    assert any(m.prior_id == "verhulst_logistic" for m in matches)
    vl = next(m for m in matches if m.prior_id == "verhulst_logistic")
    assert vl.confidence >= 0.9
    assert vl.matched_params["r"]["in_range"] is True
    assert vl.matched_params["K"]["in_range"] is True
run("matches Verhulst logistic", m3)

def m4():
    cand = {"name": "logistic", "params": {"r": -0.5, "K": 1000.0}, "rmse_test": 5.0}
    matches = match_physics_fit(cand)
    by_id = {m.prior_id: m for m in matches}
    assert "verhulst_logistic" not in by_id
run("rejects logistic on negative r", m4)

def m5():
    cand = {"name": "damped_oscillator", "params": {"c": 0.0, "k": 100.0, "dt": 0.01}, "rmse_test": 0.5}
    matches = match_physics_fit(cand)
    assert any(m.prior_id == "damped_oscillator" for m in matches)
run("damped oscillator c=0 OK", m5)

def m6():
    cand = {"name": "damped_oscillator", "params": {"c": -0.5, "k": 100.0, "dt": 0.01}, "rmse_test": 0.5}
    matches = match_physics_fit(cand)
    by_id = {m.prior_id: m for m in matches}
    assert "damped_oscillator" not in by_id
run("damped oscillator rejects c<0", m6)

def m7():
    matches = match_physics_fit({"name": "weird_neural_network", "params": {}})
    assert matches == []
run("unknown form returns []", m7)

def m8():
    assert match_physics_fit(None) == []
    assert match_physics_fit({}) == []
    assert match_physics_fit({"params": {}}) == []
run("invalid input handled", m8)

def m9():
    """Real noaa fit: a=-106720, b=4990766. k = 106720 — way out of range.
       Newton cooling should NOT match confidently."""
    cand = {"name": "linear_ode", "params": {"a": -106720.88, "b": 4990766.75}, "rmse_test": 5.83}
    matches = match_physics_fit(cand)
    by_id = {m.prior_id: m for m in matches}
    # Either filtered out entirely, or low confidence.
    if "newton_cooling" in by_id:
        assert by_id["newton_cooling"].confidence < 0.9, f"Confidence too high: {by_id['newton_cooling'].confidence}"
run("real noaa unphysical fit doesn't confidently match", m9)

# --- new prior tests ---

def m10():
    """Radioactive decay: pure first-order decay (b ≈ 0, a < 0)."""
    cand = {"name": "linear_ode", "params": {"a": -0.05, "b": 0.001}, "rmse_test": 0.5}
    matches = match_physics_fit(cand, max_results=10)
    by_id = {m.prior_id: m for m in matches}
    assert "radioactive_decay" in by_id, f"got: {list(by_id)}"
    rd = by_id["radioactive_decay"]
    assert rd.confidence >= 0.9, f"got {rd.confidence}"
run("radioactive_decay matches pure first-order decay", m10)

def m11():
    """Beer-Lambert: same shape as decay, different domain."""
    cand = {"name": "linear_ode", "params": {"a": -2.5, "b": 0.0}, "rmse_test": 0.1}
    matches = match_physics_fit(cand, max_results=10)
    by_id = {m.prior_id: m for m in matches}
    assert "beer_lambert" in by_id
    assert by_id["beer_lambert"].confidence >= 0.9
run("beer_lambert matches absorption decay", m11)

def m12():
    """Decay with a source term: b is large (NOT near-zero) → radioactive_decay
       and beer_lambert should fail their near-zero constraint, but newton_cooling
       (which has no near-zero on b) should still match."""
    cand = {"name": "linear_ode", "params": {"a": -0.05, "b": 5.0}, "rmse_test": 0.5}
    matches = match_physics_fit(cand, max_results=10)
    by_id = {m.prior_id: m for m in matches}
    # newton_cooling allows nonzero b (T_inf), should match
    assert "newton_cooling" in by_id, f"got: {list(by_id)}"
    # But radioactive decay / beer_lambert require b near-zero, so confidence should be 0 (filtered)
    assert "radioactive_decay" not in by_id, f"radioactive_decay shouldn't match with b=5: {by_id.get('radioactive_decay')}"
    assert "beer_lambert" not in by_id
run("near-zero sign constraint excludes priors with source term", m12)

def m13():
    """Gompertz growth: matches logistic-form fits at high confidence (shares params)."""
    cand = {"name": "logistic", "params": {"r": 0.05, "K": 500.0}, "rmse_test": 1.0}
    matches = match_physics_fit(cand, max_results=10)
    by_id = {m.prior_id: m for m in matches}
    assert "gompertz" in by_id
    assert by_id["gompertz"].confidence >= 0.9
    # Verhulst should also match (same form, different domain)
    assert "verhulst_logistic" in by_id
run("gompertz growth matches logistic-form fits", m13)

def m14():
    """RLC circuit: same form as damped oscillator."""
    cand = {"name": "damped_oscillator", "params": {"c": 0.5, "k": 100.0, "dt": 0.01}, "rmse_test": 0.5}
    matches = match_physics_fit(cand, max_results=10)
    by_id = {m.prior_id: m for m in matches}
    assert "rlc_circuit" in by_id
    assert by_id["rlc_circuit"].confidence >= 0.9
    # Mechanical oscillator should also match
    assert "damped_oscillator" in by_id
run("rlc_circuit matches damped oscillator form", m14)

def m15():
    """A clean exponential decay should now match 4 distinct priors covering
    different domains — Newton cooling (thermal), exponential (general),
    radioactive decay (research), Beer-Lambert (research). Top 3 returned."""
    cand = {"name": "linear_ode", "params": {"a": -0.1, "b": 0.001}, "rmse_test": 0.2}
    matches = match_physics_fit(cand, max_results=10)
    by_id = {m.prior_id: m for m in matches}
    # All 4 linear-form priors should match (b is small enough for the near-zero ones)
    assert {"newton_cooling", "exponential", "radioactive_decay", "beer_lambert"}.issubset(set(by_id))
    # max_results=3 returns top 3 by confidence
    matches_top3 = match_physics_fit(cand, max_results=3)
    assert len(matches_top3) == 3
run("clean exponential matches 4 priors across domains", m15)

print("\n=== causal explainer ===")

def c1():
    block = {"method": "WHAT_IF_LINEAR_OLS", "n": 365,
             "target_col": "temperature_c", "treatment_col": "precip_mm",
             "covariates_used": ["solar_rad", "humidity"], "delta": 1.0,
             "beta_treatment": 1.35, "estimated_effect": 1.35, "note": "ok"}
    ex = explain_causal(block)
    assert ex["available"] is True
    assert "precip_mm" in ex["headline"]
    assert "1.35" in ex["headline"]
    assert ex["conditioning_set"]["covariates_used"] == ["solar_rad", "humidity"]
    assert ex["estimate"]["effect"] == 1.35
    assert "back-door" in ex["identifiability"]["approach"]
    assert "if you randomized" in ex["falsification"]
run("with covariates", c1)

def c2():
    block = {"method": "linear_ols", "n": 100, "target_col": "y", "treatment_col": "x",
             "covariates_used": [], "delta": 1.0, "beta_treatment": 0.5,
             "estimated_effect": 0.5, "note": "ok"}
    ex = explain_causal(block)
    assert ex["available"] is True
    assert "naive" in ex["identifiability"]["approach"].lower()
    assert any("confound" in l.lower() for l in ex["limitations"])
run("without covariates flags confounding", c2)

def c3():
    ex = explain_causal({"note": "failed", "error": "no time axis"})
    assert ex["available"] is False
run("handles failed block", c3)

def c4():
    assert explain_causal(None)["available"] is False
    assert explain_causal({"note": "skipped"})["available"] is False
    assert explain_causal({"note": "ok", "treatment_col": "x"})["available"] is False
run("handles invalid input", c4)

print("\n=== physics explainer ===")

def p1():
    pd_block = {
        "best": {"name": "linear_ode", "model": "dy/dt = a*y + b",
                 "params": {"a": -0.05, "b": 1.0, "dt": 1.0},
                 "rmse_test": 1.5, "rmse_full": 1.4, "aic": 200, "k": 2},
        "runner_ups": [
            {"name": "logistic", "params": {"r": 0.1, "K": 100}, "rmse_test": 1.8, "aic": 210, "k": 2},
            {"name": "damped_oscillator", "rmse_test": 3.0, "aic": 280, "k": 2}],
        "all_candidates": [], "target_col": "temperature_c", "time_col": "timestamp",
        "physics_consistency_score": 0.94,
    }
    diag = {"y_min": 5.0, "y_max": 30.0, "dy_sign_changes": 3, "is_monotonic": False, "growth_type": "decay"}
    ex = explain_physics(pd_block, diag)
    assert ex["available"] is True
    assert "linear_ode" in ex["headline"]
    assert ex["discovered_law"]["name"] == "linear_ode"
    assert len(ex["candidates_tried"]) == 3
    assert ex["candidates_tried"][0]["is_best"] is True
    prior_ids = {pm["prior_id"] for pm in ex["prior_matches"]}
    assert "newton_cooling" in prior_ids
run("complete physics + prior match", p1)

def p2():
    ex = explain_physics({"note": "failed", "error": "no time axis"})
    assert ex["available"] is False
run("handles failed block", p2)

def p3():
    assert explain_physics(None)["available"] is False
    assert explain_physics({})["available"] is False
    assert explain_physics({"best": {}})["available"] is False
run("handles invalid input", p3)

def p4():
    pd_block = {
        "best": {"name": "linear_ode", "params": {"a": -0.1, "b": 0.5},
                 "rmse_test": 1.5, "k": 2},
        "runner_ups": [],
        "all_candidates": [
            {"name": "linear_ode", "ok": True},
            {"name": "exponential", "ok": False, "error": "scipy_not_available"}],
        "target_col": "y",
    }
    ex = explain_physics(pd_block)
    failed = [c for c in ex["candidates_tried"] if c.get("ok") is False]
    assert len(failed) == 1
    assert failed[0]["name"] == "exponential"
run("includes failed candidates", p4)

def p5():
    pd_block = {
        "best": {"name": "linear_ode", "model": "dy/dt = a*y + b",
                 "params": {"a": -106720.88, "b": 4990766.75},
                 "rmse_test": 5.83, "k": 2},
        "runner_ups": [], "all_candidates": [],
        "target_col": "temperature_c",
    }
    ex = explain_physics(pd_block)
    assert ex["available"] is True
    confident = [pm for pm in ex["prior_matches"]
                 if pm["prior_id"] == "newton_cooling" and pm["confidence"] >= 0.9]
    assert confident == []
run("unphysical fit no confident match", p5)

print(f"\n=== {passed} passed, {failed} failed ===")
sys.exit(0 if failed == 0 else 1)
