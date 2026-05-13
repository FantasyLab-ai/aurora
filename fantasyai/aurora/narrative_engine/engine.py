"""
Narrative engine — 5-step pipeline.

Inputs : a state dict (already enriched with referents) + a DecisionContract.
Outputs: the same state dict, with each finding decorated by a ``narrative``
         block AND each finding tagged with ``surface_treatment`` (one of:
         render | amplify | hedge | inspect | suppress).

The engine never deletes findings. Suppressed findings are moved to
``inspected_findings``; the user can flip them back via the EXPLAIN modal.
The state's ``findings`` array always reflects what the user should see
under the active contract.

This module never imports an LLM. Templates are deterministic strings.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from ..decision_contract import (
    DecisionContract, ConfidenceBand, confidence_band, suppression_decision,
)
from .templates import lookup_template, template_id_for_lookup


# ---------------------------------------------------------------------------
# Step 3: classify a finding into a "kind" the templates key on
# ---------------------------------------------------------------------------

def resolve_finding_kind(finding: Dict[str, Any]) -> str:
    """Map a free-form finding into a controlled vocabulary kind."""
    title = (finding.get("title") or "").lower()
    method = (finding.get("method") or "").lower()
    if "anomaly" in title or "iso-forest" in method:
        return "anomaly"
    if title.startswith("forecast") or "forecast" in method:
        return "forecast"
    if "regime" in title or "regime" in method:
        return "regime"
    if "physical model fit" in title or "matches known law" in title \
            or "physics_prior" in method:
        return "physics_match"
    if "causal" in title or "causal" in method:
        return "causal"
    if "warning" in title:
        return "warning"
    return "generic"


# ---------------------------------------------------------------------------
# Step 3b: pick the template id (stable string identifier)
# ---------------------------------------------------------------------------

def pick_template_id(domain: str, kind: str,
                     contract_class: str, band: str) -> str:
    return template_id_for_lookup(domain, kind, contract_class, band)


# ---------------------------------------------------------------------------
# Vocabulary resolution
# ---------------------------------------------------------------------------

def _vocab_for_state(state: Dict[str, Any]) -> Dict[str, str]:
    """Pull domain-specific vocabulary from the state.system_model block.

    Falls back to neutral defaults if the state has no system_model.
    """
    sm = state.get("system_model") or {}
    vocab = sm.get("vocabulary") or {}
    return {
        "anomaly_term": vocab.get("anomaly_term", "anomaly"),
        "regime_term": vocab.get("regime_term", "regime"),
        "breach_term": vocab.get("breach_term", "threshold breach"),
        "advisory_term": vocab.get("advisory_term", "advisory"),
    }


# ---------------------------------------------------------------------------
# Substitution
# ---------------------------------------------------------------------------

def _format_template(template_str: str,
                     finding: Dict[str, Any],
                     vocab: Dict[str, str]) -> str:
    """Best-effort {key} substitution; missing keys leave the placeholder."""
    if not isinstance(template_str, str):
        return ""
    ref = finding.get("referent") or {}
    confidence = float(finding.get("confidence") or 0.0)
    column = (finding.get("column") or "").strip()
    if not column:
        # Try to extract from the title.
        title = finding.get("title") or ""
        # Patterns like "Anomaly in <col> at <ref>"
        import re
        m = re.search(r"\bin\s+([\w\.\-]+)\s+at\b", title)
        if m:
            column = m.group(1)
    description = finding.get("description") or ""
    method = finding.get("method") or ""
    horizon = finding.get("horizon") or ""
    value = ""
    # Forecast headline value lives elsewhere; pull from raw if present.
    raw = finding.get("raw") or {}
    if "headline_value" in raw:
        try:
            value = f"{float(raw['headline_value']):.3g}"
        except Exception:
            value = str(raw["headline_value"])
    # Causal-style fields are sometimes embedded in description; leave them.
    fields = {
        "referent": ref.get("primary_label") or "",
        "kind": resolve_finding_kind(finding),
        "kind_phrase": "is anomalous",
        "column": column or "?",
        "value": value,
        "horizon": horizon,
        "method": method,
        "description": description,
        "confidence_pct": int(round(confidence * 100)),
        "law_name": (finding.get("law_name") or
                      finding.get("title", "").split(":", 1)[-1].strip()),
        "if_clause": finding.get("if_clause") or "",
        "then_clause": finding.get("then_clause") or "",
        **vocab,
    }
    try:
        return template_str.format(**fields)
    except Exception:
        # Unknown placeholder — strip and return best-effort.
        try:
            import string
            class _Default(dict):
                def __missing__(self, k): return ""
            return string.Formatter().vformat(template_str, (), _Default(fields))
        except Exception:
            return template_str


# ---------------------------------------------------------------------------
# Public entry: render the full state
# ---------------------------------------------------------------------------

def render_narrative(state: Dict[str, Any],
                     contract: Optional[DecisionContract] = None) -> Dict[str, Any]:
    """Apply the 5-step pipeline to ``state`` IN PLACE.

    Returns the same state dict (for chaining). If no contract is supplied,
    a neutral "explore" contract is used so the UI still shows everything.

    The engine guarantees:
      * findings array is reordered (high-priority first), never re-keyed
      * ``surface_treatment`` is set on every finding (suppress | inspect |
        hedge | render | amplify)
      * ``narrative`` block is set on every finding (title, body, template_id,
        substitution_trace)
      * suppressed/inspect findings are moved to ``state["inspected_findings"]``
        but kept reachable
    """
    if not isinstance(state, dict):
        return state

    findings = list(state.get("findings") or [])
    if not findings:
        return state

    # Default to a neutral explore contract — never crash for missing input.
    if contract is None:
        from ..decision_contract import (
            DecisionContract, ContractClass, CostAsymmetry,
        )
        contract = DecisionContract(
            id="default:explore",
            template_id="*",
            contract_class=ContractClass.EXPLORE,
            objective="Explore findings",
            cost_asymmetry=CostAsymmetry.SYMMETRIC,
            confidence_floor=0.0,
            source="default",
        )

    # Domain comes from system_model.template (or contract.template_id as fallback).
    sm = state.get("system_model") or {}
    domain = (sm.get("template_id") or contract.template_id or "*").lower()
    if domain == "*":
        domain = "*"
    vocab = _vocab_for_state(state)

    rendered: List[Dict[str, Any]] = []
    inspected: List[Dict[str, Any]] = []

    # --- Step 1: filter ---
    for f in findings:
        if not isinstance(f, dict):
            continue
        confidence = float(f.get("confidence") or 0.0)
        kind = resolve_finding_kind(f)
        band = confidence_band(confidence)

        # --- Step 2: suppression matrix (decides treatment) ---
        treatment = suppression_decision(confidence, contract.cost_asymmetry)

        # Hard floor takes precedence over the matrix.
        if confidence < float(contract.confidence_floor or 0.0):
            treatment = "suppress"

        # --- Step 3: pick template ---
        tid = pick_template_id(domain, kind, contract.contract_class, band)
        tpl = lookup_template(domain, kind, contract.contract_class, band)

        # --- Step 4: render ---
        title_text = _format_template(tpl.get("title", ""), f, vocab)
        body_text = _format_template(tpl.get("body", ""), f, vocab)
        amplified = bool(tpl.get("amplify")) and treatment == "amplify"
        if treatment == "amplify" and not tpl.get("amplify"):
            # Treatment is "amplify" but the template didn't opt in — just render.
            treatment = "render"

        # --- Step 5: trace ---
        trace = {
            "domain": domain,
            "kind": kind,
            "contract_id": contract.id,
            "contract_class": contract.contract_class,
            "cost_asymmetry": contract.cost_asymmetry,
            "confidence": confidence,
            "confidence_band": band,
            "treatment": treatment,
            "template_id": tid,
            "vocab_used": list(vocab.keys()),
            "amplified": amplified,
        }
        f["narrative"] = {
            **(f.get("narrative") or {}),
            "title": title_text or f.get("title"),
            "body": body_text,
            "template_id": tid,
            "trace": trace,
        }
        f["surface_treatment"] = treatment

        if treatment == "suppress":
            inspected.append(f)
            continue
        if treatment == "inspect":
            # Inspect-only stays in the array but is dimmed downstream.
            inspected.append(f)
            # Keep one copy in rendered so the user knows it exists; UI dims it.
            f.setdefault("inspect_only", True)
            rendered.append(f)
            continue
        rendered.append(f)

    # --- Sort: amplified first, then by severity then confidence ---
    sev_rank = {"crit": 0, "warn": 1, "info": 2, "ok": 3}

    def _sort_key(f):
        amp = 0 if f.get("surface_treatment") == "amplify" else 1
        sev = sev_rank.get((f.get("severity") or "info").lower(), 4)
        conf = -float(f.get("confidence") or 0.0)
        return (amp, sev, conf)

    rendered.sort(key=_sort_key)

    # Mutate in place.
    state["findings"] = rendered
    state["inspected_findings"] = inspected
    state["narrative_engine"] = {
        "active_contract": contract.to_dict(),
        "domain": domain,
        "n_rendered": len(rendered),
        "n_inspected": len(inspected),
    }
    return state


# ---------------------------------------------------------------------------
# Introspection helpers
# ---------------------------------------------------------------------------

def renderer_trace_for_finding(finding: Dict[str, Any]) -> Dict[str, Any]:
    """Return the trace block for a finding (or {} if missing)."""
    nar = finding.get("narrative") or {}
    return nar.get("trace") or {}
