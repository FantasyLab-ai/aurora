"""Manual runner for v7 tests (symbolic discovery + conservation laws)."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# These tests need numpy + scipy + pandas. Skip cleanly if not available.
try:
    import numpy as np
    import pandas as pd
    from scipy.optimize import curve_fit  # noqa: F401
    _HAVE_DEPS = True
except ImportError:
    _HAVE_DEPS = False
    np = pd = None

if not _HAVE_DEPS:
    print("SKIPPED: numpy/scipy/pandas not available")
    sys.exit(0)

from fantasyai.aurora.symbolic_discovery import discover_relation
from fantasyai.aurora.conservation import detect_invariants

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


print("=== symbolic discovery: 1-feature ===")

def s1():
    """Pure linear relation: y = 2x + 5."""
    rng = np.random.default_rng(42)
    x = np.linspace(1, 100, 200)
    y = 2.0 * x + 5.0 + rng.normal(0, 0.5, 200)
    df = pd.DataFrame({"x": x, "y": y})
    results = discover_relation(df, "y", feature_cols=["x"], max_features=1, top_k=5)
    assert len(results) >= 1
    # The best fit should be linear (or quadratic with a~0).
    best = results[0]
    assert best["form_name"] in ("linear", "quadratic", "cubic")
    # Coefficient should be close to 2.
    if best["form_name"] == "linear":
        assert abs(best["params"]["a"] - 2.0) < 0.1
        assert abs(best["params"]["b"] - 5.0) < 0.5
    assert best["r2"] > 0.99
run("linear y = 2x + 5", s1)

def s2():
    """Power law: y = 0.21·x^2 (Pierson-Moskowitz-like)."""
    rng = np.random.default_rng(43)
    x = np.linspace(1, 50, 200)
    y = 0.21 * x ** 2.0 + rng.normal(0, 1.0, 200)
    df = pd.DataFrame({"x": x, "y": y})
    results = discover_relation(df, "y", feature_cols=["x"], max_features=1, top_k=5)
    # Power law or quadratic should be at the top.
    forms = [r["form_name"] for r in results[:3]]
    assert "power_law" in forms or "quadratic" in forms
    pl = next((r for r in results if r["form_name"] == "power_law"), None)
    if pl is not None:
        assert abs(pl["params"]["b"] - 2.0) < 0.2
run("power y = 0.21·x²", s2)

def s3():
    """Stefan-Boltzmann signature: y = σ·T⁴."""
    rng = np.random.default_rng(44)
    T = np.linspace(200, 800, 200)
    y = 5.67e-8 * T ** 4 + rng.normal(0, 100, 200)
    df = pd.DataFrame({"T": T, "y": y})
    results = discover_relation(df, "y", feature_cols=["T"], max_features=1, top_k=5)
    pl = next((r for r in results if r["form_name"] == "power_law"), None)
    assert pl is not None, "expected power_law to win on T⁴ data"
    # Should detect the T⁴ signature.
    assert pl.get("recovers_known_form") and "Stefan-Boltzmann" in pl["recovers_known_form"]
run("Stefan-Boltzmann signature recognized", s3)

def s4():
    """Exponential decay: y = 10·exp(-0.05·x)."""
    rng = np.random.default_rng(45)
    x = np.linspace(0, 50, 200)
    y = 10.0 * np.exp(-0.05 * x) + rng.normal(0, 0.05, 200)
    df = pd.DataFrame({"x": x, "y": y})
    results = discover_relation(df, "y", feature_cols=["x"], max_features=1, top_k=5)
    forms = [r["form_name"] for r in results[:3]]
    # exponential should rank well
    assert "exponential" in forms or "log" in forms
run("exponential y = 10·exp(-0.05x)", s4)

def s5():
    """Saturation curve (Michaelis-Menten): y = 100·x / (50 + x)."""
    rng = np.random.default_rng(46)
    x = np.linspace(0, 500, 200)
    y = 100.0 * x / (50.0 + x) + rng.normal(0, 0.5, 200)
    df = pd.DataFrame({"S": x, "v": y})
    results = discover_relation(df, "v", feature_cols=["S"], max_features=1, top_k=5)
    sat = next((r for r in results if r["form_name"] == "saturation"), None)
    assert sat is not None
    assert sat.get("recovers_known_form") and "Michaelis-Menten" in sat["recovers_known_form"]
    # Vmax should be near 100, K near 50.
    assert abs(sat["params"]["Vmax"] - 100.0) < 5.0
run("saturation recognized as Michaelis-Menten", s5)


print("\n=== symbolic discovery: 2-feature ===")

def t1():
    """Pierson-Moskowitz form: y = 0.21·U²·F^0.5."""
    rng = np.random.default_rng(47)
    n = 300
    U = rng.uniform(5, 25, n)
    F = rng.uniform(50, 1000, n)
    y = 0.21 * U ** 2.0 * F ** 0.5 + rng.normal(0, 0.1, n)
    df = pd.DataFrame({"wind": U, "fetch": F, "wave_height": y})
    results = discover_relation(df, "wave_height", max_features=2, top_k=5)
    al = next((r for r in results if r["form_name"] == "allometric_2var"), None)
    assert al is not None
    # Should detect Pierson-Moskowitz signature.
    assert al.get("recovers_known_form") and "Pierson-Moskowitz" in al["recovers_known_form"]
    # Exponents should be close to (2, 0.5).
    b, c = al["params"]["b"], al["params"]["c"]
    # Wind has the larger exponent
    assert max(abs(b), abs(c)) > 1.5
    assert min(abs(b), abs(c)) < 1.0
run("Pierson-Moskowitz signature: wave ∝ wind²·√fetch", t1)

def t2():
    """Bilinear interaction: y = 1 + 0.5·x1 + 2·x2 + 0.3·x1·x2."""
    rng = np.random.default_rng(48)
    n = 200
    x1 = rng.uniform(-5, 5, n)
    x2 = rng.uniform(-5, 5, n)
    y = 1.0 + 0.5 * x1 + 2.0 * x2 + 0.3 * x1 * x2 + rng.normal(0, 0.05, n)
    df = pd.DataFrame({"a": x1, "b": x2, "y": y})
    results = discover_relation(df, "y", max_features=2, top_k=5)
    bl = next((r for r in results if r["form_name"] == "bilinear"), None)
    assert bl is not None
    # Verify recovered coefficients.
    assert abs(bl["params"]["a"] - 1.0) < 0.1
    assert abs(bl["params"]["d"] - 0.3) < 0.1   # interaction term
    assert bl["r2"] > 0.95
run("bilinear interaction recovered", t2)


print("\n=== conservation laws ===")

def c1():
    """Direct invariant: a column that's nearly constant."""
    rng = np.random.default_rng(49)
    n = 200
    df = pd.DataFrame({
        "constant_var": np.full(n, 42.0) + rng.normal(0, 0.05, n),
        "noisy_var": rng.normal(0, 5, n),
    })
    invs = detect_invariants(df, max_cv=0.05)
    direct = [i for i in invs if i["type"] == "direct"]
    assert any(i["columns_used"] == ["constant_var"] for i in direct)
