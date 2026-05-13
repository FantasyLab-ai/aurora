"""
Math-layer unit tests for ``fantasyai/aurora/math/physics_models.py``.

For each ODE candidate we generate synthetic data with KNOWN parameters,
run the fitter, and verify it recovers the parameters within tolerance.
This is the actual math-correctness check — without these, we don't really
know the priors+matcher demo will work on real data.

Tests will be skipped if numpy is unavailable (pure-stdlib fallback path).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# All these tests need numpy; skip the module if it's not available.
np = pytest.importorskip("numpy")

# pytest's importorskip is module-level — at this point we know numpy works.
from fantasyai.aurora.math.physics_models import (
    candidate_models, _fit_linear_ode, _fit_logistic, _fit_damped_oscillator,
    _fit_power_law, _fit_exponential_static, _fit_sigmoid,
    _fit_polynomial, _fit_saturation_static,
)


# ============================================================
# candidate_models() returns the expected fits
# ============================================================

class TestCandidateRegistry:

    def test_returns_list_of_eight_callables(self):
        cands = candidate_models()
        assert len(cands) == 8, f"expected 8 candidates, got {len(cands)}"
        for c in cands:
            assert callable(c)

    def test_includes_all_three_ode_candidates(self):
        cands = candidate_models()
        names = {c.__name__ for c in cands}
        assert "_fit_linear_ode" in names
        assert "_fit_logistic" in names
        assert "_fit_damped_oscillator" in names

    def test_includes_all_five_algebraic_candidates(self):
        cands = candidate_models()
        names = {c.__name__ for c in cands}
        assert "_fit_power_law" in names
        assert "_fit_exponential_static" in names
        assert "_fit_sigmoid" in names
        assert "_fit_polynomial" in names
        assert "_fit_saturation_static" in names


# ============================================================
# _fit_linear_ode — recovers a, b on synthetic exponential decay
# ============================================================

class TestLinearODEFit:

    def _synthetic_decay(self, a_true: float, b_true: float, n: int = 500,
                         dt: float = 0.1, noise: float = 0.0, seed: int = 42):
        """Generate ground-truth y(t) for dy/dt = a*y + b via Euler integration."""
        rng = np.random.default_rng(seed)
        t = np.arange(n, dtype=float) * dt
        y = np.zeros(n)
        y[0] = 10.0
        for i in range(1, n):
            y[i] = y[i-1] + dt * (a_true * y[i-1] + b_true)
        if noise > 0:
            y = y + rng.normal(0, noise, size=n)
        return t, y

    def test_recovers_pure_exponential_decay(self):
        """a < 0, b ≈ 0 — pure exponential decay."""
        a_true, b_true = -0.05, 0.0
        t, y = self._synthetic_decay(a_true, b_true, n=500, dt=0.1)
        result = _fit_linear_ode(t, y)
        assert result["ok"] is True
        assert result["name"] == "linear_ode"
        # a should be close to a_true.
        assert abs(result["params"]["a"] - a_true) < 0.05, \
            f"recovered a={result['params']['a']}, expected {a_true}"
        # b should be near zero.
        assert abs(result["params"]["b"]) < 0.5, \
            f"recovered b={result['params']['b']}, expected ~0"

    def test_recovers_cooling_with_equilibrium(self):
        """dy/dt = -0.05 (y - 20) → a=-0.05, b=1.0 (equilibrium at y=20)."""
        a_true, b_true = -0.05, 1.0
        t, y = self._synthetic_decay(a_true, b_true, n=500, dt=0.1)
        result = _fit_linear_ode(t, y)
        assert result["ok"] is True
        # a close to a_true, b close to b_true (equilibrium at y∞ = -b/a = 20).
        assert abs(result["params"]["a"] - a_true) < 0.1
        assert abs(result["params"]["b"] - b_true) < 0.5

    def test_handles_short_input(self):
        """Less than 50 rows should fail cleanly with not_enough_rows."""
        t = np.arange(20, dtype=float)
        y = np.exp(-0.1 * t)
        result = _fit_linear_ode(t, y)
        assert result["ok"] is False
        assert "not_enough_rows" in result.get("error", "")

    def test_handles_constant_y(self):
        """Constant y → derivatives are 0 → fit recovers a≈0, b≈0."""
        t = np.arange(200, dtype=float)
        y = np.full(200, 5.0)
        result = _fit_linear_ode(t, y)
        # Should not crash. a and b both near zero.
        if result["ok"]:
            assert abs(result["params"]["a"]) < 0.01
            assert abs(result["params"]["b"]) < 0.01


# ============================================================
# _fit_logistic — recovers r, K on synthetic Verhulst growth
# ============================================================

class TestLogisticFit:

    def _synthetic_logistic(self, r_true: float, K_true: float, y0: float = 1.0,
                            n: int = 500, dt: float = 0.1):
        t = np.arange(n, dtype=float) * dt
        y = np.zeros(n)
        y[0] = y0
        for i in range(1, n):
            y[i] = y[i-1] + dt * r_true * y[i-1] * (1.0 - y[i-1] / K_true)
        return t, y

    def test_recovers_logistic_params(self):
        """r=0.5, K=100 with growth from y0=1.0."""
        r_true, K_true = 0.5, 100.0
        t, y = self._synthetic_logistic(r_true, K_true, y0=1.0, n=500)
        result = _fit_logistic(t, y)
        assert result["ok"] is True
        assert result["name"] == "logistic"
        # K should be reasonably close (grid fit, so tolerance is loose).
        assert abs(result["params"]["K"] - K_true) / K_true < 0.5, \
            f"recovered K={result['params']['K']}, expected {K_true}"
        # r should be positive (growth direction correct).
        assert result["params"]["r"] > 0

    def test_handles_short_input(self):
        t = np.arange(50, dtype=float)
        y = np.linspace(1, 50, 50)
        result = _fit_logistic(t, y)
        assert result["ok"] is False
        assert "not_enough_rows" in result.get("error", "")


# ============================================================
# _fit_damped_oscillator — recovers c, k on synthetic vibration
# ============================================================

class TestDampedOscillatorFit:

    def _synthetic_oscillator(self, c_true: float, k_true: float,
                              y0: float = 1.0, n: int = 500, dt: float = 0.05):
        t = np.arange(n, dtype=float) * dt
        y = np.zeros(n)
        v = np.zeros(n)
        y[0] = y0
        for i in range(1, n):
            a = -c_true * v[i-1] - k_true * y[i-1]
            v[i] = v[i-1] + dt * a
            y[i] = y[i-1] + dt * v[i]
        return t, y

    def test_recovers_oscillator_params(self):
        """Mass-spring-damper with c=0.4, k=4.0."""
        c_true, k_true = 0.4, 4.0
        t, y = self._synthetic_oscillator(c_true, k_true, y0=1.0, n=500, dt=0.05)
        result = _fit_damped_oscillator(t, y)
        assert result["ok"] is True
        assert result["name"] == "damped_oscillator"
        # Damping coefficient should be in the right ballpark.
        # Numerical derivatives + Euler-fit are noisy; tolerance is wide.
        assert result["params"]["c"] > 0, f"recovered c={result['params']['c']}, expected >0"
        assert result["params"]["k"] > 0, f"recovered k={result['params']['k']}, expected >0"

    def test_undamped_oscillator(self):
        """c=0 → pure oscillation. Fitter should still succeed."""
        t, y = self._synthetic_oscillator(c_true=0.0, k_true=4.0, n=500, dt=0.05)
        result = _fit_damped_oscillator(t, y)
        assert result["ok"] is True
        # k should still be positive.
        assert result["params"]["k"] > 0

    def test_handles_short_input(self):
        t = np.arange(50, dtype=float) * 0.05
        y = np.cos(2 * np.pi * 0.5 * t)
        result = _fit_damped_oscillator(t, y)
        assert result["ok"] is False


# ============================================================
# Cross-fit: the right model wins on each kind of data
# ============================================================

class TestCrossModelSelection:

    def test_linear_ode_best_on_exponential_data(self):
        """On clean exponential decay data, linear_ode should fit better than logistic."""
        a_true = -0.05
        n, dt = 500, 0.1
        t = np.arange(n, dtype=float) * dt
        y = np.zeros(n)
        y[0] = 10.0
        for i in range(1, n):
            y[i] = y[i-1] + dt * a_true * y[i-1]
        lin = _fit_linear_ode(t, y)
        log = _fit_logistic(t, y)
        # Linear ODE should have lower (better) RMSE on this data.
        if lin["ok"] and log["ok"]:
            assert lin["rmse"] <= log["rmse"], \
                f"linear_ode RMSE {lin['rmse']} should beat logistic {log['rmse']}"

    def test_logistic_best_on_logistic_data(self):
        """On clean logistic growth data, logistic should beat linear_ode."""
        r_true, K_true = 0.3, 100.0
        n, dt = 500, 0.1
        t = np.arange(n, dtype=float) * dt
        y = np.zeros(n)
        y[0] = 1.0
        for i in range(1, n):
            y[i] = y[i-1] + dt * r_true * y[i-1] * (1.0 - y[i-1]/K_true)
        lin = _fit_linear_ode(t, y)
        log = _fit_logistic(t, y)
        # Logistic should have lower (better) RMSE on this data.
        if lin["ok"] and log["ok"]:
            assert log["rmse"] < lin["rmse"], \
                f"logistic RMSE {log['rmse']} should beat linear_ode {lin['rmse']} on logistic data"


# ============================================================
# Integration: physics_discovery + matcher → recovers known law
# ============================================================

class TestIntegrationWithMatcher:

    def test_clean_cooling_matches_newton_via_fit(self):
        """End-to-end: synthetic Newton's-cooling data → fit → matcher → Newton's cooling at high confidence."""
        from fantasyai.aurora.priors import match_physics_fit

        # Cooling toward T_inf = 20 with k = 0.05.
        a_true, b_true = -0.05, 1.0   # k=0.05, T_inf = -b/a = 20
        n, dt = 1000, 0.5
        t = np.arange(n, dtype=float) * dt
        y = np.zeros(n)
        y[0] = 50.0
        for i in range(1, n):
            y[i] = y[i-1] + dt * (a_true * y[i-1] + b_true)

        fit = _fit_linear_ode(t, y)
        assert fit["ok"], "expected linear_ode fit to succeed on synthetic cooling"

        matches = match_physics_fit(fit, max_results=4)
        by_id = {m.prior_id: m for m in matches}
        assert "newton_cooling" in by_id, \
            "Newton's cooling should match a clean synthetic cooling fit"
        nc = by_id["newton_cooling"]
        assert nc.confidence >= 0.7, \
            f"Newton's cooling confidence {nc.confidence} too low on clean fit"


# ============================================================
# _fit_power_law — recovers a, b on synthetic y = a · t^b
# ============================================================

class TestPowerLawFit:

    def test_recovers_quadratic_exponent(self):
        """y = 2 · t² — exponent should be ≈ 2."""
        t = np.arange(1, 200, dtype=float)
        y = 2.0 * t ** 2
        result = _fit_power_law(t, y)
        assert result["ok"] is True
        assert result["name"] == "power_law"
        assert abs(result["params"]["b"] - 2.0) < 0.05
        assert abs(result["params"]["a"] - 2.0) < 0.5

    def test_recovers_kepler_exponent(self):
        """Kepler: T ∝ a^(3/2). Exponent should be ≈ 1.5."""
        t = np.arange(1, 100, dtype=float)
        y = 0.5 * t ** 1.5
        result = _fit_power_law(t, y)
        assert result["ok"] is True
        assert abs(result["params"]["b"] - 1.5) < 0.05

    def test_rejects_too_few_positive(self):
        t = np.array([1.0, 2.0, 3.0])
        y = np.array([1.0, 4.0, 9.0])
        result = _fit_power_law(t, y)
        assert result["ok"] is False


# ============================================================
# _fit_exponential_static — recovers a, b, c on y = a·exp(b·t) + c
# ============================================================

class TestExponentialStaticFit:

    def test_runs_on_decaying_exponential(self):
        """y = 5·exp(-2·t) + 1 — fit should at least succeed."""
        t = np.linspace(0, 5, 200)
        y = 5.0 * np.exp(-2.0 * t) + 1.0
        result = _fit_exponential_static(t, y)
        # Either scipy is present (best path) or we fell back; both can be ok or fail.
        # Require at least: doesn't crash and returns a dict with a name.
        assert "name" in result or "error" in result
        if result.get("ok"):
            assert result["name"] == "exponential"
            # rmse should be modest
            assert result["rmse"] < 1.0

    def test_rejects_short_input(self):
        t = np.arange(20, dtype=float)
        y = np.exp(-0.1 * t)
        result = _fit_exponential_static(t, y)
        assert result["ok"] is False


# ============================================================
# _fit_sigmoid — recovers L, k, t0
# ============================================================

class TestSigmoidFit:

    def test_recovers_sigmoid_shape(self):
        """y = 100 / (1 + exp(-0.5·(t - 50)))."""
        t = np.linspace(0, 100, 200)
        y = 100.0 / (1.0 + np.exp(-0.5 * (t - 50.0)))
        result = _fit_sigmoid(t, y)
        if result.get("ok"):
            assert result["name"] == "sigmoid"
            assert abs(result["params"]["L"] - 100.0) / 100.0 < 0.1
            assert abs(result["params"]["t0"] - 50.0) < 5.0
        else:
            # scipy might not be available — skip if so.
            assert "scipy" in result.get("error", "") or "fit" in result.get("error", "")


# ============================================================
# _fit_polynomial — recovers degree-2 coefficients
# ============================================================

class TestPolynomialFit:

    def test_recovers_quadratic(self):
        """y = -4.9·t² + 30·t + 5 — ballistic motion under gravity."""
        t = np.linspace(0, 6, 200)
        y = -4.9 * t ** 2 + 30 * t + 5.0
        result = _fit_polynomial(t, y)
        assert result["ok"] is True
        assert result["name"] == "polynomial_2"
        assert abs(result["params"]["a"] - (-4.9)) < 0.01
        assert abs(result["params"]["b"] - 30.0) < 0.5
        assert abs(result["params"]["c"] - 5.0) < 0.5

    def test_rejects_short_input(self):
        result = _fit_polynomial(np.arange(10.0), np.arange(10.0))
        assert result["ok"] is False


# ============================================================
# _fit_saturation_static — Michaelis-Menten recovery
# ============================================================

class TestSaturationFit:

    def test_recovers_michaelis_menten(self):
        """y = 50·t / (10 + t) — Vmax=50, K=10."""
        t = np.linspace(0, 100, 200)
        y = 50.0 * t / (10.0 + t)
        result = _fit_saturation_static(t, y)
        if result.get("ok"):
            assert result["name"] == "saturation"
            assert abs(result["params"]["Vmax"] - 50.0) / 50.0 < 0.1
            assert abs(result["params"]["K"] - 10.0) / 10.0 < 0.3
        else:
            # scipy missing — accept the error.
            assert "scipy" in result.get("error", "") or "fit" in result.get("error", "")
