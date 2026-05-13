"""
Referential framing — turn raw row indices and column references into
human-readable referents (names, timestamps, entity IDs, ranges).

Public API:

    from fantasyai.aurora.referential import (
        Referent, ReferentLevel,
        resolve_referent, resolve_for_finding,
        attach_referents_to_state,
    )

Phase A of the "Human-Readable Output & Intent-Aware Framing" brief.
"""
from __future__ import annotations

from .resolver import (
    Referent,
    ReferentLevel,
    resolve_referent,
    resolve_for_finding,
    attach_referents_to_state,
    NAMED_EVENT_REGISTRY,
)

__all__ = [
    "Referent",
    "ReferentLevel",
    "resolve_referent",
    "resolve_for_finding",
    "attach_referents_to_state",
    "NAMED_EVENT_REGISTRY",
]
