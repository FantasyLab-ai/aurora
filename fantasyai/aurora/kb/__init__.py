"""
Knowledge-base grounding — strictly bounded scope.

What this does (v1):
  * For each finding Aurora produces, attempt to attach a citation chip
    pointing to a real-world reference for the matched pattern.
  * Use a curated local catalogue first (shipped with Aurora, deterministic).
  * Optionally extend with live API adapters (PubMed, FRED, NOAA) — opt-in
    via ``AURORA_KB_ENABLED=1``. Aurora itself never *requires* internet.

What this is NOT:
  * Not a chat-with-the-KB — no agent, no retrieval-augmented chat.
  * Not a substitute for the user's own literature review.
  * Not authoritative — citations are matches based on pattern, not semantic
    understanding. The user clicks through to verify.

Public API
----------

    match_finding_to_kb(finding, *, domain=None) → Optional[Citation]
    list_kb_sources()                            → metadata about what's loaded
"""
from .local_kb import (
    Citation, match_finding_to_kb, list_kb_sources,
)

__all__ = ["Citation", "match_finding_to_kb", "list_kb_sources"]
