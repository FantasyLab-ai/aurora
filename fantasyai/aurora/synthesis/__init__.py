"""
Aurora Synthesis Engine.

Turns the assembled state into an interpretive summary — the layer
between findings and understanding. Three deterministic outputs:

  state.interpretive_summary  — 1-2 paragraphs of synthesis prose
                                  (the "WHAT THIS MEANS" panel)
  state.equation_translations — per-finding equation → plain-language
  state.domain_context        — 1-3 domain-specific contextual notes

Glass-box throughout. Templates live as JSON on disk; rules that
triggered each paragraph + slot extractors used + values filled in
are recorded in state.synthesis_meta for inspection.

Public API:

    from fantasyai.aurora.synthesis import synthesize
    synthesize(state)   # mutates in place
"""
from __future__ import annotations

from .engine import synthesize, SynthesisResult
from .matcher import (
    classify_finding_kind, evaluate_match,
    list_templates, list_equation_translations,
)
from .slot_extractor import extract_slot
from .equation_translator import translate_equation

__all__ = [
    "synthesize",
    "SynthesisResult",
    "classify_finding_kind",
    "evaluate_match",
    "extract_slot",
    "translate_equation",
    "list_templates",
    "list_equation_translations",
]
