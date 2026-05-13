"""
Template evolution + deviation tracking — Phase 5 scaffolding.

Every instantiated system model writes a tiny "template-evidence" row to
``~/.aurora/template_evidence.jsonl``: which template was used, which
expected relationships were confirmed / silent / contradicted, which novel
relationships were discovered. Over time these rows accumulate evidence
about how kinds-of-systems actually behave.

We deliberately *propose* template evolutions; we never auto-apply them. A
human reviews the diff before the canonical template changes. Eventually
this can be opt-in to a community feed.

Public API:

  record_evidence(state)  → append a row tied to this run's system_model
  evolution_proposals(template_id) → list of suggested edits with rationale
  diff_template(template_id) → diff base vs. evolved version (when present)
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Any, Dict, List, Optional


def default_evidence_path() -> Path:
    override = os.environ.get("AURORA_TEMPLATE_EVIDENCE")
    if override:
        return Path(override).expanduser().resolve()
    return Path.home() / ".aurora" / "template_evidence.jsonl"


def _ensure_dir(p: Path) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)


def record_evidence(state: Dict[str, Any],
                     path: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    """Append a row summarising this run's contribution to its template.

    Returns the row written, or None on failure (silent).
    """
    try:
        sm = state.get("system_model") or {}
        prov = sm.get("provenance") or {}
        if not prov.get("template_id"):
            return None
        rels = (sm.get("topology") or {}).get("relationships") or []
        row = {
            "row_type": "template_evidence_v1",
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "run_dir": state.get("run_dir"),
            "template_id": prov.get("template_id"),
            "template_version": prov.get("template_version"),
            "confirmed_count": len(prov.get("confirmed_relationships") or []),
            "discovered_count": len(prov.get("discovered_relationships") or []),
            "deviation_count": len(prov.get("deviation_relationships") or []),
            "silent_count": sum(
                1 for r in rels
                if r.get("source_origin") == "template_expected"
                and (r.get("confidence") is None or r.get("confidence") < 0.5)
            ),
            "discovered_ids": prov.get("discovered_relationships") or [],
            "deviation_ids": prov.get("deviation_relationships") or [],
            "confidence": float(sm.get("confidence") or 0.0),
        }
        path = path or default_evidence_path()
        _ensure_dir(path)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, separators=(",", ":")) + "\n")
        return row
    except Exception:
        return None


def read_evidence(template_id: Optional[str] = None,
                  path: Optional[Path] = None) -> List[Dict[str, Any]]:
    path = path or default_evidence_path()
    if not path.exists():
        return []
    rows: List[Dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except Exception:
            continue
        if r.get("row_type") != "template_evidence_v1":
            continue
        if template_id and r.get("template_id") != template_id:
            continue
        rows.append(r)
    return rows


@dataclass
class EvolutionProposal:
    template_id: str
    kind: str    # add_relationship | remove_relationship | split_template | adjust_weight
    rationale: str
    support_n: int
    target_relationship_id: Optional[str] = None
    payload: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def evolution_proposals(template_id: str,
                        path: Optional[Path] = None,
                        min_support: int = 3,
                        ) -> List[Dict[str, Any]]:
    """Surface proposed edits to ``template_id`` based on accumulated evidence.

    Heuristics (deliberately conservative — proposals, never auto-apply):

      * If the same DISCOVERED relationship_id appears in ≥ ``min_support``
        runs, propose adding it to the template.
      * If the same EXPECTED relationship_id is silent in ≥ ``min_support``
        runs (and never confirmed), propose removing it OR narrowing the
        domain_of_validity.
      * If the same DEVIATION_id appears in ≥ ``min_support`` runs, propose
        splitting the template into a sub-domain template.
    """
    rows = read_evidence(template_id=template_id, path=path)
    if not rows:
        return []
    discovered_counts: Dict[str, int] = {}
    deviation_counts: Dict[str, int] = {}
    for r in rows:
        for rid in r.get("discovered_ids") or []:
            discovered_counts[rid] = discovered_counts.get(rid, 0) + 1
        for rid in r.get("deviation_ids") or []:
            deviation_counts[rid] = deviation_counts.get(rid, 0) + 1

    proposals: List[EvolutionProposal] = []
    for rid, n in discovered_counts.items():
        if n >= min_support:
            proposals.append(EvolutionProposal(
                template_id=template_id, kind="add_relationship",
                target_relationship_id=rid, support_n=n,
                rationale=(f"Discovered relationship {rid} appeared in {n} "
                           f"runs but isn't in the current template skeleton."),
            ))
    for rid, n in deviation_counts.items():
        if n >= min_support:
            proposals.append(EvolutionProposal(
                template_id=template_id, kind="split_template",
                target_relationship_id=rid, support_n=n,
                rationale=(f"Deviation {rid} appeared in {n} runs — possible "
                           f"sub-domain split (e.g. shallow-water variant)."),
            ))
    proposals.sort(key=lambda p: -p.support_n)
    return [p.to_dict() for p in proposals]


def evolution_summary(path: Optional[Path] = None) -> Dict[str, Any]:
    """Aggregate evidence per-template; what the morning diary can show."""
    rows = read_evidence(path=path)
    by_template: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        tid = r.get("template_id")
        if not tid:
            continue
        b = by_template.setdefault(tid, {
            "n_runs": 0, "avg_confidence": 0.0,
            "n_discovered": 0, "n_deviations": 0,
        })
        b["n_runs"] += 1
        b["avg_confidence"] += float(r.get("confidence") or 0.0)
        b["n_discovered"] += int(r.get("discovered_count") or 0)
        b["n_deviations"] += int(r.get("deviation_count") or 0)
    for b in by_template.values():
        if b["n_runs"]:
            b["avg_confidence"] /= b["n_runs"]
    return {"templates": by_template, "n_total_runs": len(rows)}
