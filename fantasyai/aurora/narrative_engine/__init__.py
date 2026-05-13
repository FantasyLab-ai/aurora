"""
Narrative engine — Phase B of the "Human-Readable Output" brief.

Pipeline (5 steps, all deterministic — no LLM):

  1. filter   — drop findings below the contract's confidence_floor
  2. rank     — order by severity × decision-relevance
  3. select   — pick the (template_id, finding_kind, contract_class,
                 confidence_band) tuple → template_id
  4. render   — substitute referent.primary_label, vocabulary, units
  5. trace    — record the rendering decision so EXPLAIN can show it

The engine NEVER invents claims. If the contract suppresses a finding,
the finding is moved to the ``inspected_findings`` bucket; it stays in
the EXPLAIN modal but doesn't appear in the headline list.

Public API:

    from fantasyai.aurora.narrative_engine import (
        render_narrative,                # main entry — mutates state in-place
        renderer_trace_for_finding,      # introspection
    )
"""
from __future__ import annotations

from .engine import (
    render_narrative,
    renderer_trace_for_finding,
    resolve_finding_kind,
    pick_template_id,
)

__all__ = [
    "render_narrative",
    "renderer_trace_for_finding",
    "resolve_finding_kind",
    "pick_template_id",
]
