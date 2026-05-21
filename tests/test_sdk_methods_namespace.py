"""
SDK MethodsView + backtest + matrix profile audit tests.

These tests cover the v0.10.1 additions:

    * `aurora_sdk.MethodsView` typed accessors (var / dtw / bocpd /
      robust_pca / emd / kalman / spectral_entropy / matrix_profile /
      granger / mutual_info / hmm / wavelet).

    * `MethodsView.backtest(...)` — strategy backtest end-to-end.

    * Matrix Profile newly wired into extended_runner.

    * backtesting.backtest_strategy + signal_from_threshold.

We avoid invoking the real Aurora pipeline (which needs scipy +
flask + many other deps) and instead exercise the accessors against
synthetic finding dicts. The accessor logic is what we're testing,
not the upstream methods.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List

import pytest


# ----------------------------------------------------------------------
# Fixtures — synthetic state mirroring what state_builder produces.
# ----------------------------------------------------------------------

def _make_finding(method: str, status: str = "fit",
                  evidence: Dict[str, Any] = None,
                  **extras) -> Dict[str, Any]:
    return {
        "method": method,
        "title": f"{method} ran",
        "severity": "info",
        "evidence": {"status": status, **(evidence or {})},
        **extras,
    }


def _state_with_findings(findings: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {"findings": findings}


# ======================================================================
# MethodsView — basic surface
# ======================================================================

class TestMethodsViewSurface:

    def test_accepts_bundle_or_state(self):
        from aurora_sdk.methods import MethodsView
        # Plain state dict.
        mv = MethodsView({"findings": []})
        assert mv.list_methods() == []
        # Bundle-like with .doc attribute.
        class _FakeBundle:
            def __init__(self, doc): self.doc = doc
        mv2 = MethodsView(_FakeBundle({"findings": []}))
        assert mv2.list_methods() == []

    def test_returns_none_when_method_didnt_fit(self):
        from aurora_sdk.methods import MethodsView
        mv = MethodsView({"findings": []})
        # Every accessor should gracefully return None on empty bundle.
        for fn in ("var", "dtw", "bocpd", "robust_pca", "emd",
                    "kalman", "spectral_entropy", "matrix_profile",
                    "granger", "mutual_info", "hmm", "wavelet"):
            assert getattr(mv, fn)() is None, (
                f"{fn}() should return None on empty bundle"
            )

    def test_returns_none_when_method_skipped(self):
        from aurora_sdk.methods import MethodsView
        # A finding with status=skipped should not produce a typed fit.
        state = _state_with_findings([
            _make_finding("var", status="skipped",
                            evidence={"reason": "needs >=2 numeric cols"}),
        ])
        mv = MethodsView(state)
        assert mv.var() is None
        # But list_methods + fit_status surface the truth.
        assert "var" in mv.list_methods()
        assert mv.fit_status("var") == "skipped"

    def test_returns_none_when_method_failed(self):
        from aurora_sdk.methods import MethodsView
        state = _state_with_findings([
            _make_finding("kalman", status="failed",
                            evidence={"error": "scipy missing"}),
        ])
        mv = MethodsView(state)
        assert mv.kalman() is None
        assert mv.fit_status("kalman") == "failed"

    def test_evidence_escape_hatch_returns_raw_dict(self):
        from aurora_sdk.methods import MethodsView
        state = _state_with_findings([
            _make_finding("var", evidence={
                "chosen_lag": 2, "n_obs": 100, "n_vars": 3,
                "future_field_we_havent_typed_yet": [1, 2, 3],
            }),
        ])
        mv = MethodsView(state)
        ev = mv.evidence("var")
        assert ev is not None
        assert ev["chosen_lag"] == 2
        assert ev["future_field_we_havent_typed_yet"] == [1, 2, 3]


# ======================================================================
# VAR accessor
# ======================================================================

class TestVarAccessor:

    def test_parses_full_var_evidence(self):
        from aurora_sdk.methods import MethodsView
        state = _state_with_findings([
            _make_finding("var", evidence={
                "chosen_lag": 2, "n_obs": 100, "n_vars": 3,
                "target_cols": ["price", "volume", "rsi"],
                "top_cross_coupling": {
                    "from_col": "volume", "to_col": "price",
                    "coefficient_lag1": 0.43,
                },
                "forecast_horizon": 24,
                "forecast_summary": {"end_value": 105.2},
            }),
        ])
        v = MethodsView(state).var()
        assert v is not None
        assert v.chosen_lag == 2
        assert v.n_vars == 3
        assert v.target_cols == ["price", "volume", "rsi"]
        assert v.top_coupling is not None
        assert v.top_coupling.from_col == "volume"
        assert v.top_coupling.to_col == "price"
        assert abs(v.top_coupling.coefficient_lag1 - 0.43) < 1e-9
        assert v.forecast_horizon == 24

    def test_handles_missing_top_coupling(self):
        from aurora_sdk.methods import MethodsView
        state = _state_with_findings([
            _make_finding("var", evidence={
                "chosen_lag": 1, "n_obs": 50, "n_vars": 2,
                "target_cols": ["a", "b"],
            }),
        ])
        v = MethodsView(state).var()
        assert v is not None
        assert v.top_coupling is None

    def test_dataclass_to_dict_roundtrip(self):
        from aurora_sdk.methods import MethodsView
        state = _state_with_findings([
            _make_finding("var", evidence={
                "chosen_lag": 1, "n_obs": 50, "n_vars": 2,
                "target_cols": ["a", "b"],
                "top_cross_coupling": {
                    "from_col": "a", "to_col": "b", "coefficient_lag1": 0.5,
                },
            }),
        ])
        v = MethodsView(state).var()
        d = v.to_dict()
        assert d["chosen_lag"] == 1
        assert d["top_coupling"]["from_col"] == "a"


# ======================================================================
# BOCPD accessor
# ======================================================================

class TestBocpdAccessor:

    def test_parses_change_points(self):
        from aurora_sdk.methods import MethodsView
        state = _state_with_findings([
            _make_finding("bocpd", evidence={
                "target_col": "btc_close", "n_obs": 1000,
                "threshold": 0.5, "hazard": 0.01,
                "change_points": [
                    {"row_idx": 312, "posterior_at_r0": 0.92},
                    {"row_idx": 645, "posterior_at_r0": 0.78},
                ],
            }),
        ])
        b = MethodsView(state).bocpd()
        assert b is not None
        assert b.target_col == "btc_close"
        assert len(b.change_points) == 2
        assert b.change_points[0].row_idx == 312
        assert abs(b.change_points[0].posterior_at_r0 - 0.92) < 1e-9


# ======================================================================
# Matrix Profile accessor
# ======================================================================

class TestMatrixProfileAccessor:

    def test_parses_motifs_and_discords(self):
        from aurora_sdk.methods import MethodsView
        state = _state_with_findings([
            _make_finding("matrix_profile", evidence={
                "target_col": "btc_1h", "n_obs": 2000, "window": 50,
                "motifs": [
                    {"rank": 1, "start_a": 100, "start_b": 1500,
                      "distance": 0.42, "window": 50},
                ],
                "discords": [
                    {"rank": 1, "start": 850, "distance": 4.21, "window": 50},
                ],
                "profile_stats": {"min": 0.1, "max": 5.0, "mean": 2.0, "std": 0.8},
            }),
        ])
        mp = MethodsView(state).matrix_profile()
        assert mp is not None
        assert mp.target_col == "btc_1h"
        assert mp.window == 50
        assert len(mp.motifs) == 1
        assert mp.motifs[0].start_a == 100
        assert mp.motifs[0].start_b == 1500
        assert len(mp.discords) == 1
        assert mp.discords[0].start == 850


# ======================================================================
# Kalman accessor (forecast steps)
# ======================================================================

class TestKalmanAccessor:

    def test_parses_kalman_with_forecast(self):
        from aurora_sdk.methods import MethodsView
        state = _state_with_findings([
            _make_finding("kalman", evidence={
                "target_col": "price", "n_obs": 500,
                "noise_reduction_fraction": 0.85,
                "forecast_horizon": 12,
                "forecast": [
                    {"step": 1, "estimate": 100.5,
                      "ci_low": 99.0, "ci_high": 102.0},
                    {"step": 2, "estimate": 100.7,
                      "ci_low": 98.5, "ci_high": 102.9},
                ],
            }),
        ])
        kf = MethodsView(state).kalman()
        assert kf is not None
        assert abs(kf.noise_reduction_fraction - 0.85) < 1e-9
        assert len(kf.forecast) == 2
        assert kf.forecast[0].step == 1
        assert abs(kf.forecast[0].estimate - 100.5) < 1e-9


# ======================================================================
# Spectral entropy accessor
# ======================================================================

class TestSpectralEntropyAccessor:

    def test_parses_spectral_entropy(self):
        from aurora_sdk.methods import MethodsView
        state = _state_with_findings([
            _make_finding("spectral_entropy", evidence={
                "target_col": "ret", "n_obs": 1000,
                "global_spectral_entropy": 0.42,
                "regime_class": "moderate",
                "window_results": [
                    {"window_idx": 0, "entropy": 0.41,
                      "dominant_period_samples": 8.0},
                    {"window_idx": 1, "entropy": 0.49,
                      "dominant_period_samples": 12.0},
                ],
                "biggest_window_jump": {"delta_h": 0.08, "window_idx": 1},
            }),
        ])
        se = MethodsView(state).spectral_entropy()
        assert se is not None
        assert se.regime_class == "moderate"
        assert abs(se.global_spectral_entropy - 0.42) < 1e-9
        assert len(se.window_results) == 2
        assert se.biggest_window_jump["delta_h"] == 0.08


# ======================================================================
# Granger / Mutual Info / HMM / Wavelet — read from state, not findings
# ======================================================================

class TestStateLevelAccessors:

    def test_granger_reads_from_state_block(self):
        from aurora_sdk.methods import MethodsView
        state = {
            "findings": [],
            "granger_causality": {
                "pairs": [
                    {"col_i": "x", "col_j": "y",
                      "forward_p": 0.01, "reverse_p": 0.4,
                      "verdict": "i_causes_j"},
                ],
            },
        }
        g = MethodsView(state).granger()
        assert g is not None
        assert len(g.pairs) == 1
        assert g.pairs[0].verdict == "i_causes_j"

    def test_mutual_info_reads_from_state_block(self):
        from aurora_sdk.methods import MethodsView
        state = {
            "findings": [],
            "mutual_info": {
                "available": True,
                "n_features": 4, "estimator": "ksg",
                "nonlinear_pairs": [
                    {"col_i": "a", "col_j": "b", "mi": 0.31},
                ],
            },
        }
        mi = MethodsView(state).mutual_info()
        assert mi is not None
        assert mi.n_features == 4
        assert mi.nonlinear_pairs[0].col_i == "a"

    def test_hmm_reads_from_state_block(self):
        from aurora_sdk.methods import MethodsView
        state = {
            "findings": [],
            "hmm": {
                "available": True, "best_K": 3,
                "current_state": 1,
                "current_state_posterior": [0.1, 0.8, 0.1],
                "means_sorted": [10.0, 20.0, 30.0],
                "expected_dwell_time": [40.0, 60.0, 30.0],
            },
        }
        h = MethodsView(state).hmm()
        assert h is not None
        assert h.best_K == 3
        assert h.current_state == 1

    def test_wavelet_reads_from_state_block(self):
        from aurora_sdk.methods import MethodsView
        state = {
            "findings": [],
            "wavelet_period": {
                "available": True, "verdict": "stationary",
                "period_first_quartile": 94.27,
                "period_last_quartile": 94.27,
                "relative_drift": 0.0,
            },
        }
        w = MethodsView(state).wavelet()
        assert w is not None
        assert w.verdict == "stationary"
        assert w.relative_drift == 0.0


# ======================================================================
# Strategy backtest
# ======================================================================

class TestBacktest:

    def test_backtest_on_uptrend_returns_positive(self):
        from aurora_sdk.methods import MethodsView
        import numpy as np
        np.random.seed(1)
        n = 300
        # Steady uptrend.
        px = 100 * np.cumprod(1 + np.full(n, 0.001))
        # Always long.
        sig = [1] * n
        r = MethodsView.backtest(px, sig, bars_per_year=252)
        assert r.n_bars == n
        assert r.total_return > 0
        assert r.equity_curve[-1] > r.equity_curve[0]

    def test_backtest_on_downtrend_returns_negative_when_long(self):
        from aurora_sdk.methods import MethodsView
        import numpy as np
        n = 200
        px = 100 * np.cumprod(1 + np.full(n, -0.001))
        sig = [1] * n
        r = MethodsView.backtest(px, sig)
        assert r.total_return < 0

    def test_backtest_short_makes_money_in_downtrend(self):
        from aurora_sdk.methods import MethodsView
        import numpy as np
        n = 200
        px = 100 * np.cumprod(1 + np.full(n, -0.002))
        sig = [-1] * n
        r = MethodsView.backtest(px, sig)
        assert r.total_return > 0

    def test_backtest_no_position_zero_return(self):
        from aurora_sdk.methods import MethodsView
        import numpy as np
        np.random.seed(0)
        n = 100
        px = 100 + np.cumsum(np.random.randn(n))
        r = MethodsView.backtest(px, [0] * n)
        assert r.n_trades == 0
        assert abs(r.total_return) < 1e-9

    def test_backtest_emits_trade_records(self):
        from aurora_sdk.methods import MethodsView
        import numpy as np
        n = 100
        px = np.linspace(100, 110, n)
        # Long for first half, flat for second half — one trade.
        sig = [1] * 50 + [0] * 50
        r = MethodsView.backtest(px, sig)
        assert r.n_trades >= 1
        # Best/worst trade dicts are populated.
        assert r.best_trade is not None
        assert "entry_idx" in r.best_trade
        assert "pnl" in r.best_trade

    def test_signal_from_threshold(self):
        from aurora_sdk.methods import MethodsView
        # Build a Z-score-driven signal: long when z > +1, exit when z < 0.
        z = [0.0, 0.5, 1.2, 1.5, 0.8, -0.1, -0.5, 0.0]
        sig = MethodsView.signal_from_threshold(
            z, enter_long_above=1.0, exit_long_below=0.0,
        )
        # Position: 0, 0, 1, 1, 1, 0, 0, 0
        assert sig == [0, 0, 1, 1, 1, 0, 0, 0]

    def test_backtest_with_callable_signal(self):
        from aurora_sdk.methods import MethodsView
        import numpy as np
        n = 200
        px = 100 * np.cumprod(1 + np.full(n, 0.001))
        # Callable signal — always long for the second half.
        def sig(p):
            out = np.zeros(len(p), dtype=int)
            out[len(p)//2:] = 1
            return out
        r = MethodsView.backtest(px, sig)
        assert r.n_trades == 1
        assert r.total_return > 0

    def test_backtest_rejects_short_series(self):
        from aurora_sdk.methods import MethodsView
        with pytest.raises(ValueError):
            MethodsView.backtest([100.0, 101.0], [1, 1])

    def test_backtest_records_sharpe_and_drawdown(self):
        from aurora_sdk.methods import MethodsView
        import numpy as np
        np.random.seed(42)
        n = 500
        px = 100 * np.cumprod(1 + np.random.randn(n) * 0.005 + 0.0002)
        sig = [1] * n
        r = MethodsView.backtest(px, sig, bars_per_year=252)
        # Drawdown is always <= 0.
        assert r.max_drawdown <= 0
        assert r.max_drawdown_pct <= 0
        # Sharpe is numeric (or None if std was zero — not on random data).
        assert r.sharpe is not None
        assert isinstance(r.sharpe, float)


# ======================================================================
# Matrix Profile method wired into extended_runner
# ======================================================================

class TestMatrixProfileWiredIntoRunner:

    def test_matrix_profile_in_method_specs(self):
        """The runner should now declare matrix_profile alongside the
        other 7 v1.2 methods."""
        import inspect
        from fantasyai.aurora.math.methods import extended_runner
        src = inspect.getsource(extended_runner)
        assert '"matrix_profile"' in src
        assert "fit_matrix_profile" in src

    def test_matrix_profile_in_state_builder_order(self):
        from fantasyai.aurora.state_builder import (
            EXTENDED_METHOD_ORDER, EXTENDED_METHOD_LABELS,
        )
        assert "matrix_profile" in EXTENDED_METHOD_ORDER
        assert "matrix_profile" in EXTENDED_METHOD_LABELS

    def test_fit_matrix_profile_runs_on_synthetic_series(self):
        from fantasyai.aurora.math.methods.matrix_profile_method import (
            fit_matrix_profile,
        )
        import numpy as np
        import pandas as pd
        np.random.seed(42)
        n = 500
        # Insert a repeating motif so the algorithm has something to find.
        base = np.sin(np.arange(n) / 12) * 5 + np.random.randn(n) * 0.5
        df = pd.DataFrame({"price": base})
        r = fit_matrix_profile(df)
        assert r["method"] == "matrix_profile"
        assert r["evidence"]["status"] == "fit"
        assert r["evidence"]["n_motifs"] >= 1
        assert r["evidence"]["n_discords"] >= 0


# ======================================================================
# SDK __init__ re-exports
# ======================================================================

class TestSdkExports:

    def test_aurora_sdk_exports_methods_view(self):
        import aurora_sdk as aurora
        assert hasattr(aurora, "MethodsView")
        assert hasattr(aurora, "VarFit")
        assert hasattr(aurora, "MatrixProfileFit")
        assert hasattr(aurora, "BocpdFit")
        assert hasattr(aurora, "KalmanFit")

    def test_run_result_has_methods_property(self):
        from aurora_sdk.runner import RunResult
        # RunResult is a dataclass; methods is a @property — it's on
        # the class, not the instance attrs.
        assert isinstance(
            getattr(type(RunResult), "methods", None) or
            getattr(RunResult, "methods", None),
            property,
        )

    def test_version_bumped(self):
        import aurora_sdk
        # v0.10.1 phase work bumps the SDK version.
        assert aurora_sdk.__version__ >= "1.0.1"
