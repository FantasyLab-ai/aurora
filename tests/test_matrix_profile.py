"""
Tests for ``fantasyai/aurora/matrix_profile.py``.

We feed synthetic series with planted motifs / discords and verify the
algorithm finds them in the right places.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

np = pytest.importorskip("numpy")

from fantasyai.aurora.matrix_profile import (
    matrix_profile, top_motifs, top_discords, compute_motifs_and_discords,
    Motif, Discord,
)


# ============================================================
# matrix_profile basic shape / sanity
# ============================================================

class TestMatrixProfile:

    def test_shape_correct(self):
        x = np.sin(np.linspace(0, 4 * np.pi, 200))
        P, P_idx = matrix_profile(x, window=20)
        assert P.shape == (200 - 20 + 1,)
        assert P_idx.shape == P.shape

    def test_short_series_returns_inf(self):
        x = np.array([1.0, 2.0, 3.0])
        P, P_idx = matrix_profile(x, window=10)
        assert P.shape[0] >= 1
        assert np.all(~np.isfinite(P) | (P == np.inf))

    def test_distance_is_nonnegative(self):
        rng = np.random.default_rng(0)
        x = rng.standard_normal(300)
        P, _ = matrix_profile(x, window=20)
        finite = P[np.isfinite(P)]
        assert (finite >= 0).all()


# ============================================================
# Motif discovery: planted repeating pattern
# ============================================================

class TestMotifs:

    def test_finds_planted_motif(self):
        """A pattern repeated at two known positions should produce a motif
        whose two starts match those positions (within window/2 tolerance)."""
        rng = np.random.default_rng(0)
        n = 600
        x = rng.standard_normal(n) * 0.05    # mostly noise
        # Plant a sinusoid at positions 100 and 350.
        pattern = np.sin(np.linspace(0, 2 * np.pi, 40))
        x[100:140] += pattern
        x[350:390] += pattern
        motifs = top_motifs(x, window=40, k=2)
        assert len(motifs) >= 1
        starts = sorted([motifs[0].start_a, motifs[0].start_b])
        # Must land within 15 samples of the planted positions.
        assert abs(starts[0] - 100) < 15, f"expected ~100, got {starts}"
        assert abs(starts[1] - 350) < 15, f"expected ~350, got {starts}"

    def test_motif_distance_is_small(self):
        """Two identical windows should produce a tiny matrix-profile distance."""
        n = 300
        x = np.zeros(n)
        # Identical sinusoid at two positions; minimal noise.
        pat = np.sin(np.linspace(0, 2 * np.pi, 30))
        x[50:80] = pat
        x[200:230] = pat
        motifs = top_motifs(x, window=30, k=1)
        assert len(motifs) == 1
        assert motifs[0].distance < 0.5    # near-identical → near-zero z-distance

    def test_no_motifs_in_pure_noise(self):
        """In pure noise, the lowest matrix profile distance should still be
        meaningful (positive and non-trivial)."""
        rng = np.random.default_rng(0)
        x = rng.standard_normal(500)
        motifs = top_motifs(x, window=30, k=3)
        # Each motif should have a positive distance.
        for m in motifs:
            assert m.distance > 0


# ============================================================
# Discord discovery: planted anomaly
# ============================================================

class TestDiscords:

    def test_finds_planted_discord(self):
        """A unique window in an otherwise-repeating series should be the top discord."""
        n = 600
        x = np.tile(np.sin(np.linspace(0, 2 * np.pi, 30)), n // 30 + 1)[:n]
        # Plant a wildly different shape at position 280 (square pulse).
        x[280:310] = 5.0
        discords = top_discords(x, window=30, k=2)
        assert len(discords) >= 1
        assert abs(discords[0].start - 280) < 15, \
            f"expected discord near 280, got {discords[0].start}"


# ============================================================
# compute_motifs_and_discords (one-shot convenience)
# ============================================================

class TestOneShot:

    def test_combined_call(self):
        n = 400
        x = np.tile(np.sin(np.linspace(0, 2 * np.pi, 25)), n // 25 + 1)[:n]
        out = compute_motifs_and_discords(x, window=25, n_motifs=2, n_discords=2)
        assert out["available"] is True
        assert out["window"] == 25
        assert out["n"] == n
        assert isinstance(out["motifs"], list)
        assert isinstance(out["discords"], list)
        assert "min" in out["profile_stats"]

    def test_too_short_returns_unavailable(self):
        out = compute_motifs_and_discords(np.array([1.0, 2.0, 3.0]), window=10)
        assert out["available"] is False
        assert "too short" in out["reason"]

    def test_default_window_picked(self):
        rng = np.random.default_rng(0)
        x = rng.standard_normal(500)
        out = compute_motifs_and_discords(x)
        assert out["available"] is True
        # Default window: 5% of n = 25, clamped to [10, 200].
        assert 10 <= out["window"] <= 200


# ============================================================
# Dataclass serialization
# ============================================================

class TestSerialization:

    def test_motif_to_dict(self):
        m = Motif(rank=1, start_a=10, start_b=20, distance=0.5, window=10)
        d = m.to_dict()
        assert d["rank"] == 1
        assert d["distance"] == 0.5

    def test_discord_to_dict(self):
        d = Discord(rank=1, start=42, distance=5.0, window=30)
        out = d.to_dict()
        assert out["start"] == 42
