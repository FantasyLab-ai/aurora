"""
Decision-aware recommendations.

Public API:

    build_recommendation(finding, *, action_library=None, base_rates=None) -> Recommendation
        Wrap a finding into a structured recommendation with predicted
        outcomes, base rates, conditions, and explicit assumptions.

    record_action_outcome(...) -> None
        Persist the observed outcome of a previously-suggested action.
        The action library learns from these so future recommendations
        are calibrated against actual rather than theoretical base rates.

    list_actions(domain=None) -> List[ActionTemplate]
        Browse the action library.

Critically, low-confidence recommendations produce a DIFFERENT shape, not a
softer version of high-confidence ones. The user knows when Aurora has
something useful to suggest vs when it's asking for more measurements.
"""
from __future__ import annotations

from .recommender import (
    Recommendation, ActionTemplate, build_recommendation,
    list_actions, record_action_outcome, action_library_path,
)

__all__ = [
    "Recommendation", "ActionTemplate",
    "build_recommendation", "list_actions",
    "record_action_outcome", "action_library_path",
]
