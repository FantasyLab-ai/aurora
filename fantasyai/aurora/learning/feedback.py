"""
Per-finding user feedback (👍 / 👎 / "ignore").

When a user marks a finding as correct or wrong, we append a row to the
same ``~/.aurora/learning_store.jsonl`` so the meta-learner can later
prefer priors that earned thumbs-up and avoid ones that drew thumbs-down.

This file purposefully does not implement re-ranking — that's
``meta_learner.suggest_priors_for_signature``'s job. We just record.
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from .store import LearningStore


def record_feedback(*, run_dir: str, finding_index: int, finding_title: str,
                    finding_method: Optional[str], verdict: str,
                    note: Optional[str] = None,
                    store: Optional[LearningStore] = None) -> Dict[str, Any]:
    """Append a feedback row to the learning store.

    Args:
        run_dir:        the run the finding came from
        finding_index:  position in the findings list
        finding_title:  human-readable title (for later inspection)
        finding_method: producer ("physics_prior_matcher", "iso-forest", etc.)
        verdict:        one of {"up", "down", "ignore"}
        note:           optional free-text reason

    Returns:
        The row that was written (for the API response).

    Raises:
        ValueError if verdict isn't one of the expected values.
    """
    if verdict not in ("up", "down", "ignore"):
        raise ValueError(f"verdict must be 'up'|'down'|'ignore', got {verdict!r}")
    store = store or LearningStore.default()
    row = {
        "row_type": "feedback_v1",
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "run_dir": str(run_dir),
        "finding_index": int(finding_index),
        "finding_title": str(finding_title),
        "finding_method": finding_method,
        "verdict": verdict,
        "note": note,
    }
    store.append(row)
    return row


def recent_feedback(limit: int = 50,
                    store: Optional[LearningStore] = None) -> List[Dict[str, Any]]:
    """Return the most-recent feedback rows (most-recent first)."""
    store = store or LearningStore.default()
    rows = [r for r in store.read_all() if r.get("row_type") == "feedback_v1"]
    rows.sort(key=lambda r: r.get("ts", ""), reverse=True)
    return rows[:limit]
