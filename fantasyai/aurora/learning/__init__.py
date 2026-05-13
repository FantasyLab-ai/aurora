"""
Persistent learning across runs.

Aurora becomes more useful as it sees more datasets — not by training a
model, but by accumulating empirical evidence about which fitted forms +
priors win on which kinds of signals (shape, domain, scale).

Three submodules:

* ``store``        — append-only JSONL ledger: one row per finished run
* ``meta_learner`` — given a new run's calculus signature + domain, find
                     similar past runs and suggest priors that won there
* ``feedback``     — user thumbs-up / thumbs-down on individual findings,
                     persisted so the meta-learner can up/down-weight

Glass-box rules apply: every suggestion carries n (sample size) and an
explicit "based on these N past runs" provenance trail. We never silently
re-rank without showing why.
"""
from __future__ import annotations

from .store import (
    LearningStore, append_run, read_all, default_store_path,
)
from .meta_learner import suggest_priors_for_signature, score_similarity
from .feedback import record_feedback, recent_feedback
from .auto_feedback import (
    record_auto_feedback, compute_signals, auto_feedback_summary,
)

__all__ = [
    "LearningStore", "append_run", "read_all", "default_store_path",
    "suggest_priors_for_signature", "score_similarity",
    "record_feedback", "recent_feedback",
    "record_auto_feedback", "compute_signals", "auto_feedback_summary",
]
