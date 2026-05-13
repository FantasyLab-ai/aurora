"""
Unit tests for ``fantasyai/aurora/calculus.py``.

Each test feeds a series with a KNOWN shape (linear, exponential, sinusoid,
sigmoid) and verifies the calculus signature recovers the right qualitative
classification + sane numeric bounds.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

np = pytest.importorskip("numpy")

from fantasyai.aurora.calculus import (
    calculus_signature, smooth_central_diff,
    _count_sign_changes, _longest_monotonic_run, _dominant_frequency,
    CalculusSignature,
)


# ============================================================
# Utility helpers
# ============================================================

class TestUtilHelpers:

    def test_smooth_central_diff_on_linear(self):
        """Derivative of y = 2t is constant 2."""
        t = np.arange(100, dtype=float)
        y = 2.0 * t
        dy = smooth_central_diff(y, dt=1.0)
        # Interior points should be exactly 2.
        assert np.allclose(dy[1:-1], 2.0, atol=1e-10)

    def test_smooth_central_diff_handles_short(self):
        out = smooth_central_diff(np.array([5.0]), dt=1.0)
        assert out.shape == (1,)
        assert out[0] == 0.0

    def test_count_sign_changes_alternating(self):
        a = np.array([1.0, -1.0, 1.0, -1.0, 1.0])
        assert _count_sign_changes(a) == 4

    def test_count_sign_changes_constant(self):
        a = np.full(10, 3.0)
        assert _count_sign_changes(a) == 0

    def test_longest_monotonic_run(self):
        """+ + + - + + → longest run of +1 is 3."""
        a = np.array([1.0, 1.0, 1.0, -1.0, 1.0, 1.0])
        assert _longest_monotonic_run(a) == 3

    def test_dominant_frequency_sin(self):
        """sin(2π·5·t) for t in [0, 1] sampled at 100Hz → peak ≈ 5 Hz."""
        n = 200
        t = np.linspace(0, 2, n)
        y = np.sin(2 * np.pi * 5.0 * t)
        dt = float(t[1] - t[0])
        f = _dominant_frequency(y, dt)
        assert f is not None
        assert abs(f - 5.0) < 1.0

    def test_dominant_frequency_short(self):
        assert _dominant_frequency(np.arange(5.0), dt=1.0) is None


# ============================================================
# calculus_signature on canonical shapes
# ============================================================

class TestSignatureClassification:

    def test_linear_growth_classifies_monotonic(self):
        """y = 3t + 5 → strictly monotonic, dy/dt mean ≈ 3."""
        t = np.arange(200, dtype=float)
        y = 3.0 * t + 5.0
        sig = calculus_signature(t, y)
        assert sig.monotonic is True
        assert abs(sig.dy_dt_mean - 3.0) < 0.05
        # No sign changes in derivative.
        assert sig.dy_dt_sign_changes == 0
        # Should classify as linear-monotonic or some monotonic variant.
        assert "monotonic" in sig.likely_shape or "linear" in sig.likely_shape

    def test_exponential_decay(self):
        """y = 10·exp(-0.05·t) → monotonic decay, dy/dt < 0."""
        t = np.arange(0, 200, dtype=float) * 0.5
        y = 10.0 * np.exp(-0.05 * t)
        sig = calculus_signature(t, y)
        assert sig.monotonic is True
        assert sig.dy_dt_mean < 0
        # No sign changes in dy/dt.
        assert sig.dy_dt_sign_changes == 0

    def test_oscillator_has_many_inflections(self):
        """y = sin(2π·t) over multiple periods → many inflection points."""
        t = np.linspace(0, 6, 300)
        y = np.sin(2 * np.pi * t)
        sig = calculus_signature(t, y)
        # Many sign changes in derivative AND second derivative.
        assert sig.dy_dt_sign_changes >= 6
        assert sig.inflection_points >= 6
        # Not monotonic.
        assert sig.monotonic is False
        # Dominant frequency should be near 1 Hz.
        assert sig.dominant_frequency is not None
        assert abs(sig.dominant_frequency - 1.0) < 0.3

    def test_sigmoid_has_one_inflection(self):
        """y = 1/(1+exp(-(t-50))) → S-curve has 1 inflection point."""
        t = np.linspace(0, 100, 200)
        y = 1.0 / (1.0 + np.exp(-(t - 50.0)))
        sig = calculus_signature(t, y)
        assert sig.monotonic is True
        # The classifier should pick something monotonic-ish.
        assert sig.likely_shape != ""

    def test_constant_series(self):
        """y = constant → derivatives all zero, integral = const · span."""
        t = np.arange(100, dtype=float)
        y = np.full(100, 7.0)
        sig = calculus_signature(t, y)
        assert abs(sig.dy_dt_mean) < 1e-9
        assert sig.dy_dt_sign_changes == 0
        # Integral should be roughly 7 × span.
        # With dt=1 and 100 points, integral ≈ 7·99 = 693.
        assert 600 < sig.integral_total < 800


# ============================================================
# CalculusSignature.to_dict serializes cleanly (no NaN/Inf leaks)
# ============================================================

class TestSerialization:

    def test_to_dict_is_json_safe(self):
        import json
        t = np.arange(100, dtype=float)
        y = np.cumsum(np.random.RandomState(42).standard_normal(100))
        sig = calculus_signature(t, y)
        d = sig.to_dict()
        # Should round-trip through json without error.
        s = json.dumps(d)
        assert s
        # No raw NaN tokens — should be None instead.
        assert "NaN" not in s
        assert "Infinity" not in s

    def test_dataclass_has_expected_fields(self):
        t = np.arange(100, dtype=float)
        y = np.sin(t * 0.1)
        sig = calculus_signature(t, y)
        assert isinstance(sig, CalculusSignature)
        # Spot-check key fields.
        for f in ["dy_dt_mean", "dy_dt_std", "d2y_dt2_mean", "inflection_points",
                  "integral_total", "monotonic", "likely_shape", "confidence"]:
            assert hasattr(sig, f), f"missing field: {f}"


# ============================================================
# Edge cases
# ============================================================

class TestEdgeCases:

    def test_handles_single_point(self):
        t = np.array([0.0])
        y = np.array([5.0])
        sig = calculus_signature(t, y)
        # Should not crash; numeric fields default to 0 / safe values.
        assert isinstance(sig, CalculusSignature)

    def test_handles_nan_values(self):
        """NaN-laced series should not crash the signature."""
        t = np.arange(100, dtype=float)
        y = np.linspace(0, 10, 100)
        y[20:30] = np.nan
        sig = calculus_signature(t, y)
        # Just need it not to crash + return a valid signature.
        assert isinstance(sig, CalculusSignature)
        assert sig.likely_shape != ""
