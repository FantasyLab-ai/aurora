"""
Domain modes — change Aurora's *render-time* defaults to feel built-for-me to
seven different audiences without re-running the analysis pipeline.

Same artifacts, different terminology + suggested-question priorities + which
priors get top billing in the physics tile. This is a pragmatic v1 — the
runner-side changes (FDR for research, log-returns for finance, etc.) are
deferred to a later sprint and would be triggered via a ``--domain`` flag
that flows into ``run_aurora_with_steps_1_4.py``.

Public API
----------

    list_domains()                     → list of domain ids ("general", "ops", ...)
    get_domain_config(domain_id)       → DomainConfig dataclass
    apply_domain_to_state(state, dom)  → mutates the state dict in place,
                                          rewriting terminology and reordering
                                          priors / suggestions per the domain.

Storage
-------
The user's selected domain lives in browser localStorage (frontend) and is
passed to ``GET /api/state?domain=ops`` for each refresh. No server-side
session state — Aurora is local-first and stateless across requests.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class DomainConfig:
    id: str
    display_name: str
    one_liner: str
    # Terminology overrides — re-skinning of finding titles + UI labels.
    # When a key matches a token in a finding title, it gets replaced.
    terminology: Dict[str, str] = field(default_factory=dict)
    # Which prior-domains to push to the front of physics-tile prior_matches.
    prior_focus: List[str] = field(default_factory=list)
    # Pre-canned questions seeded into the copilot when no run-specific suggestions yet.
    suggested_questions: List[str] = field(default_factory=list)
    # Brief domain-specific note shown as the overview narrative prefix.
    overview_prefix: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Built-in domains
# ---------------------------------------------------------------------------

GENERAL = DomainConfig(
    id="general",
    display_name="General",
    one_liner="Default analysis with no domain-specific defaults.",
    terminology={},
    prior_focus=[],
    suggested_questions=[],
    overview_prefix="",
)

ENVIRO = DomainConfig(
    id="enviro",
    display_name="Environmental",
    one_liner="Climate, atmospheric, hydrological, and oceanographic data.",
    terminology={
        "Anomaly": "Excursion",
        "anomaly": "excursion",
        "regime": "phase",
        "Regime": "Phase",
    },
    prior_focus=["thermal", "research", "enviro"],
    suggested_questions=[
        "Which seasonal cycles are present in the signal?",
        "How does the latest phase compare to the long-term baseline?",
        "What's the most extreme excursion in this dataset?",
    ],
    overview_prefix="Environmental data — seasonal patterns and regime phases interpreted under standard climatology assumptions.",
)

RESEARCH = DomainConfig(
    id="research",
    display_name="Research",
    one_liner="Scientific datasets — gene expression, clinical trials, biomarkers, lab measurements.",
    terminology={
        "Anomaly": "Outlier",
        "anomaly": "outlier",
        "Finding": "Result",
        "finding": "result",
        "regime": "phase",
        "Regime": "Phase",
    },
    prior_focus=["bio", "research"],
    suggested_questions=[
        "Which features are the strongest predictors of the outcome?",
        "Are the effect sizes robust under multiple-comparison correction?",
        "Does the discovered model match Michaelis-Menten or Hill-equation form?",
    ],
    overview_prefix="Research dataset — effect sizes are reported as point estimates; apply multiple-comparison correction before publication.",
)

OPS = DomainConfig(
    id="ops",
    display_name="Ops & Reliability",
    one_liner="Operational telemetry — latency, error rates, queue depths, SLO breaches.",
    terminology={
        "Anomaly": "Incident",
        "anomaly": "incident",
        "Regime": "Drift episode",
        "regime": "drift episode",
        "Finding": "Alert",
        "finding": "alert",
    },
    prior_focus=["industrial"],
    suggested_questions=[
        "Show me the SLO breaches in the last regime.",
        "Did latency drift correlate with any throughput change?",
        "Walk me through the root cause of the top incident.",
    ],
    overview_prefix="Operational telemetry — incidents and drift episodes flagged for on-call review.",
)

INDUSTRIAL = DomainConfig(
    id="industrial",
    display_name="Industrial",
    one_liner="Sensor data from manufacturing, energy, mechanical systems.",
    terminology={
        "Anomaly": "Fault candidate",
        "anomaly": "fault candidate",
        "Physical model fit": "Mechanism candidate",
        "physical model fit": "mechanism candidate",
        "Regime": "Operating regime",
        "regime": "operating regime",
    },
    prior_focus=["industrial", "thermal"],
    suggested_questions=[
        "Which sensor channel is the leading indicator of faults?",
        "How close is the fitted vibration model to a damped oscillator?",
        "Did the operating regime shift coincide with any maintenance event?",
    ],
    overview_prefix="Industrial sensor data — fault candidates and operating-regime shifts flagged for engineering review.",
)

FINANCE = DomainConfig(
    id="finance",
    display_name="Finance",
    one_liner="Market, transaction, and economic time-series.",
    terminology={
        "Anomaly": "Outlier event",
        "anomaly": "outlier event",
        "Regime": "Volatility regime",
        "regime": "volatility regime",
    },
    prior_focus=["finance"],
    suggested_questions=[
        "Compare drawdown depth across regimes.",
        "Are the residuals heavy-tailed?",
        "What's the conditional volatility profile?",
    ],
    overview_prefix="Financial time-series — log-returns recommended; volatility regimes interpreted under standard assumptions.",
)

MEDICAL = DomainConfig(
    id="medical",
    display_name="Medical",
    one_liner="Patient cohorts, biomarkers, clinical time series.",
    terminology={
        "Anomaly": "Clinical signal",
        "anomaly": "clinical signal",
        "Regime": "Patient regime",
        "regime": "patient regime",
    },
    prior_focus=["medical", "bio"],
    suggested_questions=[
        "Which biomarker is the leading indicator?",
        "Are there cohort-level regime shifts?",
        "Which patients deviate from the population baseline?",
    ],
    overview_prefix="Clinical / biomarker time-series — flagged for clinician review; not a diagnosis.",
)

SPORTS = DomainConfig(
    id="sports",
    display_name="Sports",
    one_liner="Player and team performance — trajectories, streaks, fatigue.",
    terminology={
        "Anomaly": "Outlier game",
        "anomaly": "outlier game",
        "Regime": "Form regime",
        "regime": "form regime",
        "Physical model fit": "Performance model fit",
    },
    prior_focus=["sports"],
    suggested_questions=[
        "Which players show fatigue accumulation across back-to-backs?",
        "What's the trajectory of the team's defensive efficiency?",
        "Are there matchup-dependent performance patterns?",
    ],
    overview_prefix="Player-game performance — trajectories and form regimes flagged for review.",
)

LOGISTICS = DomainConfig(
    id="logistics",
    display_name="Logistics",
    one_liner="Networks of nodes + flows — bottlenecks, cascades, buffer dynamics.",
    terminology={
        "Anomaly": "Exception",
        "anomaly": "exception",
        "Regime": "Operating mode",
        "regime": "operating mode",
        "Physical model fit": "Network signature",
    },
    prior_focus=["logistics"],
    suggested_questions=[
        "Where is the bottleneck emerging in the network?",
        "Which buffers are at risk of depletion?",
        "How would a delay at node X cascade downstream?",
    ],
    overview_prefix="Logistics network — bottleneck candidates and cascade signals flagged for ops review.",
)

ECONOMICS = DomainConfig(
    id="economics",
    display_name="Economics",
    one_liner="Macro indicators — GDP, employment, inflation, rates, sentiment.",
    terminology={
        "Anomaly": "Data surprise",
        "anomaly": "data surprise",
        "Regime": "Macro regime",
        "regime": "macro regime",
    },
    prior_focus=["economics", "finance"],
    suggested_questions=[
        "Are leading indicators pointing to a regime shift?",
        "Has the term structure inverted?",
        "Which indicators are diverging from the consensus?",
    ],
    overview_prefix="Macro indicators — regime transitions and term-structure signals flagged for review.",
)

CUSTOM = DomainConfig(
    id="custom",
    display_name="Custom",
    one_liner="No domain-specific defaults; user-driven configuration.",
    terminology={},
    prior_focus=[],
    suggested_questions=[],
    overview_prefix="",
)


_DOMAINS: Dict[str, DomainConfig] = {
    d.id: d for d in (
        GENERAL, ENVIRO, RESEARCH, OPS, INDUSTRIAL, FINANCE,
        MEDICAL, SPORTS, LOGISTICS, ECONOMICS,
        CUSTOM,
    )
}


def list_domains() -> List[Dict[str, Any]]:
    """Return all domain configs as plain dicts (for the API)."""
    return [d.to_dict() for d in _DOMAINS.values()]


def get_domain_config(domain_id: Optional[str]) -> DomainConfig:
    """Look up a domain by id; defaults to GENERAL on unknown / missing."""
    if not domain_id:
        return GENERAL
    return _DOMAINS.get(domain_id.lower(), GENERAL)


# ---------------------------------------------------------------------------
# Apply terminology overrides + prior-focus reordering to a state dict
# ---------------------------------------------------------------------------

def _retext(s: Optional[str], overrides: Dict[str, str]) -> Optional[str]:
    """Token-replace using the terminology overrides. Case-aware via dict keys."""
    if not s or not overrides:
        return s
    out = s
    # Apply longest matches first to avoid partial-overlap bugs.
    for key in sorted(overrides.keys(), key=len, reverse=True):
        out = out.replace(key, overrides[key])
    return out


def apply_domain_to_state(state: Dict[str, Any], domain_id: Optional[str]) -> Dict[str, Any]:
    """Mutate ``state`` in-place to apply a domain's terminology + reordering.

    Conservative: only renames *visible text fields* (titles, descriptions,
    suggestions, narrative). Numeric data is untouched.

    Returns the same state dict (for chaining).
    """
    cfg = get_domain_config(domain_id)
    if cfg.id == "general" or cfg.id == "custom":
        # Stamp the active domain so the frontend shows it, but don't rewrite anything.
        state["domain"] = {"id": cfg.id, "display_name": cfg.display_name,
                           "one_liner": cfg.one_liner}
        return state

    state["domain"] = {
        "id": cfg.id, "display_name": cfg.display_name,
        "one_liner": cfg.one_liner, "overview_prefix": cfg.overview_prefix,
    }

    # Findings: rewrite title + description.
    findings = state.get("findings") or []
    for f in findings:
        if isinstance(f, dict):
            f["title"] = _retext(f.get("title"), cfg.terminology)
            f["description"] = _retext(f.get("description"), cfg.terminology)

    # Anomalies: rewrite description.
    anoms = state.get("anomalies") or []
    for a in anoms:
        if isinstance(a, dict):
            a["description"] = _retext(a.get("description"), cfg.terminology)

    # Regimes: rewrite labels.
    regimes = state.get("regimes") or []
    for r in regimes:
        if isinstance(r, dict):
            r["label"] = _retext(r.get("label"), cfg.terminology)

    # Copilot suggestions: REPLACE if domain has its own canned set; otherwise leave.
    if cfg.suggested_questions:
        copilot = state.get("copilot") or {}
        if isinstance(copilot, dict):
            existing = copilot.get("suggestions") or []
            # Mix: dataset-specific suggestions first (they're already grounded),
            # domain canned questions second.
            merged = list(existing)[:2] + list(cfg.suggested_questions)
            copilot["suggestions"] = merged[:5]
            state["copilot"] = copilot

    # Physics: reorder prior_matches so domain-focus matches come first.
    # Within the focused subset, sort by the position of the match's domain in
    # prior_focus — so the user's most-relevant domain wins absolute first place.
    if cfg.prior_focus:
        physics = state.get("physics") or {}
        if isinstance(physics, dict):
            matches = physics.get("prior_matches") or []
            if matches:
                focus_rank = {dom: i for i, dom in enumerate(cfg.prior_focus)}
                focused = [m for m in matches if m.get("domain") in focus_rank]
                # Stable sort by focus position, preserving original order on ties.
                focused.sort(key=lambda m: focus_rank.get(m.get("domain"), 999))
                others = [m for m in matches if m.get("domain") not in focus_rank]
                physics["prior_matches"] = focused + others
                state["physics"] = physics

    # Overview narrative: prepend the domain prefix if any.
    if cfg.overview_prefix:
        ov = state.get("overview") or {}
        if isinstance(ov, dict):
            ov["domain_prefix"] = cfg.overview_prefix
            state["overview"] = ov

    return state
