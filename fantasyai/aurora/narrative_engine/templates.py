"""
Narrative templates — keyed by (domain, finding_kind, contract_class,
confidence_band).

Substitution variables available to every template:

  {referent}           — finding.referent.primary_label
  {column}             — finding.column or extracted from title
  {value}              — headline numeric value (forecast peak, anomaly score)
  {anomaly_term}       — domain vocab from system_model
  {regime_term}
  {breach_term}
  {advisory_term}
  {confidence_pct}     — int(confidence * 100)

Templates are resolved by walking from the most-specific key to the
most-general:

  (domain, kind, class, band)
  → (domain, kind, class, "*")
  → (domain, kind, "*", "*")
  → ("*", kind, class, band)
  → ("*", kind, "*", "*")
  → ("*", "*", "*", "*")    # ultimate fallback

So you can override only the parts that matter and inherit the rest.
"""
from __future__ import annotations

from typing import Any, Dict, Optional, Tuple


# Each entry maps (domain, kind, class, band) → {title, body, prefix?, amplify?}
# - prefix:  small banner (e.g. "ALERT") prepended in amplify mode
# - amplify: when True the renderer marks the card for visual emphasis
TEMPLATES: Dict[Tuple[str, str, str, str], Dict[str, Any]] = {

    # ============= UNIVERSAL FALLBACK =============
    ("*", "*", "*", "*"): {
        "title": "{kind} at {referent}",
        "body":  "{column} {kind_phrase} (confidence {confidence_pct}%).",
    },

    # ============= ANOMALY × CLASS × BAND =============
    ("*", "anomaly", "explore", "*"): {
        "title": "Anomaly in {column} at {referent}",
        "body":  "{description} — surfaced for review.",
    },
    ("*", "anomaly", "avoid_harm", "high"): {
        "title": "ACT NOW · {anomaly_term} in {column} at {referent}",
        "body":  "{description}. Confidence is high; preferred action listed below.",
        "amplify": True,
    },
    ("*", "anomaly", "avoid_harm", "medium"): {
        "title": "Suspected {anomaly_term} in {column} at {referent}",
        "body":  "{description}. Verify before acting (medium confidence).",
    },
    ("*", "anomaly", "avoid_harm", "low"): {
        "title": "Possible {anomaly_term} in {column} at {referent}",
        "body":  "{description}. Low confidence — keep watching, don't act yet.",
    },
    ("*", "anomaly", "confirm", "high"): {
        "title": "Confirmed anomaly in {column} at {referent}",
        "body":  "{description}. Replicated across tiers; suitable for log entry.",
    },
    ("*", "anomaly", "confirm", "medium"): {
        "title": "Tentative anomaly in {column} at {referent}",
        "body":  "{description}. Awaiting replication — do not certify yet.",
    },
    ("*", "anomaly", "confirm", "low"): {
        "title": "Uncertain signal in {column} at {referent}",
        "body":  "{description}. Below confirmation threshold — stays in inspect bucket.",
    },

    # ============= REGIME =============
    ("*", "regime", "*", "*"): {
        "title": "{regime_term} change at {referent}",
        "body":  "{description}.",
    },
    ("*", "regime", "avoid_harm", "high"): {
        "title": "Active {regime_term} shift detected at {referent}",
        "body":  "{description}. High-confidence shift — recommended response below.",
        "amplify": True,
    },

    # ============= FORECAST =============
    ("*", "forecast", "*", "*"): {
        "title": "Forecast peak: {value} ({method}, horizon {horizon})",
        "body":  "{description}",
    },
    ("*", "forecast", "avoid_harm", "high"): {
        "title": "PROJECTED {breach_term} · peak {value} at {referent}",
        "body":  "{description}. Pre-emptive action below; the predicted peak crosses the operational ceiling.",
        "amplify": True,
    },
    ("*", "forecast", "confirm", "*"): {
        "title": "Forecast peak: {value}",
        "body":  "{description}. Out-of-sample replication required before publication.",
    },

    # ============= PHYSICS / LAW MATCH =============
    ("*", "physics_match", "confirm", "high"): {
        "title": "Confirmed match: {law_name}",
        "body":  "{description}. Parameter values within prior operating range; confirmation can be logged.",
    },
    ("*", "physics_match", "*", "*"): {
        "title": "Possible match: {law_name}",
        "body":  "{description}. {confidence_pct}% match — see EXPLAIN for caveats.",
    },

    # ============= CAUSAL =============
    ("*", "causal", "explore", "*"): {
        "title": "Tentative causal signal: {if_clause}",
        "body":  "{then_clause}. Treat as exploratory — observational causal claims require replication.",
    },
    ("*", "causal", "avoid_harm", "high"): {
        "title": "Decision-grade causal signal: {if_clause}",
        "body":  "{then_clause}. Confidence is sufficient to support intervention planning.",
        "amplify": True,
    },

    # ============= DOMAIN-SPECIFIC OVERRIDES =============
    ("bio", "anomaly", "avoid_harm", "*"): {
        "title": "Clinical signal in {column} at {referent}",
        "body":  "{description}. Forwarding to clinician review queue (not a diagnosis).",
    },
    ("seismology", "anomaly", "avoid_harm", "*"): {
        "title": "Seismic precursor candidate in {column} at {referent}",
        "body":  "{description}. Review against neighbouring stations for corroboration.",
    },
    ("finance", "regime", "avoid_harm", "*"): {
        "title": "Volatility regime shift at {referent}",
        "body":  "{description}. Reduced-exposure actions are below.",
    },
}


def lookup_template(domain: str, kind: str,
                    contract_class: str, band: str) -> Dict[str, Any]:
    """Walk the specificity hierarchy and return the most-specific match."""
    keys = [
        (domain, kind, contract_class, band),
        (domain, kind, contract_class, "*"),
        (domain, kind, "*", "*"),
        ("*", kind, contract_class, band),
        ("*", kind, contract_class, "*"),
        ("*", kind, "*", "*"),
        ("*", "*", "*", "*"),
    ]
    for k in keys:
        if k in TEMPLATES:
            return TEMPLATES[k]
    return TEMPLATES[("*", "*", "*", "*")]


def template_id_for_lookup(domain: str, kind: str,
                            contract_class: str, band: str) -> str:
    """Return the *most-specific matched key* as a stable id string."""
    keys = [
        (domain, kind, contract_class, band),
        (domain, kind, contract_class, "*"),
        (domain, kind, "*", "*"),
        ("*", kind, contract_class, band),
        ("*", kind, contract_class, "*"),
        ("*", kind, "*", "*"),
        ("*", "*", "*", "*"),
    ]
    for k in keys:
        if k in TEMPLATES:
            return ":".join(k)
    return ":".join(("*", "*", "*", "*"))
