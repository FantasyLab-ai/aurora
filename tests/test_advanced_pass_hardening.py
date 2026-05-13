"""
Workstream-2 regression tests — advanced-methods skip-reason audit
and SINDy / GP / power preconditioning.

Pins the diagnosis from "Aurora Backend & Loading State Audit":

  * S-1: SINDy refuses to run on cross-sectional data
         (cross_sectional_no_time_axis) — no LAPACK crash.
  * S-2: SINDy drops NaN-heavy / zero-variance columns BEFORE the
         polynomial library + LAPACK see them.
  * S-3: stlsq + lstsq calls catch LinAlgError, log traceback, and
         return a clean zero matrix instead of raising.
  * S-4: every except in advanced_pass.py logs traceback.format_exc()
         to ``aurora.advanced_pass``.
  * S-5: GP downsamples n>500 to 400 instead of skipping outright.
  * S-7: power-analysis skip reason is the new
         ``awaiting_causal_effect`` framing.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

np = pytest.importorskip("numpy")


# ============================================================
# S-1: SINDy gates on time axis
# ============================================================

class TestSindyTimeAxisGate:
    def _state(self, *, has_time_axis: bool, n_rows: int = 100,
                 n_cols: int = 3):
        rng = np.random.default_rng(0)
        return {
            "structure": {"time_axis": has_time_axis,
                            "time_col": "t" if has_time_axis else None},
            "dataset_matrix": rng.normal(size=(n_rows, n_cols)).tolist(),
            "dataset_matrix_columns": [f"x{i}" for i in range(n_cols)],
            "dataset_dt": 1.0,
        }

    def test_skips_cross_sectional_data(self):
        from fantasyai.aurora.stats.advanced_pass import _maybe_sindy
        res = _maybe_sindy(self._state(has_time_axis=False))
        assert res["applied"] is False
        assert res["reason"] == "cross_sectional_no_time_axis"

    def test_runs_on_time_series(self):
        from fantasyai.aurora.stats.advanced_pass import _maybe_sindy
        res = _maybe_sindy(self._state(has_time_axis=True))
        # Should not raise and not skip with cross_sectional reason.
        assert res.get("reason") != "cross_sectional_no_time_axis"


# ============================================================
# S-2: SINDy strips NaN-heavy / zero-variance columns
# ============================================================

class TestSindyPreconditioning:
    def test_diabetic_shaped_data_does_not_crash_lapack(self):
        """Reproduces the maintainer's failing case: numeric matrix
        with ~30% NaN columns + at least one zero-variance column.
        Before S-2 this raised SVD-did-not-converge from inside
        np.linalg.lstsq. After S-2 it returns a clean structured result
        — either a successful fit on the cleaned subset OR a clear
        skip with insufficient_clean_data."""
        from fantasyai.aurora.stats.sindy import sindy_discover
        rng = np.random.default_rng(0)
        arr = rng.normal(size=(120, 5))
        arr[:, 1] = 7.0           # zero-variance
        arr[20:80, 2] = np.nan    # NaN-heavy (~50%)
        arr[5, 3] = np.inf        # one inf — must be stripped row-wise
        res = sindy_discover(arr, dt=1.0,
                                column_names=["a", "flat", "nans", "infs", "ok"])
        # Must not raise. If it did fit, verify which cols were kept.
        assert "available" in res
        if res["available"]:
            audit = res.get("clean_audit") or {}
            kept = set(audit.get("kept_columns") or [])
            dropped = {d["name"] for d in (audit.get("dropped_columns") or [])}
            assert "flat" in dropped       # zero variance
            assert "nans" in dropped       # too many NaN
            # The clean cols make it through.
            assert "a" in kept and "ok" in kept

    def test_all_columns_bad_yields_clean_skip(self):
        from fantasyai.aurora.stats.sindy import sindy_discover
        arr = np.full((100, 3), np.nan)
        res = sindy_discover(arr, dt=1.0)
        assert res["available"] is False
        assert res["skipped_reason"] == "insufficient_clean_data"
        assert "clean_audit" in res


# ============================================================
# S-3: stlsq never raises on LAPACK failure
# ============================================================

class TestSindyStlsqResilience:
    def test_stlsq_returns_zero_matrix_on_lstsq_failure(self,
                                                            monkeypatch,
                                                            caplog):
        """When np.linalg.lstsq raises (e.g. DLASCLS-illegal-value),
        stlsq must return a zero coefficient matrix and log the
        traceback. Caller then sees an honest empty discovery."""
        from fantasyai.aurora.stats import sindy as sindy_mod
        # Force every lstsq call to raise the LAPACK error pattern.
        def _raising(*args, **kwargs):
            raise np.linalg.LinAlgError(
                "SVD did not converge in Linear Least Squares")
        monkeypatch.setattr(sindy_mod.np.linalg, "lstsq", _raising)
        caplog.set_level(logging.ERROR, logger="aurora.sindy")
        Theta = np.ones((20, 3))
        Xdot  = np.ones((20, 2))
        Xi = sindy_mod.stlsq(Theta, Xdot)
        # No exception. Result is the zero matrix.
        assert Xi.shape == (3, 2)
        assert np.all(Xi == 0)
        # Traceback was logged.
        any_traceback = any(
            "SVD did not converge" in (r.exc_text or r.message or "")
            or "lstsq failed" in r.message
            for r in caplog.records
        )
        assert any_traceback, (
            "S-3 mandates traceback.format_exc() on LAPACK failure"
        )


# ============================================================
# S-4: advanced_pass except blocks log traceback
# ============================================================

class TestAdvancedPassTraceback:
    def test_exc_skip_logs_traceback(self, caplog):
        from fantasyai.aurora.stats.advanced_pass import _exc_skip
        caplog.set_level(logging.ERROR, logger="aurora.advanced_pass")
        try:
            raise ValueError("synthetic for test")
        except Exception as e:
            res = _exc_skip("test_label", e)
        assert res["applied"] is False
        assert "test_label_exception" in res["reason"]
        any_tb = any(
            "synthetic for test" in r.message or
            (r.exc_text and "synthetic for test" in r.exc_text)
            for r in caplog.records
        )
        # Even without exc_info=True (we use traceback.format_exc()
        # directly), the traceback string should appear in the log.
        assert any(
            "synthetic for test" in r.getMessage()
            for r in caplog.records
        ), "every advanced_pass except must log the traceback"


# ============================================================
# S-5: GP downsamples n>500 instead of skipping
# ============================================================

class TestGpDownsample:
    def test_n_gt_500_subsamples_to_400(self):
        from fantasyai.aurora.stats.advanced_pass import _maybe_gp
        rng = np.random.default_rng(0)
        n = 2500
        t = np.linspace(0, 100, n)
        y = np.sin(t) + 0.1 * rng.standard_normal(n)
        state = {"primary_times": t.tolist(),
                  "primary_series": y.tolist()}
        res = _maybe_gp(state)
        assert res["applied"] is True, (
            "GP must downsample large series, not skip them (S-5)"
        )
        assert res["n_train_total"] == n
        assert res["n_train_used"] <= 400
        # And state.gaussian_process records the downsampling honestly.
        gp = state["gaussian_process"]
        assert gp["downsampled"] is True

    def test_n_lt_8_still_skips(self):
        from fantasyai.aurora.stats.advanced_pass import _maybe_gp
        state = {"primary_times": [1.0, 2.0, 3.0],
                  "primary_series": [0.1, 0.2, 0.3]}
        res = _maybe_gp(state)
        assert res["applied"] is False
        assert res["reason"] == "fewer_than_8_obs"


# ============================================================
# S-7: power-analysis honest skip reason
# ============================================================

class TestPowerFraming:
    def test_no_causal_effect_returns_awaiting_causal_effect(self):
        from fantasyai.aurora.stats.advanced_pass import _maybe_power
        res = _maybe_power({})
        assert res["applied"] is False
        assert res["reason"] == "awaiting_causal_effect"

    def test_tiny_effect_size_uses_explicit_reason(self):
        from fantasyai.aurora.stats.advanced_pass import _maybe_power
        state = {"causal": {"items": [{
            "effect_size": {"value": 0.01}}]}}
        res = _maybe_power(state)
        assert res["applied"] is False
        assert res["reason"] == "causal_effect_too_small_to_target"
