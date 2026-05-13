"""
Tests for priors registry, prior matcher, causal explainer, physics explainer.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fantasyai.aurora.priors import (
    Prior, list_priors, get_prior, match_physics_fit, PriorMatch,
)
from fantasyai.aurora.priors.registry import (
    NEWTON_COOLING, EXPONENTIAL_DECAY_GROWTH, VERHULST_LOGISTIC,
    DAMPED_HARMONIC_OSCILLATOR, list_priors_for_runner_form,
)
from fantasyai.aurora.explainers import explain_causal, explain_physics


# ============================================================
# Priors registry
# ============================================================

def test_registry_has_27_priors():
    priors = list_priors()
    assert len(priors) == 27
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
    assert ids == expected


def test_get_prior_by_id():
    p = get_prior("newton_cooling")
    assert p is not None
    assert p.matches_runner_form == "linear_ode"
    assert p.sign_constraints.get("a") == "negative"
    assert get_prior("nonexistent") is None


def test_list_priors_for_runner_form():
    # 7 priors map to linear_ode now (added pharmacokinetics, RC, compound_interest).
    linear_priors = list_priors_for_runner_form("linear_ode")
    assert {p.id for p in linear_priors} == {
        "newton_cooling", "exponential", "radioactive_decay", "beer_lambert",
        "pharmacokinetics_1st_order", "rc_circuit", "compound_interest",
    }
    # 3 priors map to logistic.
    log_priors = list_priors_for_runner_form("logistic")
    assert {p.id for p in log_priors} == {"verhulst_logistic", "gompertz", "bass_diffusion"}
    # 4 priors map to damped_oscillator (added pendulum, LC circuit).
    osc_priors = list_priors_for_runner_form("damped_oscillator")
    assert {p.id for p in osc_priors} == {
        "damped_oscillator", "rlc_circuit",
        "pendulum_small_angle", "lc_circuit",
    }


# ============================================================
# Prior matcher
# ============================================================

def test_match_newton_cooling_decay():
    """A reasonable cooling fit (a < 0, k in range) should match Newton's cooling at high confidence."""
    candidate = {
        "name": "linear_ode",
        "model": "dy/dt = a*y + b",
        "params": {"a": -0.05, "b": 1.0},   # k = 0.05, T_inf = 20
        "rmse_test": 1.5,
    }
    matches = match_physics_fit(candidate)
    assert matches, "Newton cooling should match a sensible decay"
    by_id = {m.prior_id: m for m in matches}
    assert "newton_cooling" in by_id
    nc = by_id["newton_cooling"]
    assert nc.confidence >= 0.9
    assert nc.sign_constraints_satisfied is True
    assert "k" in nc.matched_params
    assert nc.matched_params["k"]["fitted"] > 0  # k = -a > 0


def test_match_newton_cooling_rejects_growth():
    """If a > 0 (growth), Newton's cooling should hard-fail (sign constraint)."""
    candidate = {
        "name": "linear_ode",
        "params": {"a": +0.05, "b": -1.0},
        "rmse_test": 1.5,
    }
    matches = match_physics_fit(candidate)
    by_id = {m.prior_id: m for m in matches}
    # Newton cooling should NOT appear (sign constraint a<0 violated → confidence=0).
    assert "newton_cooling" not in by_id
    # But exponential growth has no sign constraint, so it should appear.
    assert "exponential" in by_id


def test_match_logistic_grows_to_carrying_capacity():
    candidate = {
        "name": "logistic",
        "params": {"r": 0.2, "K": 1000.0},
        "rmse_test": 5.0,
    }
    matches = match_physics_fit(candidate)
    assert any(m.prior_id == "verhulst_logistic" for m in matches)
    vl = next(m for m in matches if m.prior_id == "verhulst_logistic")
    assert vl.confidence >= 0.9
    assert vl.matched_params["r"]["in_range"] is True
    assert vl.matched_params["K"]["in_range"] is True


def test_match_logistic_rejects_negative_r():
    """r must be positive; negative growth rate should fail logistic match."""
    candidate = {
        "name": "logistic",
        "params": {"r": -0.5, "K": 1000.0},
        "rmse_test": 5.0,
    }
    matches = match_physics_fit(candidate)
    by_id = {m.prior_id: m for m in matches}
    assert "verhulst_logistic" not in by_id


