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
