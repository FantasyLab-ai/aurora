"""
Knowledge-bank entry schema.

Every entry in Aurora's knowledge bank is a structured record with full
provenance. Schema designed so that:

  * Every field traces back to its source (no inventions).
  * Every claim can be verified against the cited reference.
  * Every entry is versioned so refresh-time updates are auditable.
  * Embeddings + structured triggers are co-located so retrieval can use
    either without a separate side-channel join.

Validation is separate from construction so we can ingest first and
quarantine failures rather than crash mid-pipeline.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Sub-records
# ---------------------------------------------------------------------------

@dataclass
class EntryReference:
    """One citation supporting an entry's claims."""
    citation: str                 # human-readable, e.g. "Reppert SM, Weaver DR (2002)"
    doi: Optional[str] = None
    url: Optional[str] = None
    page: Optional[str] = None    # for textbook references


@dataclass
class PatternSignature:
    """How this concept is expected to manifest in data Aurora analyses.

    The retrieval engine uses these to do structured matching against
    findings without going through semantic search.
    """
    pattern_type: str             # e.g. "oscillation", "monotonic_growth", "regime_shift"
    characteristics: str          # human description
    detection_hints: str = ""     # what method/parameter range to look for


@dataclass
class QuantitativeRange:
    """A numeric range or context for a parameter."""
    property: str                 # e.g. "period", "amplitude"
    typical_range: Optional[List[float]] = None    # [low, high]
    unit: Optional[str] = None
    context: Optional[str] = None


@dataclass
class Caveat:
    """A caveat or limitation associated with the entry."""
    text: str
    severity: str = "minor"       # "minor" | "important" | "critical"


# ---------------------------------------------------------------------------
# Top-level entry
# ---------------------------------------------------------------------------

@dataclass
class KnowledgeEntry:
    """One vetted concept entry.

    Shape mirrors the brief's schema; serialisable to/from JSON for
    ingest checkpoints + admin review.
    """
    # Identity + provenance
    id: str
    source: str                                       # e.g. "gene_ontology"
    source_version: str                               # "2025-12-01"
    domain: List[str] = field(default_factory=list)   # ["bio", "medical"]
    concept_type: str = ""                            # source taxonomy
    review_status: str = "pending_review"             # auto_approved | pending_review | human_approved

    # Content
    name: str = ""
    aliases: List[str] = field(default_factory=list)
    definition: str = ""
    mechanism: str = ""

    # Cross-references
    related_concepts: List[str] = field(default_factory=list)

    # Aurora-facing structured fields
    pattern_signatures: List[PatternSignature] = field(default_factory=list)
    quantitative_ranges: List[QuantitativeRange] = field(default_factory=list)
    caveats: List[Caveat] = field(default_factory=list)

    # Citations
    references: List[EntryReference] = field(default_factory=list)

    # Embeddings — list of floats; shape is intentionally permissive.
    embedding: Optional[List[float]] = None
    embedding_model: Optional[str] = None

    # Audit
    ingest_method: str = "human_authored"             # ontology_etl | llm_extracted | human_authored
    quality_score: float = 1.0
    ingested_at: Optional[str] = None
    revoked: bool = False
    revoked_reason: Optional[str] = None

    # Brief 5 — license tracking. Mandatory at ingest time. Snapshot
    # value (not a live link) so a license change at the source doesn't
    # retroactively alter what's binding for this entry.
    license: str = ""                                  # short SPDX-style label, e.g. "CC BY 4.0"
    license_url: str = ""                              # canonical URL of the license text
    license_verified_at: Optional[str] = None          # when ingest verified the license string
    commercial_use_allowed: bool = False               # convenience flag derived at ingest

    # ---------------------------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        # Lists of dataclasses serialise OK via asdict; enums and complex
        # types not used here.
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "KnowledgeEntry":
        d = dict(d)
        # Re-hydrate nested dataclasses.
        if d.get("references"):
            d["references"] = [EntryReference(**r) for r in d["references"]]
        if d.get("pattern_signatures"):
            d["pattern_signatures"] = [PatternSignature(**p)
                                          for p in d["pattern_signatures"]]
        if d.get("quantitative_ranges"):
            d["quantitative_ranges"] = [QuantitativeRange(**q)
                                           for q in d["quantitative_ranges"]]
        if d.get("caveats"):
            d["caveats"] = [Caveat(**c) for c in d["caveats"]]
        # Drop unknown fields (forward compat).
        valid_keys = {f.name for f in cls.__dataclass_fields__.values()}
        d = {k: v for k, v in d.items() if k in valid_keys}
        return cls(**d)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), default=str, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

