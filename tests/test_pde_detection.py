"""
Tests for ``fantasyai/aurora/math/pde_detection.py``.

We seed each fitter with the analytic solution to its target PDE and verify
the recovered coefficient lands within tolerance. Then test the dispatcher
picks the right family for each canonical signal.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

np = pytest.importorskip("numpy")

from fantasyai.aurora.math.pde_detection import (
    fit_heat_equation, fit_advection_equation, fit_wave_equation,
    discover_pde, detect_pde_grid_in_dataframe,
)


# ============================================================
# Heat equation: u(x, t) = exp(-α·k²·t)·sin(k·x)
#                          ↑ analytic solution to ∂u/∂t = α·∂²u/∂x²
# ============================================================

class TestHeatEquation:

    def test_recovers_alpha(self):
        alpha_true = 0.4
        n_t, n_x = 60, 60
        t = np.linspace(0, 1.0, n_t)
        x = np.linspace(0, np.pi, n_x)
        k = 1.0
        # u(x, t) = exp(-α k² t) · sin(k·x)
        T, X = np.meshgrid(t, x, indexing="ij")
        U = np.exp(-alpha_true * k * k * T) * np.sin(k * X)
        r = fit_heat_equation(t, x, U)
        assert r["ok"] is True
        # Recovered α should be close to truth.
        assert abs(r["params"]["alpha"] - alpha_true) / alpha_true < 0.10
        # R² should be very high on a clean analytic solution.
        assert r["r2"] > 0.95


# ============================================================
# Linear advection: u(x, t) = f(x - c·t)
# ============================================================

class TestAdvection:

    def test_recovers_c(self):
        c_true = 2.5
        n_t, n_x = 80, 80
        t = np.linspace(0, 1.0, n_t)
        x = np.linspace(0, 4 * np.pi, n_x)
        T, X = np.meshgrid(t, x, indexing="ij")
        # Travelling wave: cos(x - c·t)
        U = np.cos(X - c_true * T)
        r = fit_advection_equation(t, x, U)
        assert r["ok"] is True
        assert abs(r["params"]["c"] - c_true) / c_true < 0.10
        assert r["r2"] > 0.95


# ============================================================
# Wave equation: u(x, t) = sin(k·x)·cos(k·c·t)
# ============================================================

class TestWaveEquation:

    def test_recovers_c(self):
        c_true = 3.0
        k = 1.5
        n_t, n_x = 60, 60
        t = np.linspace(0, 1.0, n_t)
        x = np.linspace(0, np.pi, n_x)
        T, X = np.meshgrid(t, x, indexing="ij")
        U = np.sin(k * X) * np.cos(k * c_true * T)
        r = fit_wave_equation(t, x, U)
        assert r["ok"] is True
        # c² should be ≈ 9.0; sqrt → 3.0.
        assert abs(r["params"]["c"] - c_true) < 0.5
        assert r["r2"] > 0.95


# ============================================================
# Dispatcher picks the right family
# ============================================================

class TestDiscover:

    def test_picks_heat_on_diffusion_data(self):
        alpha_true = 0.5
        t = np.linspace(0, 1.0, 60)
        x = np.linspace(0, np.pi, 60)
        T, X = np.meshgrid(t, x, indexing="ij")
        U = np.exp(-alpha_true * T) * np.sin(X)
        out = discover_pde(t, x, U)
        assert out["note"] == "ok"
        assert out["best"]["name"] == "heat_equation"

    def test_picks_advection_on_travelling_wave(self):
        c_true = 1.7
        t = np.linspace(0, 1.0, 80)
        x = np.linspace(0, 4 * np.pi, 80)
        T, X = np.meshgrid(t, x, indexing="ij")
        U = np.cos(X - c_true * T)
        out = discover_pde(t, x, U)
        # Either advection OR wave should win on a pure travelling wave
        # (analytic solution to both).
        assert out["best"]["name"] in ("advection_equation", "wave_equation")

    def test_grid_too_small_returns_error(self):
        t = np.linspace(0, 1, 3)
        x = np.linspace(0, 1, 3)
        U = np.ones((3, 3))
        out = discover_pde(t, x, U)
        assert out["note"] == "error"
        assert out["best"] is None


# ============================================================
# DataFrame → (t, x, U) extraction
# ============================================================

class TestGridExtraction:

    def test_extracts_wide_layout(self):
        pd = pytest.importorskip("pandas")
        # Build a wide-format DataFrame: cols are 'x_0', 'x_1', ..., 'x_5'.
        cols = [f"x_{p}" for p in [0.0, 0.2, 0.4, 0.6, 0.8, 1.0]]
        df = pd.DataFrame(
            np.random.RandomState(0).standard_normal((20, len(cols))),
            columns=cols,
        )
        out = detect_pde_grid_in_dataframe(df)
        assert out is not None
        t, x, U, used = out
        assert U.shape == (20, 6)
        assert x.size == 6
        assert len(used) == 6

    def test_extracts_long_layout(self):
        pd = pytest.importorskip("pandas")
        # t, x, u long-format triples on a 5×5 grid.
        rows = []
        for ti in range(5):
            for xi in range(5):
                rows.append({"time": float(ti), "position": float(xi),
                             "value": ti + 0.1 * xi})
        df = pd.DataFrame(rows)
        out = detect_pde_grid_in_dataframe(df)
        assert out is not None
        t, x, U, used = out
        assert t.size == 5
        assert x.size == 5
        assert U.shape == (5, 5)

    def test_returns_none_for_non_gridded(self):
        pd = pytest.importorskip("pandas")
        # No spatial pattern in column names, no t/x/u long-form columns.
        df = pd.DataFrame({
            "alpha": [1, 2, 3, 4, 5],
            "beta":  [5, 4, 3, 2, 1],
            "gamma": [2, 3, 5, 8, 13],
        })
        assert detect_pde_grid_in_dataframe(df) is None
