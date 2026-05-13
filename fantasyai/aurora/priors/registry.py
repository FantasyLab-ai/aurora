"""
Prior registry — defines the schema and ships the v0 catalogue (4 priors).

Each prior describes a known physical law in a structured way the matcher
can reason about: structural form, parameter ranges, identifiability
conditions, citation. As of v0 the catalogue is intentionally small but
high-precision — better to match 4 laws confidently than 40 sloppily.

To add a prior: append a new ``Prior(...)`` to ``_BUILTIN_PRIORS`` below.
Future versions will load priors from a YAML directory; for now the
in-Python catalogue keeps the matcher simple and dependency-free.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Tuple


@dataclass(frozen=True)
class Prior:
    """A canonical physical law, structured for the matcher and validation loop.

    The first block of fields (id, display_name, ..., falsification_conditions)
    is the v1 schema — every shipped prior populates these. The second block
    is the v2 schema added for the continuous-validation loop: domain of
    validity, version, deprecation, validation_history, calibration_log,
    provenance_chain. These are all optional with safe defaults so existing
    priors load unchanged.
    """
    # ---- v1: structural definition ----
    id: str
    display_name: str
    domain: str                              # research | enviro | industrial | finance | bio | thermal
    citation: str
    structural_form: str                     # human-readable equation
    matches_runner_form: str                 # which physics_models.py candidate this maps to
    description: str
    free_parameters: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    sign_constraints: Dict[str, str] = field(default_factory=dict)
    identifiability_conditions: List[str] = field(default_factory=list)
    falsification_conditions: List[str] = field(default_factory=list)

    # ---- v2: validation-loop schema ----
    # Domain of validity: explicit conditions under which the prior holds, AND
    # explicit conditions outside which it shouldn't be applied. The matcher
    # consults this when scoring confidence on a new dataset.
    domain_of_validity: Dict[str, Any] = field(default_factory=dict)
    # ↑ e.g. {
    #     "min_n": 50, "max_n": None,
    #     "domain_tags": ["thermal"], "exclude_domains": [],
    #     "stationary_required": False, "monotonic_required": False,
    # }

    # Underlying assumptions, stated explicitly. Keep separate from
    # identifiability_conditions: assumptions are theoretical (lumped capacity,
    # constant gravity), conditions are statistical (≥50 samples, monotonic).
    assumptions: List[str] = field(default_factory=list)

    # Lifecycle: which "shelf" this prior sits on.
    #   "candidate"  → seen on too few runs to trust
    #   "validated"  → confirmed on enough runs (default for the shipped catalogue)
    #   "canonical"  → very strongly confirmed, used as a benchmark
    #   "questioned" → recent contradictions detected; under review
    #   "deprecated" → kept in the history but no longer matched
    status: str = "validated"

    # Semver-style version string. Bumped when the structural form, parameter
    # ranges, or domain of validity changes. Older versions stay queryable.
    version: str = "1.0.0"

    # Deprecation: when set, explains what to use instead. The library never
    # silently deletes a prior — we keep history so we can show "we used to
    # think X, here's why we changed our minds."
    deprecation: Optional[Dict[str, Any]] = None
    # ↑ {"date": "2026-05-04", "reason": "...", "superseded_by": "<prior_id>"}

    # NOTE: provenance_chain + calibration_log are NOT stored on the Prior
    # dataclass (which is frozen-immutable for thread-safety + reproducibility).
    # They live in ``priors/validation_log.py`` as a JSONL ledger keyed by
    # prior_id, queryable via ``get_validation_history(prior_id)``.

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    # ---- Convenience accessors used by the validation loop ----
    def is_active(self) -> bool:
        return self.status not in ("deprecated",)


# ---------------------------------------------------------------------------
# v0 catalogue
# ---------------------------------------------------------------------------

NEWTON_COOLING = Prior(
    id="newton_cooling",
    display_name="Newton's law of cooling",
    domain="thermal",
    citation="Newton, Philos. Trans. R. Soc. Lond. (1701)",
    structural_form="dT/dt = -k · (T - T_∞)",
    matches_runner_form="linear_ode",  # dy/dt = a*y + b   with a = -k, b = k*T_∞
    description=(
        "Rate of temperature change is proportional to the difference between "
        "the body's temperature and the ambient temperature. Used for "
        "thermal decay, electronic cooling, building HVAC."
    ),
    free_parameters={
        "k": {
            "name": "cooling rate constant",
            "range": [1e-6, 1.0],
            "typical": 0.01,
            "interpretation": "1/seconds — characteristic time-scale of decay",
        },
        "T_inf": {
            "name": "ambient temperature",
            "range": [-100.0, 100.0],
            "typical": 20.0,
            "interpretation": "asymptotic equilibrium",
        },
    },
    sign_constraints={"a": "negative"},  # ⇒ stable equilibrium
    identifiability_conditions=[
        "data covers a sufficient temperature range to observe equilibrium",
        "thermal mass and surface area are constant over the observation window",
        "no internal heat generation",
    ],
    falsification_conditions=[
        "if a > 0 in the fitted ODE: data is heating up unbounded → not cooling",
        "if residuals show oscillation: cooling alone cannot explain periodic patterns",
    ],
)

EXPONENTIAL_DECAY_GROWTH = Prior(
    id="exponential",
    display_name="Exponential growth/decay",
    domain="research",
    citation="Malthus (1798); Rutherford & Soddy (1903)",
    structural_form="dy/dt = a · y    →    y(t) = y₀ · exp(a · t)",
    matches_runner_form="linear_ode",  # dy/dt = a*y + b   with b ≈ 0
    description=(
        "Quantity changes at a rate proportional to itself, with no external "
        "forcing. Used for population dynamics, radioactive decay, "
        "compound interest, simple bacterial growth."
    ),
    free_parameters={
        "a": {
            "name": "rate constant",
            "range": [-10.0, 10.0],
            "typical": None,
            "interpretation": "positive = growth, negative = decay; |a| sets the time-scale",
        },
    },
    sign_constraints={},  # no constraint on a
    identifiability_conditions=[
        "no external forcing or carrying capacity reached",
        "rate is constant over the observation window",
    ],
    falsification_conditions=[
        "if data shows saturation/plateau: exponential alone won't fit the tail; a logistic prior may match better",
        "if b is large in the fitted linear_ode: there's a non-zero equilibrium → use Newton's cooling",
    ],
)

VERHULST_LOGISTIC = Prior(
    id="verhulst_logistic",
    display_name="Verhulst logistic growth",
    domain="bio",
    citation="Verhulst, Corresp. Math. Phys. (1838)",
    structural_form="dy/dt = r · y · (1 - y/K)",
    matches_runner_form="logistic",
    description=(
        "S-shaped growth toward a carrying capacity K. Used for population "
        "ecology, technology adoption (S-curve), epidemiology, sales saturation."
    ),
    free_parameters={
        "r": {
            "name": "intrinsic growth rate",
            "range": [1e-6, 10.0],
            "typical": 0.1,
            "interpretation": "growth rate in the unrestricted phase",
        },
        "K": {
            "name": "carrying capacity",
            "range": [1e-6, 1e9],
            "typical": None,
            "interpretation": "asymptotic upper bound on y",
        },
    },
    sign_constraints={"r": "positive", "K": "positive"},
    identifiability_conditions=[
        "data spans both the growth phase AND approaches saturation (otherwise K is unidentifiable)",
        "no shocks or regime shifts in the observation window",
        "the system is closed (no immigration/emigration in pop. context)",
    ],
    falsification_conditions=[
        "if r ≤ 0: not growth; the wrong direction was fitted",
        "if K is at the data range edge: data probably hasn't saturated → r is biased",
        "if residuals show multiple inflection points: there are multiple regimes; one logistic is insufficient",
    ],
)

DAMPED_HARMONIC_OSCILLATOR = Prior(
    id="damped_oscillator",
    display_name="Damped harmonic oscillator",
    domain="industrial",
    citation="Classical mechanics; Hooke's law + viscous damping",
    structural_form="m · y'' + c · y' + k · y = 0   ↔   y'' + 2ζω₀·y' + ω₀²·y = 0",
    matches_runner_form="damped_oscillator",
    description=(
        "Mass-spring-damper system. Used for mechanical vibration, RLC "
        "circuits, structural dynamics, control systems, oscillating "
        "biological/chemical systems."
    ),
    free_parameters={
        "c": {
            "name": "damping coefficient",
            "range": [0.0, 1e6],
            "typical": None,
            "interpretation": "viscous resistance; c²<4mk = underdamped (oscillates)",
        },
        "k": {
            "name": "stiffness / spring constant",
            "range": [0.0, 1e9],
            "typical": None,
            "interpretation": "restoring-force strength",
        },
    },
    sign_constraints={"c": "non-negative", "k": "non-negative"},
    identifiability_conditions=[
        "data is sampled at ≥10× the natural frequency (Nyquist + margin)",
        "no external forcing, or forcing is also being modeled",
        "system is approximately linear over the observed amplitude range",
    ],
    falsification_conditions=[
        "if c < 0: implies energy injection — not a passive oscillator",
        "if residuals show a constant frequency offset: forcing is present and unmodeled",
        "if amplitude grows over time: the system is unstable (c too negative or k < 0)",
    ],
)


RADIOACTIVE_DECAY = Prior(
    id="radioactive_decay",
    display_name="Radioactive decay (first-order)",
    domain="research",
    citation="Rutherford & Soddy, Philos. Mag. (1903)",
    structural_form="dN/dt = -λ · N    →    N(t) = N₀ · exp(-λ · t)",
    matches_runner_form="linear_ode",  # dy/dt = a*y + b   with b ≈ 0 and a < 0
    description=(
        "First-order kinetics with no source term. Rate of decay is "
        "proportional to remaining quantity. Used for radionuclide half-life, "
        "drug elimination (single-compartment pharmacokinetics), tracer "
        "washout, first-order chemical reactions."
    ),
    free_parameters={
        "lambda_decay": {
            "name": "decay constant λ",
            "range": [1e-9, 10.0],
            "typical": 0.01,
            "interpretation": "1/seconds; t_½ = ln(2)/λ",
        },
    },
    sign_constraints={"a": "negative", "b": "near-zero"},
    identifiability_conditions=[
        "no source term (no replenishment of the decaying quantity)",
        "data spans at least 1-2 half-lives",
        "single-compartment system (no multi-phase decay)",
    ],
    falsification_conditions=[
        "if b is far from zero in the fitted ODE: there's a source term → not pure decay",
        "if half-life from fitted λ differs from known reference: wrong species or contamination",
    ],
)


BEER_LAMBERT_LAW = Prior(
    id="beer_lambert",
    display_name="Beer-Lambert law (light absorption)",
    domain="research",
    citation="Bouguer (1729); Lambert (1760); Beer (1852)",
    structural_form="dI/dx = -ε · c · I    →    I(x) = I₀ · exp(-ε · c · x)",
    matches_runner_form="linear_ode",  # dy/dx = a*y + b   with b ≈ 0
    description=(
        "Light intensity decays exponentially with path length through an "
        "absorbing medium. Used for spectrophotometry (concentration "
        "measurement), atmospheric optics, near-infrared sensing. "
        "Mathematically identical to first-order decay; distinguished by "
        "domain context and the independent variable being a path length."
    ),
    free_parameters={
        "absorption_coefficient": {
            "name": "ε · c (molar absorptivity × concentration)",
            "range": [1e-6, 100.0],
            "typical": 0.5,
            "interpretation": "1/length; large values = strongly absorbing medium",
        },
    },
    sign_constraints={"a": "negative", "b": "near-zero"},
    identifiability_conditions=[
        "monochromatic light (single wavelength) or narrow band",
        "homogeneous medium (uniform concentration)",
        "no scattering, only absorption",
        "linear regime (no photo-bleaching, no saturation)",
    ],
    falsification_conditions=[
        "if curvature deviates from pure exponential at high concentrations: "
        "scattering or non-linear effects are present",
        "if a ≥ 0: light is being amplified, not absorbed (lasing medium)",
    ],
)


GOMPERTZ_GROWTH = Prior(
    id="gompertz",
    display_name="Gompertz growth curve",
    domain="bio",
    citation="Gompertz, Philos. Trans. R. Soc. (1825)",
    structural_form="dy/dt = r · y · ln(K/y)    →    y(t) = K · exp(-b · exp(-r·t))",
    matches_runner_form="logistic",
    description=(
        "S-shaped growth toward an upper asymptote, but with **asymmetric** "
        "inflection (closer to the lower bound than logistic's midpoint). "
        "Used heavily in tumor growth modeling, mortality demography (the "
        "Gompertz-Makeham law), reliability engineering, and species "
        "abundance. Often a better fit than logistic for biological growth."
    ),
    free_parameters={
        "r": {
            "name": "growth rate at carrying capacity",
            "range": [1e-6, 5.0],
            "typical": 0.05,
            "interpretation": "asymptotic instantaneous growth rate",
        },
        "K": {
            "name": "carrying capacity / asymptote",
            "range": [1e-6, 1e9],
            "typical": None,
            "interpretation": "upper bound on y as t → ∞",
        },
    },
    sign_constraints={"r": "positive", "K": "positive"},
    identifiability_conditions=[
        "data spans both the rapid-growth phase AND approaches the asymptote",
        "system is closed (no abrupt regime changes during observation)",
        "single-cohort assumption (no mixing of two populations with different K)",
    ],
    falsification_conditions=[
        "if observed inflection point is exactly at K/2: logistic fits better than Gompertz",
        "if y exceeds K consistently in the data: K is mis-estimated or data has a different mechanism",
        "if residuals show oscillation: predator-prey or seasonal effects unmodeled",
    ],
)


BASS_DIFFUSION = Prior(
    id="bass_diffusion",
    display_name="Bass diffusion model (technology adoption)",
    domain="finance",
    citation="Bass, Mgmt. Sci. (1969)",
    structural_form="dN/dt = (p + q · N/M) · (M - N)",
    matches_runner_form="logistic",  # mathematically logistic-family when q ≫ p
    description=(
        "Adoption of a new product / technology / behavior in a population. "
        "Innovators (rate p) start using the product independently; imitators "
        "(rate q) adopt based on prior adopters. Used for product launch "
        "forecasting, epidemic spread of behaviors, viral marketing, "
        "membership growth in networks."
    ),
    free_parameters={
        "r": {
            "name": "effective growth rate (≈ p + q)",
            "range": [1e-4, 5.0],
            "typical": 0.5,
            "interpretation": "combined innovator + imitator rate",
        },
        "K": {
            "name": "market potential M",
            "range": [1e-3, 1e10],
            "typical": None,
            "interpretation": "total eventual adopters",
        },
    },
    sign_constraints={"r": "positive", "K": "positive"},
    identifiability_conditions=[
        "single, well-defined product / behavior in a fixed population",
        "no marketing-spend changes during the observation window",
        "no competing products triggering an adoption shift",
    ],
    falsification_conditions=[
        "if data shows multiple adoption peaks: multi-product or generation effects unmodeled",
        "if early-time growth is slower than late-time: imitator-dominated; pure logistic missed nuance",
        "if K is reached too quickly: market is smaller than estimated; saturation arrived early",
    ],
)


RLC_CIRCUIT = Prior(
    id="rlc_circuit",
    display_name="RLC series circuit (electrical)",
    domain="industrial",
    citation="Kirchhoff's voltage law applied to R-L-C series",
    structural_form="L · q'' + R · q' + (1/C) · q = 0",
    matches_runner_form="damped_oscillator",  # m·y'' + c·y' + k·y = 0  with m=L, c=R, k=1/C
    description=(
        "Charge oscillation in a resistor-inductor-capacitor series circuit "
        "with no source. Mathematically identical to a mechanical "
        "mass-spring-damper. Used for oscillator design, antenna tuning, "
        "filter analysis, transient response in power electronics. "
        "Distinguished from mechanical oscillator by domain context."
    ),
    free_parameters={
        "c": {
            "name": "resistance R (damping)",
            "range": [0.0, 1e6],
            "typical": None,
            "interpretation": "ohms; underdamped if R²<4L/C",
        },
        "k": {
            "name": "1/(L·C) — resonant frequency squared (loosely)",
            "range": [1e-12, 1e12],
            "typical": None,
            "interpretation": "ω₀² = 1/(LC) for the natural frequency",
        },
    },
    sign_constraints={"c": "non-negative", "k": "non-negative"},
    identifiability_conditions=[
        "linear circuit (no diodes, no saturating cores)",
        "passive components only (no active gain)",
        "sample rate ≥ 10× the resonant frequency",
    ],
    falsification_conditions=[
        "if c < 0: circuit is amplifying (active component present)",
        "if amplitude grows: the system has a positive-feedback loop (oscillator)",
        "if frequency drifts during observation: components are nonlinear or aging",
    ],
)


FIRST_ORDER_PHARMACOKINETICS = Prior(
    id="pharmacokinetics_1st_order",
    display_name="First-order drug elimination (pharmacokinetics)",
    domain="research",
    citation="Wagner, J. Pharm. Sci. (1971); textbook in any clinical pharmacology course",
    structural_form="dC/dt = -k_e · C    →    C(t) = C₀ · exp(-k_e · t)",
    matches_runner_form="linear_ode",
    description=(
        "Single-compartment model: drug concentration in blood decreases at "
        "a rate proportional to current concentration. Used for half-life "
        "calculations, dosing intervals, drug clearance modeling. The same "
        "math as radioactive decay, applied to pharmacokinetics."
    ),
    free_parameters={
        "k_elimination": {
            "name": "elimination rate constant",
            "range": [1e-6, 5.0],
            "typical": 0.1,
            "interpretation": "1/hours typically; t_½ = ln(2)/k_e",
        },
    },
    sign_constraints={"a": "negative", "b": "near-zero"},
    identifiability_conditions=[
        "single-compartment kinetics (no multi-phase distribution)",
        "no source term during observation (drug not being re-administered)",
        "linear kinetics (no saturable enzymes; not Michaelis-Menten regime)",
    ],
    falsification_conditions=[
        "if a > 0: drug is accumulating, not eliminating — wrong setup",
        "if biphasic decline observed: two-compartment model needed",
        "if non-linear at high doses: zero-order (Michaelis-Menten) regime — different prior",
    ],
)


RC_CIRCUIT_DISCHARGE = Prior(
    id="rc_circuit",
    display_name="RC circuit discharge",
    domain="industrial",
    citation="Standard EE textbook; Sedra & Smith, Microelectronic Circuits",
    structural_form="V(t) = V₀ · exp(-t / (R·C))",
    matches_runner_form="linear_ode",
    description=(
        "Capacitor discharging through a resistor. Voltage decays exponentially "
        "with time constant τ = RC. Used for filter design, timing circuits, "
        "EMI suppression, sensor signal conditioning. Universal in electronics."
    ),
    free_parameters={
        "tau": {
            "name": "time constant τ = R·C",
            "range": [1e-9, 1e6],
            "typical": 0.001,
            "interpretation": "seconds; voltage falls to 37% of V₀ at t = τ",
        },
    },
    sign_constraints={"a": "negative", "b": "near-zero"},
    identifiability_conditions=[
        "no source (battery / external voltage) during observation",
        "linear circuit (no saturating components)",
        "constant temperature (R doesn't drift)",
    ],
    falsification_conditions=[
        "if a > 0: capacitor is being charged, not discharging",
        "if non-exponential: nonlinear element present (diode, varistor)",
        "if oscillation: there's an inductor too — RLC, not RC",
    ],
)


PENDULUM_SMALL_ANGLE = Prior(
    id="pendulum_small_angle",
    display_name="Pendulum (small-angle approximation)",
    domain="research",
    citation="Galileo (1602); Huygens, Horologium Oscillatorium (1673)",
    structural_form="θ'' + (g/L) · θ = 0    →    θ(t) = A · cos(ω·t + φ),  ω = √(g/L)",
    matches_runner_form="damped_oscillator",  # special case c ≈ 0
    description=(
        "Simple gravitational pendulum under small-angle (sin θ ≈ θ) "
        "approximation. Period depends only on length and gravity, not "
        "amplitude. Foundational mechanical oscillator. Aurora matches when "
        "fitted damping c ≈ 0, indicating an undamped oscillator."
    ),
    free_parameters={
        "k": {
            "name": "ω² = g/L (squared natural frequency)",
            "range": [1e-9, 1e6],
            "typical": 9.81,
            "interpretation": "1/s²; on Earth, k ≈ 9.81/L when L is in meters",
        },
    },
    sign_constraints={"c": "non-negative", "k": "positive"},
    identifiability_conditions=[
        "small angles only (θ < ~10° for the linear approximation)",
        "rigid massless rod / inextensible string",
        "no air resistance (c = 0)",
        "constant gravity",
    ],
    falsification_conditions=[
        "if c is meaningfully positive: damping present (air resistance, friction)",
        "if amplitude affects period: large-angle regime (full nonlinear pendulum)",
        "if k ≤ 0: not a restoring force; not a pendulum",
    ],
)


LC_CIRCUIT_OSCILLATION = Prior(
    id="lc_circuit",
    display_name="LC circuit oscillation (undamped)",
    domain="industrial",
    citation="Hertz (1888); standard EE: Pozar, Microwave Engineering",
    structural_form="L·q'' + (1/C)·q = 0    →    ω₀ = 1/√(L·C)",
    matches_runner_form="damped_oscillator",  # special case c ≈ 0
    description=(
        "Charge oscillates between an inductor and capacitor with no "
        "resistive losses. Energy alternates between magnetic field of L "
        "and electric field of C. Used for resonant circuits, oscillator "
        "design, antenna tuning, RF filters. Distinguished from RLC by "
        "fitted damping c ≈ 0."
    ),
    free_parameters={
        "k": {
            "name": "ω₀² = 1/(L·C) (resonant frequency squared)",
            "range": [1e-9, 1e18],
            "typical": None,
            "interpretation": "1/s²; e.g. for L=1H, C=1μF, ω₀² = 10⁶",
        },
    },
    sign_constraints={"c": "near-zero", "k": "positive"},
    identifiability_conditions=[
        "ideal lossless components (no resistance)",
        "no external driving signal during observation",
        "linear regime (no core saturation, no breakdown)",
    ],
    falsification_conditions=[
        "if c > 0 meaningfully: there's resistance — RLC, not pure LC",
        "if amplitude grows: there's an active element injecting energy",
        "if k ≤ 0: not oscillatory; circuit is misidentified",
    ],
)


COMPOUND_INTEREST = Prior(
    id="compound_interest",
    display_name="Continuously compounded interest",
    domain="finance",
    citation="Bernoulli (1683); modern finance — Hull, Options Futures and Other Derivatives",
    structural_form="dV/dt = r · V    →    V(t) = V₀ · exp(r · t)",
    matches_runner_form="linear_ode",
    description=(
        "Continuously compounded interest / continuous growth at rate r. "
        "Used for risk-free asset modeling, present-value calculations, "
        "Black-Scholes derivations. Mathematically identical to exponential "
        "growth, distinguished by financial domain."
    ),
    free_parameters={
        "r": {
            "name": "continuously-compounded annual rate",
            "range": [-0.5, 1.0],
            "typical": 0.03,
            "interpretation": "annualized rate; e.g. 0.03 = 3%/year continuously",
        },
    },
    sign_constraints={"b": "near-zero"},  # no constraint on a; can be ±
    identifiability_conditions=[
        "no contributions / withdrawals during observation",
        "constant rate (no rate changes)",
        "no fees, taxes, or transaction costs",
    ],
    falsification_conditions=[
        "if step-changes in growth rate: a regime-switching model is better",
        "if rate compounds discretely (annually/monthly): use the discrete formula",
        "if observed rate matches a known macro rate (Fed funds, LIBOR): verify against external data",
    ],
)


# ============================================================
# v2.0 priors — match the new algebraic / closed-form physics candidates
# ============================================================

STEFAN_BOLTZMANN = Prior(
    id="stefan_boltzmann",
    display_name="Stefan-Boltzmann law",
    domain="thermal",
    citation="Stefan, Sitzungsber. Akad. Wiss. Wien (1879); Boltzmann (1884)",
    structural_form="j* = σ · ε · T⁴",
    matches_runner_form="power_law",
    description=(
        "Total radiant flux from a black/grey body scales with the fourth power "
        "of absolute temperature. Used for stellar luminosity, planetary energy "
        "balance, infrared imaging, furnace design. The fitted exponent should "
        "be approximately 4."
    ),
    free_parameters={
        "exponent": {
            "name": "power-law exponent b in y = a·t^b",
            "range": [3.5, 4.5],
            "typical": 4.0,
            "interpretation": "must be ≈ 4 for Stefan-Boltzmann radiation",
        },
    },
    sign_constraints={"a": "non-negative"},
    identifiability_conditions=[
        "the independent variable is absolute temperature (Kelvin)",
        "near black-body emission (emissivity ε ≈ 1) or known ε",
        "no convective / conductive heat transfer dominating",
    ],
    falsification_conditions=[
        "if exponent is far from 4: not Stefan-Boltzmann (could be Newton cooling instead)",
        "if T is in Celsius (not Kelvin): coefficient won't match the physical σε",
    ],
)


KEPLER_THIRD_LAW = Prior(
    id="kepler_third",
    display_name="Kepler's third law (orbital period)",
    domain="research",
    citation="Kepler, Harmonices Mundi (1619)",
    structural_form="P² ∝ a³  →  P = k · a^(3/2)",
    matches_runner_form="power_law",
    description=(
        "Orbital period scales with the 3/2 power of semi-major axis. Used for "
        "exoplanet detection, satellite orbit design, double-star characterization. "
        "Fitted exponent should be ≈ 1.5."
    ),
    free_parameters={
        "exponent": {
            "name": "power-law exponent",
            "range": [1.3, 1.7],
            "typical": 1.5,
            "interpretation": "must be ≈ 3/2 for Keplerian orbits",
        },
    },
    sign_constraints={"a": "positive"},
    identifiability_conditions=[
        "the independent variable is orbital semi-major axis (or proxy)",
        "two-body approximation holds (negligible perturbations)",
        "consistent units (e.g. AU + years for solar system)",
    ],
    falsification_conditions=[
        "exponent ≠ 1.5: Keplerian assumption violated, perhaps relativistic or N-body effects",
    ],
)


KLEIBERS_LAW = Prior(
    id="kleibers_law",
    display_name="Kleiber's law (metabolic scaling)",
    domain="bio",
    citation="Kleiber, Hilgardia (1932)",
    structural_form="metabolic_rate = a · mass^(3/4)",
    matches_runner_form="power_law",
    description=(
        "Across animal species, basal metabolic rate scales with the 3/4 power "
        "of body mass. Used in physiology, ecology, allometric scaling of "
        "drug doses. Fitted exponent should be ≈ 0.75."
    ),
    free_parameters={
        "exponent": {
            "name": "allometric exponent",
            "range": [0.6, 0.9],
            "typical": 0.75,
            "interpretation": "must be ≈ 3/4 for Kleiber scaling",
        },
    },
    sign_constraints={"a": "positive"},
    identifiability_conditions=[
        "across-species comparison (within a single species the law often differs)",
        "basal / resting state (not active metabolism)",
    ],
    falsification_conditions=[
        "exponent far from 3/4: alternative scaling theories (e.g. surface-area = 2/3 exponent)",
    ],
)


MICHAELIS_MENTEN_KINETICS = Prior(
    id="michaelis_menten",
    display_name="Michaelis-Menten enzyme kinetics",
    domain="bio",
    citation="Michaelis & Menten, Biochem. Z. (1913)",
    structural_form="v = V_max · [S] / (K_M + [S])",
    matches_runner_form="saturation",
    description=(
        "Enzyme reaction rate as a function of substrate concentration. Used "
        "throughout biochemistry, drug-receptor binding (sigmoidal binding "
        "curves), and ad-spend ROI saturation models. The fitted V_max is the "
        "asymptotic max rate; K_M is the substrate concentration at half-V_max."
    ),
    free_parameters={
        "Vmax": {
            "name": "maximum reaction rate",
            "range": [1e-9, 1e9],
            "typical": None,
            "interpretation": "asymptotic rate as substrate → ∞",
        },
        "K": {
            "name": "Michaelis constant (substrate at half-Vmax)",
            "range": [1e-9, 1e9],
            "typical": None,
            "interpretation": "substrate concentration where v = V_max/2",
        },
    },
    sign_constraints={"Vmax": "positive", "K": "positive"},
    identifiability_conditions=[
        "single-substrate, single-enzyme system",
        "no allosteric / cooperative effects (those would need Hill equation, n>1)",
        "data spans both unsaturated and saturated regimes",
    ],
    falsification_conditions=[
        "if curve is sigmoidal rather than hyperbolic: cooperativity present (Hill equation)",
        "if rate decreases at high [S]: substrate inhibition",
    ],
)


SIGMOID_ADOPTION = Prior(
    id="sigmoid_adoption",
    display_name="Sigmoid adoption / dose-response",
    domain="finance",
    citation="Hill, J. Physiol. (1910); Rogers, Diffusion of Innovations (1962)",
    structural_form="y = L / (1 + exp(-k·(t - t₀)))",
    matches_runner_form="sigmoid",
    description=(
        "Closed-form S-curve. Used for technology adoption, viral spread, "
        "dose-response curves in pharmacology, NPS-style satisfaction "
        "thresholds. Distinguished from Verhulst logistic (ODE form) by being "
        "the analytical solution; both should fit S-curve data."
    ),
    free_parameters={
        "L": {
            "name": "asymptotic ceiling (max y)",
            "range": [1e-9, 1e15],
            "typical": None,
            "interpretation": "the value y approaches as t → ∞",
        },
        "k": {
            "name": "steepness",
            "range": [1e-9, 1e6],
            "typical": 1.0,
            "interpretation": "rate of change at the inflection point",
        },
    },
    sign_constraints={"L": "positive", "k": "positive"},
    identifiability_conditions=[
        "data spans the inflection point (otherwise k is unidentifiable)",
        "single S-curve (no multiple cohorts mixed)",
        "stable ceiling (no late-stage growth resumption)",
    ],
    falsification_conditions=[
        "if k ≤ 0: not adoption — decay direction",
        "if y exceeds L consistently: ceiling mis-estimated",
    ],
)


BALLISTIC_MOTION = Prior(
    id="ballistic_motion",
    display_name="Ballistic / free-fall motion (constant gravity)",
    domain="industrial",
    citation="Galileo, Two New Sciences (1638); Newton's mechanics",
    structural_form="h(t) = h₀ + v₀·t - ½·g·t²",
    matches_runner_form="polynomial_2",
    description=(
        "Vertical position vs time under constant gravity, no air resistance. "
        "Used for projectile motion, drop tests, vehicle stopping distance "
        "(decelerating), simple beam mechanics. Fitted coefficient ``a`` should "
        "be approximately -g/2 ≈ -4.905 m/s² for Earth gravity."
    ),
    free_parameters={
        "a": {
            "name": "quadratic coefficient (≈ -g/2)",
            "range": [-100.0, 100.0],
            "typical": -4.905,
            "interpretation": "for Earth gravity, ≈ -4.905 m/s²; positive for upward-curving",
        },
        "b": {
            "name": "linear coefficient (initial velocity v₀)",
            "range": [-1e6, 1e6],
            "typical": None,
            "interpretation": "initial velocity in the chosen units",
        },
    },
    sign_constraints={},  # no constraint — both directions valid (rising/falling)
    identifiability_conditions=[
        "negligible air resistance",
        "constant gravitational field over the trajectory",
        "1D motion (or projection onto vertical axis)",
    ],
    falsification_conditions=[
        "non-quadratic curvature: drag is significant — different model needed",
        "|a| far from 4.905 (in m/s²): different gravity (other planets) or non-vertical projection",
    ],
)


# ============================================================
# Coupled-system priors (multi-target ODE families)
# ============================================================

LOTKA_VOLTERRA_PRIOR = Prior(
    id="lotka_volterra",
    display_name="Lotka–Volterra predator–prey",
    domain="bio",
    citation="Lotka (1925); Volterra (1926). Variations of the struggle for existence.",
    structural_form="dx/dt = α·x − β·x·y; dy/dt = −γ·y + δ·x·y",
    matches_runner_form="lotka_volterra",
    description=(
        "Classic 2-species ecology. x = prey, y = predator. The cyclic phase "
        "portrait (no fixed equilibrium except at origin) is the canonical "
        "predator-prey signature."
    ),
    free_parameters={
        "alpha": {"name": "prey growth rate", "range": [1e-6, 10.0], "typical": 1.0,
                  "interpretation": "intrinsic growth of x in absence of y"},
        "beta": {"name": "predation rate", "range": [1e-9, 10.0], "typical": 0.1,
                 "interpretation": "rate of prey loss per encounter"},
        "gamma": {"name": "predator death rate", "range": [1e-6, 10.0], "typical": 1.5,
                  "interpretation": "intrinsic death of y in absence of x"},
        "delta": {"name": "predator efficiency", "range": [1e-9, 10.0], "typical": 0.075,
                  "interpretation": "predator gain per encounter"},
    },
    sign_constraints={"alpha": "positive", "beta": "positive",
                      "gamma": "positive", "delta": "positive"},
    identifiability_conditions=[
        "two clearly oscillating populations",
        "phase relationship: predator peaks lag prey peaks",
    ],
    falsification_conditions=[
        "negative α/β/γ/δ → not a real predator-prey system",
        "no oscillation in phase space → linear coupled or competitive better",
    ],
)


SIR_EPIDEMIC = Prior(
    id="sir_epidemic",
    display_name="SIR epidemiological model",
    domain="bio",
    citation="Kermack & McKendrick (1927). Mathematical theory of epidemics.",
    structural_form="dS/dt = −β·S·I/N; dI/dt = β·S·I/N − γ·I; dR/dt = γ·I",
    matches_runner_form="sir",
    description=(
        "Compartmental epidemiological model. β is the contact rate, γ is the "
        "recovery rate, R₀ = β/γ is the basic reproduction number — if R₀ > 1 "
        "an outbreak grows; if R₀ < 1 it dies out."
    ),
    free_parameters={
        "beta": {"name": "contact / transmission rate", "range": [1e-6, 5.0], "typical": 0.3,
                 "interpretation": "expected new infections per S·I/N per day"},
        "gamma": {"name": "recovery rate", "range": [1e-6, 5.0], "typical": 0.1,
                  "interpretation": "1/expected infectious period"},
        "R0": {"name": "basic reproduction number", "range": [0.1, 30.0], "typical": 3.0,
               "interpretation": "β/γ — outbreak threshold at 1.0"},
    },
    sign_constraints={"beta": "positive", "gamma": "positive"},
    identifiability_conditions=[
        "S + I + R ≈ N (constant population)",
        "I peaks then decays; cumulative R monotonically increases",
    ],
    falsification_conditions=[
        "negative β/γ → not a real epidemic",
        "I keeps oscillating → SIRS / endemic model better",
    ],
)


COMPETITIVE_LV = Prior(
    id="competitive_lv",
    display_name="Competitive Lotka–Volterra (two species)",
    domain="bio",
    citation="Gause (1934); Volterra (1926).",
    structural_form="dx/dt = r₁·x·(1 − (x + a·y)/K₁); dy/dt = r₂·y·(1 − (y + b·x)/K₂)",
    matches_runner_form="competitive_lv",
    description=(
        "Two species sharing a finite resource. Fixed points and competitive "
        "exclusion principle live here. Used in ecology, market-share models, "
        "epidemiology of competing strains."
    ),
    free_parameters={
        "r1": {"name": "species 1 growth rate", "range": [1e-6, 10.0], "typical": 0.5},
        "K1": {"name": "species 1 carrying capacity", "range": [1.0, 1e6], "typical": 100.0},
        "a":  {"name": "competition coefficient (y on x)",
                "range": [0.0, 10.0], "typical": 0.4},
        "r2": {"name": "species 2 growth rate", "range": [1e-6, 10.0], "typical": 0.3},
        "K2": {"name": "species 2 carrying capacity", "range": [1.0, 1e6], "typical": 80.0},
        "b":  {"name": "competition coefficient (x on y)",
                "range": [0.0, 10.0], "typical": 0.6},
    },
    sign_constraints={"r1": "positive", "r2": "positive"},
    identifiability_conditions=[
        "Both species reach plateaus (vs LV which oscillates)",
        "Mutual influence: removing one boosts the other",
    ],
    falsification_conditions=[
        "Sustained oscillations → predator-prey LV better",
        "Negative growth rates → not a real competition system",
    ],
)


LINEAR_COUPLED_2D = Prior(
    id="linear_coupled_2d",
    display_name="Linear 2D coupled ODE",
    domain="industrial",
    citation="Strogatz, Nonlinear Dynamics and Chaos, Ch. 5",
    structural_form="dx/dt = a₁₁·x + a₁₂·y; dy/dt = a₂₁·x + a₂₂·y",
    matches_runner_form="linear_coupled_2d",
    description=(
        "Generic 2D linear ODE — recovers harmonic oscillation in phase space, "
        "two-tank diffusion, simple population sharing. The eigenvalues of the "
        "coefficient matrix tell you whether the system spirals, decays, "
        "or grows exponentially."
    ),
    free_parameters={
        "a11": {"name": "self-influence x", "range": [-100.0, 100.0], "typical": 0.0},
        "a12": {"name": "y → x coupling", "range": [-100.0, 100.0], "typical": -1.0},
        "a21": {"name": "x → y coupling", "range": [-100.0, 100.0], "typical": 1.0},
        "a22": {"name": "self-influence y", "range": [-100.0, 100.0], "typical": 0.0},
    },
    sign_constraints={},
    identifiability_conditions=[
        "Linear phase portrait (no quadratic / cubic features)",
        "Eigenvalues meaningful: imaginary → rotation, negative real → decay",
    ],
    falsification_conditions=[
        "Limit cycle / nonlinear features → use LV or competitive LV",
    ],
)


# ============================================================
# PDE priors (spatial-temporal field equations)
# ============================================================

HEAT_EQUATION_PRIOR = Prior(
    id="heat_equation",
    display_name="Heat / diffusion equation",
    domain="thermal",
    citation="Fourier (1822); Fick (1855).",
    structural_form="∂u/∂t = α · ∂²u/∂x²",
    matches_runner_form="heat_equation",
    description=(
        "First-order in time, second-order in space. α is thermal diffusivity "
        "(or chemical diffusivity). Spreading without transport. Recovers heat "
        "conduction, ink-in-water, neutron transport, capacitor charging."
    ),
    free_parameters={
        "alpha": {"name": "diffusivity", "range": [1e-9, 1e6], "typical": 0.1,
                  "interpretation": "[length²/time] — how fast the field spreads"},
    },
    sign_constraints={"alpha": "positive"},
    identifiability_conditions=[
        "field smooths out monotonically (no oscillations)",
        "high spatial frequencies decay faster than low",
    ],
    falsification_conditions=[
        "α < 0 → unstable / anti-diffusion (different physics)",
        "wave-like propagation present → use wave_equation instead",
    ],
)


WAVE_EQUATION_PRIOR = Prior(
    id="wave_equation",
    display_name="Wave equation",
    domain="thermal",   # cross-domain — using thermal as the closest available bucket
    citation="d'Alembert (1747); Maxwell (1861).",
    structural_form="∂²u/∂t² = c² · ∂²u/∂x²",
    matches_runner_form="wave_equation",
    description=(
        "Second-order in both space and time. c is the wave speed. Recovers "
        "string vibration, sound, electromagnetic waves, surface waves on water. "
        "Distinguishes from heat by having c² > 0 (oscillatory, not diffusive)."
    ),
    free_parameters={
        "c": {"name": "wave speed", "range": [1e-9, 3e8], "typical": 343.0,
              "interpretation": "[length/time] — propagation speed; ≈343 m/s for sound in air"},
    },
    sign_constraints={},  # c can be reported as |c|; sign isn't physical
    identifiability_conditions=[
        "second derivatives in time and space are proportional",
        "field oscillates / propagates without amplitude decay",
    ],
    falsification_conditions=[
        "c² < 0 (anti-wave) → not a real wave equation",
        "monotonic spreading → use heat_equation",
    ],
)


ADVECTION_EQUATION_PRIOR = Prior(
    id="advection_equation",
    display_name="Linear advection equation",
    domain="enviro",
    citation="Classical fluid mechanics (Euler's equations, simplified).",
    structural_form="∂u/∂t + c · ∂u/∂x = 0",
    matches_runner_form="advection_equation",
    description=(
        "Pure transport at constant speed c. The field travels without "
        "changing shape: u(x, t) = f(x − c·t). Recovers contamination plumes "
        "in flowing water/air, traffic flow at low density, kinematic wave "
        "models in hydrology."
    ),
    free_parameters={
        "c": {"name": "advection velocity", "range": [-1e6, 1e6], "typical": 1.0,
              "interpretation": "[length/time] — sign indicates direction of transport"},
    },
    sign_constraints={},  # negative c is valid (transport in -x direction)
    identifiability_conditions=[
        "shape of u preserved over time, only translated",
        "first derivatives in time and space are proportional with opposite sign",
    ],
    falsification_conditions=[
        "shape distorts (diffusion) → use heat_equation",
        "amplitude decays → mixed advection-diffusion",
    ],
)


_BUILTIN_PRIORS: Tuple[Prior, ...] = (
    # ---- linear_ode form (7 priors) ----
    NEWTON_COOLING,
    EXPONENTIAL_DECAY_GROWTH,
    RADIOACTIVE_DECAY,
    BEER_LAMBERT_LAW,
    FIRST_ORDER_PHARMACOKINETICS,
    RC_CIRCUIT_DISCHARGE,
    COMPOUND_INTEREST,
    # ---- logistic form (3 priors) ----
    VERHULST_LOGISTIC,
    GOMPERTZ_GROWTH,
    BASS_DIFFUSION,
    # ---- damped_oscillator form (4 priors) ----
    DAMPED_HARMONIC_OSCILLATOR,
    RLC_CIRCUIT,
    PENDULUM_SMALL_ANGLE,
    LC_CIRCUIT_OSCILLATION,
    # ---- v2.0 algebraic forms (6 priors) ----
    STEFAN_BOLTZMANN,                 # power_law (b≈4)
    KEPLER_THIRD_LAW,                 # power_law (b≈1.5)
    KLEIBERS_LAW,                     # power_law (b≈0.75)
    MICHAELIS_MENTEN_KINETICS,        # saturation
    SIGMOID_ADOPTION,                 # sigmoid
    BALLISTIC_MOTION,                 # polynomial_2
    # ---- v2.0 coupled multi-target families (4 priors) ----
    LOTKA_VOLTERRA_PRIOR,             # lotka_volterra
    SIR_EPIDEMIC,                     # sir
    COMPETITIVE_LV,                   # competitive_lv
    LINEAR_COUPLED_2D,                # linear_coupled_2d
    # ---- v2.0 PDE families (3 priors) ----
    HEAT_EQUATION_PRIOR,              # heat_equation
    WAVE_EQUATION_PRIOR,              # wave_equation
    ADVECTION_EQUATION_PRIOR,         # advection_equation
)


def list_priors() -> List[Prior]:
    """Return all loaded priors. Cheap; the catalogue is small and immutable."""
    return list(_BUILTIN_PRIORS)


def get_prior(prior_id: str) -> Optional[Prior]:
    for p in _BUILTIN_PRIORS:
        if p.id == prior_id:
            return p
    return None


def list_priors_for_runner_form(form: str) -> List[Prior]:
    """All priors whose ``matches_runner_form`` equals the given runner form."""
    return [p for p in _BUILTIN_PRIORS if p.matches_runner_form == form]
