"""
Template-aware narrator.

Findings are produced in column-language by ``state_builder._build_findings``.
This module re-narrates them in domain-language using the matched template's
vocabulary + active patterns, *without* inventing knowledge the math doesn't
support.

Glass-box rules:
  * Never replace a finding's title/description outright. We add a parallel
    field ``narrative`` so the original signal-language stays inspectable.
  * If confidence in the system model is low (<0.5), we narrate cautiously
    or skip entirely.
  * Domain vocabulary is read from the template — no LLM, no ad-libbing.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional


def _safe_lower(s: Any) -> str:
    return str(s or "").lower()


def renarrate_finding(finding: Dict[str, Any],
                       sm_dict: Dict[str, Any]) -> None:
    """Add a ``narrative`` block to ``finding`` in-place. No-op on failure."""
    try:
        confidence = float(sm_dict.get("confidence") or 0.0)
        vocab = sm_dict.get("vocabulary") or {}
        patterns = sm_dict.get("characteristic_patterns") or []
        active_patterns = ((sm_dict.get("state") or {}).get("active_patterns") or [])
        pat_by_id = {p.get("id"): p for p in patterns}
        active_pat_names = [pat_by_id.get(p.get("pattern_id"), {}).get("name")
                            for p in active_patterns
                            if p.get("pattern_id") in pat_by_id]
        active_pat_names = [n for n in active_pat_names if n]

        anomaly_term = vocab.get("anomaly_term", "anomaly")
        breach_term = vocab.get("breach_term", "threshold breach")
        regime_term = vocab.get("regime_term", "regime")
        advisory_term = vocab.get("advisory_term", "advisory")

        title = str(finding.get("title") or "")
        description = str(finding.get("description") or "")
        method = str(finding.get("method") or "")

        # Phase A: prefer the resolved referent when available. The narrator
        # itself doesn't change behavior — title was already rewritten to use
        # primary_label upstream — but we record which level was used so
        # provenance/explainer panels can surface it.
        ref = finding.get("referent") or {}
        ref_label = ref.get("primary_label")
        ref_level = ref.get("level_used")

        narrative_title: Optional[str] = None
        narrative_body: Optional[str] = None

        title_low = title.lower()
        if "anomaly" in title_low:
            narrative_title = title.replace("Anomaly", anomaly_term.title())
            if active_pat_names:
                narrative_body = (
                    f"{narrative_title}. Consistent with the "
                    f"{active_pat_names[0]} signature currently active in this "
                    f"system."
                )
            else:
                narrative_body = (f"{narrative_title}. No characteristic "
                                  f"pattern is active right now.")
        elif "forecast" in title_low and ("peak" in title_low or "breach" in title_low):
            narrative_title = title.replace("Forecast peak", f"Forecast peak (potential {breach_term})")
            narrative_body = (
                f"{narrative_title}. "
                + (f"Driven by the {active_pat_names[0]} pattern."
                   if active_pat_names else
                   "No template-named pattern detected as the cause.")
            )
        elif "matches known law" in title_low or "matches" in title_low and "law" in title_low:
            narrative_title = title
            narrative_body = (
                f"{title} — this is a known relationship in the "
                f"template's expected topology, so the {advisory_term} "
                f"language reflects high confidence."
            )
        elif title_low.startswith("regime") or "regime" in title_low:
            narrative_title = title.replace("regime", regime_term)
            narrative_body = (
                f"{narrative_title}. Latest segment matches "
                f"{vocab.get('extra', {}).get('high_state', 'high-state')}/"
                f"{vocab.get('extra', {}).get('low_state', 'low-state')} pattern."
            )
        else:
            narrative_title = title
            narrative_body = description

        # Suppress narrative when model confidence is low — honest defaults.
        if confidence < 0.3:
            narrative_body = ("(low system-model confidence — narrative "
                              "suppressed; raw finding still available)")

        finding["narrative"] = {
            "title": narrative_title,
            "body": narrative_body,
            "vocabulary_used": [anomaly_term, breach_term, regime_term, advisory_term],
            "active_patterns": active_pat_names,
            "model_confidence": confidence,
            "referent_label": ref_label,
            "referent_level": ref_level,
        }
    except Exception:
        pass


def renarrate_findings(findings: List[Dict[str, Any]],
                        sm_dict: Dict[str, Any]) -> None:
    """Re-narrate every finding in-place. Safe to call repeatedly."""
    for f in findings:
        if not isinstance(f, dict):
            continue
        renarrate_finding(f, sm_dict)
