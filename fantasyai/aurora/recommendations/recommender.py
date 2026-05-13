"""
Decision-aware recommendation builder.

A finding answers "what is going on." A recommendation answers "what should
I do about it." Aurora's identity demands the recommendation layer be
explicit about uncertainty: high-confidence → "do X"; medium → "do X if
Y, here's how to check"; low → "we don't have enough signal; here are the
measurements that would resolve it."

Architecture
------------

* ``ActionTemplate``  — a known action with predicted-outcome model + base rate
* ``Recommendation``  — a finding + selected action + structured outcome predictions
* ``build_recommendation`` — pure function: maps (finding, action_library) → Recommendation
* ``record_action_outcome`` — append-only ledger of observed outcomes; the
  base-rate calculator uses it for calibration
* ``list_actions`` — query the action library (built-in + user-extended)

The action library starts as a thin in-Python catalogue of conservative
templates and is extensible at runtime via ``add_action_template`` (or by
just appending to a YAML file later).
"""
from __future__ import annotations

import json
import math
import os
import time
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Any, Dict, List, Optional


def action_library_path() -> Path:
    override = os.environ.get("AURORA_ACTION_LIBRARY")
    if override:
        return Path(override).expanduser().resolve()
    return Path.home() / ".aurora" / "action_outcomes.jsonl"


# ---------------------------------------------------------------------------
# Action template
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ActionTemplate:
    id: str
    label: str
    description: str
    domain: str                        # thermal | finance | enviro | bio | industrial | general
    suggested_when: List[str]          # human-readable conditions
    expected_outcome_summary: str
    base_rate_success: float           # priors-only base rate ∈ [0, 1]
    relevant_finding_kinds: List[str] = field(default_factory=list)
    # ↑ {"anomaly", "regime_shift", "physics_match", "forecast_breach"}
    assumptions: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# Conservative built-in catalogue. Use this list as a starting point;
# user can grow it via ``add_action_template`` (in-process) or, later, YAML.
_DEFAULT_ACTIONS: List[ActionTemplate] = [
    ActionTemplate(
        id="investigate_anomaly",
        label="Investigate anomaly with operations team",
        description=(
            "Pull the timeline ±1 hour around the flagged row, request "
            "confirmation from anyone monitoring the asset at that time, and "
            "rule out instrument failure before treating as a real signal."
        ),
        domain="general",
        suggested_when=[
            "anomaly score ≥ 3.0 AND involves a measured (not derived) variable",
        ],
        expected_outcome_summary=(
            "≈40% of high-z anomalies are real events; ≈30% are sensor faults; "
            "≈30% are explainable by missed context."
        ),
        base_rate_success=0.40,
        relevant_finding_kinds=["anomaly"],
        assumptions=["sensor data has a known monitoring point of contact"],
    ),
    ActionTemplate(
        id="lower_operational_threshold",
        label="Lower operational threshold proactively",
        description=(
            "When a forecast peak crosses the operational ceiling at high "
            "confidence, derate the system or schedule maintenance before "
            "the predicted breach. Cost of the action is cheap; cost of the "
            "breach is expensive."
        ),
        domain="industrial",
        suggested_when=[
            "forecast peak > operational ceiling AND CRPS / phi confidence high",
        ],
        expected_outcome_summary=(
            "Pre-emptive action reduces breach probability by ≈70% in similar "
            "industrial cases (measured via outcome ledger when available)."
        ),
        base_rate_success=0.70,
        relevant_finding_kinds=["forecast_breach"],
        assumptions=[
            "operational ceiling is well-defined for the asset",
            "derating is cheaper than breach + repair",
        ],
    ),
    ActionTemplate(
        id="confirm_with_known_law",
        label="Confirm against the known law in operating manual",
        description=(
            "When the discovered model matches a canonical prior at ≥80% "
            "confidence, this is not a discovery — it's a confirmation. "
            "Document the agreement (with parameter values + tolerance) and "
            "use the law's known operating range to predict next behaviour."
        ),
        domain="general",
        suggested_when=[
            "physics_prior_matcher confidence ≥ 0.80",
        ],
        expected_outcome_summary=(
            "When a prior matches at ≥80% confidence, fitted parameters "
            "almost always fall within the prior's stated operating range."
        ),
        base_rate_success=0.85,
        relevant_finding_kinds=["physics_match"],
        assumptions=[],
    ),
    ActionTemplate(
        id="collect_more_data_to_resolve",
        label="Collect more data before deciding",
        description=(
            "Findings flagged as low-confidence shouldn't drive irreversible "
            "actions. Instead, identify the specific measurement that would "
            "raise confidence and collect it."
        ),
        domain="general",
        suggested_when=[
            "confidence < 0.4 OR n_samples below identifiability threshold",
        ],
        expected_outcome_summary=(
            "Adding the right additional measurement raises confidence above "
            "the actionable threshold in ≈60% of cases."
        ),
        base_rate_success=0.60,
        relevant_finding_kinds=["anomaly", "physics_match", "forecast_breach",
                                "regime_shift"],
        assumptions=["the bottleneck is data, not model"],
    ),
]


