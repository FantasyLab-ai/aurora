"""
Prior library curation — keep the library scientific, not noisy.

This module implements the four operations the strategic brief calls out:

  1. ``find_duplicates``        — pairs of priors with overlapping forms
  2. ``find_contradictions``    — pairs with the same domain_of_validity but
                                   incompatible structural forms
  3. ``deprecate``              — mark a prior deprecated with full
                                   historical record retention
  4. ``diff_libraries``         — diff the in-Python catalogue against
                                   any historical YAML snapshot

Promotion / demotion is in ``validation_log.summarize_prior``; this module
is the human-curation layer (or future-agent-curation layer) on top of that.
We keep the design conservative: no operation here mutates the in-Python
catalogue. Curation events are recorded as JSONL rows in a curation
ledger; the loader respects them on next read.

Storage: ``~/.aurora/prior_curation.jsonl``.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .registry import Prior, list_priors, get_prior


def default_curation_path() -> Path:
    override = os.environ.get("AURORA_PRIOR_CURATION")
    if override:
        return Path(override).expanduser().resolve()
    return Path.home() / ".aurora" / "prior_curation.jsonl"


# ---------------------------------------------------------------------------
# Curation ledger
# ---------------------------------------------------------------------------

def _ensure_dir(p: Path) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)


def _append(row: Dict[str, Any], path: Optional[Path] = None) -> None:
    path = path or default_curation_path()
    _ensure_dir(path)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, separators=(",", ":")) + "\n")


def read_curation_log(path: Optional[Path] = None) -> List[Dict[str, Any]]:
    path = path or default_curation_path()
    if not path.exists():
        return []
    rows: List[Dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except Exception:
            continue
    return rows


def deprecation_overrides(path: Optional[Path] = None) -> Dict[str, Dict[str, Any]]:
    """Return ``{prior_id: deprecation_payload}`` for any priors with a
    deprecation row. Used by the matcher to skip deprecated priors at runtime.
    """
    out: Dict[str, Dict[str, Any]] = {}
    for r in read_curation_log(path):
        if r.get("op") == "deprecate" and r.get("prior_id"):
            out[r["prior_id"]] = {
                "date": r.get("ts"),
                "reason": r.get("reason"),
                "superseded_by": r.get("superseded_by"),
            }
        elif r.get("op") == "undeprecate" and r.get("prior_id") in out:
            del out[r["prior_id"]]
    return out


# ---------------------------------------------------------------------------
# Duplicate detection
# ---------------------------------------------------------------------------

@dataclass
class DuplicatePair:
    a_id: str
    b_id: str
    score: float                # 0..1
    reason: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def find_duplicates(threshold: float = 0.85) -> List[DuplicatePair]:
    """Find priors that look near-duplicate.

    Heuristic similarity score:
      * +0.5 if matches_runner_form is identical
      * +0.2 if domain is identical
      * +0.2 if structural_form strings are highly similar (Jaccard on tokens)
      * +0.1 if free_parameters keys overlap heavily
    """
    priors = list_priors()
    out: List[DuplicatePair] = []
    for i in range(len(priors)):
        for j in range(i + 1, len(priors)):
            a, b = priors[i], priors[j]
            score = 0.0
            reasons: List[str] = []
            if a.matches_runner_form and a.matches_runner_form == b.matches_runner_form:
                score += 0.5
                reasons.append(f"both map to runner_form={a.matches_runner_form}")
            if a.domain == b.domain:
                score += 0.2
                reasons.append(f"both in domain={a.domain}")
            jacc = _jaccard_tokens(a.structural_form, b.structural_form)
            if jacc >= 0.6:
                score += 0.2
                reasons.append(f"structural_form similarity {jacc:.2f}")
            ka = set((a.free_parameters or {}).keys())
            kb = set((b.free_parameters or {}).keys())
            if ka and kb:
                ovl = len(ka & kb) / max(len(ka | kb), 1)
                if ovl >= 0.6:
                    score += 0.1
                    reasons.append(f"param-key overlap {ovl:.2f}")
            if score >= threshold:
                out.append(DuplicatePair(
                    a_id=a.id, b_id=b.id, score=score,
                    reason="; ".join(reasons),
                ))
    out.sort(key=lambda d: -d.score)
    return out


def _jaccard_tokens(a: str, b: str) -> float:
    """Quick set-similarity over alphanumeric tokens."""
    import re
    ta = set(re.findall(r"[a-zA-Z0-9_]+", a or ""))
    tb = set(re.findall(r"[a-zA-Z0-9_]+", b or ""))
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


# ---------------------------------------------------------------------------
# Contradiction detection
# ---------------------------------------------------------------------------

@dataclass
class Contradiction:
    a_id: str
    b_id: str
    reason: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def find_contradictions() -> List[Contradiction]:
    """Find priors that share a domain_of_validity but assert opposite signs.

    Conservative: we only flag when priors have OVERLAPPING domain tags AND
    incompatible sign_constraints on shared parameter keys. Often the right
    response is to keep both with refined domain_of_validity, not to delete.
    """
    priors = list_priors()
    out: List[Contradiction] = []
    for i in range(len(priors)):
        for j in range(i + 1, len(priors)):
            a, b = priors[i], priors[j]
            # Quick filter: must share domain.
            if a.domain != b.domain:
                continue
            shared = set(a.sign_constraints.keys()) & set(b.sign_constraints.keys())
            if not shared:
                continue
            for k in shared:
                sa = a.sign_constraints.get(k)
                sb = b.sign_constraints.get(k)
                if sa and sb and sa != sb and {sa, sb} == {"positive", "negative"}:
                    out.append(Contradiction(
                        a_id=a.id, b_id=b.id,
                        reason=(f"both in domain={a.domain}, but param '{k}' "
                                f"sign disagrees: {a.id}→{sa}, {b.id}→{sb}"),
                    ))
                    break
    return out


# ---------------------------------------------------------------------------
# Deprecation workflow
# ---------------------------------------------------------------------------

def deprecate(prior_id: str, *, reason: str,
              superseded_by: Optional[str] = None,
              path: Optional[Path] = None) -> Dict[str, Any]:
    """Mark a prior deprecated. Records the event; does NOT delete the prior.

    Future calls to ``get_prior(prior_id)`` still return the prior — but
    matchers consult ``deprecation_overrides()`` and skip deprecated priors.
    To revive a prior, call ``undeprecate(prior_id)``.
    """
    if get_prior(prior_id) is None:
        raise ValueError(f"unknown prior: {prior_id!r}")
    row = {
        "row_type": "prior_curation_v1",
        "op": "deprecate",
        "prior_id": prior_id,
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "reason": reason,
        "superseded_by": superseded_by,
    }
    _append(row, path)
    return row


def undeprecate(prior_id: str, *, reason: str,
                path: Optional[Path] = None) -> Dict[str, Any]:
    if get_prior(prior_id) is None:
        raise ValueError(f"unknown prior: {prior_id!r}")
    row = {
        "row_type": "prior_curation_v1",
        "op": "undeprecate",
        "prior_id": prior_id,
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "reason": reason,
    }
    _append(row, path)
    return row


# ---------------------------------------------------------------------------
# Library diff
# ---------------------------------------------------------------------------

def snapshot_library() -> List[Dict[str, Any]]:
    """Return a list of dicts capturing every prior's CURRENT definition.

    Pair with ``diff_libraries`` to compute what changed between two
    snapshots (e.g. before vs. after a curation pass).
    """
    return [p.to_dict() for p in list_priors()]


def diff_libraries(a: List[Dict[str, Any]],
                   b: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    """Diff two library snapshots. ``a`` is "before", ``b`` is "after".

    Returns ``{added, removed, modified}`` lists. ``modified`` entries carry
    only the keys that differ.
    """
    a_by_id = {p["id"]: p for p in a}
    b_by_id = {p["id"]: p for p in b}
    added = [b_by_id[k] for k in b_by_id if k not in a_by_id]
    removed = [a_by_id[k] for k in a_by_id if k not in b_by_id]
    modified: List[Dict[str, Any]] = []
    for k in a_by_id:
        if k not in b_by_id:
            continue
        before, after = a_by_id[k], b_by_id[k]
        diff: Dict[str, Any] = {}
        for key in set(before.keys()) | set(after.keys()):
            if before.get(key) != after.get(key):
                diff[key] = {"before": before.get(key), "after": after.get(key)}
        if diff:
            modified.append({"id": k, "changes": diff})
    return {"added": added, "removed": removed, "modified": modified}
