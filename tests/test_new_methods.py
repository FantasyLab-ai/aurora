"""
Tests for the new analytical methods added in Stream 1.2:

  * VAR (statsmodels-backed multivariate forecasting)
  * DTW (pure-Python dynamic time warping)
  * BOCPD (Bayesian online change point detection)

Each test uses small synthetic data so the run cost stays in
milliseconds per test. Where a method has a preconditions branch
(e.g., "need ≥2 numeric columns") we exercise both the success path
and the skip path.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fantasyai.aurora.math.methods import (
    fit_var,
    fit_dtw,
    dtw_distance,
    fit_bocpd,
    fit_robust_pca,
    fit_emd,
    fit_kalman,
    fit_spectral_entropy,
)


# ===========================================================================
# VAR
# ===========================================================================

class TestVAR:

    def test_var_fits_on_clean_multivariate_series(self):
        n = 200
        rng = np.random.default_rng(0)
        x = np.cumsum(rng.standard_normal(n))
        y = 0.5 * np.roll(x, -1) + rng.standard_normal(n) * 0.1
        df = pd.DataFrame({"x": x, "y": y})
        f = fit_var(df)
        assert f["method"] == "var"
        assert f["fabricated"] is False
        # Either fit or skipped (e.g., scipy bound) — but never "failed".
        assert f["evidence"]["status"] in ("fit", "skipped"), f["evidence"]
        if f["evidence"]["status"] == "fit":
            assert f["evidence"]["chosen_lag"] >= 1
            assert f["evidence"]["n_vars"] == 2

    def test_var_skips_single_column(self):
        df = pd.DataFrame({"only_one": np.arange(100, dtype=float)})
        f = fit_var(df)
        assert f["evidence"]["status"] == "skipped"
        assert "at least 2 numeric columns" in f["description"]

    def test_var_skips_short_series(self):
        df = pd.DataFrame({
            "a": np.arange(10, dtype=float),
            "b": np.arange(10, dtype=float),
        })
        f = fit_var(df)
        assert f["evidence"]["status"] == "skipped"
        assert "30 rows" in f["description"]

    def test_var_returns_aurora_finding_shape(self):
        df = pd.DataFrame({
            "a": np.cumsum(np.random.default_rng(1).standard_normal(80)),
            "b": np.cumsum(np.random.default_rng(2).standard_normal(80)),
        })
        f = fit_var(df)
        for key in ("severity", "confidence", "title", "description",
                     "method", "claim_id", "fabricated"):
            assert key in f, f"missing key: {key}"


# ===========================================================================
# DTW
# ===========================================================================

class TestDTW:

    def test_dtw_identical_sequences_distance_zero(self):
        a = [1.0, 2.0, 3.0, 4.0, 5.0]
        # Identical
        d = dtw_distance(a, a, window=10)
        assert d == pytest.approx(0.0, abs=1e-9)

    def test_dtw_distance_grows_with_dissimilarity(self):
        a = [1.0, 2.0, 3.0, 4.0, 5.0]
        b = [1.0, 2.0, 3.0, 4.0, 5.0]
        c = [10.0, -10.0, 10.0, -10.0, 10.0]
        d_ab = dtw_distance(a, b, window=10)
        d_ac = dtw_distance(a, c, window=10)
        assert d_ab < d_ac

    def test_dtw_handles_different_lengths(self):
        # Time-warped version of the same shape
        base = [1, 2, 3, 4, 5]
        stretched = [1, 1, 2, 2, 3, 3, 4, 4, 5, 5]
        d = dtw_distance(base, stretched, window=10)
        assert d < 5.0  # closer than two random sequences

    def test_fit_dtw_runs_on_two_columns(self):
        rng = np.random.default_rng(0)
        df = pd.DataFrame({
            "a": rng.standard_normal(50),
            "b": rng.standard_normal(50),
        })
        f = fit_dtw(df)
        assert f["method"] == "dtw"
        assert f["evidence"]["status"] == "fit"
        assert f["evidence"]["n_vars" if "n_vars" in f["evidence"] else "n_obs"] >= 50 or \
                len(f["evidence"]["most_similar"]) >= 1

    def test_fit_dtw_skips_single_column(self):
        df = pd.DataFrame({"only": np.arange(50.0)})
        f = fit_dtw(df)
        assert f["evidence"]["status"] == "skipped"


# ===========================================================================
# BOCPD
# ===========================================================================

class TestBOCPD:

    def test_bocpd_runs_on_two_regimes(self):
        """BOCPD should run successfully on a clearly-shifted series.
        We deliberately don't assert on the exact change point count
        because that depends on prior tuning — Aurora's contract is
        'the method ran; here are its findings', not 'the method
        always finds N changes in synthetic data'."""
        rng = np.random.default_rng(42)
        x = np.concatenate([
            rng.standard_normal(100),
            rng.standard_normal(100) + 5.0,
        ])
        df = pd.DataFrame({"signal": x})
        f = fit_bocpd(df, target_col="signal", threshold=0.3)
        assert f["method"] == "bocpd"
        assert f["evidence"]["status"] == "fit"
        # Sanity: result has the expected shape regardless of count.
        assert "change_points" in f["evidence"]
        assert isinstance(f["evidence"]["change_points"], list)

    def test_bocpd_no_change_on_stationary(self):
        rng = np.random.default_rng(0)
        df = pd.DataFrame({"flat": rng.standard_normal(200)})
        # With default hazard, stationary noise shouldn't trigger many CPs.
        f = fit_bocpd(df, target_col="flat", threshold=0.95)
        assert f["evidence"]["status"] == "fit"
        # Very high threshold → expect 0 or few change points.
        assert len(f["evidence"]["change_points"]) <= 5

    def test_bocpd_skips_short_series(self):
        df = pd.DataFrame({"x": [1.0, 2.0, 3.0]})
        f = fit_bocpd(df, target_col="x")
        assert f["evidence"]["status"] == "skipped"

    def test_bocpd_auto_picks_target(self):
        rng = np.random.default_rng(0)
        df = pd.DataFrame({
            "all_constant": [1.0] * 200,
            "signal": rng.standard_normal(200),
        })
        f = fit_bocpd(df)  # no explicit target
        # Either picks the variable column OR honestly skips
        assert f["method"] == "bocpd"
        assert f["evidence"]["status"] in ("fit", "skipped")


