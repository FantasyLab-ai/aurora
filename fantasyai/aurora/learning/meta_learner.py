"""
Meta-learner: given a new run's calculus signature + domain, look up past
runs in the cross-run store and surface "what won on similar data."

This is deliberately *not* an ML model. It's a transparent nearest-neighbour
score with a documented similarity function, so a user can trace any
suggestion back to the rows that drove it. That trade-off — interpretability
over fancy fitting — is the whole point of Aurora's glass-box design.

Public API
----------

    suggest_priors_for_signature(calculus_dict, domain_hint=None,
                                  min_n=2, top_k=3) -> List[Dict]

        Returns the top-k prior IDs most likely to fit, each with:
          {prior_id, display_name, support_n, mean_confidence,
           example_run_dirs[≤3], rationale}

    score_similarity(sig_a, sig_b) -> float in [0, 1]

        Pure function: how alike are these two calculus signatures? Used
        internally + exposed for tests / debugging.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple

from .store import LearningStore, read_all


# ---------------------------------------------------------------------------
# Similarity scoring
# ---------------------------------------------------------------------------

# Categorical features dominate similarity. Numeric ones are gentler nudges.
# Tweak by use-case; current weights chosen so a shape-match alone can
# already promote a row, while differing shapes need other strong matches
# to compensate.
_W_SHAPE      = 0.45
_W_MONOTONIC  = 0.10
_W_DOMAIN     = 0.20
_W_DERIV_DIR  = 0.10   # sign of dy/dt mean (growth vs decay)
_W_INFLECTION = 0.10   # binned inflection-point count
_W_FREQUENCY  = 0.05   # presence of dominant frequency


def score_similarity(sig_a: Dict[str, Any], sig_b: Dict[str, Any],
                     domain_a: Optional[str] = None,
                     domain_b: Optional[str] = None) -> float:
    """Compute a similarity score in [0, 1] between two calculus signatures.

    Higher = more alike. The score is the weighted sum of feature matches
    with the weights above. Returns 0.0 when either signature is missing.
    """
    if not sig_a or not sig_b:
        return 0.0
    score = 0.0

    sa = (sig_a.get("likely_shape") or "").strip().lower()
    sb = (sig_b.get("likely_shape") or "").strip().lower()
    if sa and sb and sa == sb:
        score += _W_SHAPE
    elif sa and sb and (sa.split()[0] == sb.split()[0]):
        # "exponential decay" partially matches "exponential-like growth".
        score += 0.5 * _W_SHAPE

    if sig_a.get("monotonic") == sig_b.get("monotonic") and sig_a.get("monotonic") is not None:
        score += _W_MONOTONIC

    if domain_a and domain_b and domain_a == domain_b:
        score += _W_DOMAIN

    da = sig_a.get("dy_dt_mean")
    db = sig_b.get("dy_dt_mean")
    if da is not None and db is not None and (da == 0 or db == 0 or (da > 0) == (db > 0)):
        score += _W_DERIV_DIR

    ia = sig_a.get("inflection_points")
    ib = sig_b.get("inflection_points")
    if ia is not None and ib is not None and _bin_inflection(ia) == _bin_inflection(ib):
        score += _W_INFLECTION

    fa = sig_a.get("dominant_frequency")
    fb = sig_b.get("dominant_frequency")
    if (fa is not None) == (fb is not None):
        score += _W_FREQUENCY

    return min(1.0, score)


def _bin_inflection(n: Any) -> str:
    """Bucket inflection counts so 'a few' matches 'a few' and 'lots' matches 'lots'."""
    try:
        v = int(n)
    except Exception:
        return "?"
    if v == 0:
        return "none"
    if v <= 3:
        return "few"
    if v <= 12:
        return "moderate"
    return "many"


# ---------------------------------------------------------------------------
# Suggestion engine
# ---------------------------------------------------------------------------

def suggest_priors_for_signature(calculus_dict: Optional[Dict[str, Any]],
                                  domain_hint: Optional[str] = None,
                                  min_n: int = 2,
                                  top_k: int = 3,
                                  min_similarity: float = 0.5,
                                  store: Optional[LearningStore] = None,
                                  ) -> List[Dict[str, Any]]:
    """Look up past runs whose calculus signatures resemble the current one
    and report which priors won most often.

    Args:
        calculus_dict: the current run's calculus signature (a dict of the same
            shape ``store.build_run_row`` writes). May be None.
        domain_hint:   optional domain string to bias the search.
        min_n:         only include suggestions backed by ≥ this many runs.
        top_k:         max number of suggestions to return.
        min_similarity: only count past runs whose similarity ≥ this.
        store:         override the global store (for testing).

    Returns:
        List of dicts, each:
          {
            "prior_id": str,
            "display_name": str,
            "support_n": int,                  # how many similar past runs picked this
            "mean_confidence": float,          # avg of those runs' confidences
            "example_run_dirs": [str],         # up to 3 run_dir paths
            "rationale": str,                  # one-line "why" for the user
          }
        Empty list when there's no useful evidence.
    """
    if not calculus_dict:
        return []
    rows = read_all(store=store)
    if not rows:
        return []

    # Score every prior past row and tally votes per prior_id.
    tally: Dict[str, Dict[str, Any]] = defaultdict(
        lambda: {"weighted": 0.0, "n": 0, "confs": [], "examples": [],
                 "display_name": None, "domain": None}
    )

    for r in rows:
        if r.get("row_type") != "run_learning_v1":
            continue
        bp = r.get("best_prior") or {}
        prior_id = bp.get("id")
        if not prior_id:
            continue
        past_calc = r.get("calculus") or {}
        sim = score_similarity(calculus_dict, past_calc,
                                domain_a=domain_hint,
                                domain_b=r.get("domain"))
        if sim < min_similarity:
            continue
        slot = tally[prior_id]
        slot["weighted"] += sim
        slot["n"] += 1
        if isinstance(bp.get("confidence"), (int, float)):
            slot["confs"].append(float(bp["confidence"]))
        if r.get("run_dir") and len(slot["examples"]) < 3:
            slot["examples"].append(r["run_dir"])
        slot["display_name"] = bp.get("display_name") or slot["display_name"]
        slot["domain"] = bp.get("domain") or slot["domain"]

    # Build the ranked output.
    out: List[Dict[str, Any]] = []
    for pid, slot in tally.items():
        if slot["n"] < min_n:
            continue
        mean_conf = sum(slot["confs"]) / len(slot["confs"]) if slot["confs"] else 0.0
        rationale = (
            f"Matched on {slot['n']} similar past run(s), "
            f"weighted similarity {slot['weighted']:.2f}, "
            f"mean confidence {mean_conf*100:.0f}%."
        )
        out.append({
            "prior_id": pid,
            "display_name": slot["display_name"] or pid,
            "domain": slot["domain"],
            "support_n": slot["n"],
            "mean_confidence": mean_conf,
            "weighted_similarity": slot["weighted"],
            "example_run_dirs": slot["examples"],
            "rationale": rationale,
        })
    out.sort(key=lambda d: (-d["weighted_similarity"], -d["support_n"]))
    return out[:top_k]
