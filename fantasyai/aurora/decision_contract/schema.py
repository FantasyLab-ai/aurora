"""
Decision contract schema + 21 default contracts (3 per template × 7
templates).

Why
---
Aurora's findings are computed in column-language ("temperature exceeds
threshold at row 1248"). To turn them into action, the *user's intent*
matters: a hospital's intent ("don't miss sepsis") suppresses different
findings than a researcher's ("explore the dynamics"). The decision
contract carries that intent into the renderer so the surface text
reflects the user's actual decision frame.

Schema
------
The contract is intentionally small (5 fields) so it remains intelligible:

  - id               : "thermal:avoid_breach", "research:explore_dynamics", ...
  - template_id      : matches the system_model template id
                       ({thermal, finance, enviro, bio, industrial, research,
                         seismology})
  - contract_class   : the abstract class — see ContractClass below
  - objective        : 1-line phrase the user can read aloud
  - constraints      : hard limits the recommendation engine MUST respect
  - available_actions: actions the user can actually take (chips)
  - cost_asymmetry   : which side of the trade-off is more expensive
                       (false_positive_costly, false_negative_costly,
                        symmetric)
  - confidence_floor : findings below this confidence are suppressed
  - notes            : human description (shown in modal)
  - source           : where this contract came from (default | inferred |
                       user_typed | conversational)

Confidence × cost-asymmetry matrix
----------------------------------
A finding's surface treatment follows the 3×3 matrix:

                  | low conf | medium conf | high conf
    --------------+----------+-------------+-----------
    FP costly     | suppress | hedge       | render
    symmetric     | inspect  | render      | render
    FN costly     | render   | render      | render+amplify

  * suppress    — don't surface (still in EXPLAIN modal)
  * inspect     — render but in INSPECT-only zone (low priority)
  * hedge       — render with strong "if conditions hold" language
  * render      — normal rendering
  * render+amplify — bold + visual emphasis ("don't miss this")

This is the key behavior change: same artifacts → different surface text
under different contracts.

The 21 defaults
---------------
Each template ships 3 contract presets:
  - "{template}:explore"           — symmetric, low confidence_floor
  - "{template}:avoid_{badthing}"  — FN costly, high confidence_floor
  - "{template}:confirm"           — FP costly, very high confidence_floor

So the user can pick any of 21 starting points (or write their own).
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Enums-as-string-constants
# ---------------------------------------------------------------------------

class ContractClass:
    """The abstract intent class. The template + class together pick a
    rendering style."""
    EXPLORE = "explore"               # researcher mode — discovery
    AVOID_HARM = "avoid_harm"         # operations mode — never miss bad
    CONFIRM = "confirm"               # auditor mode — never claim wrong
    OPTIMIZE = "optimize"             # planning mode — best expected value
    DIAGNOSE = "diagnose"             # debug mode — root cause


class CostAsymmetry:
    """Which way the cost is asymmetric for this user."""
    FALSE_POSITIVE_COSTLY = "false_positive_costly"   # crying wolf hurts more
    FALSE_NEGATIVE_COSTLY = "false_negative_costly"   # missing it hurts more
    SYMMETRIC = "symmetric"


class ConfidenceBand:
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


def confidence_band(c: float) -> str:
    """Map a numeric confidence ∈ [0, 1] to a band."""
    try:
        c = float(c)
    except Exception:
        return ConfidenceBand.LOW
    if c >= 0.7:
        return ConfidenceBand.HIGH
    if c >= 0.4:
        return ConfidenceBand.MEDIUM
    return ConfidenceBand.LOW


# ---------------------------------------------------------------------------
# Decision contract
# ---------------------------------------------------------------------------

@dataclass
class DecisionContract:
    """A user-intent contract that the narrative engine consumes."""
    id: str
    template_id: str
    contract_class: str
    objective: str
    constraints: List[str] = field(default_factory=list)
    available_actions: List[str] = field(default_factory=list)
    cost_asymmetry: str = CostAsymmetry.SYMMETRIC
    confidence_floor: float = 0.4
    notes: str = ""
    source: str = "default"            # default | inferred | user_typed | conversational

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Confidence × cost-asymmetry suppression matrix
# ---------------------------------------------------------------------------

# Output values: "suppress" | "inspect" | "hedge" | "render" | "amplify"
SUPPRESSION_MATRIX: Dict[str, Dict[str, str]] = {
    CostAsymmetry.FALSE_POSITIVE_COSTLY: {
        ConfidenceBand.LOW:    "suppress",
        ConfidenceBand.MEDIUM: "hedge",
        ConfidenceBand.HIGH:   "render",
    },
    CostAsymmetry.SYMMETRIC: {
        ConfidenceBand.LOW:    "inspect",
        ConfidenceBand.MEDIUM: "render",
        ConfidenceBand.HIGH:   "render",
    },
    CostAsymmetry.FALSE_NEGATIVE_COSTLY: {
        ConfidenceBand.LOW:    "render",
        ConfidenceBand.MEDIUM: "render",
        ConfidenceBand.HIGH:   "amplify",
    },
}


def suppression_decision(confidence: float,
                         cost_asymmetry: str) -> str:
    """Return one of: suppress | inspect | hedge | render | amplify."""
    band = confidence_band(confidence)
    asym = cost_asymmetry if cost_asymmetry in SUPPRESSION_MATRIX else \
        CostAsymmetry.SYMMETRIC
    return SUPPRESSION_MATRIX[asym][band]


# ---------------------------------------------------------------------------
# 21 default contracts (3 × 7 templates)
# ---------------------------------------------------------------------------

_DEFAULTS: List[DecisionContract] = [
    # ---- thermal (3) ----
    DecisionContract(
        id="thermal:explore",
        template_id="thermal",
        contract_class=ContractClass.EXPLORE,
        objective="Understand how the system thermally behaves",
        constraints=["no operational change yet — observation only"],
        available_actions=["mark for review", "compare regimes", "request literature"],
        cost_asymmetry=CostAsymmetry.SYMMETRIC,
        confidence_floor=0.30,
        notes="Researcher mode. Surface anything mathematically interesting.",
    ),
    DecisionContract(
        id="thermal:avoid_overheat",
        template_id="thermal",
        contract_class=ContractClass.AVOID_HARM,
        objective="Avoid thermal breach of operational limits",
        constraints=["no destructive load tests", "alert team for any breach risk"],
        available_actions=["derate load", "schedule maintenance",
                            "notify operator on call"],
        cost_asymmetry=CostAsymmetry.FALSE_NEGATIVE_COSTLY,
        confidence_floor=0.20,        # err on the side of "warn anyway"
        notes="Operations mode. Missing a breach is far worse than a false alarm.",
    ),
    DecisionContract(
        id="thermal:confirm_law",
        template_id="thermal",
        contract_class=ContractClass.CONFIRM,
        objective="Confirm system obeys a known thermal law",
        constraints=["only claim a match if statistically supported",
                     "preserve original-units presentation"],
        available_actions=["cite manual", "log confirmation", "export comparison"],
        cost_asymmetry=CostAsymmetry.FALSE_POSITIVE_COSTLY,
        confidence_floor=0.80,
        notes="Auditor mode. False claims of conformity are expensive.",
    ),

    # ---- finance (3) ----
    DecisionContract(
        id="finance:explore",
        template_id="finance",
        contract_class=ContractClass.EXPLORE,
        objective="Explore the structure of the financial series",
        constraints=["no trading actions taken from this view"],
        available_actions=["compare regimes", "rank motifs", "annotate event"],
        cost_asymmetry=CostAsymmetry.SYMMETRIC,
        confidence_floor=0.30,
        notes="Pre-decision exploration; risk is informational only.",
    ),
    DecisionContract(
        id="finance:avoid_loss",
        template_id="finance",
        contract_class=ContractClass.AVOID_HARM,
        objective="Reduce exposure to a regime change",
        constraints=["respect liquidity limits", "no leverage increase"],
        available_actions=["reduce position", "raise stop", "hedge"],
        cost_asymmetry=CostAsymmetry.FALSE_NEGATIVE_COSTLY,
        confidence_floor=0.25,
        notes="Risk-management mode. Missing a regime shift hurts more than overreacting.",
    ),
    DecisionContract(
        id="finance:confirm_alpha",
        template_id="finance",
        contract_class=ContractClass.CONFIRM,
        objective="Confirm a hypothesised alpha pattern is real",
        constraints=["replication across tiers required",
                     "out-of-sample test mandatory"],
        available_actions=["request replication", "log confirmation",
                            "export factor"],
        cost_asymmetry=CostAsymmetry.FALSE_POSITIVE_COSTLY,
        confidence_floor=0.85,
        notes="False confirmation of alpha is the worst outcome.",
    ),

    # ---- enviro (3) ----
    DecisionContract(
        id="enviro:explore",
        template_id="enviro",
        contract_class=ContractClass.EXPLORE,
        objective="Characterise the environmental dynamics",
        constraints=["non-invasive analysis only"],
        available_actions=["map regimes", "compare to baseline", "request more data"],
        cost_asymmetry=CostAsymmetry.SYMMETRIC,
        confidence_floor=0.30,
        notes="Research / monitoring mode.",
    ),
    DecisionContract(
        id="enviro:avoid_extreme_event",
        template_id="enviro",
        contract_class=ContractClass.AVOID_HARM,
        objective="Detect early signal of extreme events",
        constraints=["alert lead-time ≥ 1 cadence period",
                     "false alarms allowed up to documented rate"],
        available_actions=["raise alert", "trigger sampling burst",
                            "open investigation"],
        cost_asymmetry=CostAsymmetry.FALSE_NEGATIVE_COSTLY,
        confidence_floor=0.20,
        notes="Public-safety / hazard monitoring.",
    ),
    DecisionContract(
        id="enviro:confirm_trend",
        template_id="enviro",
        contract_class=ContractClass.CONFIRM,
        objective="Confirm a long-term environmental trend",
        constraints=["multi-tier replication", "method documented"],
        available_actions=["log finding", "export to report", "submit to peers"],
        cost_asymmetry=CostAsymmetry.FALSE_POSITIVE_COSTLY,
        confidence_floor=0.80,
        notes="Publication-grade confirmation.",
    ),

    # ---- bio (3) ----
    DecisionContract(
        id="bio:explore",
        template_id="bio",
        contract_class=ContractClass.EXPLORE,
        objective="Understand the biological signal landscape",
        constraints=["no clinical decision from this view"],
        available_actions=["compare cohorts", "rank motifs", "request gene annotation"],
        cost_asymmetry=CostAsymmetry.SYMMETRIC,
        confidence_floor=0.30,
        notes="Discovery / characterisation mode.",
    ),
    DecisionContract(
        id="bio:avoid_missed_diagnosis",
        template_id="bio",
        contract_class=ContractClass.AVOID_HARM,
        objective="Don't miss a clinical signal that warrants follow-up",
        constraints=["never replace clinician judgement",
                     "clearly mark inferred severity"],
        available_actions=["flag for review", "open patient record",
                            "request additional test"],
        cost_asymmetry=CostAsymmetry.FALSE_NEGATIVE_COSTLY,
        confidence_floor=0.20,
        notes="Clinical screening — undermissing is the worst outcome.",
    ),
    DecisionContract(
        id="bio:confirm_finding",
        template_id="bio",
        contract_class=ContractClass.CONFIRM,
        objective="Confirm a discovered biomarker / signature",
        constraints=["replication across cohorts",
                     "false discovery rate documented"],
        available_actions=["request validation cohort", "submit to registry",
                            "draft methods section"],
        cost_asymmetry=CostAsymmetry.FALSE_POSITIVE_COSTLY,
        confidence_floor=0.85,
        notes="Publication / clinical-claim grade evidence.",
    ),

    # ---- industrial (3) ----
    DecisionContract(
        id="industrial:explore",
        template_id="industrial",
        contract_class=ContractClass.EXPLORE,
        objective="Characterise machine / process behaviour",
        constraints=["no production change from this view"],
        available_actions=["compare lines", "rank patterns", "open log"],
        cost_asymmetry=CostAsymmetry.SYMMETRIC,
        confidence_floor=0.30,
        notes="Engineering exploration mode.",
    ),
    DecisionContract(
        id="industrial:avoid_downtime",
        template_id="industrial",
        contract_class=ContractClass.AVOID_HARM,
        objective="Prevent unplanned downtime / equipment failure",
        constraints=["respect maintenance window",
                     "no over-aggressive shutdowns"],
        available_actions=["schedule maintenance", "derate", "increase monitoring"],
        cost_asymmetry=CostAsymmetry.FALSE_NEGATIVE_COSTLY,
        confidence_floor=0.25,
        notes="Predictive maintenance — missed failure costs more than over-action.",
    ),
    DecisionContract(
        id="industrial:confirm_root_cause",
        template_id="industrial",
        contract_class=ContractClass.CONFIRM,
        objective="Confirm the root cause of an incident",
        constraints=["evidence chain complete",
                     "alternative hypotheses ruled out"],
        available_actions=["log RCA report", "implement fix",
                            "schedule audit"],
        cost_asymmetry=CostAsymmetry.FALSE_POSITIVE_COSTLY,
        confidence_floor=0.85,
        notes="Wrong RCA → wrong fix → recurrence. Confirm carefully.",
    ),

    # ---- research (3) ----
    DecisionContract(
        id="research:explore",
        template_id="research",
        contract_class=ContractClass.EXPLORE,
        objective="Discover novel structure in the data",
        constraints=["replicate before publishing",
                     "all priors documented"],
        available_actions=["save hypothesis", "request more data",
                            "compare to literature"],
        cost_asymmetry=CostAsymmetry.SYMMETRIC,
        confidence_floor=0.20,
        notes="Pure discovery mode — almost everything is interesting.",
    ),
    DecisionContract(
        id="research:avoid_p_hacking",
        template_id="research",
        contract_class=ContractClass.AVOID_HARM,
        objective="Avoid spurious findings from multiple comparisons",
        constraints=["FDR correction applied",
                     "all tested hypotheses recorded"],
        available_actions=["log multiple-comparison correction",
                            "request hold-out validation"],
        cost_asymmetry=CostAsymmetry.FALSE_POSITIVE_COSTLY,
        confidence_floor=0.70,
        notes="Statistical-rigor mode; protect against false positives.",
    ),
    DecisionContract(
        id="research:confirm_publication",
        template_id="research",
        contract_class=ContractClass.CONFIRM,
        objective="Reach publication-grade confidence in a finding",
        constraints=["pre-registered analysis",
                     "out-of-sample replication"],
        available_actions=["draft methods", "submit pre-registration",
                            "freeze analysis pipeline"],
        cost_asymmetry=CostAsymmetry.FALSE_POSITIVE_COSTLY,
        confidence_floor=0.90,
        notes="Maximal-rigor mode for journal submission.",
    ),

    # ---- seismology (3) — new template per Phase B brief ----
    DecisionContract(
        id="seismology:explore",
        template_id="seismology",
        contract_class=ContractClass.EXPLORE,
        objective="Characterise tremor / seismic activity",
        constraints=["non-public analysis"],
        available_actions=["compare epochs", "rank events", "annotate region"],
        cost_asymmetry=CostAsymmetry.SYMMETRIC,
        confidence_floor=0.30,
        notes="Geophysics exploration mode.",
    ),
    DecisionContract(
        id="seismology:avoid_missed_quake",
        template_id="seismology",
        contract_class=ContractClass.AVOID_HARM,
        objective="Don't miss a precursor to a damaging event",
        constraints=["alert lead-time ≥ 1 hour where possible",
                     "false alarms tracked but acceptable"],
        available_actions=["raise alert", "request additional sensors",
                            "notify regional authority"],
        cost_asymmetry=CostAsymmetry.FALSE_NEGATIVE_COSTLY,
        confidence_floor=0.15,
        notes="Hazard early-warning mode.",
    ),
    DecisionContract(
        id="seismology:confirm_event",
        template_id="seismology",
        contract_class=ContractClass.CONFIRM,
        objective="Confirm an event matches a known seismic class",
        constraints=["multi-station corroboration required",
                     "magnitude estimate with CI"],
        available_actions=["log event", "export to catalogue",
                            "notify affiliated network"],
        cost_asymmetry=CostAsymmetry.FALSE_POSITIVE_COSTLY,
        confidence_floor=0.85,
        notes="Catalogue / publication-grade event registration.",
    ),
]


_INDEX: Dict[str, DecisionContract] = {c.id: c for c in _DEFAULTS}


def list_default_contracts() -> List[DecisionContract]:
    """Return the 21 packaged defaults."""
    return list(_DEFAULTS)


def get_default_contract(contract_id: str) -> Optional[DecisionContract]:
    """Look up a default by id, e.g. ``thermal:avoid_overheat``."""
    return _INDEX.get(contract_id)


def suggest_contracts_for_template(template_id: str) -> List[DecisionContract]:
    """Return the 3 default contracts (explore / avoid / confirm) for a
    template — e.g. when the user opens a thermal run we surface the
    three thermal contracts as chips.
    """
    return [c for c in _DEFAULTS if c.template_id == template_id]


__all__ = [
    "DecisionContract",
    "ContractClass",
    "CostAsymmetry",
    "ConfidenceBand",
    "SUPPRESSION_MATRIX",
    "confidence_band",
    "suppression_decision",
    "list_default_contracts",
    "get_default_contract",
    "suggest_contracts_for_template",
]