def test_match_damped_oscillator_with_zero_damping_OK():
    """An oscillator with c=0 (undamped) is a degenerate but valid physical case."""
    candidate = {
        "name": "damped_oscillator",
        "params": {"c": 0.0, "k": 100.0, "dt": 0.01},
        "rmse_test": 0.5,
    }
    matches = match_physics_fit(candidate)
    assert any(m.prior_id == "damped_oscillator" for m in matches)


def test_match_damped_oscillator_rejects_negative_damping():
    """Negative damping = energy injection — not a passive oscillator."""
    candidate = {
        "name": "damped_oscillator",
        "params": {"c": -0.5, "k": 100.0, "dt": 0.01},
        "rmse_test": 0.5,
    }
    matches = match_physics_fit(candidate)
    by_id = {m.prior_id: m for m in matches}
    assert "damped_oscillator" not in by_id


def test_match_returns_empty_for_unknown_form():
    candidate = {"name": "weird_neural_network", "params": {}}
    matches = match_physics_fit(candidate)
    assert matches == []


def test_match_handles_invalid_input():
    assert match_physics_fit(None) == []
    assert match_physics_fit({}) == []
    assert match_physics_fit({"params": {}}) == []   # missing name


def test_match_real_noaa_run_unphysical_fit():
    """The real noaa run produced a=-106720, b=4990766 — way outside Newton's cooling range.

    These params would make k = -a = 106720 (k expected in [1e-6, 1.0]).
    Newton's cooling MAY still appear in the match list at low confidence
    (sign constraint satisfied, but parameter is 1e5× too large) — what matters
    is that confidence < 0.7 so it doesn't get promoted to a finding card,
    and ``why_not`` explicitly states the parameter is out of range.
    """
    candidate = {
        "name": "linear_ode",
        "params": {"a": -106720.88, "b": 4990766.75},
        "rmse_test": 5.83,
    }
    matches = match_physics_fit(candidate)
    by_id = {m.prior_id: m for m in matches}
    # Below the finding-promotion threshold (0.7) and below the green-badge
    # threshold (also 0.7). Should still show in the EXPLAIN modal with full
    # ``why_not`` detail — that's the glass-box honesty.
    if "newton_cooling" in by_id:
        nc = by_id["newton_cooling"]
        assert nc.confidence < 0.7, (
            f"Unphysical fit should not confidently match Newton cooling, "
            f"got confidence={nc.confidence}"
        )
        # The ``why_not`` field must explicitly call out the out-of-range parameter.
        assert any("outside expected range" in w for w in nc.why_not), \
            f"Expected explicit out-of-range note, got why_not={nc.why_not}"


# ============================================================
# Causal explainer
# ============================================================

def test_causal_explanation_with_covariates():
    block = {
        "method": "WHAT_IF_LINEAR_OLS",
        "n": 365,
        "target_col": "temperature_c",
        "treatment_col": "precip_mm",
        "covariates_used": ["solar_rad", "humidity"],
        "delta": 1.0,
        "beta_treatment": 1.35,
        "estimated_effect": 1.35,
        "note": "ok",
    }
    ex = explain_causal(block)
    assert ex["available"] is True
    assert "precip_mm" in ex["headline"]
    assert "+1.35" in ex["headline"] or "1.35" in ex["headline"]
    assert ex["conditioning_set"]["treatment"] == "precip_mm"
    assert ex["conditioning_set"]["outcome"] == "temperature_c"
    assert ex["conditioning_set"]["covariates_used"] == ["solar_rad", "humidity"]
    assert ex["estimate"]["effect"] == 1.35
    assert "back-door criterion" in ex["identifiability"]["approach"]
    assert "if you randomized" in ex["falsification"]
    assert isinstance(ex["limitations"], list)


def test_causal_explanation_without_covariates_flags_confounding():
    block = {
        "method": "linear_ols", "n": 100,
        "target_col": "y", "treatment_col": "x",
        "covariates_used": [], "delta": 1.0,
        "beta_treatment": 0.5, "estimated_effect": 0.5, "note": "ok",
    }
    ex = explain_causal(block)
    assert ex["available"] is True
    # When no covariates, identifiability approach should signal "naive".
    assert "naive" in ex["identifiability"]["approach"].lower()
    # Limitations should include a confounding warning.
    confounding_mentioned = any("confound" in l.lower() for l in ex["limitations"])
    assert confounding_mentioned


