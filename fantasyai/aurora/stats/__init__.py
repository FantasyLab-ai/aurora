"""
fantasyai.aurora.stats — credibility-infrastructure utilities.

Three modules ship in Wave A of the analytical-methods expansion:

* :mod:`bootstrap`           — non-parametric, BCa, and block bootstrap
                                confidence intervals for any point statistic.
* :mod:`multiple_comparisons` — Benjamini-Hochberg (FDR) and Bonferroni
                                (FWER) corrections for findings lists with
                                p-values.
* :mod:`effect_size`         — Cohen's d, eta-squared, odds ratio, R² with
                                conventional small/medium/large interpretation.

Glass-box: every function records the inputs / parameters / assumptions in
its return dict, so the provenance ledger and the synthesis engine can
reproduce the result without re-running the code.
"""
from .bootstrap import (
    bootstrap_ci,
    block_bootstrap_ci,
    bca_bootstrap_ci,
)
from .multiple_comparisons import (
    benjamini_hochberg,
    bonferroni,
    correct_findings,
    summarize_correction,
)
from .effect_size import (
    cohens_d,
    eta_squared,
    odds_ratio,
    r_squared,
    interpret_cohens_d,
    interpret_eta_squared,
    interpret_odds_ratio,
    attach_effect_size,
)
from .credibility_pass import apply_credibility_pass
from .multivariate_outliers import (
    mahalanobis_outliers,
    isolation_forest_outliers,
    lof_outliers,
    detect_multivariate_outliers,
)
from .sensitivity import (
    parameter_sweep,
    sensitivity_z_threshold,
    sensitivity_lof_k,
    attach_sensitivity_profile,
)
from .panel import two_way_fixed_effects
from .clustering import (
    kmeans,
    silhouette_score,
    adjusted_rand_index,
    cluster_stability,
    select_best_k,
)
from .survival import (
    kaplan_meier,
    log_rank_test,
    cox_proportional_hazards,
    schoenfeld_residuals_test,
)
from .spatial import morans_i, gearys_c
from .network import (
    degree_centrality,
    betweenness_centrality,
    eigenvector_centrality,
    triangle_count,
    louvain_communities,
)
from .did import difference_in_differences

# ----- Brief 3 (advanced methods) ------------------------------------------
from .bayesian import (
    beta_binomial_posterior,
    normal_normal_posterior,
    normal_inverse_gamma,
    metropolis_hastings,
    bayes_factor_savage_dickey,
)
from .mutual_info import (
    mi_binning,
    mi_ksg,
    conditional_mi,
    mi_matrix,
)
from .granger import (
    granger_causality_test,
    bivariate_granger,
    transfer_entropy,
)
from .sindy import sindy_discover, polynomial_library, stlsq
from .hmm import (
    forward_backward,
    viterbi,
    baum_welch,
    fit_hmm_select_k,
)
from .spectral import (
    continuous_wavelet,
    detect_nonstationary_period,
    lomb_scargle,
)
from .gaussian_process import fit_gp, predict_gp
from .topology import persistent_homology, topological_summary
from .power_analysis import (
    power_t_test,
    power_correlation,
    detectable_effect,
    expected_information_gain,
)
from .compositional import (
    is_compositional,
    clr_transform,
    ilr_transform,
    compositional_correlation,
)
from .advanced_pass import apply_advanced_pass

__all__ = [
    # bootstrap
    "bootstrap_ci",
    "block_bootstrap_ci",
    "bca_bootstrap_ci",
    # multiple comparisons
    "benjamini_hochberg",
    "bonferroni",
    "correct_findings",
    "summarize_correction",
    # effect size
    "cohens_d",
    "eta_squared",
    "odds_ratio",
    "r_squared",
    "interpret_cohens_d",
    "interpret_eta_squared",
    "interpret_odds_ratio",
    "attach_effect_size",
    # credibility pass
    "apply_credibility_pass",
    # Wave B — multivariate outliers
    "mahalanobis_outliers",
    "isolation_forest_outliers",
    "lof_outliers",
    "detect_multivariate_outliers",
    # Wave B — sensitivity
    "parameter_sweep",
    "sensitivity_z_threshold",
    "sensitivity_lof_k",
    "attach_sensitivity_profile",
    # Wave C — panel data + clustering
    "two_way_fixed_effects",
    "kmeans",
    "silhouette_score",
    "adjusted_rand_index",
    "cluster_stability",
    "select_best_k",
    # Wave D — survival
    "kaplan_meier",
    "log_rank_test",
    "cox_proportional_hazards",
    "schoenfeld_residuals_test",
    # Wave E — spatial + network
    "morans_i",
    "gearys_c",
    "degree_centrality",
    "betweenness_centrality",
    "eigenvector_centrality",
    "triangle_count",
    "louvain_communities",
    # Wave F — DiD
    "difference_in_differences",
    # ----- Brief 3 (advanced) ----------------------------------------------
    # Wave G — Bayesian inference
    "beta_binomial_posterior",
    "normal_normal_posterior",
    "normal_inverse_gamma",
    "metropolis_hastings",
    "bayes_factor_savage_dickey",
    # Wave G — mutual information
    "mi_binning",
    "mi_ksg",
    "conditional_mi",
    "mi_matrix",
    # Wave G — Granger / transfer entropy
    "granger_causality_test",
    "bivariate_granger",
    "transfer_entropy",
    # Wave H — SINDy
    "sindy_discover",
    "polynomial_library",
    "stlsq",
    # Wave H — HMM
    "forward_backward",
    "viterbi",
    "baum_welch",
    "fit_hmm_select_k",
    # Wave H — spectral
    "continuous_wavelet",
    "detect_nonstationary_period",
    "lomb_scargle",
    # Wave I — Gaussian process
    "fit_gp",
    "predict_gp",
    # Wave I — topology
    "persistent_homology",
    "topological_summary",
    # Wave I — experimental design
    "power_t_test",
    "power_correlation",
    "detectable_effect",
    "expected_information_gain",
    # Wave I — compositional
    "is_compositional",
    "clr_transform",
    "ilr_transform",
    "compositional_correlation",
    # Brief 3 orchestrator
    "apply_advanced_pass",
]
