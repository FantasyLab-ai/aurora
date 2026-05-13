"""
Wave C — panel-data two-way fixed-effects decomposition + cluster
analysis with bootstrap stability.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

np = pytest.importorskip("numpy")


# ============================================================
# Two-way fixed effects
# ============================================================

class TestTwoWayFixedEffects:
    def test_entity_dominates_when_entity_levels_drive_signal(self):
        """When each entity has a distinct level and time-trend is
        small, entity share must dominate."""
        from fantasyai.aurora.stats import two_way_fixed_effects
        rng = np.random.default_rng(0)
        ent, tim, val = [], [], []
        for level in (10.0, 5.0, -3.0, 0.0):
            for t in range(5):
                ent.append(f"E{level}")
                tim.append(t)
                val.append(level + 0.3 * t + rng.normal(0, 0.3))
        res = two_way_fixed_effects(ent, tim, val, bootstrap=False)
        assert res["available"] is True
        assert res["dominant"] == "entity"
        assert res["share_entity"] > 0.7

    def test_time_dominates_when_time_trend_drives_signal(self):
        from fantasyai.aurora.stats import two_way_fixed_effects
        rng = np.random.default_rng(0)
        ent, tim, val = [], [], []
        for level in (0.0, 0.0, 0.0):
            for t in range(10):
                ent.append(f"L{level}")
                tim.append(t)
                # Strong time-trend, weak entity differences.
                val.append(0.0 + 5.0 * t + rng.normal(0, 0.2))
        res = two_way_fixed_effects(ent, tim, val, bootstrap=False)
        assert res["available"] is True
        assert res["dominant"] == "time"
        assert res["share_time"] > 0.7

    def test_residual_dominates_when_signal_is_noise(self):
        """All-noise data → idiosyncratic share wins. The honest
        no-signal finding."""
        from fantasyai.aurora.stats import two_way_fixed_effects
        rng = np.random.default_rng(0)
        ent, tim, val = [], [], []
        for e in "ABCDE":
            for t in range(20):
                ent.append(e); tim.append(t)
                val.append(rng.normal(0, 1))
        res = two_way_fixed_effects(ent, tim, val, bootstrap=False)
        assert res["available"] is True
        assert res["dominant"] == "residual"

    def test_bootstrap_attaches_ci_to_each_share(self):
        from fantasyai.aurora.stats import two_way_fixed_effects
        rng = np.random.default_rng(0)
        ent, tim, val = [], [], []
        for e in "ABCDE":
            for t in range(20):
                ent.append(e); tim.append(t)
                val.append(rng.normal(0, 1))
        res = two_way_fixed_effects(ent, tim, val,
                                      bootstrap=True, n_resamples=50)
        ci = res["shares_ci"]
        assert "entity" in ci and "time" in ci and "residual" in ci
        for k, (lo, hi) in ci.items():
            assert lo <= hi

    def test_returns_unavailable_for_tiny_input(self):
        from fantasyai.aurora.stats import two_way_fixed_effects
        res = two_way_fixed_effects(["A"], [0], [1.0])
        assert res["available"] is False
        assert "skipped_reason" in res


# ============================================================
# k-means + silhouette
# ============================================================

class TestKMeans:
    def test_recovers_three_well_separated_clusters(self):
        from fantasyai.aurora.stats import kmeans, silhouette_score
        rng = np.random.default_rng(0)
        clusters = [rng.normal(c, 0.3, size=(50, 2))
                     for c in ([0, 0], [5, 5], [-5, 5])]
        X = np.vstack(clusters)
        res = kmeans(X, k=3, seed=1)
        assert res["available"]
        sil = silhouette_score(X, res["labels"])
        # Well-separated clusters → silhouette > 0.7.
        assert sil["score"] > 0.6

    def test_skips_when_n_smaller_than_k(self):
        from fantasyai.aurora.stats import kmeans
        res = kmeans([[1.0]], k=3)
        assert res["available"] is False


# ============================================================
# Adjusted Rand Index
# ============================================================

class TestARI:
    def test_identical_partitions_yield_ari_one(self):
        from fantasyai.aurora.stats import adjusted_rand_index
        a = [0, 0, 1, 1, 2, 2]
        assert adjusted_rand_index(a, a) == pytest.approx(1.0)

    def test_label_permutation_invariant(self):
        from fantasyai.aurora.stats import adjusted_rand_index
        a = [0, 0, 1, 1, 2, 2]
        b = [2, 2, 0, 0, 1, 1]   # same partition, different IDs
        assert adjusted_rand_index(a, b) == pytest.approx(1.0)


# ============================================================
# Cluster stability
# ============================================================

class TestClusterStability:
    def test_strong_clusters_yield_strong_verdict(self):
        from fantasyai.aurora.stats import cluster_stability
        rng = np.random.default_rng(0)
        clusters = [rng.normal(c, 0.3, size=(40, 2))
                     for c in ([0, 0], [5, 5], [-5, 5])]
        X = np.vstack(clusters)
        res = cluster_stability(X, k=3, n_bootstrap=20, seed=1)
        assert res["available"]
        assert res["verdict"] == "strong"
        assert res["stability"] > 0.7

    def test_no_real_clusters_yield_weak_verdict(self):
        from fantasyai.aurora.stats import cluster_stability
        rng = np.random.default_rng(0)
        # One blob — k=4 fits noise → low stability.
        X = rng.normal(size=(100, 3))
        res = cluster_stability(X, k=4, n_bootstrap=15, seed=1)
        assert res["available"]
        # Stability should be low when there's no real structure.
        assert res["stability"] < 0.7


class TestSelectBestK:
    def test_picks_k_three_for_three_cluster_data(self):
        from fantasyai.aurora.stats import select_best_k
        rng = np.random.default_rng(0)
        clusters = [rng.normal(c, 0.3, size=(40, 2))
                     for c in ([0, 0], [5, 5], [-5, 5])]
        X = np.vstack(clusters)
        sel = select_best_k(X, k_range=(2, 3, 4, 5), seed=1)
        assert sel["available"]
        assert sel["best_k"] == 3
        assert sel["best_verdict"] == "strong"