_LIBRARY: Dict[str, ActionTemplate] = {a.id: a for a in _DEFAULT_ACTIONS}


def list_actions(domain: Optional[str] = None) -> List[ActionTemplate]:
    if domain:
        return [a for a in _LIBRARY.values()
                if a.domain == domain or a.domain == "general"]
    return list(_LIBRARY.values())


def add_action_template(action: ActionTemplate) -> None:
    """Register a user-defined action template at runtime."""
    _LIBRARY[action.id] = action


# ---------------------------------------------------------------------------
# Outcome ledger (calibrates base rates)
# ---------------------------------------------------------------------------

def record_action_outcome(*, action_id: str, run_dir: str,
                          finding_index: int, outcome: str,
                          note: Optional[str] = None,
                          path: Optional[Path] = None) -> Dict[str, Any]:
    """Persist the observed outcome of a previously-suggested action.

    outcome ∈ {"successful", "ineffective", "harmful", "unknown"}. The
    base-rate calculator uses these to refine future recommendations.
    """
    if outcome not in ("successful", "ineffective", "harmful", "unknown"):
        raise ValueError(f"invalid outcome: {outcome!r}")
    path = path or action_library_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "row_type": "action_outcome_v1",
        "action_id": action_id,
        "run_dir": run_dir,
        "finding_index": int(finding_index),
        "outcome": outcome,
        "note": note,
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, separators=(",", ":")) + "\n")
    return row