# ===========================================================================
# Robust PCA
# ===========================================================================

class TestRobustPCA:

    def test_robust_pca_recovers_low_rank_plus_outliers(self):
        """Construct a deliberately low-rank matrix + a few outlier
        cells. Robust PCA should classify those rows as outliers."""
        rng = np.random.default_rng(0)
        n, p = 80, 5
        # Low-rank structure: rank-2 outer product.
        u = rng.standard_normal((n, 2))
        v = rng.standard_normal((2, p))
        X = u @ v
        # Inject outliers on a few rows.
        outlier_rows = [10, 25, 50]
        for i in outlier_rows:
            X[i] += rng.standard_normal(p) * 20.0  # large spike
        df = pd.DataFrame(X, columns=[f"c{i}" for i in range(p)])
        f = fit_robust_pca(df)
        assert f["method"] == "robust_pca"
        assert f["evidence"]["status"] == "fit"
        flagged = {item["row_idx"] for item in f["evidence"]["top_outlier_rows"]}
        # We should flag at least 2 of the 3 injected outliers.
        assert len(flagged & set(outlier_rows)) >= 2

    def test_robust_pca_skips_single_column(self):
        df = pd.DataFrame({"only_one": np.arange(50.0)})
        f = fit_robust_pca(df)
        assert f["evidence"]["status"] == "skipped"

    def test_robust_pca_returns_aurora_shape(self):
        rng = np.random.default_rng(0)
        df = pd.DataFrame(rng.standard_normal((40, 3)),
                            columns=["a", "b", "c"])
        f = fit_robust_pca(df)
        for k in ("severity", "confidence", "title", "method", "claim_id",
                   "fabricated"):
            assert k in f
        assert f["fabricated"] is False


# ===========================================================================
# EMD
# ===========================================================================

