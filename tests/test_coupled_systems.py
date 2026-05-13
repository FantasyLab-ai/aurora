"""
Math-correctness tests for ``fantasyai/aurora/math/coupled_systems.py``.

Each test seeds synthetic data from a known ODE system, runs the fitter,
and verifies the recovered parameters land within tolerance of the
ground-truth values. Without these tests we don't really know the
multi-target physics layer works on real data.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

np = pytest.importorskip("numpy")

from fantasyai.aurora.math.coupled_systems import (
    fit_lotka_volterra, fit_competitive_lv, fit_sir, fit_linear_coupled,
    candidate_coupled_models, discover_coupled,
)


# ============================================================
# Lotka-Volterra recovery
# ============================================================

class TestLotkaVolterra:

    def _synthetic_lv(self, alpha=1.0, beta=0.1, gamma=1.5, delta=0.075,
                      x0=10.0, y0=5.0, n=2000, dt=0.01):
        """Generate ground-truth (x, y) under Euler integration."""
        t = np.arange(n, dtype=float) * dt
        x = np.zeros(n); y = np.zeros(n)
        x[0], y[0] = x0, y0
        for i in range(1, n):
            x[i] = x[i-1] + dt * (alpha * x[i-1] - beta * x[i-1] * y[i-1])
            y[i] = y[i-1] + dt * (-gamma * y[i-1] + delta * x[i-1] * y[i-1])
        return t, x, y

    def test_recovers_parameters_within_tolerance(self):
        alpha_t, beta_t, gamma_t, delta_t = 1.0, 0.1, 1.5, 0.075
        t, x, y = self._synthetic_lv(alpha_t, beta_t, gamma_t, delta_t,
                                     n=2000, dt=0.01)
        r = fit_lotka_volterra(t, x, y)
        assert r["ok"] is True
        p = r["params"]
        # Loose tolerance — Euler-from-truth + Euler-from-fit have stacking errors.
        assert abs(p["alpha"] - alpha_t) / alpha_t < 0.10
        assert abs(p["beta"]  - beta_t)  / beta_t  < 0.15
        assert abs(p["gamma"] - gamma_t) / gamma_t < 0.10
        assert abs(p["delta"] - delta_t) / delta_t < 0.15

    def test_rmse_reasonable(self):
        t, x, y = self._synthetic_lv(n=2000, dt=0.01)
        r = fit_lotka_volterra(t, x, y)
        # On clean data, simulated RMSE should be << signal scale.
        assert r["rmse"] < 1.0

    def test_short_input_rejected(self):
        t = np.linspace(0, 1, 30)
        x = np.ones(30); y = np.ones(30)
        r = fit_lotka_volterra(t, x, y)
        assert r["ok"] is False


# ============================================================
# Competitive Lotka-Volterra recovery
# ============================================================

class TestCompetitiveLV:

    def test_runs_on_synthetic(self):
        """Two species with logistic growth + competition."""
        t = np.arange(800, dtype=float) * 0.05
        x = np.zeros(800); y = np.zeros(800)
        r1, K1, a = 0.5, 100.0, 0.4
        r2, K2, b = 0.3, 80.0, 0.6
        x[0], y[0] = 5.0, 4.0
        dt = 0.05
        for i in range(1, 800):
            dx = r1 * x[i-1] * (1 - (x[i-1] + a * y[i-1]) / K1)
            dy = r2 * y[i-1] * (1 - (y[i-1] + b * x[i-1]) / K2)
            x[i] = x[i-1] + dt * dx
            y[i] = y[i-1] + dt * dy
        r = fit_competitive_lv(t, x, y)
        assert r["ok"] is True
        p = r["params"]
        # Recovered carrying capacities should be in the right ballpark.
        assert 50 < p["K1"] < 200
        assert 30 < p["K2"] < 200


# ============================================================
# SIR epidemiology recovery
# ============================================================

class TestSIR:

    def _synthetic_sir(self, beta=0.3, gamma=0.1, S0=999, I0=1, N=1000,
                       n=300, dt=0.5):
        t = np.arange(n, dtype=float) * dt
        S = np.zeros(n); I = np.zeros(n); R = np.zeros(n)
        S[0], I[0] = S0, I0
        for i in range(1, n):
            si = S[i-1] * I[i-1] / N
            S[i] = S[i-1] + dt * (-beta * si)
            I[i] = I[i-1] + dt * (beta * si - gamma * I[i-1])
            R[i] = R[i-1] + dt * (gamma * I[i-1])
        return t, S, I, R, N

    def test_recovers_beta_gamma(self):
        beta_t, gamma_t = 0.3, 0.1
        t, S, I, R, N = self._synthetic_sir(beta_t, gamma_t)
        result = fit_sir(t, S, I, R=R, N=N)
        assert result["ok"] is True
        p = result["params"]
        assert abs(p["beta"]  - beta_t)  < 0.05
        assert abs(p["gamma"] - gamma_t) < 0.05
        # R0 = β/γ = 3.0 for the canonical example.
        assert 2.5 < p["R0"] < 3.5


# ============================================================
# Linear 2D recovery
# ============================================================

class TestLinearCoupled:

    def test_recovers_rotation(self):
        """Pure rotation: dx/dt = -y, dy/dt = x  → eigenvalues ±i."""
        t = np.linspace(0, 4 * np.pi, 1000)
        x = np.cos(t); y = np.sin(t)
        r = fit_linear_coupled(t, x, y)
        assert r["ok"] is True
        p = r["params"]
        # a11 ≈ 0, a12 ≈ -1, a21 ≈ 1, a22 ≈ 0.
        assert abs(p["a11"]) < 0.1
        assert abs(p["a12"] + 1.0) < 0.2
        assert abs(p["a21"] - 1.0) < 0.2
        assert abs(p["a22"]) < 0.1


# ============================================================
# Discover top-level — picks the right model
# ============================================================

class TestDiscover:

    def test_picks_lv_on_predator_prey_data(self):
        """LV-generated data should pick lotka_volterra as best."""
        t = np.arange(2000, dtype=float) * 0.01
        x = np.zeros(2000); y = np.zeros(2000)
        x[0], y[0] = 10, 5
        for i in range(1, 2000):
            x[i] = x[i-1] + 0.01 * (1.0 * x[i-1] - 0.1 * x[i-1] * y[i-1])
            y[i] = y[i-1] + 0.01 * (-1.5 * y[i-1] + 0.075 * x[i-1] * y[i-1])
        out = discover_coupled(t, x, y)
        assert out["note"] == "ok"
        assert out["best"]["name"] == "lotka_volterra"

    def test_returns_all_candidates(self):
        t = np.arange(200, dtype=float) * 0.05
        x = np.cos(t); y = np.sin(t)
        out = discover_coupled(t, x, y)
        # Every fitter should be listed (even failed ones).
        names = {c.get("name") for c in out["all_candidates"]}
        assert "lotka_volterra" in names
        assert "linear_coupled_2d" in names
        assert "sir" in names
        assert "competitive_lv" in names


# ============================================================
# Registry
# ============================================================

class TestRegistry:

    def test_returns_four_callables(self):
        cands = candidate_coupled_models()
        assert len(cands) == 4
        for c in cands:
            assert callable(c)
