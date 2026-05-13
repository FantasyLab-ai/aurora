# Analytical Methods

This document lists every analytical method Aurora ships with, the citation it traces back to, what it detects, and when Aurora skips it.

The Research Kit (`fantasyai/aurora/research_kit.py`) generates a `references.bib` from this same catalog — every method tag your run uses gets a BibTeX entry automatically.

## How methods are organised

Aurora runs methods in two passes:

1. **Deep math** (`compute_deep_math_v3`) — the core: forecasting, regime detection, bootstrap CI, monte carlo risk surface, physics discovery, physics invariants.
2. **Advanced pass** (`apply_advanced_pass`) — research-grade additions: HMM, wavelet, mutual info, Granger, SINDy, topology, GP, etc.

Every method:
- Has clear input shape requirements (e.g., "requires a numeric column with ≥ 200 observations")
- Skips honestly with a reason when preconditions don't hold
- Is wrapped in a 90-second timeout (configurable)
- Outputs a structured finding with explicit confidence

## Anomaly detection

### Robust z-score — `ROBUST-Z`

Hampel-1974 robust z-score on each numeric column. Per-row scores compare the value to the column's median absolute deviation; thresholds of `|z| ≥ 3` flag a warning, `|z| ≥ 5` flag a critical anomaly.

**Citation:** Hampel, F. R. (1974). The Influence Curve and Its Role in Robust Estimation. *Journal of the American Statistical Association*, 69(346), 383–393. [doi:10.2307/2285666](https://doi.org/10.2307/2285666).

**Skips when:** all columns are categorical, or the column has < 30 numeric observations.

### Isolation Forest — `ISO-FOREST`

Random recursive partitioning isolates anomalous points faster than dense ones; the average path length across an ensemble of isolation trees yields the anomaly score.

**Citation:** Liu, F. T., Ting, K. M., & Zhou, Z.-H. (2008). Isolation Forest. *IEEE 8th International Conference on Data Mining*, 413–422. [doi:10.1109/ICDM.2008.17](https://doi.org/10.1109/ICDM.2008.17).

### Local Outlier Factor — `LOF`

Density-based score: ratio of a point's local reachability density to that of its `k`-nearest neighbours. Catches outliers that are anomalous *relative to their local cluster* rather than the whole dataset.

**Citation:** Breunig, M. M., Kriegel, H.-P., Ng, R. T., & Sander, J. (2000). LOF: Identifying Density-based Local Outliers. *ACM SIGMOD Record*, 29(2), 93–104.

### Robust Mahalanobis (MCD) — `MAHALANOBIS_ROBUST`

Mahalanobis distance using the Minimum Covariance Determinant estimator. Robust to up to ~25% contamination in the dataset.

**Citation:** Rousseeuw, P. J., & Van Driessen, K. (1999). A Fast Algorithm for the Minimum Covariance Determinant Estimator. *Technometrics*, 41(3), 212–223.

### Multivariate Outlier Consensus — `MULTIVARIATE_OUTLIER_CONSENSUS`

A row is flagged when ≥ 2 of {`MAHALANOBIS_ROBUST`, `ISO-FOREST`, `LOF`} agree. Reduces false positives from any single detector.

## Forecasting

### AR(1) baseline — `AR(1)`

First-order autoregression `y_t = φ y_{t-1} + b + ε_t` fitted by ordinary least squares. Confidence intervals propagate analytically: `σ_{t+1}² = (1 + φ Δt)² σ_t²`.

**Citation:** Hyndman, R. J., & Athanasopoulos, G. (2021). *Forecasting: principles and practice* (3rd ed.). OTexts. <https://otexts.com/fpp3/>.

**Skips when:** no time axis detected, or the target column has < 200 numeric observations.

### AR(2) — `AR(2)`

Second-order autoregression — uses the last two values to forecast the next. Used in the transparent ML ensemble alongside AR(1).

### Gaussian Process Regression — `GAUSSIAN_PROCESS`

Non-parametric regression with the radial basis (RBF) kernel. Hyperparameters fit by maximising the log-marginal likelihood. Useful when AR assumptions don't hold and you have ≤ ~10 K points.

**Citation:** Rasmussen, C. E., & Williams, C. K. I. (2006). *Gaussian Processes for Machine Learning*. MIT Press.

**Skips when:** dataset is too large for cubic-time GP fitting (> 5 K rows triggers sampling; > 50 K skips with reason).

## Regime / change-point detection

### HMM with Baum-Welch — `HMM_BAUM_WELCH`

Hidden Markov Model fit via Baum-Welch EM (expectation-maximisation). Latent state count `k` selected by BIC; emissions are Gaussian per state.

**Citation:** Baum, L. E., Petrie, T., Soules, G., & Weiss, N. (1970). A Maximization Technique Occurring in the Statistical Analysis of Probabilistic Functions of Markov Chains. *Annals of Mathematical Statistics*, 41(1), 164–171.

### PELT change-point detection — `PELT`

Pruned Exact Linear Time algorithm for detecting locations where the time series' statistical properties change. Default cost function: RBF kernel (`PELT-RBF`).

**Citation:** Killick, R., Fearnhead, P., & Eckley, I. A. (2012). Optimal Detection of Changepoints With a Linear Computational Cost. *Journal of the American Statistical Association*, 107(500), 1590–1598.

**Skips when:** PELT-RBF times out (90 s default) on highly categorical large datasets. Honestly disclosed.

## Motif / pattern

### Matrix Profile — `MATRIX_PROFILE`

For each subsequence of length `w`, the distance to its nearest non-trivial neighbour. Minima locate *motifs* (recurring shapes); maxima locate *discords* (one-off events).

**Citation:** Yeh, C.-C. M., Zhu, Y., Ulanova, L., et al. (2016). Matrix Profile I: All Pairs Similarity Joins for Time Series. *IEEE 16th International Conference on Data Mining*, 1317–1322.

### Morlet wavelet CWT — `MORLET_WAVELET`

Continuous wavelet transform with the Morlet mother wavelet. Ridges in the scalogram identify persistent periodic structure across multiple scales.

**Citation:** Torrence, C., & Compo, G. P. (1998). A Practical Guide to Wavelet Analysis. *Bulletin of the American Meteorological Society*, 79(1), 61–78.

### Lomb-Scargle — `LOMB_SCARGLE`

Periodogram robust to unevenly-sampled time series. Detects periodic structure when regular sampling assumptions don't hold.

**Citation:** VanderPlas, J. T. (2018). Understanding the Lomb-Scargle Periodogram. *The Astrophysical Journal Supplement Series*, 236(1), 16.

## Causality / dependency

### Granger Causality — `GRANGER`

Tests whether `X` helps predict `Y` beyond `Y`'s own past via F-test on residuals. Rejection at `p < 0.05` indicates Granger causality.

**Citation:** Granger, C. W. J. (1969). Investigating Causal Relations by Econometric Models and Cross-spectral Methods. *Econometrica*, 37(3), 424–438.

**Skips when:** no time axis, or fewer than two numeric columns.

### Mutual Information (KSG) — `MUTUAL_INFO_KSG`

Kraskov-Stögbauer-Grassberger non-parametric mutual information estimator using `k`-nearest-neighbour distances on the joint and marginal samples. Catches nonlinear dependencies that correlation misses.

**Citation:** Kraskov, A., Stögbauer, H., & Grassberger, P. (2004). Estimating Mutual Information. *Physical Review E*, 69(6), 066138.

## Topology

### Persistent Homology (Vietoris-Rips) — `VIETORIS_RIPS_H0_H1`

Filtration on `ε`-balls around data points produces a persistence diagram; H₀ (connected components) and H₁ (1-cycles) are extracted from the diagram's most-persistent bars.

**Citation:** Edelsbrunner, H., & Harer, J. L. (2010). *Computational Topology: An Introduction*. American Mathematical Society.

## Physics / dynamics

### Physics Prior Matcher — `PHYSICS_PRIOR_MATCHER`

Matches the discovered `dy/dt` form against known priors:

- **Newton's law of cooling** — `dy/dt = -k·(y - y∞)`; exponential decay toward ambient. *Citation: Newton, I. (1701). Scala graduum caloris. Philosophical Transactions, 22.*
- **Logistic ODE** — `dy/dt = r·y·(1 - y/K)`; bounded growth. *Citation: Verhulst, P.-F. (1838). Notice sur la loi que la population suit dans son accroissement. Correspondance Mathématique et Physique, 10, 113–121.*
- **Damped oscillator** — `d²y/dt² + c·dy/dt + k·y = 0`; second-order linear ODE.
- **Exponential growth/decay** — `y = A·exp(-k·t)`; half-life `ln(2)/k`. *Citation: Malthus, T. R. (1798). An Essay on the Principle of Population.*

### SINDy (Sparse Identification of Nonlinear Dynamics) — `SINDY`

Sparse regression on a library of candidate basis functions to identify the governing equations of a dynamical system from data.

**Citation:** Brunton, S. L., Proctor, J. L., & Kutz, J. N. (2016). Discovering Governing Equations from Data by Sparse Identification of Nonlinear Dynamical Systems. *PNAS*, 113(15), 3932–3937.

## Clustering / stability

### KMeans Bootstrap Stability — `KMEANS_BOOTSTRAP_STABILITY`

`k`-means run on bootstrap samples; silhouette + cluster stability across resamples gives a robust `k` estimate plus a confidence score.

## Honest disclosure of skipping

Methods skip with a clear reason that surfaces in:

- The "Σ ADVANCED METHODS" section of the Studio (each card shows the skip reason)
- The Aurora Pulse summary
- The synthesis layer's "Pipeline notes" disclosure
- The Aurora Bundle's `method_registry` (skipped methods appear with `count=0`)

Common skip reasons you'll see:

- `no_time_axis` — method requires temporal structure
- `no_target_col` — method requires a single target column; this dataset doesn't define one
- `cross_sectional_no_time_axis` — explicit signal that this dataset is i.i.d. rows
- `too_few_observations` — `n` below the method's minimum (varies, typically 30–200)
- `timeout` — exceeded the 90 s per-method budget; honestly deferred
- `awaiting_causal_effect` — placeholder before causal results land

If a method you expected is consistently skipping on your data, [file an issue](https://github.com/fantasylab/aurora/issues) — that's exactly the kind of feedback that shapes which methods Aurora prioritises.