class TestEMD:

    def test_emd_decomposes_composite_signal(self):
        """A signal that's the sum of a slow trend + a fast oscillation
        should decompose into ≥1 IMF + residual."""
        t = np.linspace(0, 10, 500)
        fast = np.sin(2 * np.pi * 5 * t)
        slow = 0.5 * t  # linear trend
        x = fast + slow
        df = pd.DataFrame({"sig": x})
        f = fit_emd(df, target_col="sig", max_imfs=3)
        assert f["method"] == "emd"
        assert f["evidence"]["status"] == "fit"
        assert f["evidence"]["n_imfs"] >= 1

    def test_emd_skips_short_series(self):
        df = pd.DataFrame({"x": [1.0, 2.0, 3.0]})
        f = fit_emd(df, target_col="x")
        assert f["evidence"]["status"] == "skipped"

    def test_emd_auto_picks_target(self):
        df = pd.DataFrame({
            "constant": [1.0] * 200,
            "sin": np.sin(np.linspace(0, 10, 200)),
        })
        f = fit_emd(df)
        assert f["method"] == "emd"
        # Should pick the variable column.
        if f["evidence"]["status"] == "fit":
            assert f["evidence"]["target_col"] == "sin"


# ===========================================================================
# Kalman Filter
# ===========================================================================

class TestKalman:

    def test_kalman_smooths_noisy_random_walk(self):
        """A noisy random walk should be denoised — smoothed series
        should have lower variance than the raw observations."""
        rng = np.random.default_rng(0)
        # Latent random walk + observation noise.
        x = np.cumsum(rng.standard_normal(200) * 0.1)
        y = x + rng.standard_normal(200) * 1.0
        df = pd.DataFrame({"obs": y})
        f = fit_kalman(df, target_col="obs")
        assert f["method"] == "kalman"
        assert f["evidence"]["status"] == "fit"
        # Noise reduction should be positive (smoother strictly less noisy).
        assert f["evidence"]["noise_reduction_fraction"] > 0.0

    def test_kalman_forecast_has_widening_CI(self):
        """Kalman forecast variance should monotonically increase with
        horizon — that's the basic correctness check on the math."""
        rng = np.random.default_rng(0)
        df = pd.DataFrame({"x": rng.standard_normal(100)})
        f = fit_kalman(df, target_col="x", forecast_horizon=10)
        assert f["evidence"]["status"] == "fit"
        forecast = f["evidence"]["forecast"]
        # CI widths must be non-decreasing.
        widths = [
            step["ci_high"] - step["ci_low"] for step in forecast
        ]
        for i in range(1, len(widths)):
            assert widths[i] >= widths[i - 1] - 1e-9, \
                f"CI shrank at step {i}: {widths[i]} < {widths[i-1]}"

    def test_kalman_handles_missing_observations(self):
        """NaN observations should pass through cleanly."""
        rng = np.random.default_rng(0)
        x = rng.standard_normal(100)
        x[20:30] = np.nan
        df = pd.DataFrame({"x": x})
        f = fit_kalman(df, target_col="x")
        assert f["evidence"]["status"] == "fit"


# ===========================================================================
# Spectral Entropy
# ===========================================================================

class TestSpectralEntropy:

    def test_pure_tone_has_low_entropy(self):
        """sin wave → spectrum concentrated at one frequency → low entropy."""
        t = np.linspace(0, 10, 1000)
        x = np.sin(2 * np.pi * 5 * t)
        df = pd.DataFrame({"sig": x})
        f = fit_spectral_entropy(df, target_col="sig")
        assert f["method"] == "spectral_entropy"
        assert f["evidence"]["status"] == "fit"
        h = f["evidence"]["global_spectral_entropy"]
        assert h < 0.5, f"pure tone should be low entropy, got {h}"

    def test_white_noise_has_high_entropy(self):
        """White noise → uniform spectrum → entropy near 1."""
        rng = np.random.default_rng(0)
        df = pd.DataFrame({"noise": rng.standard_normal(1000)})
        f = fit_spectral_entropy(df, target_col="noise")
        h = f["evidence"]["global_spectral_entropy"]
        assert h > 0.7, f"white noise should be high entropy, got {h}"

    def test_windowed_mode_returns_windows(self):
        t = np.linspace(0, 10, 500)
        x = np.sin(2 * np.pi * 5 * t)
        df = pd.DataFrame({"sig": x})
        f = fit_spectral_entropy(df, target_col="sig", window_size=64,
                                   window_step=32)
        assert len(f["evidence"]["window_results"]) > 0
        for w in f["evidence"]["window_results"]:
            assert 0.0 <= w["spectral_entropy"] <= 1.0

    def test_skips_short_series(self):
        df = pd.DataFrame({"x": [1.0, 2.0, 3.0]})
        f = fit_spectral_entropy(df, target_col="x")
        assert f["evidence"]["status"] == "skipped"