REVIEW_STATUSES = {"auto_approved", "pending_review", "human_approved"}
INGEST_METHODS = {"ontology_etl", "llm_extracted", "human_authored",
                    "wikipedia_extract", "wikidata_extract", "textbook_extract",
                    "api_etl", "ndbc_scrape", "obo_parse", "swissprot_parse"}


# License registry — the authoritative list of accepted licenses. Each
# entry maps the canonical short label to (url, commercial_use_allowed).
# The ingest pipeline checks against this; an unknown license blocks
# ingestion rather than guessing.
KNOWN_LICENSES: Dict[str, Dict[str, Any]] = {
    "CC0 1.0":         {"url": "https://creativecommons.org/publicdomain/zero/1.0/",
                          "commercial": True},
    "CC BY 4.0":       {"url": "https://creativecommons.org/licenses/by/4.0/",
                          "commercial": True},
    "CC BY-SA 4.0":    {"url": "https://creativecommons.org/licenses/by-sa/4.0/",
                          "commercial": True,
                          "share_alike": True},
    "Public Domain (US Government)": {
        "url": "https://www.usa.gov/government-works",
        "commercial": True},
    "FRED Public Data — US Federal Reserve, free for any use": {
        "url": "https://fred.stlouisfed.org/legal",
        "commercial": True},
    "HPO License":     {"url": "https://hpo.jax.org/app/license",
                          "commercial": True},
    "KEGG Open Subset": {"url": "https://www.kegg.jp/kegg/legal.html",
                            "commercial": False,
                            "academic_only": True},
    "OpenStax CC BY 4.0": {"url": "https://openstax.org/about",
                              "commercial": True},
    "Aurora Internal":  {"url": "n/a",
                            "commercial": True,
                            "internal": True},
}


def validate_entry(entry: KnowledgeEntry) -> Dict[str, Any]:
    """Run quality checks. Returns ``{ok, errors, warnings}``.

    Required fields: id, source, source_version, name, definition.
    Definition non-empty (no stub entries).
    Review status in known set.
    Ingest method in known set.
    Quality score in [0, 1].
    """
    errors: List[str] = []
    warnings: List[str] = []

    if not entry.id or not isinstance(entry.id, str):
        errors.append("id missing or non-string")
    elif not re.match(r"^[a-z0-9_\-:.]+$", entry.id):
        warnings.append(f"id '{entry.id}' contains unusual characters")
    if not entry.source:
        errors.append("source missing")
    if not entry.source_version:
        errors.append("source_version missing")
    if not entry.name:
        errors.append("name missing")
    if not entry.definition or len(entry.definition.strip()) < 8:
        errors.append("definition missing or too short (need ≥ 8 chars)")
    if entry.review_status not in REVIEW_STATUSES:
        errors.append(f"review_status '{entry.review_status}' not in "
                       f"{sorted(REVIEW_STATUSES)}")
    if entry.ingest_method not in INGEST_METHODS:
        warnings.append(f"ingest_method '{entry.ingest_method}' "
                          f"not in {sorted(INGEST_METHODS)}")
    if not (0.0 <= entry.quality_score <= 1.0):
        errors.append(f"quality_score {entry.quality_score} not in [0, 1]")
    if not entry.domain:
        warnings.append("entry has no domain tags")
    if not entry.references:
        warnings.append("entry has no references")
    # Brief 5 — license is mandatory.
    if not entry.license:
        errors.append(
            "license missing (Brief 5: every entry must carry its source "
            "license string)"
        )
    elif entry.license not in KNOWN_LICENSES and not entry.license.startswith("Aurora Internal"):
        warnings.append(
            f"license '{entry.license}' not in KNOWN_LICENSES — "
            "register it in schema.py before bulk ingest"
        )

    return {"ok": not errors, "errors": errors, "warnings": warnings}


def attach_license(entry: KnowledgeEntry, license_label: str,
                     *, verified_at: Optional[str] = None) -> KnowledgeEntry:
    """Helper for ingest modules: attach a registered license to an
    entry. Looks up commercial-use flag + canonical URL from
    :data:`KNOWN_LICENSES`. Raises if the label isn't known."""
    info = KNOWN_LICENSES.get(license_label)
    if info is None:
        raise ValueError(
            f"License '{license_label}' is not in KNOWN_LICENSES. "
            f"Register it in fantasyai/aurora/knowledge_bank/schema.py "
            f"before ingest."
        )
    entry.license = license_label
    entry.license_url = info.get("url", "")
    entry.commercial_use_allowed = bool(info.get("commercial", False))
    entry.license_verified_at = verified_at or _now_iso()
    return entry


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
