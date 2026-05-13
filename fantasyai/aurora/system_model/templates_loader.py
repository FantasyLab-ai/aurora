"""
Domain-template loader and registry.

Templates are declarative JSON files at ``system_model/templates/<id>/v1.json``
plus optional small Python modules for domain-specific identification logic.
The framework reads these dynamically; adding a new domain is content not code.

Each template carries:

  * id, version, display_name, domain
  * identification: how to recognise a dataset belongs to this domain
        - column_keywords: list of token patterns we expect to see
        - value_range_hints: per-token expected ranges
        - cadence_hints: list of allowed cadences ("daily", "subhourly", ...)
  * topology_skeleton: the entity slots (with role + unit hints) and the
                       template-expected relationships
  * characteristic_patterns: named patterns + detection signatures
  * vocabulary: domain language
  * action_library: canonical responses
  * constraints: hard / soft bounds
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Identification + scoring
# ---------------------------------------------------------------------------

@dataclass
class IdentificationRules:
    """Cheap, deterministic rules for matching a dataset to a template."""
    column_keywords: List[str] = field(default_factory=list)
    value_range_hints: Dict[str, Tuple[float, float]] = field(default_factory=dict)
    cadence_hints: List[str] = field(default_factory=list)
    domain_tags: List[str] = field(default_factory=list)
    min_keyword_hits: int = 1

    def to_dict(self) -> Dict[str, Any]:
        return {
            "column_keywords": list(self.column_keywords),
            "value_range_hints":
                {k: list(v) for k, v in self.value_range_hints.items()},
            "cadence_hints": list(self.cadence_hints),
            "domain_tags": list(self.domain_tags),
            "min_keyword_hits": self.min_keyword_hits,
        }


@dataclass
class DomainTemplate:
    """A complete, instantiable domain template."""
    id: str
    version: str
    display_name: str
    domain_tag: str
    identification: IdentificationRules
    topology_skeleton: Dict[str, Any]                # entity slots + expected rels
    characteristic_patterns: List[Dict[str, Any]]
    vocabulary: Dict[str, Any]
    action_library: List[Dict[str, Any]]
    constraints: List[Dict[str, Any]] = field(default_factory=list)
    description: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id, "version": self.version,
            "display_name": self.display_name,
            "domain_tag": self.domain_tag,
            "description": self.description,
            "identification": self.identification.to_dict(),
            "topology_skeleton": self.topology_skeleton,
            "characteristic_patterns": self.characteristic_patterns,
            "vocabulary": self.vocabulary,
            "action_library": self.action_library,
            "constraints": self.constraints,
        }


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------

def _templates_dir() -> Path:
    here = Path(__file__).resolve().parent
    return here / "templates"


def _load_one(json_path: Path) -> DomainTemplate:
    raw = json.loads(json_path.read_text(encoding="utf-8"))
    ident = IdentificationRules(
        column_keywords=raw.get("identification", {}).get("column_keywords") or [],
        value_range_hints={
            k: tuple(v) for k, v in
            (raw.get("identification", {}).get("value_range_hints") or {}).items()
        },
        cadence_hints=raw.get("identification", {}).get("cadence_hints") or [],
        domain_tags=raw.get("identification", {}).get("domain_tags") or [],
        min_keyword_hits=raw.get("identification", {}).get("min_keyword_hits") or 1,
    )
    return DomainTemplate(
        id=raw.get("id", json_path.parent.name),
        version=raw.get("version", "v1"),
        display_name=raw.get("display_name", raw.get("id", "?")),
        domain_tag=raw.get("domain_tag", raw.get("id", "?")),
        identification=ident,
        topology_skeleton=raw.get("topology_skeleton") or {},
        characteristic_patterns=raw.get("characteristic_patterns") or [],
        vocabulary=raw.get("vocabulary") or {},
        action_library=raw.get("action_library") or [],
        constraints=raw.get("constraints") or [],
        description=raw.get("description", ""),
    )


def list_templates(reload: bool = False) -> List[DomainTemplate]:
    """Walk the templates directory; return one DomainTemplate per <id>/v1.json."""
    out: List[DomainTemplate] = []
    root = _templates_dir()
    if not root.exists():
        return []
    for sub in sorted(root.iterdir()):
        if not sub.is_dir():
            continue
        # Pick the highest version available; for v1 we just read v1.json.
        candidates = sorted(sub.glob("v*.json"))
        if not candidates:
            continue
        try:
            out.append(_load_one(candidates[-1]))
        except Exception:
            # A broken template never blocks the rest.
            continue
    return out


def get_template(template_id: str) -> Optional[DomainTemplate]:
    for t in list_templates():
        if t.id == template_id:
            return t
    return None


# ---------------------------------------------------------------------------
# Scoring a dataset against a template
# ---------------------------------------------------------------------------

@dataclass
class IdentificationScore:
    template_id: str
    score: float                # 0..1
    keyword_hits: List[str]
    cadence_match: bool
    rationale: str


def _normalize(s: str) -> str:
    return re.sub(r"[^a-z0-9]", " ", str(s or "").lower())


def score_template(template: DomainTemplate, columns: List[str],
                   cadence: Optional[str] = None,
                   numeric_ranges: Optional[Dict[str, Tuple[float, float]]] = None,
                   ) -> IdentificationScore:
    """Score how well a dataset (its column names + cadence) matches a template.

    Pure deterministic: keyword overlap × cadence match × value-range match.
    Returns 0..1.
    """
    cols_norm = {_normalize(c) for c in columns}
    keywords = [k.lower() for k in template.identification.column_keywords]
    hits: List[str] = []
    for kw in keywords:
        kw_norm = _normalize(kw).strip()
        if not kw_norm:
            continue
        # Short keywords (≤ 3 chars) require an exact word match — substring
        # would generate false positives like "hr" in "three". Long keywords
        # may match as substrings of column names ("temp" in "temperature").
        if len(kw_norm) <= 3:
            matched = any(kw_norm in c.split() for c in cols_norm)
        else:
            matched = any(kw_norm in c.split() or kw_norm in c for c in cols_norm)
        if matched:
            hits.append(kw)
    keyword_score = (len(hits) /
                      max(len(keywords), 1)) if keywords else 0.0
    # Cadence multiplier (1.0 if match or no hint, 0.5 if mismatch).
    cad_match = True
    if template.identification.cadence_hints and cadence:
        cad_match = cadence in template.identification.cadence_hints
    cad_factor = 1.0 if cad_match else 0.5
    # Value range hits — count how many keywords have a known typical range
    # AND the data contains a numeric column whose range overlaps.
    range_factor = 1.0
    if numeric_ranges and template.identification.value_range_hints:
        hits_in_range = 0
        total = 0
        for k, (lo, hi) in template.identification.value_range_hints.items():
            total += 1
            for col, (mn, mx) in numeric_ranges.items():
                if _normalize(k) in _normalize(col):
                    if (mx >= lo) and (mn <= hi):
                        hits_in_range += 1
                        break
        if total:
            range_factor = 0.5 + 0.5 * (hits_in_range / total)
    score = keyword_score * cad_factor * range_factor
    score = min(1.0, max(0.0, score))
    rationale = (
        f"{len(hits)}/{max(1,len(keywords))} keyword hits "
        f"({hits[:3]}…); cadence_match={cad_match}; "
        f"range_factor={range_factor:.2f}"
    )
    return IdentificationScore(
        template_id=template.id, score=score,
        keyword_hits=hits, cadence_match=cad_match, rationale=rationale,
    )


def best_template_for(columns: List[str],
                       cadence: Optional[str] = None,
                       numeric_ranges: Optional[Dict[str, Tuple[float, float]]] = None,
                       ) -> Tuple[Optional[DomainTemplate], List[IdentificationScore]]:
    """Score every loaded template; return the best match + the full ranking."""
    templates = list_templates()
    if not templates:
        return None, []
    scores = [score_template(t, columns, cadence, numeric_ranges) for t in templates]
    scores.sort(key=lambda s: -s.score)
    by_id = {t.id: t for t in templates}
    best = by_id[scores[0].template_id] if scores and scores[0].score > 0 else None
    return best, scores