def test_causal_explanation_handles_failed_block():
    ex = explain_causal({"note": "failed", "error": "no time axis"})
    assert ex["available"] is False
    assert "no time axis" in (ex["reason"] or "")


def test_causal_explanation_handles_invalid_input():
    assert explain_causal(None)["available"] is False
    assert explain_causal({"note": "skipped"})["available"] is False
    assert explain_causal({"note": "ok", "treatment_col": "x"})["available"] is False  # missing target


# ============================================================
# Physics explainer
# ============================================================

def test_physics_explanation_complete():
    pd_block = {
        "best": {"name": "linear_ode", "model": "dy/dt = a*y + b",
                 "params": {"a": -0.05, "b": 1.0, "dt": 1.0},
                 "rmse_test": 1.5, "rmse_full": 1.4, "aic": 200, "k": 2},
        "runner_ups": [
            {"name": "logistic", "model": "dy/dt = r*y*(1-y/K)",
             "params": {"r": 0.1, "K": 100}, "rmse_test": 1.8, "aic": 210, "k": 2},
            {"name": "damped_oscillator", "rmse_test": 3.0, "aic": 280, "k": 2},
        ],
        "all_candidates": [],
        "target_col": "temperature_c",
        "time_col": "timestamp",
        "physics_consistency_score": 0.94,
    }
    diag = {
        "y_min": 5.0, "y_max": 30.0, "y_mean": 18.0,
        "dy_dt_mean": -0.01, "dy_sign_changes": 3,
        "is_monotonic": False, "growth_type": "decay",
    }
    ex = explain_physics(pd_block, diag)
    assert ex["available"] is True
    assert "linear_ode" in ex["headline"]
    assert "RMSE" in ex["headline"]
    assert ex["discovered_law"]["name"] == "linear_ode"
    # Three candidates: best + 2 runner-ups.
    assert len(ex["candidates_tried"]) == 3
    assert ex["candidates_tried"][0]["is_best"] is True
    assert ex["candidates_tried"][1]["rmse_pct_worse_than_best"] is not None
    assert ex["residual_diagnostics"]["growth_type"] == "decay"
    # Newton cooling should be in prior_matches (a < 0 + k in range).
    prior_ids = {pm["prior_id"] for pm in ex["prior_matches"]}
    assert "newton_cooling" in prior_ids


def test_physics_explanation_handles_failed_block():
    ex = explain_physics({"note": "failed", "error": "no time axis"})
    assert ex["available"] is False
    assert "no time axis" in (ex["reason"] or "")


def test_physics_explanation_handles_invalid_input():
    assert explain_physics(None)["available"] is False
    assert explain_physics({})["available"] is False  # no best
    assert explain_physics({"best": {}})["available"] is False


def test_physics_explanation_includes_failed_candidates():
    pd_block = {
        "best": {"name": "linear_ode", "params": {"a": -0.1, "b": 0.5},
                 "rmse_test": 1.5, "k": 2},
        "runner_ups": [],
        "all_candidates": [
            {"name": "linear_ode", "ok": True},
            {"name": "exponential", "ok": False, "error": "scipy_not_available"},
        ],
        "target_col": "y",
    }
    ex = explain_physics(pd_block)
    failed = [c for c in ex["candidates_tried"] if c.get("ok") is False]
    assert len(failed) == 1
    assert failed[0]["name"] == "exponential"
    assert "scipy" in failed[0]["error"]


def test_physics_explanation_unphysical_fit_no_match():
    """The real noaa fit had wildly out-of-range params — explainer should still
    work, and prior_matches should be empty (or at least not confidently)."""
    pd_block = {
        "best": {"name": "linear_ode", "model": "dy/dt = a*y + b",
                 "params": {"a": -106720.88, "b": 4990766.75},
                 "rmse_test": 5.83, "k": 2},
        "runner_ups": [],
        "all_candidates": [],
        "target_col": "temperature_c",
    }
    ex = explain_physics(pd_block)
    assert ex["available"] is True
    # Prior matches should not include newton_cooling at high confidence
    # because k = 106720 is way outside [1e-6, 1.0].
    confident = [pm for pm in ex["prior_matches"]
                 if pm["prior_id"] == "newton_cooling" and pm["confidence"] >= 0.9]
    assert confident == []