def _read_outcomes(path: Optional[Path] = None) -> List[Dict[str, Any]]:
    path = path or action_library_path()
    if not path.exists():
        return []
    rows: List[Dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except Exception:
            continue
    return rows


def calibrated_base_rate(action_id: str,
                         path: Optional[Path] = None) -> Dict[str, Any]:
    """Compute the calibrated success rate for a given action_id.

    Uses Beta-Bernoulli smoothing with the template's prior base rate as
    the alpha+beta initial belief (so 1 successful outcome doesn't
    swing the estimate by 0.5).
    """
    template = _LIBRARY.get(action_id)
    if template is None:
        return {"action_id": action_id, "n": 0, "rate": None,
                "source": "unknown_action"}
    rows = [r for r in _read_outcomes(path)
            if r.get("action_id") == action_id]
    n_succ = sum(1 for r in rows if r.get("outcome") == "successful")
    n_inef = sum(1 for r in rows if r.get("outcome") == "ineffective")
    n_harm = sum(1 for r in rows if r.get("outcome") == "harmful")
    n_total = n_succ + n_inef + n_harm
    if n_total == 0:
        return {
            "action_id": action_id,
            "n": 0,
            "rate": template.base_rate_success,
            "source": "prior",
        }
    # Beta(alpha=template.base*K, beta=(1-base)*K) with K=4 strength.
    K = 4.0
    alpha = template.base_rate_success * K + n_succ
    beta = (1 - template.base_rate_success) * K + (n_inef + n_harm)
    rate = alpha / (alpha + beta)
    return {
        "action_id": action_id,
        "n": n_total,
        "rate": float(rate),
        "n_successful": n_succ,
        "n_ineffective": n_inef,
        "n_harmful": n_harm,
        "source": "calibrated",
    }


# ---------------------------------------------------------------------------
# Recommendation
# ---------------------------------------------------------------------------

@dataclass
class Recommendation:
    """Decision-aware recommendation tied to a finding."""
    kind: str                          # "do_x" | "do_x_if_y" | "collect_more_data"
    headline: str                      # one-line action label
    description: str
    confidence: float                  # 0..1
    confidence_band: str               # "high" | "medium" | "low"
    action_id: Optional[str]           # which template (or None for "collect more data")
    predicted_outcome: Dict[str, Any]
    predicted_no_action: Dict[str, Any]
    alternatives: List[Dict[str, Any]] = field(default_factory=list)
    base_rate: Optional[Dict[str, Any]] = None
    conditions_under_which_changes: List[str] = field(default_factory=list)
    assumptions: List[str] = field(default_factory=list)
    finding_ref: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        for k, v in list(d.items()):
            if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
                d[k] = None
        return d


def _confidence_band(c: float) -> str:
    if c >= 0.7:
        return "high"
    if c >= 0.4:
        return "medium"
    return "low"


def _kind_for_band(band: str) -> str:
    if band == "high":
        return "do_x"
    if band == "medium":
        return "do_x_if_y"
    return "collect_more_data"


def _predict_outcome(action: ActionTemplate, finding: Dict[str, Any],
                     calibrated: Dict[str, Any]) -> Dict[str, Any]:
    """Build the predicted-outcome block. Honest about uncertainty."""
    rate = calibrated.get("rate")
    n = calibrated.get("n") or 0
    # CI half-width with Wilson-style approximation: ±1.96 √(p(1-p)/N) when N>0.
    ci_low = ci_high = None
    if rate is not None and n > 0:
        margin = 1.96 * math.sqrt(rate * (1 - rate) / max(n, 1))
        ci_low = max(0.0, rate - margin)
        ci_high = min(1.0, rate + margin)
    return {
        "summary": action.expected_outcome_summary,
        "success_probability": rate,
        "ci_low": ci_low,
        "ci_high": ci_high,
        "n_observed": n,
        "rate_source": calibrated.get("source"),
    }


def _predict_no_action(finding: Dict[str, Any]) -> Dict[str, Any]:
    """What happens if the user does nothing? Pulled from finding metadata."""
    sev = finding.get("severity", "info")
    if sev == "crit":
        return {"summary": "Critical anomaly remains active; impact compounds.",
                "ci_low": None, "ci_high": None}
    if sev == "warn":
        return {"summary": ("Warning condition continues; mostly recoverable "
                            "but with elevated cost-of-delay."),
                "ci_low": None, "ci_high": None}
    return {"summary": ("Status quo persists; no immediate downside but "
                        "miss opportunity for confirmation."),
            "ci_low": None, "ci_high": None}


def _select_action(finding: Dict[str, Any]) -> Optional[ActionTemplate]:
    """Pick the most-relevant action template for this finding (deterministic)."""
    title = (finding.get("title") or "").lower()
    method = (finding.get("method") or "").lower()
    severity = finding.get("severity", "info")

    if "anomaly" in title or "iso-forest" in method:
        return _LIBRARY.get("investigate_anomaly")
    if "forecast" in title or "forecast" in method:
        if severity in ("crit", "warn"):
            return _LIBRARY.get("lower_operational_threshold")
    if "matches known law" in title or "physics_prior" in method:
        return _LIBRARY.get("confirm_with_known_law")
    return _LIBRARY.get("collect_more_data_to_resolve")


def build_recommendation(finding: Dict[str, Any]) -> Recommendation:
    """Wrap a finding into a structured Recommendation.

    The output shape always carries the same fields — the *kind* and the
    *language* change between confidence bands so low-confidence outputs
    don't masquerade as soft high-confidence ones.
    """
    raw_conf = float(finding.get("confidence") or 0.0)
    band = _confidence_band(raw_conf)
    kind = _kind_for_band(band)
    action = _select_action(finding)
    if action is None:
        # No suitable action found — emit a "collect more data" stub.
        action = _LIBRARY["collect_more_data_to_resolve"]
        kind = "collect_more_data"

    base = calibrated_base_rate(action.id)
    predicted = _predict_outcome(action, finding, base)
    no_action = _predict_no_action(finding)

    # Alternatives: enumerate other templates whose suggested_when overlaps
    # this finding's domain. Keep it light — top 2.
    alts: List[Dict[str, Any]] = []
    for alt in _LIBRARY.values():
        if alt.id == action.id:
            continue
        kinds = set(alt.relevant_finding_kinds or [])
        # Crude relevance: any overlap with the finding's title keywords.
        title = (finding.get("title") or "").lower()
        if "anomaly" in title and "anomaly" in kinds:
            alts.append(alt.to_dict())
        elif "forecast" in title and "forecast_breach" in kinds:
            alts.append(alt.to_dict())
    alts = alts[:2]

    # Confidence-band-specific phrasing.
    if band == "high":
        headline = f"DO: {action.label}"
        description = action.description
    elif band == "medium":
        headline = f"CONSIDER: {action.label} — verify Y first"
        description = (
            f"{action.description}\n\nVerify this first: "
            + ("; ".join(action.suggested_when) or
               "the conditions under which this action is appropriate.")
        )
    else:
        headline = "GATHER MORE DATA — confidence is too low to act"
        description = (
            "We don't have enough signal to recommend an action. "
            "Collect the specific measurements listed below; rerun analysis."
        )

    return Recommendation(
        kind=kind,
        headline=headline,
        description=description,
        confidence=raw_conf,
        confidence_band=band,
        action_id=action.id,
        predicted_outcome=predicted,
        predicted_no_action=no_action,
        alternatives=alts,
        base_rate=base,
        conditions_under_which_changes=action.suggested_when,
        assumptions=action.assumptions,
        finding_ref={
            "title": finding.get("title"),
            "method": finding.get("method"),
            "severity": finding.get("severity"),
            # Phase A — carry the resolved referent so downstream consumers
            # (export, copilot, decision-contract narrative engine) can speak
            # in domain language instead of "row N".
            "referent": (finding.get("referent") or {}).get("primary_label"),
            "referent_level": (finding.get("referent") or {}).get("level_used"),
        },
    )