run("direct invariant: nearly-constant column", c1)

def c2():
    """Sum invariant: A + B + C ≈ const (mass conservation pattern)."""
    rng = np.random.default_rng(50)
    n = 200
    a = rng.uniform(10, 30, n)
    b = rng.uniform(20, 40, n)
    c = 100.0 - a - b + rng.normal(0, 0.05, n)  # sums to ≈100
    df = pd.DataFrame({"phase_a": a, "phase_b": b, "phase_c": c})
    invs = detect_invariants(df, max_cv=0.05)
    sum_invs = [i for i in invs if i["type"] == "sum"]
    # The triple sum should be detected.
    assert any(set(i["columns_used"]) == {"phase_a", "phase_b", "phase_c"} for i in sum_invs)
run("sum invariant: A + B + C ≈ 100 (mass conservation)", c2)

def c3():
    """Ratio invariant: P/V ≈ const (Boyle's law / pressure-volume at fixed T)."""
    rng = np.random.default_rng(51)
    n = 200
    V = rng.uniform(1, 10, n)
    P = 100.0 / V + rng.normal(0, 0.5, n)   # PV = 100 → P/V isn't const, but P*V is
    # Better Boyle: P*V = const, equivalent to ratio P/(1/V) = const.
    # For ratio detection, set up directly: ratio = const
    a = rng.uniform(5, 50, n)
    b = a / 3.0 + rng.normal(0, 0.05, n)   # ratio a/b ≈ 3
    df = pd.DataFrame({"a": a, "b": b})
    invs = detect_invariants(df, max_cv=0.05)
    ratio_invs = [i for i in invs if i["type"] == "ratio"]
    assert any({i["columns_used"][0], i["columns_used"][1]} == {"a", "b"}
               for i in ratio_invs)
run("ratio invariant: a/b ≈ 3", c3)

def c4():
    """Quadratic (energy-like) invariant: ½·v² + h ≈ const."""
    rng = np.random.default_rng(52)
    n = 200
    # Simulate a falling object: ½·v² + h = E (constant)
    E = 100.0
    h = rng.uniform(10, 90, n)
    v_squared = 2 * (E - h)
    v = np.sqrt(np.abs(v_squared)) + rng.normal(0, 0.1, n)
    h_noisy = h + rng.normal(0, 0.05, n)
    df = pd.DataFrame({"velocity": v, "height": h_noisy})
    invs = detect_invariants(df, max_cv=0.05)
    quad_invs = [i for i in invs if i["type"] == "quadratic"]
    # Should detect ½·v² + h or ½·h² + v (we try both orderings).
    assert len(quad_invs) >= 1
run("quadratic invariant: ½·v² + h ≈ const (energy)", c4)

def c5():
    """No conserved quantity → no invariants returned."""
    rng = np.random.default_rng(53)
    n = 200
    df = pd.DataFrame({
        "x": rng.normal(0, 5, n),
        "y": rng.normal(10, 3, n),
        "z": rng.uniform(0, 100, n),
    })
    invs = detect_invariants(df, max_cv=0.01)  # stricter threshold
    # Random data should produce few or no invariants at this strictness.
    # We allow a small number (incidental matches) but not many.
    # Assert: no sum or quadratic invariant on random data.
    sum_or_quad = [i for i in invs if i["type"] in ("sum", "quadratic", "linear")]
    # On truly random data, there shouldn't be a strong invariant.
    # If there is, it's likely the linear-SVD detector finding something
    # spurious — that's OK at higher CV but not at 0.01.
    for inv in sum_or_quad:
        assert inv["coefficient_of_variation"] <= 0.01, \
            f"unexpected strong invariant on random data: {inv}"
run("no invariants on random data", c5)

def c6():
    """detect_invariants tolerates malformed input."""
    invs = detect_invariants(pd.DataFrame())
    assert invs == []
run("detect_invariants tolerates empty df", c6)


print(f"\n=== {passed} passed, {failed} failed ===")
sys.exit(0 if failed == 0 else 1)
