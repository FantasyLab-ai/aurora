"""
Brief 3 — advanced analytical methods (Waves G, H, I) + Brief-2 caveat
fixes (full Louvain, FAST-MCD-style Mahalanobis).

Each method gets sanity tests on synthetic data with a known answer.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

np = pytest.importorskip("numpy")


# ============================================================
# Brief 2 caveat verification
# ============================================================

class TestLouvainFullMultiLevel:
    def test_finds_two_clear_communities_on_clique_graph(self):
        from fantasyai.aurora.stats import louvain_communities
        edges = []
        # Two 5-cliques joined by a single bridge.
        for i in range(5):
            for j in range(i + 1, 5):
                edges.append((i, j))
        for i in range(5, 10):
            for j in range(i + 1, 10):
                edges.append((i, j))
        edges.append((4, 5))
        res = louvain_communities(edges, 10)
        assert res["available"]
        # Full Louvain (multi-level) should achieve high modularity here.
        assert res["modularity"] > 0.2
        assert res["n_communities"] == 2
        assert res["method"] == "louvain_multi_level"

    def test_records_n_levels(self):
        from fantasyai.aurora.stats import louvain_communities
        edges = [(i, (i + 1) % 6) for i in range(6)]
        res = louvain_communities(edges, 6)
        assert res["available"]
        assert "n_levels" in res
        assert res["n_levels"] >= 1


class TestMahalanobisFastMCDStyle:
    def test_uses_robust_mcd_method_label(self):
        from fantasyai.aurora.stats import mahalanobis_outliers
        rng = np.random.default_rng(0)
        X = rng.normal(size=(60, 2))
        res = mahalanobis_outliers(X, robust=True)
        assert res["cov_method"] == "robust_mcd"


# ============================================================
# Wave G — Bayesian inference
# ============================================================

class TestBayesianInference:
    def test_beta_binomial_posterior_credible_interval(self):
        from fantasyai.aurora.stats import beta_binomial_posterior
        res = beta_binomial_posterior(30, 50)
        assert res["available"]
        # Posterior mean ≈ 31/52 = 0.596 with Beta(1,1) prior.
        assert 0.55 < res["posterior_mean"] < 0.65
        assert res["ci_low"] < res["posterior_mean"] < res["ci_high"]

    def test_normal_inverse_gamma_recovers_mu(self):
        from fantasyai.aurora.stats import normal_inverse_gamma
        rng = np.random.default_rng(0)
        y = rng.normal(5.0, 2.0, 100)
        res = normal_inverse_gamma(y)
        assert res["available"]
        assert abs(res["posterior_mu_mean"] - 5.0) < 0.5

    def test_metropolis_hastings_recovers_normal_mean(self):
        """Target N(0, 1); MH should produce samples with mean ≈ 0 and
        acceptance rate in a sensible range."""
        from fantasyai.aurora.stats import metropolis_hastings
        log_p = lambda t: -0.5 * t[0] ** 2
        res = metropolis_hastings(log_p, [0.0],
                                    n_samples=2000, burn_in=500,
                                    proposal_sd=2.0)
        assert abs(res["posterior_mean"][0]) < 0.3
        assert 0.1 < res["acceptance_rate"] < 0.7

    def test_savage_dickey_bayes_factor(self):
        from fantasyai.aurora.stats import bayes_factor_savage_dickey
        rng = np.random.default_rng(0)
        # Posterior centred at 2 — strong evidence against null=0.
        samples = rng.normal(2.0, 0.3, 1000)
        bf = bayes_factor_savage_dickey(samples, null_value=0.0)
        assert bf["available"]
        assert bf["evidence_direction"] == "for_alternative"
        assert bf["bf_10"] > 1


# ============================================================
# Wave G — Mutual information
# ============================================================

class TestMutualInformation:
    def test_ksg_detects_nonlinear_relationship(self):
        from fantasyai.aurora.stats import mi_ksg
        rng = np.random.default_rng(0)
        x = rng.uniform(-3, 3, 300)
        y = np.sin(2 * x) + rng.normal(0, 0.1, 300)
        mi = mi_ksg(x, y, k=3)
        # Strong dependency despite |Pearson r| being small (curve).
        assert mi > 0.3
        r = float(np.corrcoef(x, y)[0, 1])
        assert abs(r) < 0.5

    def test_mi_matrix_runs_and_returns_matrix(self):
        from fantasyai.aurora.stats import mi_matrix
        rng = np.random.default_rng(0)
        x1 = rng.uniform(-3, 3, 300)
        x2 = np.sin(2 * x1) + rng.normal(0, 0.1, 300)
        x3 = rng.normal(size=300)
        X = np.column_stack([x1, x2, x3])
        res = mi_matrix(X, columns=["x1", "x2", "x3"], estimator="ksg")
        assert res["available"]
        # MI between strongly-related x1, x2 should clearly exceed
        # MI(x1, x3) (independent variables).
        m = np.asarray(res["mi_matrix"])
        assert m[0, 1] > m[0, 2]
        # And a nonlinear-pairs list is always emitted (may be empty).
        assert isinstance(res["nonlinear_pairs"], list)

    def test_conditional_mi_drops_when_z_explains(self):
        from fantasyai.aurora.stats import conditional_mi, mi_binning
        rng = np.random.default_rng(0)
        z = rng.normal(size=300)
        x = z + rng.normal(0, 0.1, 300)
        y = z + rng.normal(0, 0.1, 300)
        unc = mi_binning(x, y)
        cmi = conditional_mi(x, y, z)
        # Conditioning on z should reduce dependence substantially.
        assert cmi < unc


# ============================================================
# Wave G — Granger / transfer entropy
# ============================================================

class TestGranger:
    def test_detects_bidirectional_feedback(self):
        from fantasyai.aurora.stats import bivariate_granger
        rng = np.random.default_rng(0)
        N = 300
        a = np.zeros(N); b = np.zeros(N)
        for t in range(2, N):
            a[t] = 0.5 * a[t - 1] + 0.4 * b[t - 1] + rng.normal()
            b[t] = 0.5 * b[t - 1] + 0.4 * a[t - 1] + rng.normal()
        gc = bivariate_granger(a, b, max_lag=3)
        assert gc["available"]
        assert gc["verdict"] == "bidirectional"

    def test_one_way_causation_is_detected(self):
        from fantasyai.aurora.stats import bivariate_granger
        rng = np.random.default_rng(0)
        N = 300
        x = rng.normal(size=N)
        y = np.zeros(N)
        for t in range(2, N):
            y[t] = 0.3 * y[t - 1] + 0.6 * x[t - 1] + rng.normal()
        gc = bivariate_granger(x, y, max_lag=3,
                                  var_x_label="x", var_y_label="y")
        assert gc["available"]
        # Forward (x → y) significant; reverse should be weaker.
        assert gc["forward"]["p"] < 0.05

    def test_transfer_entropy_positive_for_dependence(self):
        from fantasyai.aurora.stats import transfer_entropy
        rng = np.random.default_rng(0)
        N = 400
        x = rng.normal(size=N)
        y = np.zeros(N)
        for t in range(1, N):
            y[t] = 0.3 * y[t - 1] + 0.6 * x[t - 1] + rng.normal()
        te = transfer_entropy(x, y, lag=1)
        assert te["available"]
        assert te["transfer_entropy"] > 0


# ============================================================
# Wave H — SINDy
# ============================================================

class TestSINDy:
    def test_recovers_lotka_volterra(self):
        """SINDy on synthetic Lotka-Volterra should identify x*y product
        terms in both equations."""
        from fantasyai.aurora.stats import sindy_discover
        dt = 0.01; T = 30
        N = int(T / dt)
        X = np.zeros((N, 2)); X[0] = [10.0, 5.0]
        for i in range(N - 1):
            x, y = X[i]
            dx = 1.0 * x - 0.1 * x * y
            dy = 0.075 * x * y - 1.5 * y
            X[i + 1] = X[i] + dt * np.array([dx, dy])
        res = sindy_discover(X, dt, column_names=["x", "y"],
                                poly_degree=2, threshold=0.1)
        assert res["available"]
        # The cross-term xy must appear in at least one equation.
        xy_term_present = any(
            "x*y" in t["feature"]
            for terms in res["active_terms_per_state"]
            for t in terms
        )
        assert xy_term_present, "SINDy didn't recover the xy product term"
        # Total RMSE should be small.
        assert res["rmse_total"] < 0.2

    def test_polynomial_library_includes_products(self):
        from fantasyai.aurora.stats import polynomial_library
        X = np.array([[1.0, 2.0], [3.0, 4.0]])
        Theta, names = polynomial_library(X, degree=2,
                                              column_names=["a", "b"])
        # Names must include the product term a*b.
        assert any(n in names for n in ("a*b", "b*a"))


# ============================================================
# Wave H — HMM
# ============================================================

class TestHMM:
    def test_recovers_two_state_structure(self):
        from fantasyai.aurora.stats import fit_hmm_select_k
        rng = np.random.default_rng(0)
        T = 600
        states = np.zeros(T, dtype=int)
        for t in range(1, T):
            if rng.random() < 0.02:
                states[t] = 1 - states[t - 1]
            else:
                states[t] = states[t - 1]
        y = np.where(states == 0,
                       rng.normal(0, 0.5, T),
                       rng.normal(3, 0.5, T))
        res = fit_hmm_select_k(y, k_range=(2, 3, 4), seed=1)
        assert res["available"]
        assert res["best_K"] == 2
        # Two state means are well-separated.
        means = sorted(res["means_sorted"])
        assert means[1] - means[0] > 1.5

    def test_viterbi_runs_on_known_params(self):
        from fantasyai.aurora.stats import viterbi
        pi = np.array([0.5, 0.5])
        A = np.array([[0.9, 0.1], [0.1, 0.9]])
        means = np.array([0.0, 5.0])
        sigmas = np.array([0.5, 0.5])
        y = [0.0, 0.1, -0.1, 5.0, 5.1, 4.9, 0.0]
        states, log_lik = viterbi(y, pi, A, means, sigmas)
        # Path: state 0, 0, 0, 1, 1, 1, 0.
        assert list(states[:3]) == [0, 0, 0]
        assert list(states[3:6]) == [1, 1, 1]


# ============================================================
# Wave H — Wavelet + Lomb-Scargle
# ============================================================

class TestWavelet:
    def test_detects_chirp_drift(self):
        from fantasyai.aurora.stats import detect_nonstationary_period
        t = np.linspace(0, 10, 500)
        chirp = np.sin(2 * np.pi * (1.0 + 0.5 * t) * t)
        res = detect_nonstationary_period(chirp, dt=t[1] - t[0])
        assert res["available"]
        assert res["verdict"] in ("drifting", "switching")

    def test_stationary_signal_detected_as_stationary(self):
        from fantasyai.aurora.stats import detect_nonstationary_period
        t = np.linspace(0, 10, 500)
        steady = np.sin(2 * np.pi * t)  # period 1.0 throughout
        res = detect_nonstationary_period(steady, dt=t[1] - t[0])
        assert res["available"]
        assert res["verdict"] == "stationary"


class TestLombScargle:
    def test_recovers_period_under_irregular_sampling(self):
        from fantasyai.aurora.stats import lomb_scargle
        rng = np.random.default_rng(0)
        N = 100
        t = np.sort(rng.uniform(0, 50, N))
        period_true = 5.0
        y = np.sin(2 * np.pi * t / period_true) + rng.normal(0, 0.3, N)
        res = lomb_scargle(t, y, n_freq=500)
        assert res["available"]
        assert abs(res["dominant_period"] - period_true) / period_true < 0.05
        assert res["false_alarm_probability"] < 0.05


# ============================================================
# Wave I — Gaussian process
# ============================================================

class TestGaussianProcess:
    def test_fits_and_predicts_smooth_function(self):
        from fantasyai.aurora.stats import fit_gp, predict_gp
        rng = np.random.default_rng(0)
        X = np.linspace(0, 10, 30)
        y = np.sin(X) + 0.1 * rng.standard_normal(30)
        fit = fit_gp(X, y, kernel="rbf")
        assert fit["available"]
        # Posterior mean at the training points must be close to y.
        pred_train = predict_gp(fit, X)
        assert pred_train["available"]
        residuals = np.abs(np.asarray(pred_train["mean"]) - y)
        assert residuals.mean() < 0.5
        # And out-of-sample sigma is non-zero (extrapolation has uncertainty).
        pred_far = predict_gp(fit, [15.0])
        assert pred_far["sigma"][0] > 0

    def test_extrapolation_uncertainty_grows(self):
        from fantasyai.aurora.stats import fit_gp, predict_gp
        rng = np.random.default_rng(0)
        X = np.linspace(0, 5, 20)
        y = X + 0.1 * rng.standard_normal(20)
        fit = fit_gp(X, y, kernel="rbf")
        pred_in = predict_gp(fit, [2.5])
        pred_out = predict_gp(fit, [12.0])  # far past training
        assert pred_out["sigma"][0] > pred_in["sigma"][0]


# ============================================================
# Wave I — Topology
# ============================================================

class TestPersistentHomology:
    def test_circle_detects_loop(self):
        """Points sampled densely on a circle should produce H₁ births."""
        from fantasyai.aurora.stats import persistent_homology, topological_summary
        rng = np.random.default_rng(0)
        theta = np.linspace(0, 2 * np.pi, 40, endpoint=False)
        circle = np.column_stack([np.cos(theta), np.sin(theta)])
        circle = circle + 0.05 * rng.standard_normal(circle.shape)
        ph = persistent_homology(circle)
        assert ph["available"]
        # At least one 1-cycle should be born.
        assert ph["n_h1_births"] >= 1
        summary = topological_summary(ph)
        assert summary["verdict"] in ("loops_present", "complex")


# ============================================================
# Wave I — Power analysis
# ============================================================

class TestPowerAnalysis:
    def test_t_test_sample_size_matches_textbook(self):
        from fantasyai.aurora.stats import power_t_test
        # d=0.5, α=0.05 two-sided, 80% power → ~63/group (Cohen 1988).
        res = power_t_test(0.5)
        assert 55 <= res["n_per_group"] <= 75

    def test_correlation_sample_size_decreases_with_r(self):
        from fantasyai.aurora.stats import power_correlation
        small = power_correlation(0.1)["n_required"]
        large = power_correlation(0.5)["n_required"]
        assert large < small


# ============================================================
# Wave I — Compositional
# ============================================================

class TestCompositional:
    def test_detects_simplex_data(self):
        from fantasyai.aurora.stats import is_compositional
        rows = np.array([[0.1, 0.3, 0.6], [0.5, 0.3, 0.2],
                           [0.4, 0.4, 0.2]])
        det = is_compositional(rows)
        assert det["compositional"] is True

    def test_rejects_non_compositional_data(self):
        from fantasyai.aurora.stats import is_compositional
        det = is_compositional(np.array([[1, 2, 3], [4, 5, 6],
                                            [7, 8, 9]]))
        assert det["compositional"] is False

    def test_clr_transform_zero_centred_rows(self):
        from fantasyai.aurora.stats import clr_transform
        rows = np.array([[0.2, 0.3, 0.5], [0.1, 0.4, 0.5]])
        res = clr_transform(rows)
        assert res["available"]
        for row in res["transformed"]:
            assert abs(sum(row)) < 1e-9, "CLR rows must sum to zero"

    def test_ilr_reduces_dimension_by_one(self):
        from fantasyai.aurora.stats import ilr_transform
        rows = np.array([[0.2, 0.3, 0.5], [0.1, 0.4, 0.5]])
        res = ilr_transform(rows)
        assert res["available"]
        assert res["n_output_cols"] == res["n_input_cols"] - 1


# ============================================================
# Advanced-pass orchestrator end-to-end
# ============================================================

class TestAdvancedPass:
    def test_runs_steps_and_emits_findings_for_lv(self):
        from fantasyai.aurora.stats import apply_advanced_pass
        # Lotka-Volterra
        dt = 0.01; T = 30
        N = int(T / dt)
        X = np.zeros((N, 2)); X[0] = [10.0, 5.0]
        for i in range(N - 1):
            x, y = X[i]
            X[i + 1] = X[i] + dt * np.array([x - 0.1 * x * y,
                                                0.075 * x * y - 1.5 * y])
        state = {
            # WS2-S1 fix: SINDy now requires a time axis. Lotka-Volterra
            # IS a time series; the test just didn't model the structure
            # block. Real datasets always carry one.
            "structure": {"time_axis": True, "time_col": "t"},
            "dataset_matrix": X.tolist(),
            "dataset_matrix_columns": ["x", "y"],
            "dataset_dt": dt,
            "primary_series": X[:, 0].tolist(),
            "primary_times": np.arange(N).tolist(),
            "findings": [],
        }
        apply_advanced_pass(state)
        adv = state["advanced_methods"]
        assert adv["applied"] is True
        # SINDy should have run successfully.
        assert adv["steps"]["sindy"]["applied"] is True
        # At least one finding with kind_hint='discovered_dynamics'.
        kh = [f.get("kind_hint") for f in state["findings"]]
        assert "discovered_dynamics" in kh

    def test_skips_cleanly_with_empty_state(self):
        from fantasyai.aurora.stats import apply_advanced_pass
        state = {"findings": []}
        res = apply_advanced_pass(state)
        assert res["applied"] is True
        # All steps should report "applied": False with a reason.
        for step_name, step_res in res["steps"].items():
            if step_res.get("applied"):
                continue
            assert "reason" in step_res
