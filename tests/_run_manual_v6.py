"""Manual runner for v6 tests (units + dimensions + 5 new priors)."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fantasyai.aurora.units import (
    Dimension, DIMENSIONLESS, MASS, LENGTH, TIME, TEMPERATURE,
    UnitGuess, infer_column_unit, infer_columns,
    dimensions_balance, dimensional_consistency_check,
    dimension_from_unit_string,
)
from fantasyai.aurora.units.dimensions import (
    VELOCITY, ACCELERATION, FORCE, ENERGY, POWER, PRESSURE, FREQUENCY,
)
from fantasyai.aurora.units.inference import summarize_inference
from fantasyai.aurora.priors import list_priors, match_physics_fit, get_prior

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


print("=== dimension algebra ===")

def d1():
    """velocity = length / time."""
    v = LENGTH / TIME
    assert v.length == 1.0
    assert v.time == -1.0
    assert v.equals(VELOCITY)
run("velocity = length / time", d1)

def d2():
    """force = mass * acceleration = M·L/T²."""
    F = MASS * ACCELERATION
    assert F.equals(FORCE)
    assert F.mass == 1.0
    assert F.length == 1.0
    assert F.time == -2.0
run("force = mass * acceleration", d2)

def d3():
    """energy = force * length = M·L²/T²."""
    E = FORCE * LENGTH
    assert E.equals(ENERGY)
run("energy = force * length", d3)

def d4():
    """power = energy / time."""
    P = ENERGY / TIME
    assert P.equals(POWER)
run("power = energy / time", d4)

def d5():
    """pressure = force / area."""
    AREA = LENGTH ** 2
    P = FORCE / AREA
    assert P.equals(PRESSURE)
run("pressure = force / area", d5)

def d6():
    """frequency = 1/time."""
    f = TIME ** -1
    assert f.equals(FREQUENCY)
run("frequency = 1/time", d6)

def d7():
    """dimensionless * any dimension = same dimension."""
    assert (DIMENSIONLESS * MASS).equals(MASS)
    assert (LENGTH / LENGTH).equals(DIMENSIONLESS)
run("dimensionless × M = M; L/L = dimensionless", d7)

def d8():
    """short_name() returns named compound dimensions."""
    assert VELOCITY.short_name() == "velocity"
    assert FORCE.short_name() == "force"
    assert PRESSURE.short_name() == "pressure"
    # Random combination should fall through to symbolic.
    weird = LENGTH ** 5 * MASS ** 3
    name = weird.short_name()
    assert "M" in name and "L" in name
run("short_name returns named dimensions", d8)


print("\n=== unit inference ===")

def i1():
    """temperature_c → temperature, celsius."""
    g = infer_column_unit("temperature_c")
    assert g.dimension is not None
    assert g.dimension.equals(TEMPERATURE)
    assert g.confidence >= 0.9
    assert g.unit == "celsius"
run("temperature_c → temperature/celsius", i1)

def i2():
    """wind_speed_mps → velocity, mps."""
    g = infer_column_unit("wind_speed_mps")
    assert g.dimension is not None
    assert g.dimension.equals(VELOCITY)
    assert g.unit == "mps"
run("wind_speed_mps → velocity/mps", i2)

def i3():
    """barometric_pressure_hpa → pressure, hpa."""
    g = infer_column_unit("barometric_pressure_hpa")
    assert g.dimension is not None
    assert g.dimension.equals(PRESSURE)
    assert g.unit == "hpa"
run("barometric_pressure_hpa → pressure/hpa", i3)

def i4():
    """vibration_g → acceleration, g0 (gravity)."""
    g = infer_column_unit("vibration_g")
    assert g.dimension is not None
    assert g.dimension.equals(ACCELERATION)
run("vibration_g → acceleration", i4)

def i5():
    """rpm → frequency."""
    g = infer_column_unit("rpm")
    assert g.dimension is not None
    assert g.dimension.equals(FREQUENCY)
run("rpm → frequency", i5)

def i6():
    """timestamp / date → time."""
    g = infer_column_unit("timestamp_utc")
    assert g.dimension is not None
    assert g.dimension.equals(TIME)
    g2 = infer_column_unit("date")
    assert g2.dimension is not None
    assert g2.dimension.equals(TIME)
run("timestamp / date → time", i6)

def i7():
    """unknown column → no inference, confidence 0."""
    g = infer_column_unit("xyz_random_name")
    assert g.confidence == 0.0
    assert g.dimension is None
run("unknown column → no inference", i7)

def i8():
    """humidity_pct → dimensionless."""
    g = infer_column_unit("humidity_pct")
    assert g.dimension is not None
    assert g.dimension.equals(DIMENSIONLESS)
run("humidity_pct → dimensionless", i8)

def i9():
    """bulk infer + summary."""
    cols = ["timestamp_utc", "temperature_c", "wind_speed_mps", "humidity_pct",
            "barometric_pressure_hpa", "garbage_xyz"]
    guesses = infer_columns(cols)
    assert len(guesses) == 6
    summary = summarize_inference(guesses)
    assert summary["total_columns"] == 6
    assert summary["with_dimension"] >= 5
    assert summary["without_dimension"] <= 1
    assert summary["average_confidence"] > 0.5
run("bulk infer + summary", i9)

def i10():
    """dimension_from_unit_string handles known + unknown."""
    assert dimension_from_unit_string("kg").equals(MASS)
    assert dimension_from_unit_string("celsius").equals(TEMPERATURE)
    assert dimension_from_unit_string("hpa").equals(PRESSURE)
    assert dimension_from_unit_string("xyz") is None
    assert dimension_from_unit_string("") is None
    assert dimension_from_unit_string(None) is None
run("dimension_from_unit_string", i10)


print("\n=== dimensional consistency check ===")

def c1():
    """linear_ode dimensional check: target=temperature, time=seconds."""
    dc = dimensional_consistency_check("linear_ode", TEMPERATURE, TIME, {})
    assert dc.balances is True
    assert dc.expected is not None
    assert dc.confidence == 1.0
    # Note should mention what dimensions a and b must carry.
    assert "must carry" in dc.note
run("linear_ode check with units", c1)

def c2():
    """When time is unknown, check returns confidence=0."""
    dc = dimensional_consistency_check("linear_ode", TEMPERATURE, None, {})
    assert dc.confidence == 0.0
    assert "cannot verify" in dc.note
run("linear_ode check skips when no time dim", c2)

def c3():
    """logistic and damped_oscillator both produce expected param dim hints."""
    dc1 = dimensional_consistency_check("logistic", MASS, TIME, {})
    assert dc1.balances is True
    assert "K must carry" in dc1.note

    dc2 = dimensional_consistency_check("damped_oscillator", LENGTH, TIME, {})
    assert dc2.balances is True
    assert "c must carry" in dc2.note and "k must carry" in dc2.note
run("logistic + damped_oscillator dim hints", c3)

def c4():
    """unknown runner form returns benign default."""
    dc = dimensional_consistency_check("weird_unknown", LENGTH, TIME, {})
    assert dc.confidence == 0.0
    assert "no dimensional check" in dc.note
run("unknown runner form returns benign", c4)

def c5():
    """dimensions_balance pure check."""
    assert dimensions_balance(LENGTH, LENGTH) is True
    assert dimensions_balance(LENGTH, TIME) is False
    # None on either side → cannot verify; treat as True.
    assert dimensions_balance(LENGTH, None) is True
    assert dimensions_balance(None, LENGTH) is True
run("dimensions_balance pure check", c5)


print("\n=== 5 new priors ===")

def p1():
    """Catalogue grew 9 → 14."""
    priors = list_priors()
    assert len(priors) == 14
run("14 priors loaded", p1)

def p2():
    """All 5 new priors registered."""
    new_ids = [
        "pharmacokinetics_1st_order", "rc_circuit", "compound_interest",
        "pendulum_small_angle", "lc_circuit",
    ]
    for pid in new_ids:
        p = get_prior(pid)
        assert p is not None, f"missing prior: {pid}"
        assert p.matches_runner_form in ("linear_ode", "damped_oscillator")
run("5 new priors registered", p2)

def p3():
    """First-order pharmacokinetics matches a clean drug-decay fit."""
    fit = {"name": "linear_ode", "params": {"a": -0.1, "b": 0.001}, "rmse_test": 0.5}
    matches = match_physics_fit(fit, max_results=10)
    by_id = {m.prior_id: m for m in matches}
    assert "pharmacokinetics_1st_order" in by_id
    nc = by_id["pharmacokinetics_1st_order"]
    assert nc.confidence >= 0.7
    # k_elimination should be 0.1 (= -a)
    assert abs(nc.matched_params["k_elimination"]["fitted"] - 0.1) < 0.001
run("pharmacokinetics matches clean decay", p3)

def p4():
    """Compound interest matches positive growth."""
    fit = {"name": "linear_ode", "params": {"a": 0.05, "b": 0.001}, "rmse_test": 0.5}
    matches = match_physics_fit(fit, max_results=10)
    by_id = {m.prior_id: m for m in matches}
    assert "compound_interest" in by_id
    ci = by_id["compound_interest"]
    # r should be positive (growth)
    assert ci.matched_params["r"]["fitted"] > 0
    # Sign constraint allows ± for compound interest, just b near-zero.
    assert ci.confidence >= 0.7
run("compound_interest matches growth", p4)

def p5():
    """RC circuit matches a clean discharge."""
    fit = {"name": "linear_ode", "params": {"a": -100.0, "b": 0.001}, "rmse_test": 0.1}
    matches = match_physics_fit(fit, max_results=10)
    by_id = {m.prior_id: m for m in matches}
    assert "rc_circuit" in by_id
    rc = by_id["rc_circuit"]
    # tau = -1/a = 0.01 seconds.
    assert abs(rc.matched_params["tau"]["fitted"] - 0.01) < 1e-6
run("rc_circuit matches discharge", p5)

def p6():
    """Pendulum matches an undamped oscillator (c=0)."""
    fit = {"name": "damped_oscillator", "params": {"c": 0.0, "k": 9.81, "dt": 0.01}, "rmse_test": 0.1}
    matches = match_physics_fit(fit, max_results=10)
    by_id = {m.prior_id: m for m in matches}
    assert "pendulum_small_angle" in by_id
    p = by_id["pendulum_small_angle"]
    assert p.confidence >= 0.7
run("pendulum matches undamped oscillator", p6)

def p7():
    """LC circuit matches an undamped oscillator (c near 0)."""
    fit = {"name": "damped_oscillator", "params": {"c": 0.0, "k": 1e6, "dt": 1e-6}, "rmse_test": 0.1}
    matches = match_physics_fit(fit, max_results=10)
    by_id = {m.prior_id: m for m in matches}
    assert "lc_circuit" in by_id
run("lc_circuit matches undamped oscillator", p7)

def p8():
    """Damped oscillator with c>0 should NOT match LC circuit (sign constraint near-zero)."""
    fit = {"name": "damped_oscillator", "params": {"c": 5.0, "k": 1e6, "dt": 1e-6}, "rmse_test": 0.1}
    matches = match_physics_fit(fit, max_results=10)
    by_id = {m.prior_id: m for m in matches}
    # LC requires c near zero; with c=5.0 it should be filtered out.
    assert "lc_circuit" not in by_id
    # But damped_oscillator + rlc_circuit should still match (they accept non-negative c).
    assert "damped_oscillator" in by_id or "rlc_circuit" in by_id
run("LC rejects c=5.0 (near-zero constraint)", p8)

def p9():
    """A clean exponential decay should now match across MANY domains:
    Newton cooling (thermal), exponential, radioactive (research),
    Beer-Lambert (research), pharmacokinetics (research), RC circuit (industrial)."""
    fit = {"name": "linear_ode", "params": {"a": -0.05, "b": 0.001}, "rmse_test": 0.1}
    matches = match_physics_fit(fit, max_results=20)
    by_id = {m.prior_id: m for m in matches}
    expected = {"newton_cooling", "exponential", "radioactive_decay",
                "beer_lambert", "pharmacokinetics_1st_order", "rc_circuit"}
    found = expected & set(by_id)
    # All should match — that's the whole point of having multiple domain interpretations.
    assert len(found) >= 5, f"only matched {found}, expected most of {expected}"
run("clean decay matches 5+ priors across domains", p9)


print(f"\n=== {passed} passed, {failed} failed ===")
sys.exit(0 if failed == 0 else 1)
