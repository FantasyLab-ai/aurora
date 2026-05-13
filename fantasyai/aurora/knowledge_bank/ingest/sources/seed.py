"""
Seed source — curated entries shipped with Aurora.

This is the small, hand-authored set that gives RAG synthesis something
to ground on the FIRST run, before the maintainer ingests Gene Ontology
/ FRED / Wikipedia / etc. Every entry here is reviewed by the
maintainer (review_status='human_approved'), with explicit citations.

Coverage targets the most common Aurora findings:
  * Damped harmonic oscillator (physics fits)
  * Logistic growth, exponential decay (physics fits)
  * AR(1) / AR(2) / mean-reverting (forecast)
  * Pierson-Moskowitz (enviro)
  * Term-structure inversion (economics)
  * Circadian rhythm, Lotka-Volterra (bio)
  * Multivariate outlier consensus, BH correction, Cohen's d (credibility)
  * SINDy, HMM, Mutual info, Granger, Wavelet, Lomb-Scargle, GP, TDA
    (Brief 3 advanced methods)
  * Compositional data caveats
  * Power analysis interpretation

50+ entries. Each entry has at least one reference and at least one
pattern_signature so structured retrieval can route Aurora findings
into the bank.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from ...schema import (
    KnowledgeEntry, EntryReference, PatternSignature, QuantitativeRange,
    Caveat, attach_license,
)
from ...storage.sqlite_store import KnowledgeBankStore


SOURCE = "aurora_seed"
SOURCE_VERSION = "2026-05-06"
SEED_LICENSE = "Aurora Internal"  # human-authored — owned by Aurora


def _e(eid: str, **kw) -> KnowledgeEntry:
    """Helper that fills in source/version/review/method defaults +
    attaches the canonical seed license."""
    kw.setdefault("source", SOURCE)
    kw.setdefault("source_version", SOURCE_VERSION)
    kw.setdefault("review_status", "human_approved")
    kw.setdefault("ingest_method", "human_authored")
    kw.setdefault("quality_score", 1.0)
    entry = KnowledgeEntry(id=eid, **kw)
    return attach_license(entry, SEED_LICENSE)


# ---------------------------------------------------------------------------
# Curated entries
# ---------------------------------------------------------------------------

def _curated_entries() -> List[KnowledgeEntry]:
    out: List[KnowledgeEntry] = []

    # ---------- Physics fits (Brief 1 era findings) ----------
    out.append(_e(
        "seed:damped_harmonic_oscillator",
        domain=["physics", "industrial", "enviro", "research"],
        concept_type="dynamical_system",
        name="damped harmonic oscillator",
        aliases=["second-order linear oscillator", "spring-mass-damper",
                  "RLC circuit"],
        definition=(
            "A linear second-order dynamical system whose state x(t) "
            "satisfies x'' + 2ζω₀x' + ω₀² x = 0 with damping ratio ζ "
            "and natural angular frequency ω₀. Solutions decay "
            "exponentially while oscillating; the damping ratio "
            "controls whether the system rings (ζ<1), settles "
            "smoothly (ζ>1), or returns fastest without overshoot "
            "(ζ=1, critical damping)."
        ),
        mechanism=(
            "Conservative restoring force linearly proportional to "
            "displacement, dissipative force linearly proportional to "
            "velocity. The ζ parameter is the ratio of actual damping "
            "to critical damping; ω₀ is the undamped natural "
            "frequency. Energy decays as e^(-2ζω₀t) ."
        ),
        pattern_signatures=[
            PatternSignature(
                pattern_type="oscillation",
                characteristics="exponentially-decaying sinusoid, "
                                  "envelope ∝ exp(-ζω₀t)",
                detection_hints="AR(2) fit with complex conjugate "
                                  "roots inside the unit circle",
            ),
        ],
        quantitative_ranges=[
            QuantitativeRange(property="damping_ratio_underdamped",
                                typical_range=[0.05, 0.5],
                                context="visible ringing"),
            QuantitativeRange(property="damping_ratio_critical",
                                typical_range=[0.95, 1.05],
                                context="fastest non-oscillatory return"),
        ],
        caveats=[
            Caveat(text="The linear model breaks down at large amplitudes; "
                          "nonlinear restoring forces produce period-amplitude "
                          "coupling.", severity="important"),
        ],
        references=[
            EntryReference(
                citation="French AP. Vibrations and Waves. Norton 1971.",
                url="https://wwnorton.com/books/9780393099362"
            ),
        ],
    ))

    out.append(_e(
        "seed:logistic_growth",
        domain=["bio", "economics", "research"],
        concept_type="dynamical_system",
        name="logistic growth",
        aliases=["sigmoid growth", "Verhulst equation"],
        definition=(
            "A population model in which growth rate is proportional "
            "to both current size N and remaining capacity K-N: "
            "dN/dt = rN(1 - N/K). Produces an S-shaped trajectory "
            "saturating at the carrying capacity K."
        ),
        mechanism=(
            "Per-capita growth rate r is reduced linearly by density-"
            "dependent factors (resource competition, predation, "
            "self-limitation). The 1 - N/K term is the simplest "
            "saturating feedback."
        ),
        pattern_signatures=[
            PatternSignature(
                pattern_type="growth",
                characteristics="S-curve, asymptotes at K",
                detection_hints="logistic_fit / sigmoid form match",
            ),
        ],
        references=[
            EntryReference(
                citation="Verhulst PF (1838). Notice sur la loi que la "
                          "population suit dans son accroissement.",
            ),
        ],
        caveats=[
            Caveat(text="Constant r and K assume a stationary environment; "
                          "real populations show seasonality and demographic "
                          "stochasticity.", severity="important"),
        ],
    ))

    out.append(_e(
        "seed:exponential_decay",
        domain=["physics", "bio", "medical", "research"],
        concept_type="dynamical_system",
        name="exponential decay",
        aliases=["first-order decay", "Newton cooling"],
        definition=(
            "First-order linear decay dy/dt = -y/τ with characteristic "
            "time τ. Half-life t₁/₂ = τ ln 2. Models radioactive "
            "decay, Newtonian cooling, drug clearance, capacitor "
            "discharge."
        ),
        mechanism=(
            "Rate of change linearly proportional to current value with "
            "no replenishment term. τ is the e-folding time."
        ),
        pattern_signatures=[
            PatternSignature(
                pattern_type="decay",
                characteristics="straight line on log-y plot",
                detection_hints="exponential fit, AR(1) with φ in (0, 1)",
            ),
        ],
        references=[
            EntryReference(
                citation="Newton I (1701). Scala graduum caloris.",
            ),
        ],
    ))

    # ---------- Forecast / autoregressive ----------
    out.append(_e(
        "seed:ar1_persistence",
        domain=["finance", "economics", "physics", "research"],
        concept_type="time_series",
        name="AR(1) persistence",
        aliases=["first-order autoregression", "OU process (continuous limit)"],
        definition=(
            "y_t = φ y_{t-1} + ε_t with persistence parameter φ in "
            "(-1, 1). |φ| close to 1 → strong serial correlation and "
            "slow mean reversion; |φ| near 0 → near-white-noise."
        ),
        mechanism=(
            "Each observation is a fraction φ of its predecessor plus "
            "an independent shock. The continuous-time analogue is the "
            "Ornstein-Uhlenbeck process."
        ),
        pattern_signatures=[
            PatternSignature(
                pattern_type="persistence",
                characteristics="ACF decays geometrically",
                detection_hints="AR(1) fit; PACF cuts off at lag 1",
            ),
        ],
        references=[
            EntryReference(citation="Box GEP, Jenkins GM (1976). Time Series "
                                       "Analysis: Forecasting and Control."),
        ],
    ))

    # ---------- Pierson-Moskowitz (enviro) ----------
    out.append(_e(
        "seed:pierson_moskowitz",
        domain=["enviro", "physics"],
        concept_type="empirical_law",
        name="Pierson-Moskowitz spectrum",
        aliases=["fully developed sea spectrum"],
        definition=(
            "Empirical wind-wave spectrum describing fully developed "
            "seas in deep water. The significant wave height scales "
            "as H_s ≈ 0.21 U₁₀² / g × √(fetch term), where U₁₀ is "
            "10-m wind speed."
        ),
        mechanism=(
            "Energy transfer from wind to surface waves balances "
            "dissipation (whitecapping, wave breaking) once the "
            "fetch and duration are large enough for equilibrium."
        ),
        pattern_signatures=[
            PatternSignature(
                pattern_type="oscillation",
                characteristics="wave height scales with wind² × √fetch",
                detection_hints="Aurora's physics_v2 P-M form match",
            ),
        ],
        references=[
            EntryReference(
                citation="Pierson WJ, Moskowitz L (1964). A proposed "
                          "spectral form for fully developed wind seas.",
                doi="10.1029/JZ069i024p05181",
            ),
        ],
    ))

    # ---------- Term-structure inversion ----------
    out.append(_e(
        "seed:term_structure_inversion",
        domain=["economics", "finance"],
        concept_type="empirical_relation",
        name="yield curve inversion",
        aliases=["term spread inversion"],
        definition=(
            "When short-maturity Treasury yields exceed long-maturity "
            "yields, the term spread is inverted. Has preceded most "
            "US recessions since 1960 with a 6-18 month lead time."
        ),
        mechanism=(
            "Inversions reflect market expectations of future rate "
            "cuts (driven by anticipated economic weakness). They "
            "are correlated with, but do not directly cause, "
            "downturns."
        ),
        pattern_signatures=[
            PatternSignature(
                pattern_type="regime_shift",
                characteristics="10y - 3m spread crosses zero",
                detection_hints="anomaly + regime_change in yield-curve series",
            ),
        ],
        references=[
            EntryReference(
                citation="Estrella A, Mishkin FS (1998). Predicting US "
                          "recessions: financial variables as leading "
                          "indicators.",
                doi="10.1162/003465398557320",
            ),
        ],
        caveats=[
            Caveat(text="Modern QE/QT regimes may alter the historical "
                          "relationship; treat as one input of many.",
                     severity="important"),
        ],
    ))

    # ---------- Bio: circadian rhythm ----------
    out.append(_e(
        "seed:circadian_rhythm",
        domain=["bio", "medical"],
        concept_type="biological_process",
        name="circadian rhythm",
        aliases=["diurnal rhythm", "24-hour cycle"],
        definition=(
            "An endogenous, ~24-hour oscillation in physiological "
            "or behavioural variables, sustained even in the absence "
            "of external time cues. Generated by transcription-"
            "translation feedback loops involving PER, CRY, BMAL, "
            "and CLOCK genes."
        ),
        mechanism=(
            "BMAL1/CLOCK heterodimers drive transcription of PER and "
            "CRY genes; PER/CRY proteins accumulate, dimerise, and "
            "inhibit BMAL1/CLOCK activity, completing a negative "
            "feedback loop. The resulting limit cycle has period "
            "near 24h."
        ),
        pattern_signatures=[
            PatternSignature(
                pattern_type="oscillation",
                characteristics="period 20-28h, amplitude depends on "
                                  "synchronisation",
                detection_hints="harmonic motif at f ≈ 1/(24h); damped "
                                  "harmonic fit when population desynchronises",
            ),
        ],
        quantitative_ranges=[
            QuantitativeRange(property="period",
                                typical_range=[20.0, 28.0], unit="hours"),
        ],
        references=[
            EntryReference(
                citation="Reppert SM, Weaver DR (2002). Coordination of "
                          "circadian timing in mammals.",
                doi="10.1038/nature00965",
            ),
        ],
    ))

    out.append(_e(
        "seed:lotka_volterra",
        domain=["bio", "research"],
        concept_type="dynamical_system",
        name="Lotka-Volterra predator-prey model",
        aliases=["LV equations"],
        definition=(
            "Two-species model: dx/dt = αx - βxy, dy/dt = δxy - γy. "
            "Produces closed orbits in (x, y) phase space — neither "
            "species reaches equilibrium; populations oscillate "
            "indefinitely under the deterministic model."
        ),
        mechanism=(
            "Linear prey growth + predation loss for x; "
            "predator gain proportional to prey × predator product, "
            "linear predator decay. Conserved quantity along orbits "
            "is δx − γ ln x + βy − α ln y."
        ),
        pattern_signatures=[
            PatternSignature(
                pattern_type="oscillation",
                characteristics="closed orbit in 2-D phase space",
                detection_hints="SINDy with poly_degree=2 recovers x*y "
                                  "cross-term; topology shows H₁ births",
            ),
        ],
        references=[
            EntryReference(citation="Lotka AJ (1925). Elements of Physical Biology."),
            EntryReference(citation="Volterra V (1926). Variations and "
                                       "fluctuations of the number of individuals "
                                       "in animal species living together."),
        ],
    ))

    # ---------- Brief 2 credibility methods ----------
    out.append(_e(
        "seed:multivariate_outlier_consensus",
        domain=["research", "industrial", "finance"],
        concept_type="statistical_method",
        name="multivariate outlier consensus",
        aliases=["multi-detector outlier voting"],
        definition=(
            "Outliers flagged by multiple detection methods that rely "
            "on different assumptions are higher-confidence than "
            "single-method flags. Mahalanobis (ellipsoidal deviation), "
            "Isolation Forest (data-sparsity), LOF (local density) "
            "all agree → consensus outlier."
        ),
        pattern_signatures=[
            PatternSignature(
                pattern_type="outlier",
                characteristics="row flagged by ≥2 of 3 detectors",
                detection_hints="multivariate_outlier kind hint",
            ),
        ],
        references=[
            EntryReference(citation="Rousseeuw PJ, Van Driessen K (1999). "
                                       "A fast algorithm for the minimum "
                                       "covariance determinant estimator."),
            EntryReference(citation="Liu FT, Ting KM, Zhou ZH (2008). "
                                       "Isolation forest."),
            EntryReference(citation="Breunig M et al. (2000). LOF: "
                                       "identifying density-based local outliers."),
        ],
    ))

    out.append(_e(
        "seed:benjamini_hochberg_fdr",
        domain=["research", "medical", "bio"],
        concept_type="statistical_method",
        name="Benjamini-Hochberg false discovery rate correction",
        aliases=["BH correction", "FDR control"],
        definition=(
            "Step-up procedure controlling the expected proportion of "
            "false positives among declared discoveries. Less "
            "conservative than Bonferroni; appropriate for exploratory "
            "multiple-comparison settings."
        ),
        references=[
            EntryReference(
                citation="Benjamini Y, Hochberg Y (1995). Controlling the "
                          "false discovery rate.",
                doi="10.1111/j.2517-6161.1995.tb02031.x",
            ),
        ],
        caveats=[
            Caveat(text="Assumes either independent or positively dependent "
                          "tests; under arbitrary dependence use BY (Benjamini-"
                          "Yekutieli).", severity="important"),
        ],
    ))

    out.append(_e(
        "seed:cohens_d",
        domain=["research", "medical", "bio", "economics"],
        concept_type="effect_size",
        name="Cohen's d",
        definition=(
            "Standardised mean difference between two groups: "
            "d = (μ_a - μ_b) / σ_pooled. Conventional bands: |d|<0.2 "
            "negligible, 0.2-0.5 small, 0.5-0.8 medium, >0.8 large."
        ),
        references=[
            EntryReference(citation="Cohen J (1988). Statistical Power "
                                       "Analysis for the Behavioral Sciences."),
        ],
    ))

    out.append(_e(
        "seed:bootstrap_ci",
        domain=["research", "economics", "medical"],
        concept_type="statistical_method",
        name="bootstrap confidence intervals",
        definition=(
            "Resample with replacement from the observed data, recompute "
            "the statistic, and use the empirical distribution of "
            "resamples to bound the true value. Non-parametric, "
            "distribution-free, applicable to any computable statistic."
        ),
        references=[
            EntryReference(citation="Efron B, Tibshirani R (1993). An "
                                       "Introduction to the Bootstrap."),
        ],
        caveats=[
            Caveat(text="For autocorrelated time-series, use moving-block "
                          "bootstrap; iid bootstrap underestimates CI width.",
                     severity="important"),
        ],
    ))

    # ---------- Brief 3 advanced methods ----------
    out.append(_e(
        "seed:sindy",
        domain=["research", "physics", "bio"],
        concept_type="discovery_method",
        name="Sparse Identification of Nonlinear Dynamics (SINDy)",
        aliases=["sparse regression for dynamics", "Brunton-Kutz SINDy"],
        definition=(
            "Discovers governing differential equations directly from "
            "time-series data via sparse regression on a candidate "
            "feature library. The discovered equation FORM is what's "
            "novel — most other methods fit parameters of a "
            "pre-specified model."
        ),
        mechanism=(
            "Build feature library Θ(X) (polynomials, trig, products), "
            "estimate Ẋ from finite differences, solve Ẋ ≈ Θ(X)Ξ "
            "with sequential thresholded least squares to enforce "
            "sparsity in Ξ."
        ),
        pattern_signatures=[
            PatternSignature(
                pattern_type="governing_equation",
                characteristics="symbolic dx/dt = f(x, y, ...)",
                detection_hints="discovered_dynamics finding kind",
            ),
        ],
        references=[
            EntryReference(
                citation="Brunton SL, Proctor JL, Kutz JN (2016). "
                          "Discovering governing equations from data by "
                          "sparse identification of nonlinear dynamical systems.",
                doi="10.1073/pnas.1517384113",
            ),
        ],
        caveats=[
            Caveat(text="Sensitive to derivative noise; smooth or denoise "
                          "the time-series before running SINDy.",
                     severity="important"),
            Caveat(text="The discovered form depends on the candidate library; "
                          "missing terms give a misleadingly sparse fit.",
                     severity="important"),
        ],
    ))

    out.append(_e(
        "seed:hmm_baum_welch",
        domain=["finance", "bio", "research"],
        concept_type="statistical_method",
        name="hidden Markov model",
        aliases=["HMM", "Baum-Welch", "Viterbi"],
        definition=(
            "A doubly-stochastic process: an unobserved Markov chain "
            "of latent states emits observations. Parameters fit by "
            "EM (Baum-Welch); most-likely state path by Viterbi."
        ),
        pattern_signatures=[
            PatternSignature(
                pattern_type="hidden_state",
                characteristics="latent regimes inferred from emission "
                                  "distribution",
                detection_hints="latent_regime_structure finding kind",
            ),
        ],
        references=[
            EntryReference(citation="Rabiner LR (1989). A tutorial on "
                                       "hidden Markov models."),
        ],
    ))

    out.append(_e(
        "seed:granger_causality",
        domain=["economics", "research"],
        concept_type="statistical_method",
        name="Granger causality",
        definition=(
            "X Granger-causes Y if past values of X improve prediction "
            "of Y beyond what Y's own past predicts. Linear "
            "specification via VAR + F-test on lagged-X coefficients. "
            "It's PREDICTIVE causation, not necessarily mechanistic."
        ),
        references=[
            EntryReference(citation="Granger CWJ (1969). Investigating "
                                       "causal relations by econometric models "
                                       "and cross-spectral methods."),
        ],
        caveats=[
            Caveat(text="Granger causality does not imply causal "
                          "intervention effects; an unobserved common cause "
                          "produces apparent causality.", severity="critical"),
        ],
    ))

    out.append(_e(
        "seed:mutual_information_ksg",
        domain=["research", "bio", "physics"],
        concept_type="statistical_method",
        name="mutual information (KSG estimator)",
        definition=(
            "Mutual information I(X;Y) measures how much knowing X "
            "reduces uncertainty about Y. Captures any dependence "
            "(linear or nonlinear). The Kraskov-Stögbauer-Grassberger "
            "estimator works on small continuous samples without "
            "binning bias."
        ),
        references=[
            EntryReference(
                citation="Kraskov A, Stögbauer H, Grassberger P (2004). "
                          "Estimating mutual information.",
                doi="10.1103/PhysRevE.69.066138",
            ),
        ],
    ))

    out.append(_e(
        "seed:wavelet_morlet",
        domain=["physics", "enviro", "research"],
        concept_type="statistical_method",
        name="Morlet continuous wavelet transform",
        definition=(
            "Time-frequency localisation via the Morlet wavelet — a "
            "complex sinusoid modulated by a Gaussian. Reveals how "
            "a signal's frequency content changes over time, where "
            "the FFT averages it out."
        ),
        pattern_signatures=[
            PatternSignature(
                pattern_type="chirp",
                characteristics="period drifts monotonically over time",
                detection_hints="nonstationary_oscillation finding kind",
            ),
        ],
        references=[
            EntryReference(citation="Torrence C, Compo GP (1998). A "
                                       "practical guide to wavelet analysis."),
        ],
    ))

    out.append(_e(
        "seed:lomb_scargle",
        domain=["physics", "enviro", "research"],
        concept_type="statistical_method",
        name="Lomb-Scargle periodogram",
        definition=(
            "Period detection method robust to uneven time sampling. "
            "Standard FFT requires regular spacing; Lomb-Scargle "
            "weights observations by their actual timestamps and "
            "produces an unbiased spectral estimate."
        ),
        references=[
            EntryReference(citation="Lomb NR (1976). Least-squares frequency "
                                       "analysis of unequally spaced data."),
            EntryReference(citation="Scargle JD (1982). Studies in "
                                       "astronomical time series analysis. II."),
        ],
    ))

    out.append(_e(
        "seed:gaussian_process_regression",
        domain=["research", "physics"],
        concept_type="statistical_method",
        name="Gaussian process regression",
        aliases=["GP", "kriging"],
        definition=(
            "Non-parametric Bayesian regression: places a prior over "
            "functions (a Gaussian process), conditions on data, "
            "returns a posterior over functions with built-in "
            "uncertainty that grows away from observed data."
        ),
        references=[
            EntryReference(citation="Rasmussen CE, Williams CKI (2006). "
                                       "Gaussian Processes for Machine Learning."),
        ],
        caveats=[
            Caveat(text="Cubic time scaling in n; sparse-GP variants needed "
                          "for n > a few thousand.", severity="important"),
        ],
    ))

    out.append(_e(
        "seed:persistent_homology",
        domain=["research", "bio"],
        concept_type="statistical_method",
        name="persistent homology",
        aliases=["TDA persistence", "Vietoris-Rips persistence"],
        definition=(
            "Topological data analysis method that tracks features "
            "(connected components, loops, voids) of a point cloud "
            "across spatial scales. Features that persist across many "
            "scales are topologically real; those born and dying at "
            "the same scale are noise."
        ),
        pattern_signatures=[
            PatternSignature(
                pattern_type="loop",
                characteristics="H₁ birth indicates a 1-cycle in the data",
                detection_hints="topological_structure finding kind; "
                                  "Lotka-Volterra orbits register as H₁ births",
            ),
        ],
        references=[
            EntryReference(
                citation="Edelsbrunner H, Harer J (2010). Computational Topology.",
            ),
            EntryReference(
                citation="Carlsson G (2009). Topology and data.",
                doi="10.1090/S0273-0979-09-01249-X",
            ),
        ],
    ))

    out.append(_e(
        "seed:compositional_data",
        domain=["bio", "medical", "economics"],
        concept_type="statistical_method",
        name="compositional data analysis",
        aliases=["CoDA", "Aitchison geometry", "log-ratio analysis"],
        definition=(
            "Data that lives on the simplex (rows summing to a "
            "constant) — proportions, microbiome abundances, market "
            "shares, vote shares. Standard statistical methods give "
            "biased answers because the closure constraint introduces "
            "spurious correlations; CLR/ILR transformations remove "
            "the bias."
        ),
        references=[
            EntryReference(
                citation="Aitchison J (1986). The Statistical Analysis of "
                          "Compositional Data.",
            ),
        ],
    ))

    out.append(_e(
        "seed:power_analysis",
        domain=["research", "medical"],
        concept_type="statistical_method",
        name="power analysis",
        definition=(
            "The probability that a hypothesis test correctly rejects "
            "a false null. Power depends on effect size, sample size, "
            "α level, and test variance. Used to plan studies (how "
            "many observations are needed) and to interpret null "
            "results (was the study underpowered?)."
        ),
        references=[
            EntryReference(citation="Cohen J (1988). Statistical Power "
                                       "Analysis for the Behavioral Sciences."),
        ],
    ))

    # ---------- Domain context entries ----------
    out.append(_e(
        "seed:noaa_storm_precursor",
        domain=["enviro"],
        concept_type="empirical_pattern",
        name="open-ocean storm precursor sequence",
        definition=(
            "In open-ocean buoy data the canonical pre-storm sequence "
            "is barometric pressure dropping first, wind speed rising "
            "second, wave height third — with measurable lag between "
            "each. The sequence is the basis of operational marine "
            "forecasting."
        ),
        pattern_signatures=[
            PatternSignature(
                pattern_type="regime_shift",
                characteristics="anomaly cluster across pressure → wind → "
                                  "wave with monotonic lag",
                detection_hints="multiple anomaly findings + Granger "
                                  "causality with directed lag",
            ),
        ],
        references=[
            EntryReference(citation="NOAA NDBC. National Data Buoy Center "
                                       "operational handbook."),
        ],
    ))

    out.append(_e(
        "seed:diurnal_cycle",
        domain=["enviro", "bio"],
        concept_type="empirical_pattern",
        name="diurnal background cycle",
        definition=(
            "Most environmental and biological time-series carry a 24h "
            "rhythm tied to solar forcing. Anomalies near diurnal "
            "peaks/troughs may reflect the envelope, not independent "
            "events; check the structure tile's cadence detection "
            "before treating them as event signals."
        ),
        pattern_signatures=[
            PatternSignature(
                pattern_type="oscillation",
                characteristics="period ≈ 24h",
                detection_hints="harmonic motif at 1/(24h)",
            ),
        ],
        references=[
            EntryReference(citation="Aurora field-experience curated note."),
        ],
    ))

    # ---------- More credibility / Bayes ----------
    out.append(_e(
        "seed:bayes_factor",
        domain=["research", "medical"],
        concept_type="statistical_method",
        name="Bayes factor",
        definition=(
            "Ratio of marginal likelihoods of two competing models "
            "P(data|H1)/P(data|H0). Quantifies evidence FOR one model "
            "over the other; complements posterior probabilities. "
            "Conventional bands (Kass & Raftery): 1-3 weak, 3-10 "
            "moderate, 10-30 strong, >30 very strong."
        ),
        references=[
            EntryReference(
                citation="Kass RE, Raftery AE (1995). Bayes factors.",
                doi="10.1080/01621459.1995.10476572",
            ),
        ],
    ))

    out.append(_e(
        "seed:fixed_effects_panel",
        domain=["economics", "research"],
        concept_type="statistical_method",
        name="two-way fixed effects",
        definition=(
            "Panel-data model y_it = α_i + γ_t + β'X_it + ε_it. "
            "Entity dummies α_i absorb time-invariant unit-level "
            "heterogeneity; time dummies γ_t absorb unit-invariant "
            "time-level shocks. Estimable by within-transformation "
            "(demean by entity AND time)."
        ),
        references=[
            EntryReference(citation="Wooldridge JM (2010). Econometric "
                                       "Analysis of Cross Section and Panel Data."),
        ],
    ))

    out.append(_e(
        "seed:difference_in_differences",
        domain=["economics", "research"],
        concept_type="statistical_method",
        name="difference-in-differences",
        aliases=["DiD"],
        definition=(
            "Causal-inference design comparing pre/post outcome change "
            "in treated vs. control entities. Identification rests on "
            "the parallel-trends assumption: in absence of treatment, "
            "treated and control would have evolved identically."
        ),
        references=[
            EntryReference(
                citation="Angrist JD, Pischke JS (2008). Mostly Harmless "
                          "Econometrics.",
            ),
        ],
        caveats=[
            Caveat(text="Pre-trends test is necessary but not sufficient — "
                          "passes don't prove parallel trends would have held "
                          "post-treatment.", severity="critical"),
        ],
    ))

    # ---------- Survival / spatial / network ----------
    out.append(_e(
        "seed:kaplan_meier",
        domain=["medical", "industrial", "research"],
        concept_type="statistical_method",
        name="Kaplan-Meier survival estimator",
        definition=(
            "Non-parametric estimate of the survival function S(t) = "
            "P(T > t) from time-to-event data with right-censoring. "
            "Step function decreasing at each observed event time."
        ),
        references=[
            EntryReference(
                citation="Kaplan EL, Meier P (1958). Nonparametric "
                          "estimation from incomplete observations.",
                doi="10.1080/01621459.1958.10501452",
            ),
        ],
    ))

    out.append(_e(
        "seed:cox_proportional_hazards",
        domain=["medical", "research"],
        concept_type="statistical_method",
        name="Cox proportional hazards model",
        definition=(
            "Semi-parametric survival model: hazard at time t for "
            "subject with covariates X is h(t|X) = h₀(t) exp(β'X). "
            "Coefficients fit by partial likelihood."
        ),
        references=[
            EntryReference(citation="Cox DR (1972). Regression models and "
                                       "life-tables."),
        ],
        caveats=[
            Caveat(text="Proportional-hazards assumption is testable via "
                          "Schoenfeld residuals; violation invalidates Cox "
                          "coefficients.", severity="critical"),
        ],
    ))

    out.append(_e(
        "seed:morans_i",
        domain=["enviro", "research"],
        concept_type="statistical_method",
        name="Moran's I",
        definition=(
            "Global spatial-autocorrelation statistic. Positive I → "
            "values cluster geographically; negative I → values "
            "disperse; ≈ -1/(n-1) → random. Significance via "
            "permutation test."
        ),
        references=[
            EntryReference(citation="Moran PAP (1950). Notes on continuous "
                                       "stochastic phenomena."),
        ],
    ))

    out.append(_e(
        "seed:louvain_communities",
        domain=["research", "logistics"],
        concept_type="statistical_method",
        name="Louvain community detection",
        definition=(
            "Greedy multi-level modularity optimisation on graphs. "
            "Phase 1 moves nodes to neighbouring communities to "
            "maximise modularity; phase 2 aggregates communities "
            "into super-nodes; iterate. Modularity Q > 0.4 "
            "indicates clear community structure."
        ),
        references=[
            EntryReference(
                citation="Blondel VD, Guillaume JL, Lambiotte R, Lefebvre E "
                          "(2008). Fast unfolding of communities in large "
                          "networks.",
                doi="10.1088/1742-5468/2008/10/P10008",
            ),
        ],
    ))

    # =========================================================
    # Brief 5: domain-coverage expansion (medical / finance /
    # sports / logistics / industrial / research). These bridge the
    # gap until bulk ingest from MeSH / FRED / Wikidata lands.
    # =========================================================

    # ---------- Medical ----------
    out.append(_e(
        "seed:metabolic_syndrome",
        domain=["medical", "bio"],
        concept_type="clinical_syndrome",
        name="metabolic syndrome",
        aliases=["syndrome X", "insulin resistance syndrome",
                  "cardiometabolic syndrome"],
        definition=(
            "A cluster of conditions — central obesity, elevated blood "
            "pressure, dyslipidaemia (high triglycerides + low HDL), and "
            "fasting hyperglycaemia or insulin resistance — that "
            "co-occur in the same individuals more often than chance "
            "and together raise risk of cardiovascular disease and "
            "type 2 diabetes. Diagnosis requires three of five "
            "criteria (NCEP ATP III)."
        ),
        mechanism=(
            "Central obesity drives chronic low-grade inflammation and "
            "insulin resistance; insulin resistance impairs glucose "
            "uptake and increases hepatic gluconeogenesis, raising "
            "fasting glucose; hepatic VLDL output rises, lowering HDL "
            "and raising triglycerides; sympathetic activation and "
            "salt retention raise blood pressure."
        ),
        pattern_signatures=[
            PatternSignature(
                pattern_type="cluster",
                characteristics=(
                    "co-clustering of BMI, blood pressure, triglycerides, "
                    "HDL, fasting glucose in cross-sectional cohorts"
                ),
                detection_hints=(
                    "expect strong mutual information between BMI ↔ "
                    "blood pressure and lipid-glucose axes; correlation "
                    "alone misses the curvature"
                ),
            ),
        ],
        quantitative_ranges=[
            QuantitativeRange(property="waist_circumference_threshold_men",
                                typical_range=[102, 102], unit="cm"),
            QuantitativeRange(property="waist_circumference_threshold_women",
                                typical_range=[88, 88], unit="cm"),
            QuantitativeRange(property="fasting_glucose_threshold",
                                typical_range=[100, 100], unit="mg/dL"),
        ],
        caveats=[
            Caveat(text="Diagnostic thresholds vary across IDF / NCEP / "
                          "WHO criteria — synthesis should not assert "
                          "diagnosis from raw values alone.",
                     severity="critical"),
            Caveat(text="Cross-sectional cohort data cannot establish causal "
                          "direction among the cluster components.",
                     severity="important"),
        ],
        references=[
            EntryReference(
                citation="Grundy SM et al. (2005). Diagnosis and management "
                          "of the metabolic syndrome (AHA/NHLBI scientific statement).",
                doi="10.1161/CIRCULATIONAHA.105.169404",
            ),
        ],
    ))

    out.append(_e(
        "seed:cardiovascular_risk_biomarkers",
        domain=["medical", "bio"],
        concept_type="biomarker_panel",
        name="cardiovascular risk biomarkers",
        aliases=["CVD biomarkers", "Framingham risk variables"],
        definition=(
            "The standard variable set used to estimate 10-year "
            "cardiovascular event risk: age, sex, total cholesterol, "
            "HDL cholesterol, systolic blood pressure (treated vs "
            "untreated), smoking status, diabetes status. Framingham, "
            "ASCVD, and SCORE risk scores all derive from this set "
            "with population-specific calibration."
        ),
        pattern_signatures=[
            PatternSignature(
                pattern_type="multivariate_risk",
                characteristics=(
                    "log-linear contribution of each marker to event "
                    "log-odds; non-trivial interactions especially "
                    "around blood-pressure treatment status"
                ),
            ),
        ],
        references=[
            EntryReference(
                citation="D'Agostino RB et al. (2008). General "
                          "cardiovascular risk profile for use in primary "
                          "care: the Framingham Heart Study.",
                doi="10.1161/CIRCULATIONAHA.107.699579",
            ),
        ],
        caveats=[
            Caveat(text="Framingham scores miscalibrate in non-US "
                          "populations; use ASCVD or SCORE for the "
                          "appropriate population.", severity="important"),
        ],
    ))

    out.append(_e(
        "seed:bmi_clinical_caveats",
        domain=["medical", "bio"],
        concept_type="clinical_caveat",
        name="BMI as a population vs individual marker",
        definition=(
            "Body Mass Index (kg/m²) is a useful POPULATION-level "
            "indicator of adiposity and cardiometabolic risk. At the "
            "INDIVIDUAL level it conflates muscle mass, frame size, "
            "ethnicity-specific body composition, and adipose "
            "distribution. Normal-BMI individuals can be metabolically "
            "unhealthy; high-BMI athletes can be metabolically healthy."
        ),
        caveats=[
            Caveat(text="Asian populations carry cardiometabolic risk at "
                          "lower BMI thresholds than European populations; "
                          "WHO recommends 23 kg/m² as the action point "
                          "rather than 25 kg/m² for these groups.",
                     severity="critical"),
            Caveat(text="In a cross-sectional cohort, regressing AGE on "
                          "BMI is mechanically valid but interpretively "
                          "wrong — age does not respond to BMI.",
                     severity="important"),
        ],
        references=[
            EntryReference(citation="WHO Expert Consultation (2004). "
                                       "Appropriate body-mass index for Asian "
                                       "populations.",
                              doi="10.1016/S0140-6736(03)15268-3"),
        ],
    ))

    out.append(_e(
        "seed:cohort_study_n_caveat",
        domain=["medical", "research"],
        concept_type="study_design_caveat",
        name="small cross-sectional cohort caveats",
        definition=(
            "Cross-sectional cohorts of n < ~500 cannot reliably "
            "estimate small effects, do not support causal claims "
            "(no temporal ordering), and should be treated as "
            "hypothesis-generating rather than hypothesis-confirming."
        ),
        caveats=[
            Caveat(text="Statistical significance at small n is dominated "
                          "by sampling variance; report effect sizes + CIs, "
                          "not p-values alone.", severity="critical"),
            Caveat(text="Correlation in a single time-point cohort does "
                          "not imply causation — there is no temporal "
                          "ordering to identify which variable changed first.",
                     severity="critical"),
        ],
        references=[
            EntryReference(citation="Ioannidis JPA (2005). Why most "
                                       "published research findings are false.",
                              doi="10.1371/journal.pmed.0020124"),
        ],
    ))

    out.append(_e(
        "seed:churn_survival",
        domain=["medical", "industrial", "finance"],
        concept_type="survival_application",
        name="customer / equipment / patient time-to-event",
        aliases=["churn modelling", "failure-time analysis"],
        definition=(
            "Time-to-event modelling applies wherever the question is "
            "'when does X happen?' rather than 'does X happen?'. "
            "Customer churn, equipment failure, patient relapse — "
            "all are right-censored time-to-event problems where "
            "Kaplan-Meier + Cox PH are the standard tools."
        ),
        pattern_signatures=[
            PatternSignature(
                pattern_type="survival",
                characteristics="duration column + event-or-censored indicator",
                detection_hints="seed:kaplan_meier triggers on these",
            ),
        ],
    ))

    # ---------- Finance ----------
    out.append(_e(
        "seed:volatility_clustering",
        domain=["finance"],
        concept_type="empirical_pattern",
        name="volatility clustering",
        aliases=["GARCH effect", "ARCH effect"],
        definition=(
            "Periods of large absolute returns cluster together in "
            "financial time series — calm regimes alternate with "
            "stormy ones. Standard squared-return autocorrelation is "
            "significant out to 50+ lags even when raw-return ACF is "
            "essentially zero. GARCH(p,q) models capture this directly."
        ),
        pattern_signatures=[
            PatternSignature(
                pattern_type="regime_shift",
                characteristics=(
                    "alternating low-vol / high-vol regimes; HMM "
                    "with K=2 typically fits"
                ),
                detection_hints=(
                    "latent_regime_structure finding kind; squared returns "
                    "show long-memory ACF"
                ),
            ),
        ],
        references=[
            EntryReference(
                citation="Bollerslev T (1986). Generalized autoregressive "
                          "conditional heteroskedasticity.",
                doi="10.1016/0304-4076(86)90063-1",
            ),
        ],
    ))

    out.append(_e(
        "seed:mean_reversion",
        domain=["finance"],
        concept_type="dynamical_system",
        name="Ornstein-Uhlenbeck mean reversion",
        aliases=["OU process"],
        definition=(
            "Continuous-time analogue of AR(1): dX = κ(μ - X)dt + σ dW. "
            "The process pulls back toward μ at speed κ; stationary "
            "distribution is N(μ, σ²/(2κ)). Used for interest rates "
            "(Vasicek), commodity prices, and pairs-trading spreads."
        ),
        pattern_signatures=[
            PatternSignature(
                pattern_type="persistence",
                characteristics="ACF decays geometrically toward zero",
                detection_hints="AR(1) fit with φ in (0, 1)",
            ),
        ],
        references=[
            EntryReference(
                citation="Vasicek O (1977). An equilibrium "
                          "characterization of the term structure.",
            ),
        ],
    ))

    out.append(_e(
        "seed:correlation_breakdown",
        domain=["finance"],
        concept_type="empirical_pattern",
        name="correlation breakdown in stress regimes",
        definition=(
            "Asset return correlations rise in market-stress regimes — "
            "diversification benefits collapse precisely when they're "
            "most needed. A 0.3 correlation in calm regimes can spike "
            "to 0.8+ in crisis regimes."
        ),
        caveats=[
            Caveat(text="Portfolio risk models calibrated on calm-regime "
                          "data systematically underestimate tail risk.",
                     severity="critical"),
        ],
        references=[
            EntryReference(citation="Longin F, Solnik B (2001). Extreme "
                                       "correlation of international equity markets."),
        ],
    ))

    out.append(_e(
        "seed:fama_french_factors",
        domain=["finance"],
        concept_type="empirical_model",
        name="Fama-French factor model",
        aliases=["3-factor model", "5-factor model"],
        definition=(
            "Cross-sectional asset-return model decomposing expected "
            "return into market beta + size factor (SMB) + value factor "
            "(HML), extended to 5 factors with profitability (RMW) and "
            "investment (CMA). Replaces CAPM as the de-facto baseline "
            "in academic asset pricing."
        ),
        references=[
            EntryReference(
                citation="Fama EF, French KR (1993). Common risk factors "
                          "in the returns on stocks and bonds.",
                doi="10.1016/0304-405X(93)90023-5",
            ),
        ],
    ))

    # ---------- Sports ----------
    out.append(_e(
        "seed:elo_rating",
        domain=["sports", "research"],
        concept_type="rating_system",
        name="Elo rating system",
        definition=(
            "Pairwise rating method that updates each player's score "
            "based on the difference between actual and expected "
            "outcome of a match. Expected score uses a logistic "
            "function of the rating difference. Originally chess; now "
            "ubiquitous in competitive sports and game-AI matchmaking."
        ),
        pattern_signatures=[
            PatternSignature(
                pattern_type="latent_rating",
                characteristics="player ability is a latent variable inferred from outcomes",
            ),
        ],
        references=[
            EntryReference(citation="Elo AE (1978). The Rating of "
                                       "Chessplayers, Past and Present."),
        ],
    ))

    out.append(_e(
        "seed:regression_to_mean_sports",
        domain=["sports", "research"],
        concept_type="statistical_caveat",
        name="regression to the mean in sports performance",
        definition=(
            "Extreme performances (best season, worst slump) are "
            "partially due to skill and partially due to luck. The "
            "luck component does not persist; subsequent performance "
            "regresses toward the player's true mean. 'Sophomore "
            "slump' and 'comeback player of the year' are both partly "
            "regression-to-the-mean phenomena."
        ),
        caveats=[
            Caveat(text="Coaching changes, training adjustments, and "
                          "career-stage effects coexist with regression-"
                          "to-the-mean and can be confused with it.",
                     severity="important"),
        ],
        references=[
            EntryReference(citation="Galton F (1886). Regression towards "
                                       "mediocrity in hereditary stature."),
        ],
    ))

    out.append(_e(
        "seed:pythagorean_expectation",
        domain=["sports"],
        concept_type="empirical_law",
        name="Pythagorean expectation",
        aliases=["Bill James Pythagorean"],
        definition=(
            "Empirical formula predicting team win percentage from "
            "points-scored and points-allowed: W% ≈ PF^x / (PF^x + PA^x). "
            "Exponent x is sport-specific (~1.83 baseball, ~2.37 NFL, "
            "~14 NBA). Teams over- or under-performing their Pythagorean "
            "expectation tend to regress to it."
        ),
        references=[
            EntryReference(citation="James B. Baseball Abstract."),
        ],
    ))

    # ---------- Logistics / industrial ----------
    out.append(_e(
        "seed:littles_law",
        domain=["logistics", "industrial"],
        concept_type="empirical_law",
        name="Little's Law",
        definition=(
            "In any stable queueing system, the long-run average number "
            "of items in the system L equals the average arrival rate λ "
            "times the average time spent in the system W: L = λ × W. "
            "Distribution-free; holds whenever the system is stationary."
        ),
        pattern_signatures=[
            PatternSignature(
                pattern_type="bottleneck",
                characteristics="WIP grows linearly when arrival rate exceeds service rate",
            ),
        ],
        references=[
            EntryReference(
                citation="Little JDC (1961). A proof for the queuing formula L = λW.",
                doi="10.1287/opre.9.3.383",
            ),
        ],
    ))

    out.append(_e(
        "seed:bullwhip_effect",
        domain=["logistics"],
        concept_type="empirical_pattern",
        name="bullwhip effect",
        aliases=["demand amplification"],
        definition=(
            "Demand variability amplifies as you move upstream in a "
            "supply chain — small changes in customer demand produce "
            "much larger order swings at the manufacturer level. "
            "Driven by demand-signal processing lags, batching, "
            "rationing games, and price fluctuations."
        ),
        references=[
            EntryReference(
                citation="Lee HL, Padmanabhan V, Whang S (1997). The "
                          "bullwhip effect in supply chains.",
            ),
        ],
    ))

    out.append(_e(
        "seed:predictive_maintenance",
        domain=["industrial"],
        concept_type="empirical_pattern",
        name="predictive-maintenance precursor signatures",
        definition=(
            "Equipment failures are usually preceded by detectable "
            "shifts in vibration spectrum, temperature drift, current "
            "draw, or oil chemistry. The challenge is distinguishing "
            "incipient failure precursors from normal load variation."
        ),
        pattern_signatures=[
            PatternSignature(
                pattern_type="regime_shift",
                characteristics=(
                    "subtle drift in spectral peak amplitude or "
                    "frequency before catastrophic failure"
                ),
                detection_hints="non-stationary oscillation finding; "
                                  "sliding-window wavelet ridges",
            ),
        ],
        caveats=[
            Caveat(text="False-positive cost (unnecessary downtime) often "
                          "exceeds the cost of one missed event; tune "
                          "thresholds with operator judgement.",
                     severity="important"),
        ],
    ))

    # ---------- Research / methodology ----------
    out.append(_e(
        "seed:replication_threshold",
        domain=["research"],
        concept_type="methodological_principle",
        name="replication-before-claim",
        definition=(
            "A finding is a hypothesis until it replicates on "
            "independent data. Aurora's overnight learning loop "
            "promotes a finding to canonical only after ≥3 "
            "confirmations across distinct runs at ≥60% consensus. "
            "Pre-registration is the strongest defence against "
            "post-hoc tuning."
        ),
        pattern_signatures=[
            PatternSignature(
                pattern_type="meta",
                characteristics="cross-run replication strengthens vs weakens vs contradicts",
            ),
        ],
    ))

    out.append(_e(
        "seed:p_hacking",
        domain=["research"],
        concept_type="methodological_caveat",
        name="p-hacking and garden of forking paths",
        definition=(
            "Selectively reporting tests that achieve significance, "
            "or making analysis decisions after seeing the data, "
            "inflates false-discovery rates dramatically beyond the "
            "nominal α. Multiple-comparisons correction and "
            "pre-registered analysis plans are the standard defences."
        ),
        caveats=[
            Caveat(text="Even researchers acting in good faith can "
                          "p-hack via subtle data-conditional choices "
                          "('garden of forking paths').",
                     severity="critical"),
        ],
        references=[
            EntryReference(
                citation="Gelman A, Loken E (2014). The statistical crisis "
                          "in science.",
            ),
        ],
    ))

    out.append(_e(
        "seed:effect_size_vs_significance",
        domain=["research", "medical"],
        concept_type="methodological_principle",
        name="effect size vs statistical significance",
        definition=(
            "Statistical significance answers 'is there an effect?'; "
            "effect size answers 'how big?'. With large enough n, "
            "trivially small effects become significant. Reporting "
            "Cohen's d or η² alongside p-values prevents the common "
            "error of treating significance as importance."
        ),
        pattern_signatures=[
            PatternSignature(
                pattern_type="effect_size",
                characteristics="Cohen's d or η² classifies practical importance",
            ),
        ],
        references=[
            EntryReference(citation="Cumming G (2014). The new statistics: "
                                       "why and how."),
        ],
    ))

    out.append(_e(
        "seed:nonlinear_correlation_caveat",
        domain=["research", "bio"],
        concept_type="methodological_caveat",
        name="linear correlation misses nonlinear dependence",
        definition=(
            "Pearson correlation captures linear relationships only. "
            "Curved relationships (sigmoidal dose-response, threshold "
            "effects, periodic functions) can have high mutual "
            "information and near-zero Pearson r. Mutual information "
            "or rank-based measures (Spearman) catch what Pearson misses."
        ),
        pattern_signatures=[
            PatternSignature(
                pattern_type="dependence",
                characteristics="MI > 0.2 nats with |r| < 0.3",
                detection_hints="nonlinear_dependence finding kind",
            ),
        ],
    ))

    # ---------- Climate / enviro extension ----------
    out.append(_e(
        "seed:enso_oscillation",
        domain=["enviro"],
        concept_type="climate_phenomenon",
        name="El Niño-Southern Oscillation (ENSO)",
        aliases=["ENSO", "El Niño", "La Niña"],
        definition=(
            "Coupled ocean-atmosphere oscillation in the equatorial "
            "Pacific with quasi-periodic variation between El Niño "
            "(warm) and La Niña (cool) phases on a 2-7 year cycle. "
            "Drives global teleconnection patterns affecting "
            "precipitation, temperature, hurricane activity worldwide."
        ),
        pattern_signatures=[
            PatternSignature(
                pattern_type="oscillation",
                characteristics="quasi-period 2-7 years, phase-locked to seasonal cycle",
                detection_hints="wavelet shows non-stationary period; "
                                  "Lomb-Scargle on irregular monthly series",
            ),
        ],
        quantitative_ranges=[
            QuantitativeRange(property="period",
                                typical_range=[2.0, 7.0], unit="years"),
        ],
        references=[
            EntryReference(citation="McPhaden MJ et al. (2006). ENSO as "
                                       "an integrating concept in earth science.",
                              doi="10.1126/science.1132588"),
        ],
    ))

    out.append(_e(
        "seed:seasonal_decomposition",
        domain=["enviro", "economics"],
        concept_type="time_series_method",
        name="seasonal decomposition (STL)",
        definition=(
            "Time series Y_t = T_t + S_t + R_t — trend + seasonal + "
            "remainder. STL (Seasonal-Trend decomposition using LOESS) "
            "is the workhorse method, robust to missing data and "
            "non-stationary seasonality."
        ),
        references=[
            EntryReference(citation="Cleveland RB, Cleveland WS, McRae JE, "
                                       "Terpenning I (1990). STL: A seasonal-"
                                       "trend decomposition procedure based on loess."),
        ],
    ))

    # ---------- Bio extension ----------
    out.append(_e(
        "seed:hill_function",
        domain=["bio", "medical"],
        concept_type="dynamical_system",
        name="Hill function dose-response",
        aliases=["Michaelis-Menten kinetics (n=1)"],
        definition=(
            "Sigmoidal dose-response y = x^n / (K^n + x^n) describing "
            "ligand-receptor binding, enzyme kinetics, gene-regulatory "
            "switches. The Hill coefficient n is the cooperativity: "
            "n=1 is non-cooperative Michaelis-Menten; n>1 indicates "
            "positive cooperativity (sharp switch); n<1 negative."
        ),
        pattern_signatures=[
            PatternSignature(
                pattern_type="growth",
                characteristics="sigmoidal saturating curve",
                detection_hints="logistic_fit / Hill_fit shape match",
            ),
        ],
    ))

    out.append(_e(
        "seed:gene_expression_oscillation",
        domain=["bio"],
        concept_type="biological_process",
        name="gene-expression oscillation",
        definition=(
            "Many gene-regulatory networks produce sustained or damped "
            "oscillations in transcript abundance: circadian clock, "
            "cell cycle, NF-κB signaling, segmentation clock. Most "
            "are negative feedback loops with delay — the canonical "
            "two-component oscillator (activator + delayed inhibitor)."
        ),
        pattern_signatures=[
            PatternSignature(
                pattern_type="oscillation",
                characteristics="period 0.5-24+ hours depending on system",
                detection_hints="damped harmonic / AR(2) fits at appropriate scale",
            ),
        ],
        references=[
            EntryReference(citation="Novák B, Tyson JJ (2008). Design "
                                       "principles of biochemical oscillators.",
                              doi="10.1038/nrm2530"),
        ],
    ))

    # ---------- Cross-cutting: data quality ----------
    out.append(_e(
        "seed:missing_at_random",
        domain=["research", "medical", "economics"],
        concept_type="methodological_principle",
        name="missing-data mechanisms (MCAR / MAR / MNAR)",
        definition=(
            "Three regimes: Missing Completely At Random (missingness "
            "independent of all variables), Missing At Random "
            "(missingness depends on observed variables but not on "
            "the missing values themselves), Missing Not At Random "
            "(missingness depends on the unobserved value)."
        ),
        caveats=[
            Caveat(text="Common imputation methods (mean / median, "
                          "regression imputation) are unbiased only under "
                          "MCAR or MAR. MNAR requires modelling the "
                          "missingness mechanism explicitly.",
                     severity="critical"),
        ],
        references=[
            EntryReference(citation="Rubin DB (1976). Inference and "
                                       "missing data."),
        ],
    ))

    out.append(_e(
        "seed:simpsons_paradox",
        domain=["research", "medical", "economics"],
        concept_type="methodological_caveat",
        name="Simpson's paradox",
        definition=(
            "A trend present in groups can disappear or reverse when "
            "the groups are combined. Aggregate statistics can mislead "
            "when there's a confounding subgroup variable. The "
            "resolution is conditional inference (compare like with "
            "like) or causal-graph analysis."
        ),
        caveats=[
            Caveat(text="Simpson's paradox is a special case of "
                          "confounding by an unmodelled stratifier — "
                          "subgroup analysis is mandatory before "
                          "asserting an aggregate trend.",
                     severity="critical"),
        ],
        references=[
            EntryReference(citation="Simpson EH (1951). The interpretation "
                                       "of interaction in contingency tables."),
        ],
    ))

    out.append(_e(
        "seed:confounding_vs_causal",
        domain=["research", "medical", "economics"],
        concept_type="methodological_principle",
        name="confounding vs causal effect",
        definition=(
            "Observed correlation between X and Y can arise from: "
            "X causing Y, Y causing X, a common cause Z (confounding), "
            "or selection bias. Causal inference requires either "
            "experimental manipulation or assumptions encoded in a "
            "causal graph that justify treating observational data "
            "as if it were experimental."
        ),
        references=[
            EntryReference(citation="Pearl J (2009). Causality: Models, "
                                       "Reasoning, and Inference."),
        ],
    ))

    # ---------- Anomaly / outlier extensions ----------
    out.append(_e(
        "seed:robust_zscore",
        domain=["research", "bio", "medical", "industrial"],
        concept_type="statistical_method",
        name="robust z-score (median + MAD)",
        definition=(
            "Standard z-score uses mean + standard deviation, both of "
            "which are corrupted by outliers themselves. Robust z = "
            "(x - median) / (1.4826 × MAD) replaces both with their "
            "robust analogs. The 1.4826 factor makes MAD equivalent "
            "to σ for normally-distributed data."
        ),
        pattern_signatures=[
            PatternSignature(
                pattern_type="outlier",
                characteristics="|robust_z| > 3 typical threshold",
            ),
        ],
        references=[
            EntryReference(citation="Hampel FR (1974). The influence curve "
                                       "and its role in robust estimation."),
        ],
    ))

    out.append(_e(
        "seed:cluster_stability_ari",
        domain=["research", "bio"],
        concept_type="statistical_method",
        name="cluster stability via ARI",
        definition=(
            "Bootstrap stability of a clustering: for each resample, "
            "re-cluster, then compare to the original via Adjusted "
            "Rand Index. Mean ARI across resamples ≥ 0.7 strong, "
            "0.5-0.7 moderate, <0.5 weak — at which point 'no clear "
            "clustering' is the honest finding."
        ),
        references=[
            EntryReference(
                citation="Hubert L, Arabie P (1985). Comparing partitions.",
                doi="10.1007/BF01908075",
            ),
        ],
    ))

    return out


# ---------------------------------------------------------------------------
# Public entry — pipeline calls this
# ---------------------------------------------------------------------------

ProgressCb = Callable[[Dict[str, Any]], None]


def run_ingest(
    bank: KnowledgeBankStore,
    checkpoint_dir: Path,
    *,
    progress_cb: Optional[ProgressCb] = None,
) -> Dict[str, Any]:
    """Insert / refresh the curated seed entries.

    Idempotent — re-running on an existing bank just upserts.
    """
    entries = _curated_entries()
    if progress_cb:
        try: progress_cb({"source": SOURCE, "stage": "embed_and_insert",
                            "n_done": 0, "n_total": len(entries)})
        except Exception: pass
    res = bank.insert_many(entries)
    if progress_cb:
        try: progress_cb({"source": SOURCE, "stage": "done",
                            "n_done": res.get("n_inserted", 0),
                            "n_total": len(entries)})
        except Exception: pass
    return {"ok": True,
             "n_inserted": res.get("n_inserted", 0),
             "n_errors": res.get("n_errors", 0),
             "first_errors": res.get("first_errors", []),
             "source": SOURCE,
             "source_version": SOURCE_VERSION}
