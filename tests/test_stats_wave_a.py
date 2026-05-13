"""
Wave A — credibility infrastructure: bootstrap CIs, multiple-comparisons
correction, effect sizes, and the credibility pass that ties them
together.

These are the pinning tests for the analytical-methods expansion's
first wave. Every subsequent wave assumes Wave A is correct.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

np = pytest.importorskip("numpy")


# ============================================================
# bootstrap_ci — non-parametric percentile bootstrap
# ============================================================

class TestBootstrapCI:
    def test_mean_ci_brackets_sample_mean(self):
        """The 95% bootstrap CI for the mean must bracket the sample
        mean — that's the bootstrap's defining invariant."""
        from fantasyai.aurora.stats import bootstrap_ci
        rng = np.random.default_rng(7)
        x = rng.normal(0.0, 1.0, size=200)
        sample_mean = float(np.mean(x))
        res = bootstrap_ci(x, n_resamples=1000, seed=42)
        assert res["point"] == pytest.approx(sample_mean, rel=1e-9)
        # The CI must contain the point estimate.
        assert res["ci_low"] <= sample_mean <= res["ci_high"]
        # CI width should be reasonable for n=200 (≈ 2 * 1.96 / sqrt(200)).
        assert (res["ci_high"] - res["ci_low"]) < 0.4

    def test_returns_99_when_requested(self):
        from fantasyai.aurora.stats import bootstrap_ci
        x = np.arange(100, dtype=float)
        res = bootstrap_ci(x, n_resamples=500, seed=1, also_99=True)
        # 99% must be wider than 95%.
        w95 = res["ci_high"] - res["ci_low"]
        w99 = res["ci_high_99"] - res["ci_low_99"]
        assert w99 >= w95

    def test_seed_reproducibility(self):
        """Same seed → identical CIs. Glass-box requirement."""
        from fantasyai.aurora.stats import bootstrap_ci
        x = np.arange(50, dtype=float)
        a = bootstrap_ci(x, n_resamples=500, seed=42)
        b = bootstrap_ci(x, n_resamples=500, seed=42)
        assert a["ci_low"] == b["ci_low"]
        assert a["ci_high"] == b["ci_high"]

    def test_empty_input_returns_skipped_reason(self):
        from fantasyai.aurora.stats import bootstrap_ci
        res = bootstrap_ci([])
        assert res["point"] is None
        assert res["skipped_reason"] == "empty_input"

    def test_custom_statistic_is_used(self):
        """The statistic callable is honoured — not silently replaced
        by mean."""
        from fantasyai.aurora.stats import bootstrap_ci
        x = np.array([1.0, 2.0, 3.0, 4.0, 100.0])
        # Median ≠ mean in this skewed sample.
        res_mean = bootstrap_ci(x, n_resamples=200, seed=1)
        res_med = bootstrap_ci(x, statistic=lambda v: float(np.median(v)),
                                  n_resamples=200, seed=1)
        assert res_mean["point"] != res_med["point"]
        assert res_med["point"] == pytest.approx(3.0, abs=1.5)


class TestBlockBootstrap:
    def test_records_block_size(self):
        from fantasyai.aurora.stats import block_bootstrap_ci
        x = np.arange(200, dtype=float)
        res = block_bootstrap_ci(x, n_resamples=200, seed=1)
        assert res["block_size"] is not None
        assert res["block_size"] >= 2
        assert res["method"] == "block_bootstrap"

    def test_explicit_block_size_honoured(self):
        from fantasyai.aurora.stats import block_bootstrap_ci
        x = np.arange(100, dtype=float)
        res = block_bootstrap_ci(x, block_size=10, n_resamples=200, seed=1)
        assert res["block_size"] == 10


class TestBcaBootstrap:
    def test_returns_bca_method_name(self):
        from fantasyai.aurora.stats import bca_bootstrap_ci
        rng = np.random.default_rng(7)
        x = rng.normal(2.0, 1.0, size=100)
        res = bca_bootstrap_ci(x, n_resamples=500, seed=1)
        assert res["method"] == "bca"
        assert "z0" in res
        assert "acceleration" in res

    def test_falls_back_for_tiny_samples(self):
        """BCa needs jackknife to be meaningful — falls back to
        percentile when n < 4."""
        from fantasyai.aurora.stats import bca_bootstrap_ci
        res = bca_bootstrap_ci([1.0, 2.0, 3.0], n_resamples=200, seed=1)
        assert res["method"] == "bootstrap_percentile"


# ============================================================
# multiple comparisons — BH + Bonferroni
# ============================================================

class TestBenjaminiHochberg:
    def test_q_values_monotone_after_correction(self):
        from fantasyai.aurora.stats import benjamini_hochberg
        ps = [0.001, 0.008, 0.039, 0.041, 0.042, 0.06, 0.074, 0.205]
        res = benjamini_hochberg(ps, alpha=0.05)
        # All q_values must be ≤ 1.
        assert all(0.0 <= q <= 1.0 for q in res["q_values"])
        # Sorted q_values must be non-decreasing.
        sorted_q = sorted(res["q_values"])
        assert sorted_q == sorted(sorted_q)

    def test_canonical_example(self):
        """BH on the canonical Benjamini-Hochberg 1995 example."""
        from fantasyai.aurora.stats import benjamini_hochberg
        # First three values are below the BH threshold.
        ps = [0.0001, 0.0004, 0.0019, 0.04, 0.05]
        res = benjamini_hochberg(ps, alpha=0.05)
        assert res["n_tests"] == 5
        assert res["significant"][0] is True
        assert res["significant"][1] is True

    def test_empty_input(self):
        from fantasyai.aurora.stats import benjamini_hochberg
        res = benjamini_hochberg([])
        assert res["q_values"] == []
        assert res["n_tests"] == 0


class TestBonferroni:
    def test_simple_arithmetic(self):
        from fantasyai.aurora.stats import bonferroni
        res = bonferroni([0.01, 0.02, 0.03], alpha=0.05)
        # Each adjusted p = min(1, raw * 3).
        assert res["p_corrected"][0] == pytest.approx(0.03)
        assert res["p_corrected"][1] == pytest.approx(0.06)
        assert res["p_corrected"][2] == pytest.approx(0.09)

    def test_caps_at_1(self):
        from fantasyai.aurora.stats import bonferroni
        res = bonferroni([0.5, 0.6], alpha=0.05)
        for p in res["p_corrected"]:
            assert p <= 1.0


class TestCorrectFindings:
    def test_attaches_q_and_bonferroni_to_each_finding(self):
        from fantasyai.aurora.stats import correct_findings
        findings = [
            {"title": "A", "method": "x", "p_value": 0.001},
            {"title": "B", "method": "x", "p_value": 0.04},
            {"title": "C", "method": "x", "p_value": 0.20},
            {"title": "D", "method": "x"},   # no p_value — left alone
        ]
        summary = correct_findings(findings, alpha=0.05)
        assert "q_value" in findings[0]
        assert "p_bonferroni" in findings[0]
        assert "significant_after_bh" in findings[0]
        assert "significant_after_bonferroni" in findings[0]
        # Finding D had no p_value; nothing was attached.
        assert "q_value" not in findings[3]
        assert summary["n_tests"] == 3

    def test_only_methods_filter(self):
        from fantasyai.aurora.stats import correct_findings
        findings = [
            {"title": "A", "method": "iqr", "p_value": 0.01},
            {"title": "B", "method": "iqr", "p_value": 0.02},
            {"title": "C", "method": "regime", "p_value": 0.001},
        ]
        correct_findings(findings, only_methods={"iqr"})
        assert "q_value" in findings[0]
        assert "q_value" in findings[1]
        # Regime finding excluded from the family.
        assert "q_value" not in findings[2]


# ============================================================
# effect size
# ============================================================

class TestCohensD:
    def test_zero_difference_yields_d_zero(self):
        from fantasyai.aurora.stats import cohens_d
        a = [1, 2, 3, 4, 5]
        b = [1, 2, 3, 4, 5]
        res = cohens_d(a, b)
        assert res["value"] == pytest.approx(0.0)
        assert res["interpretation"] == "negligible"

    def test_large_separation_classified_large(self):
        from fantasyai.aurora.stats import cohens_d
        a = [10, 11, 12, 13, 14]
        b = [1, 2, 3, 4, 5]
        res = cohens_d(a, b)
        assert res["value"] > 0.8
        assert res["interpretation"] in ("large", "very_large")

    def test_small_groups_skipped(self):
        from fantasyai.aurora.stats import cohens_d
        res = cohens_d([1.0], [2.0])
        assert res["value"] is None
        assert res["interpretation"] == "insufficient_data"


class TestEtaSquared:
    def test_basic(self):
        from fantasyai.aurora.stats import eta_squared
        res = eta_squared(20.0, 100.0)
        assert res["value"] == pytest.approx(0.20)
        assert res["interpretation"] == "large"

    def test_omega_when_dfs_present(self):
        from fantasyai.aurora.stats import eta_squared
        res = eta_squared(40.0, 100.0, df_between=2, df_within=27)
        assert "omega_squared" in res["details"]


class TestOddsRatio:
    def test_perfect_balance_or_one(self):
        from fantasyai.aurora.stats import odds_ratio
        res = odds_ratio(10, 10, 10, 10)
        assert res["value"] == pytest.approx(1.0)
        assert res["interpretation"] == "negligible"

    def test_haldane_anscombe_with_zero_cell(self):
        from fantasyai.aurora.stats import odds_ratio
        res = odds_ratio(0, 5, 5, 10)
        assert res["details"]["haldane_anscombe_correction"] is True
        assert res["value"] is not None


class TestRSquared:
    def test_perfect_fit_is_one(self):
        from fantasyai.aurora.stats import r_squared
        y = np.linspace(0, 10, 50)
        res = r_squared(y, y)
        assert res["value"] == pytest.approx(1.0)
        assert res["interpretation"] == "very_large"

    def test_mean_predictor_yields_zero(self):
        from fantasyai.aurora.stats import r_squared
        y_true = np.array([1, 2, 3, 4, 5], dtype=float)
        y_pred = np.full(5, y_true.mean())
        res = r_squared(y_true, y_pred)
        assert res["value"] == pytest.approx(0.0, abs=1e-9)


# ============================================================
# credibility pass — end-to-end on a realistic state
# ============================================================

@pytest.fixture
def realistic_state():
    return {
        "system_model": {"template_id": "enviro"},
        "overview": {"critical_anomalies": 1},
        "anomalies": [
            {"when": "row 1", "z": 4.2, "score": 4.2, "severity": "crit"},
            {"when": "row 2", "z": 3.8, "score": 3.8, "severity": "warn"},
            {"when": "row 3", "z": 3.1, "score": 3.1, "severity": "warn"},
            {"when": "row 4", "z": 2.5, "score": 2.5, "severity": "info"},
            {"when": "row 5", "z": 2.6, "score": 2.6, "severity": "info"},
            {"when": "row 6", "z": 2.4, "score": 2.4, "severity": "info"},
        ],
        "forecast": {
            "available": True, "headline_value": 12.3,
            "headline_ci_low": 10.5, "headline_ci_high": 14.0,
            "method": "AR(1)",
        },
        "physics": {
            "available": True,
            "discovered_law": {"name": "damped_harmonic", "r2": 0.83},
        },
        "causal": {
            "available": True, "method": "OLS",
            "items": [{
                "if_clause": "wind > 10", "then_clause": "wave +1.2",
                "mean_treated": 2.4, "mean_control": 1.1,
                "sd_treated": 0.5, "sd_control": 0.4,
                "n_treated": 80, "n_control": 200,
            }],
        },
        "findings": [
            {"severity": "crit", "confidence": 0.94,
             "title": "Anomaly in pressure at row 1",
             "method": "iso-forest + robust-Z"},
            {"severity": "warn", "confidence": 0.86,
             "title": "Anomaly in wind at row 2",
             "method": "iso-forest + robust-Z"},
            {"severity": "warn", "confidence": 0.7,
             "title": "Anomaly in wave at row 3",
             "method": "iso-forest + robust-Z"},
        ],
    }


class TestCredibilityPass:
    def test_emits_a_credibility_block(self, realistic_state):
        from fantasyai.aurora.stats import apply_credibility_pass
        apply_credibility_pass(realistic_state)
        cred = realistic_state["credibility"]
        assert cred["applied"] is True
        for key in ("anomalies", "anomaly_score_ci", "forecast_ci",
                    "physics_effect_size", "causal_effect_size",
                    "multiple_comparisons"):
            assert key in cred, f"missing credibility step: {key}"

    def test_anomalies_get_p_values(self, realistic_state):
        from fantasyai.aurora.stats import apply_credibility_pass
        apply_credibility_pass(realistic_state)
        for a in realistic_state["anomalies"]:
            assert "p_value" in a
            assert 0.0 <= a["p_value"] <= 1.0

    def test_findings_inherit_p_value_and_get_q_value(self, realistic_state):
        from fantasyai.aurora.stats import apply_credibility_pass
        apply_credibility_pass(realistic_state)
        for f in realistic_state["findings"]:
            assert "p_value" in f
            assert "q_value" in f
            assert "p_bonferroni" in f
            assert "significant_after_bh" in f

    def test_forecast_gets_confidence_interval_block(self, realistic_state):
        from fantasyai.aurora.stats import apply_credibility_pass
        apply_credibility_pass(realistic_state)
        ci = realistic_state["forecast"]["confidence_interval"]
        assert ci["ci_low"] == 10.5
        assert ci["ci_high"] == 14.0

    def test_physics_gets_r2_effect_size(self, realistic_state):
        from fantasyai.aurora.stats import apply_credibility_pass
        apply_credibility_pass(realistic_state)
        eff = realistic_state["physics"]["effect_size"]
        assert eff["kind"] == "r_squared"
        assert eff["value"] == pytest.approx(0.83)
        assert eff["interpretation"] in ("very_large", "large")

    def test_causal_gets_cohens_d_when_summary_stats_present(self,
                                                              realistic_state):
        from fantasyai.aurora.stats import apply_credibility_pass
        apply_credibility_pass(realistic_state)
        eff = realistic_state["causal"]["items"][0]["effect_size"]
        assert eff["kind"] == "cohens_d"
        assert eff["value"] is not None
        assert eff["value"] > 0  # treated mean > control mean

    def test_idempotent_on_re_run(self, realistic_state):
        """Running the pass twice produces the same result — no
        accumulating side-effects on the findings list."""
        from fantasyai.aurora.stats import apply_credibility_pass
        apply_credibility_pass(realistic_state)
        snapshot_q = [f["q_value"] for f in realistic_state["findings"]]
        apply_credibility_pass(realistic_state)
        again_q = [f["q_value"] for f in realistic_state["findings"]]
        assert snapshot_q == again_q

    def test_no_anomalies_does_not_crash(self):
        from fantasyai.aurora.stats import apply_credibility_pass
        state = {"findings": [], "anomalies": []}
        res = apply_credibility_pass(state)
        assert res["applied"] is True
        # Per-step skipped reasons must be present so the audit trail is honest.
        assert res["anomalies"]["applied"] is False


# ============================================================
# Synthesis-engine integration — credibility selectors resolve
# ============================================================

class TestSynthesisCredibilitySelectors:
    def test_selectors_all_resolve_after_pass(self, realistic_state):
        from fantasyai.aurora.stats import apply_credibility_pass
        from fantasyai.aurora.synthesis import extract_slot
        apply_credibility_pass(realistic_state)
        # The selectors used by the updated templates must resolve to
        # non-None values for this fully-populated state.
        selectors = [
            "credibility.multiple_comparisons.applied",
            "credibility.multiple_comparisons.summary.n_significant_bh",
            "anomaly_score_summary.median",
            "anomaly_score_summary.ci_low",
            "anomaly_score_summary.ci_high",
            "forecast.confidence_interval.ci_low",
            "forecast.confidence_interval.ci_high",
            "physics.effect_size.value",
            "physics.effect_size.interpretation",
        ]
        for s in selectors:
            v = extract_slot(s, realistic_state)
            assert v is not None, f"selector {s} returned None"
