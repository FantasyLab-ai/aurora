"""
Wave B — multivariate outliers + sensitivity analysis.

Pinning tests for:
* Mahalanobis (sample + iterative-trimmed robust covariance).
* Isolation Forest (pure-numpy).
* Local Outlier Factor.
* Combined detector with consensus voting.
* Sensitivity sweeps (z-threshold, LOF k).
* Credibility-pass integration writing the multivariate-outlier
  finding + sensitivity profile.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

np = pytest.importorskip("numpy")


# ============================================================
# Mahalanobis
# ============================================================

class TestMahalanobis:
    def test_flags_obvious_multivariate_outliers(self):
        from fantasyai.aurora.stats import mahalanobis_outliers
        rng = np.random.default_rng(0)
        inliers = rng.multivariate_normal([0, 0], [[1, 0]
                                                     , [0, 1]], size=200)
        # 5 obvious far-from-origin points.
        outliers = np.array([[10, 10], [-9, 8], [8, -8],
                              [-7, -7], [12, 0]])
        X = np.vstack([inliers, outliers])
        res = mahalanobis_outliers(X, robust=True)
        # All 5 injected outliers must be flagged.
        for i in range(200, 205):
            assert res["is_outlier"][i] is True, f"row {i} not flagged"

    def test_robust_handles_leverage_points(self):
        """Sample covariance gets pulled toward outliers; the robust
        estimator should still flag them."""
        from fantasyai.aurora.stats import mahalanobis_outliers
        rng = np.random.default_rng(1)
        # 100 inliers + 30 contaminating points (~25% contamination).
        inliers = rng.multivariate_normal([0, 0], [[1, 0], [0, 1]], 100)
        contamination = np.array([[15, 15]] * 30)
        X = np.vstack([inliers, contamination])
        sample = mahalanobis_outliers(X, robust=False)
        robust = mahalanobis_outliers(X, robust=True)
        # Robust should flag at least as many of the contamination
        # points, and ideally more.
        sample_hits = sum(sample["is_outlier"][100:130])
        robust_hits = sum(robust["is_outlier"][100:130])
        assert robust_hits >= sample_hits

    def test_records_method_and_cov_method(self):
        from fantasyai.aurora.stats import mahalanobis_outliers
        rng = np.random.default_rng(0)
        X = rng.normal(size=(50, 3))
        res = mahalanobis_outliers(X, robust=True)
        assert res["method"] == "mahalanobis_robust"
        # FAST-MCD-style estimator (post-caveat-fix).
        assert res["cov_method"] in ("robust_trimmed", "robust_mcd")
        assert res["n_features"] == 3
        assert "notes" in res

    def test_handles_insufficient_observations(self):
        from fantasyai.aurora.stats import mahalanobis_outliers
        # 3 obs, 5 features → can't fit a covariance.
        X = np.eye(3, 5)
        res = mahalanobis_outliers(X)
        assert res["skipped_reason"] == "insufficient_observations"


# ============================================================
# Isolation Forest
# ============================================================

class TestIsolationForest:
    def test_flags_planted_outlier(self):
        from fantasyai.aurora.stats import isolation_forest_outliers
        rng = np.random.default_rng(0)
        inliers = rng.normal(size=(200, 3))
        outlier = np.array([[10, 10, 10]])
        X = np.vstack([inliers, outlier])
        res = isolation_forest_outliers(X, n_trees=100, seed=1)
        assert res["is_outlier"][-1] is True
        # Score is in [0, 1], higher = more anomalous.
        assert 0.0 <= res["scores"][0] <= 1.0
        assert res["scores"][-1] > res["scores"][0]

    def test_seed_reproducibility(self):
        from fantasyai.aurora.stats import isolation_forest_outliers
        rng = np.random.default_rng(0)
        X = rng.normal(size=(100, 2))
        a = isolation_forest_outliers(X, n_trees=50, seed=42)
        b = isolation_forest_outliers(X, n_trees=50, seed=42)
        assert a["scores"] == b["scores"]


# ============================================================
# LOF
# ============================================================

class TestLOF:
    def test_flags_dense_cluster_against_sparse_outlier(self):
        from fantasyai.aurora.stats import lof_outliers
        rng = np.random.default_rng(0)
        cluster = rng.normal(0.0, 0.1, size=(60, 2))
        outlier = np.array([[5, 5]])  # far from the cluster
        X = np.vstack([cluster, outlier])
        res = lof_outliers(X, k=10, threshold=1.5)
        # The far point should have the largest LOF.
        scores = res["scores"]
        assert scores[-1] == max(scores)
        assert res["is_outlier"][-1] is True

    def test_skips_when_too_few_obs(self):
        from fantasyai.aurora.stats import lof_outliers
        X = np.array([[1.0, 2.0], [3.0, 4.0]])
        res = lof_outliers(X, k=20)
        assert "skipped_reason" in res


# ============================================================
# Combined detector
# ============================================================

class TestDetectMultivariateOutliers:
    def test_returns_per_method_and_consensus(self):
        from fantasyai.aurora.stats import detect_multivariate_outliers
        rng = np.random.default_rng(0)
        inliers = rng.multivariate_normal([0, 0], [[1, 0.5],
                                                     [0.5, 1]], 200)
        outliers = np.array([[10, 10], [-9, 8], [8, -8],
                              [-7, -7], [12, 0]])
        X = np.vstack([inliers, outliers])
        res = detect_multivariate_outliers(X, seed=1)
        assert "per_method" in res
        assert "points" in res
        assert res["n_observations"] == 205
        # The 5 injected outliers should each have consensus 2 or 3.
        for i in range(200, 205):
            pt = res["points"][i]
            assert pt["consensus"] >= 2, f"row {i} has consensus {pt['consensus']}"


# ============================================================
# Sensitivity sweep
# ============================================================

class TestSensitivity:
    def test_z_threshold_sweep_shape(self):
        from fantasyai.aurora.stats import sensitivity_z_threshold
        z = [4.2, 3.8, 3.1, 2.5, 2.6, 2.4, 1.5, 1.0, 0.3]
        res = sensitivity_z_threshold(z, canonical=3.0)
        assert res["parameter"] == "z_threshold"
        assert res["robustness"] in ("robust", "fragile", "absent")
        assert res["n_values"] == len(res["trajectory"])

    def test_robustness_classification(self):
        """All-positive sweep should classify as robust; all-zero as
        absent."""
        from fantasyai.aurora.stats import parameter_sweep
        # Always-present finding.
        always = parameter_sweep(
            lambda v: True, "p", [0.1, 0.2, 0.3, 0.4],
            presence_check=lambda r: r is True)
        assert always["robustness"] == "robust"
        # Never-present finding.
        never = parameter_sweep(
            lambda v: False, "p", [0.1, 0.2, 0.3, 0.4],
            presence_check=lambda r: r is True)
        assert never["robustness"] == "absent"

    def test_attach_sensitivity_profile_writes_short_label(self):
        from fantasyai.aurora.stats import (
            attach_sensitivity_profile, sensitivity_z_threshold,
        )
        f = {"title": "test"}
        prof = sensitivity_z_threshold([4.2, 3.5, 3.0, 2.5])
        attach_sensitivity_profile(f, prof)
        assert "sensitivity_profile" in f
        assert f["parameter_robustness"] in ("robust", "fragile", "absent")


# ============================================================
# Credibility-pass integration
# ============================================================

class TestCredibilityPassWaveB:
    def test_emits_multivariate_finding_when_consensus_present(self):
        from fantasyai.aurora.stats import apply_credibility_pass
        rng = np.random.default_rng(0)
        inliers = rng.multivariate_normal([0, 0], [[1, 0.5],
                                                     [0.5, 1]], 200)
        outliers = np.array([[10, 10], [-9, 8], [8, -8],
                              [-7, -7], [12, 0]])
        X = np.vstack([inliers, outliers])
        state = {
            "system_model": {"template_id": "enviro"},
            "dataset_matrix": X.tolist(),
            "dataset_matrix_columns": ["feat_a", "feat_b"],
            "anomalies": [
                {"when": "row 1", "z": 4.2, "score": 4.2, "severity": "crit"},
                {"when": "row 2", "z": 3.5, "score": 3.5, "severity": "warn"},
                {"when": "row 3", "z": 3.0, "score": 3.0, "severity": "warn"},
            ],
            "findings": [],
        }
        apply_credibility_pass(state)
        assert state["multivariate_outliers"]["available"] is True
        # A new multivariate-outlier finding was appended.
        mvo_findings = [
            f for f in state["findings"]
            if f.get("kind_hint") == "multivariate_outlier"
        ]
        assert len(mvo_findings) == 1
        f = mvo_findings[0]
        assert f["method"] == "multivariate_outlier_consensus"
        assert f["confidence"] > 0.5

    def test_no_finding_when_no_dataset_matrix(self):
        """If no dataset_matrix is in scope, the step is cleanly skipped."""
        from fantasyai.aurora.stats import apply_credibility_pass
        state = {"findings": [], "anomalies": []}
        res = apply_credibility_pass(state)
        assert res["multivariate_outliers"]["applied"] is False
        assert res["multivariate_outliers"]["reason"] == "no_dataset_matrix"

    def test_attaches_sensitivity_profile_to_anomaly_findings(self):
        from fantasyai.aurora.stats import apply_credibility_pass
        state = {
            "anomalies": [
                {"when": "row 1", "z": 4.2, "score": 4.2, "severity": "crit"},
                {"when": "row 2", "z": 3.5, "score": 3.5, "severity": "warn"},
                {"when": "row 3", "z": 3.0, "score": 3.0, "severity": "warn"},
            ],
            "findings": [
                {"title": "Anomaly in pressure at row 1",
                 "method": "iso-forest + robust-Z"},
            ],
        }
        apply_credibility_pass(state)
        assert state["sensitivity_z_threshold"]["available"] is True
        f = state["findings"][0]
        assert "sensitivity_profile" in f
        assert "parameter_robustness" in f


# ============================================================
# Synthesis template wiring
# ============================================================

class TestSynthesisIntegration:
    def test_multivariate_template_fires_when_consensus_present(self):
        from fantasyai.aurora.stats import apply_credibility_pass
        from fantasyai.aurora.synthesis import synthesize, list_templates
        list_templates(force_reload=True)
        rng = np.random.default_rng(0)
        inliers = rng.multivariate_normal([0, 0], [[1, 0.5],
                                                     [0.5, 1]], 200)
        outliers = np.array([[10, 10], [-9, 8], [8, -8],
                              [-7, -7], [12, 0]])
        X = np.vstack([inliers, outliers])
        state = {
            "system_model": {"template_id": "enviro"},
            "dataset_matrix": X.tolist(),
            "dataset_matrix_columns": ["feat_a", "feat_b"],
            "anomalies": [
                {"when": "row 1", "z": 4.2, "score": 4.2, "severity": "crit"},
                {"when": "row 2", "z": 3.5, "score": 3.5, "severity": "warn"},
                {"when": "row 3", "z": 3.0, "score": 3.0, "severity": "warn"},
            ],
            "findings": [
                {"title": "Anomaly in pressure at row 1",
                 "method": "iso-forest + robust-Z",
                 "severity": "crit", "confidence": 0.94},
            ],
        }
        apply_credibility_pass(state)
        synthesize(state)
        meta = state["synthesis_meta"]
        assert meta["template_id"] == "multivariate_outliers_consensus"
        assert "multivariate" in state["interpretive_summary"].lower()
        assert "{" not in state["interpretive_summary"]
